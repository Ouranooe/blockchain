"""迭代 7：CouchDB 富查询后端集成测试。"""

import io


def _hospital(make_user, login_token, username, hosp, org):
    make_user(
        username,
        password="h123",
        role="hospital",
        real_name=f"{hosp} 医生",
        hospital_name=hosp,
        msp_org=org,
    )
    return login_token(username, "h123")


def _patient(make_user, login_token, username="patA"):
    make_user(username, password="p123", role="patient", real_name="患者")
    return login_token(username, "p123")


def _upload(client, hosp_token, patient_id, payload=b"x" * 32):
    files = {"file": ("r.pdf", io.BytesIO(payload), "application/pdf")}
    form = {
        "patient_id": str(patient_id),
        "title": "t",
        "diagnosis": "d",
        "description": "",
    }
    return client.post(
        "/api/records/upload",
        headers={"Authorization": f"Bearer {hosp_token}"},
        files=files,
        data=form,
    ).json()


class TestChainRecordsByHospital:
    def test_hospital_sees_own_records(self, client, make_user, login_token):
        pt = _patient(make_user, login_token)
        pid = client.get(
            "/api/auth/me", headers={"Authorization": f"Bearer {pt}"}
        ).json()["id"]
        hA = _hospital(make_user, login_token, "hospA", "HospitalA", "Org1MSP")
        hB = _hospital(make_user, login_token, "hospB", "HospitalB", "Org2MSP")
        _upload(client, hA, pid)
        _upload(client, hA, pid)
        _upload(client, hB, pid)

        # hospA 默认查自己
        resp = client.get(
            "/api/records/chain/by-hospital",
            headers={"Authorization": f"Bearer {hA}"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["fetched_count"] == 2
        assert all(r["uploader_hospital"] == "HospitalA" for r in body["records"])

        # hospB
        resp = client.get(
            "/api/records/chain/by-hospital",
            headers={"Authorization": f"Bearer {hB}"},
        )
        assert resp.json()["fetched_count"] == 1

    def test_admin_can_query_any_hospital(self, client, make_user, login_token):
        pt = _patient(make_user, login_token)
        pid = client.get(
            "/api/auth/me", headers={"Authorization": f"Bearer {pt}"}
        ).json()["id"]
        hA = _hospital(make_user, login_token, "hospA", "HospitalA", "Org1MSP")
        _upload(client, hA, pid)
        _upload(client, hA, pid)

        make_user("adm", password="x", role="admin", real_name="A")
        adm = login_token("adm", "x")

        resp = client.get(
            "/api/records/chain/by-hospital?hospital=HospitalA",
            headers={"Authorization": f"Bearer {adm}"},
        )
        assert resp.status_code == 200
        assert resp.json()["fetched_count"] == 2

    def test_admin_without_hospital_400(self, client, make_user, login_token):
        make_user("adm", password="x", role="admin", real_name="A")
        adm = login_token("adm", "x")
        resp = client.get(
            "/api/records/chain/by-hospital",
            headers={"Authorization": f"Bearer {adm}"},
        )
        assert resp.status_code == 400

    def test_patient_forbidden(self, client, make_user, login_token):
        pt = _patient(make_user, login_token)
        resp = client.get(
            "/api/records/chain/by-hospital?hospital=HospitalA",
            headers={"Authorization": f"Bearer {pt}"},
        )
        assert resp.status_code == 403

    def test_pagination_no_loss_no_dup(
        self, client, make_user, login_token
    ):
        pt = _patient(make_user, login_token)
        pid = client.get(
            "/api/auth/me", headers={"Authorization": f"Bearer {pt}"}
        ).json()["id"]
        hA = _hospital(make_user, login_token, "hospA", "HospitalA", "Org1MSP")
        for _ in range(25):
            _upload(client, hA, pid)

        seen = set()
        bookmark = ""
        pages = 0
        while True:
            r = client.get(
                f"/api/records/chain/by-hospital?page_size=7&bookmark={bookmark}",
                headers={"Authorization": f"Bearer {hA}"},
            )
            body = r.json()
            for rec in body["records"]:
                assert rec["record_id"] not in seen
                seen.add(rec["record_id"])
            pages += 1
            if not body.get("bookmark"):
                break
            bookmark = body["bookmark"]
            if pages > 10:
                break
        assert len(seen) == 25
        assert pages == 4  # ceil(25/7)


class TestChainRecordsByDate:
    def test_admin_can_query_date_range(
        self, client, make_user, login_token
    ):
        pt = _patient(make_user, login_token)
        pid = client.get(
            "/api/auth/me", headers={"Authorization": f"Bearer {pt}"}
        ).json()["id"]
        hA = _hospital(make_user, login_token, "hospA", "HospitalA", "Org1MSP")
        _upload(client, hA, pid)

        make_user("adm", password="x", role="admin", real_name="A")
        adm = login_token("adm", "x")

        # 范围覆盖所有
        resp = client.get(
            "/api/records/chain/by-date?from=2020-01-01T00:00:00Z&to=2099-01-01T00:00:00Z",
            headers={"Authorization": f"Bearer {adm}"},
        )
        assert resp.status_code == 200
        assert resp.json()["fetched_count"] >= 1

    def test_non_admin_forbidden(self, client, make_user, login_token):
        hA = _hospital(make_user, login_token, "hospA", "HospitalA", "Org1MSP")
        resp = client.get(
            "/api/records/chain/by-date?from=2020-01-01T00:00:00Z&to=2099-01-01T00:00:00Z",
            headers={"Authorization": f"Bearer {hA}"},
        )
        assert resp.status_code == 403

    def test_missing_params_400(self, client, make_user, login_token):
        make_user("adm", password="x", role="admin", real_name="A")
        adm = login_token("adm", "x")
        resp = client.get(
            "/api/records/chain/by-date",
            headers={"Authorization": f"Bearer {adm}"},
        )
        assert resp.status_code == 422


class TestChainPendingForPatient:
    def test_patient_sees_own_pending_only(
        self, client, make_user, login_token
    ):
        pA_tok = _patient(make_user, login_token, "patA")
        pA_id = client.get(
            "/api/auth/me", headers={"Authorization": f"Bearer {pA_tok}"}
        ).json()["id"]
        make_user("patB", password="p", role="patient", real_name="B")
        pB_tok = login_token("patB", "p")
        pB_id = client.get(
            "/api/auth/me", headers={"Authorization": f"Bearer {pB_tok}"}
        ).json()["id"]

        hA = _hospital(make_user, login_token, "hospA", "HospitalA", "Org1MSP")
        hB = _hospital(make_user, login_token, "hospB", "HospitalB", "Org2MSP")

        recA = _upload(client, hA, pA_id)
        recB = _upload(client, hB, pB_id)

        # 两个申请，分别指向 A/B 的病历
        client.post(
            "/api/access-requests",
            headers={"Authorization": f"Bearer {hB}"},
            json={"record_id": recA["id"], "reason": "x"},
        )
        client.post(
            "/api/access-requests",
            headers={"Authorization": f"Bearer {hA}"},
            json={"record_id": recB["id"], "reason": "y"},
        )

        # patA 查自己 → 1 条（针对 recA）
        rA = client.get(
            "/api/access-requests/chain/pending",
            headers={"Authorization": f"Bearer {pA_tok}"},
        )
        assert rA.status_code == 200
        bodyA = rA.json()
        assert bodyA["fetched_count"] == 1
        assert bodyA["requests"][0]["patient_id"] == str(pA_id)

        # patB 查自己 → 1 条（针对 recB）
        rB = client.get(
            "/api/access-requests/chain/pending",
            headers={"Authorization": f"Bearer {pB_tok}"},
        ).json()
        assert rB["fetched_count"] == 1
        assert rB["requests"][0]["patient_id"] == str(pB_id)

    def test_approved_request_excluded(
        self, client, make_user, login_token
    ):
        pA_tok = _patient(make_user, login_token)
        pA_id = client.get(
            "/api/auth/me", headers={"Authorization": f"Bearer {pA_tok}"}
        ).json()["id"]
        hA = _hospital(make_user, login_token, "hospA", "HospitalA", "Org1MSP")
        hB = _hospital(make_user, login_token, "hospB", "HospitalB", "Org2MSP")
        rec = _upload(client, hA, pA_id)

        req = client.post(
            "/api/access-requests",
            headers={"Authorization": f"Bearer {hB}"},
            json={"record_id": rec["id"], "reason": "x"},
        ).json()
        # 批准 → 从 PENDING 出列
        client.post(
            f"/api/access-requests/{req['id']}/review",
            headers={"Authorization": f"Bearer {pA_tok}"},
            json={"decision": "APPROVED", "duration_days": 7, "max_reads": 3},
        )

        resp = client.get(
            "/api/access-requests/chain/pending",
            headers={"Authorization": f"Bearer {pA_tok}"},
        ).json()
        assert resp["fetched_count"] == 0

    def test_non_patient_forbidden(self, client, make_user, login_token):
        hA = _hospital(make_user, login_token, "hospA", "HospitalA", "Org1MSP")
        resp = client.get(
            "/api/access-requests/chain/pending",
            headers={"Authorization": f"Bearer {hA}"},
        )
        assert resp.status_code == 403
