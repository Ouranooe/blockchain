"""迭代 2：病历 CRUD + 版本链修订测试。"""


def _hospital_header(client, login_token, make_user, *, username="hospA"):
    make_user(
        username,
        password="hosp123",
        role="hospital",
        real_name="医院甲医生",
        hospital_name="HospitalA",
        msp_org="Org1MSP",
    )
    token = login_token(username, "hosp123")
    return {"Authorization": f"Bearer {token}"}


def _create_patient(make_user, username="patientA"):
    return make_user(
        username, password="pat123", role="patient", real_name="张三"
    )


class TestCreateRecord:
    def test_create_record_first_version(self, client, make_user, login_token):
        patient = _create_patient(make_user)
        header = _hospital_header(client, login_token, make_user)

        resp = client.post(
            "/api/records",
            headers=header,
            json={
                "patient_id": patient.id,
                "title": "门诊病历 001",
                "diagnosis": "感冒",
                "content": "患者发热 38 度",
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["version"] == 1
        assert body["previous_tx_id"] is None
        assert body["updated_at"] is None
        assert body["tx_id"] == f"rec-{body['id']}-v1"
        assert body["content_hash"]  # 有哈希
        assert body["can_view_content"] is True

    def test_create_record_rejects_non_patient(
        self, client, make_user, login_token
    ):
        header = _hospital_header(client, login_token, make_user)
        # patient_id 指向的不是 patient 角色
        other_hospital = make_user(
            "hospB", password="h", role="hospital", hospital_name="HospitalB"
        )
        resp = client.post(
            "/api/records",
            headers=header,
            json={
                "patient_id": other_hospital.id,
                "title": "x",
                "diagnosis": "x",
                "content": "x",
            },
        )
        assert resp.status_code == 400

    def test_patient_cannot_create_record(self, client, make_user, login_token):
        make_user("p", password="p", role="patient")
        token = login_token("p", "p")
        resp = client.post(
            "/api/records",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "patient_id": 1,
                "title": "x",
                "diagnosis": "x",
                "content": "x",
            },
        )
        assert resp.status_code == 403


class TestReviseRecord:
    def _seed_record(self, client, make_user, login_token):
        patient = _create_patient(make_user)
        header = _hospital_header(client, login_token, make_user)
        resp = client.post(
            "/api/records",
            headers=header,
            json={
                "patient_id": patient.id,
                "title": "原标题",
                "diagnosis": "原诊断",
                "content": "原内容 v1",
            },
        )
        return header, resp.json()

    def test_revise_bumps_version_and_sets_previous_tx_id(
        self, client, make_user, login_token
    ):
        header, rec = self._seed_record(client, make_user, login_token)
        rid = rec["id"]
        v1_tx = rec["tx_id"]

        resp = client.post(
            f"/api/records/{rid}/revise",
            headers=header,
            json={"content": "修订后内容 v2", "diagnosis": "更新诊断"},
        )
        assert resp.status_code == 200
        v2 = resp.json()
        assert v2["version"] == 2
        assert v2["previous_tx_id"] == v1_tx
        assert v2["tx_id"] == f"rec-{rid}-v2"
        assert v2["tx_id"] != v1_tx
        assert v2["updated_at"] is not None
        assert v2["diagnosis"] == "更新诊断"
        assert v2["content"] == "修订后内容 v2"
        assert v2["content_hash"] != rec["content_hash"]

    def test_revise_five_times_forms_version_chain(
        self, client, make_user, login_token
    ):
        header, rec = self._seed_record(client, make_user, login_token)
        rid = rec["id"]
        tx_history = [rec["tx_id"]]

        for v in range(2, 7):  # 共修订 5 次 → v2..v6
            resp = client.post(
                f"/api/records/{rid}/revise",
                headers=header,
                json={"content": f"内容 v{v}"},
            )
            assert resp.status_code == 200
            body = resp.json()
            assert body["version"] == v
            assert body["previous_tx_id"] == tx_history[-1]
            tx_history.append(body["tx_id"])

        # 从链上历史接口回验
        history = client.get(f"/api/records/{rid}/history", headers=header).json()
        assert history["latest_version"] == 6
        assert len(history["versions"]) == 6
        for idx, v in enumerate(history["versions"]):
            assert v["version"] == idx + 1
            expected_prev = "" if idx == 0 else tx_history[idx - 1]
            assert v["previous_tx_id"] == expected_prev
            assert v["tx_id"] == tx_history[idx]

    def test_revise_by_other_hospital_is_forbidden(
        self, client, make_user, login_token
    ):
        _, rec = self._seed_record(client, make_user, login_token)

        # 另一医院尝试修订
        make_user(
            "hospB",
            password="hp",
            role="hospital",
            real_name="医生B",
            hospital_name="HospitalB",
            msp_org="Org2MSP",
        )
        token = login_token("hospB", "hp")
        resp = client.post(
            f"/api/records/{rec['id']}/revise",
            headers={"Authorization": f"Bearer {token}"},
            json={"content": "恶意改写"},
        )
        assert resp.status_code == 403

    def test_revise_nonexistent_record_404(self, client, make_user, login_token):
        header = _hospital_header(client, login_token, make_user)
        resp = client.post(
            "/api/records/9999/revise",
            headers=header,
            json={"content": "x"},
        )
        assert resp.status_code == 404

    def test_revise_with_same_content_rejected(
        self, client, make_user, login_token
    ):
        header, rec = self._seed_record(client, make_user, login_token)
        resp = client.post(
            f"/api/records/{rec['id']}/revise",
            headers=header,
            json={"content": "原内容 v1"},
        )
        assert resp.status_code == 400

    def test_patient_cannot_revise(self, client, make_user, login_token):
        _, rec = self._seed_record(client, make_user, login_token)
        patient_token = login_token("patientA", "pat123")
        resp = client.post(
            f"/api/records/{rec['id']}/revise",
            headers={"Authorization": f"Bearer {patient_token}"},
            json={"content": "patient 试图修订"},
        )
        assert resp.status_code == 403


class TestRecordHistory:
    def test_history_contains_single_version_after_create(
        self, client, make_user, login_token
    ):
        patient = _create_patient(make_user)
        header = _hospital_header(client, login_token, make_user)
        rec = client.post(
            "/api/records",
            headers=header,
            json={
                "patient_id": patient.id,
                "title": "t",
                "diagnosis": "d",
                "content": "c",
            },
        ).json()

        history = client.get(
            f"/api/records/{rec['id']}/history", headers=header
        ).json()
        assert history["latest_version"] == 1
        assert len(history["versions"]) == 1
        assert history["versions"][0]["version"] == 1
        assert history["versions"][0]["previous_tx_id"] == ""

    def test_history_accessible_to_owner_patient(
        self, client, make_user, login_token
    ):
        patient = _create_patient(make_user)
        header = _hospital_header(client, login_token, make_user)
        rec = client.post(
            "/api/records",
            headers=header,
            json={
                "patient_id": patient.id,
                "title": "t",
                "diagnosis": "d",
                "content": "c",
            },
        ).json()

        ptoken = login_token("patientA", "pat123")
        resp = client.get(
            f"/api/records/{rec['id']}/history",
            headers={"Authorization": f"Bearer {ptoken}"},
        )
        assert resp.status_code == 200
        assert resp.json()["latest_version"] == 1

    def test_history_forbidden_for_unrelated_hospital(
        self, client, make_user, login_token
    ):
        patient = _create_patient(make_user)
        header = _hospital_header(client, login_token, make_user)
        rec = client.post(
            "/api/records",
            headers=header,
            json={
                "patient_id": patient.id,
                "title": "t",
                "diagnosis": "d",
                "content": "c",
            },
        ).json()

        make_user(
            "hospB", password="hp", role="hospital", hospital_name="HospitalB"
        )
        other_token = login_token("hospB", "hp")
        resp = client.get(
            f"/api/records/{rec['id']}/history",
            headers={"Authorization": f"Bearer {other_token}"},
        )
        assert resp.status_code == 403
