"""迭代 12：链码 v2 升级 + 状态迁移 后端集成测试。"""


def _admin(make_user, login_token, username="adm"):
    make_user(
        username,
        password="apass",
        role="admin",
        real_name=f"管理员 {username}",
        msp_org="Org1MSP",
    )
    return login_token(username, "apass")


def _hospital(make_user, login_token, username="hospA"):
    make_user(
        username,
        password="h123",
        role="hospital",
        real_name="医院甲医生",
        hospital_name="HospitalA",
        msp_org="Org1MSP",
    )
    return login_token(username, "h123")


def _patient(make_user, login_token, username="patA"):
    make_user(username, password="p123", role="patient", real_name="患者")
    return login_token(username, "p123")


class TestSystemInfo:
    def test_returns_schema_version_and_kinds(
        self, client, make_user, login_token
    ):
        adm = _admin(make_user, login_token)
        r = client.get(
            "/api/system/info", headers={"Authorization": f"Bearer {adm}"}
        )
        assert r.status_code == 200
        body = r.json()
        assert body["schema_version"] == "v2"
        assert "GENERAL" in body["contract_kinds"]["record_categories"]
        assert "RESEARCH" in body["contract_kinds"]["request_purposes"]


class TestCreateWithV2Fields:
    def test_record_create_with_category_persists(
        self, client, make_user, login_token
    ):
        pt = _patient(make_user, login_token)
        pid = client.get(
            "/api/auth/me", headers={"Authorization": f"Bearer {pt}"}
        ).json()["id"]
        h = _hospital(make_user, login_token)
        r = client.post(
            "/api/records",
            headers={"Authorization": f"Bearer {h}"},
            json={
                "patient_id": pid,
                "title": "急诊病历",
                "diagnosis": "急性阑尾炎",
                "content": "...",
                "category": "EMERGENCY",
            },
        )
        assert r.status_code == 200, r.text
        assert r.json()["category"] == "EMERGENCY"

    def test_record_create_default_general(
        self, client, make_user, login_token
    ):
        pt = _patient(make_user, login_token)
        pid = client.get(
            "/api/auth/me", headers={"Authorization": f"Bearer {pt}"}
        ).json()["id"]
        h = _hospital(make_user, login_token)
        r = client.post(
            "/api/records",
            headers={"Authorization": f"Bearer {h}"},
            json={
                "patient_id": pid,
                "title": "一般病历",
                "diagnosis": "感冒",
                "content": "...",
            },
        )
        assert r.status_code == 200
        assert r.json()["category"] == "GENERAL"

    def test_record_create_invalid_category_422(
        self, client, make_user, login_token
    ):
        pt = _patient(make_user, login_token)
        pid = client.get(
            "/api/auth/me", headers={"Authorization": f"Bearer {pt}"}
        ).json()["id"]
        h = _hospital(make_user, login_token)
        r = client.post(
            "/api/records",
            headers={"Authorization": f"Bearer {h}"},
            json={
                "patient_id": pid,
                "title": "x",
                "diagnosis": "y",
                "content": "z",
                "category": "DENTAL",
            },
        )
        assert r.status_code == 422

    def test_access_request_purpose_persists(
        self, client, make_user, login_token
    ):
        pt = _patient(make_user, login_token)
        pid = client.get(
            "/api/auth/me", headers={"Authorization": f"Bearer {pt}"}
        ).json()["id"]
        hA = _hospital(make_user, login_token, "hospA")
        rid = client.post(
            "/api/records",
            headers={"Authorization": f"Bearer {hA}"},
            json={
                "patient_id": pid,
                "title": "x",
                "diagnosis": "y",
                "content": "z",
            },
        ).json()["id"]

        make_user(
            "hospB",
            password="h",
            role="hospital",
            real_name="医院乙",
            hospital_name="HospitalB",
            msp_org="Org2MSP",
        )
        hB = login_token("hospB", "h")
        r = client.post(
            "/api/access-requests",
            headers={"Authorization": f"Bearer {hB}"},
            json={
                "record_id": rid,
                "reason": "需要研究数据",
                "purpose": "RESEARCH",
            },
        )
        assert r.status_code == 200
        assert r.json()["purpose"] == "RESEARCH"


class TestMigration:
    def test_admin_migrate_records_persists_to_db(
        self, client, make_user, login_token
    ):
        pt = _patient(make_user, login_token)
        pid = client.get(
            "/api/auth/me", headers={"Authorization": f"Bearer {pt}"}
        ).json()["id"]
        h = _hospital(make_user, login_token)
        rid = client.post(
            "/api/records",
            headers={"Authorization": f"Bearer {h}"},
            json={
                "patient_id": pid,
                "title": "x",
                "diagnosis": "y",
                "content": "z",
            },
        ).json()["id"]

        adm = _admin(make_user, login_token)
        r = client.post(
            "/api/admin/migrate/records-v2",
            headers={"Authorization": f"Bearer {adm}"},
            json={"items": [{"record_id": rid, "category": "INPATIENT"}]},
        )
        assert r.status_code == 200, r.text
        assert r.json()["count"] == 1
        # 验证 DB 镜像已同步
        list_resp = client.get(
            "/api/records", headers={"Authorization": f"Bearer {adm}"}
        )
        target = next((x for x in list_resp.json() if x["id"] == rid), None)
        assert target is not None
        assert target["category"] == "INPATIENT"

    def test_migrate_non_admin_403(self, client, make_user, login_token):
        h = _hospital(make_user, login_token)
        r = client.post(
            "/api/admin/migrate/records-v2",
            headers={"Authorization": f"Bearer {h}"},
            json={"items": [{"record_id": 1, "category": "INPATIENT"}]},
        )
        assert r.status_code == 403

    def test_migrate_missing_record_404(
        self, client, make_user, login_token
    ):
        adm = _admin(make_user, login_token)
        r = client.post(
            "/api/admin/migrate/records-v2",
            headers={"Authorization": f"Bearer {adm}"},
            json={"items": [{"record_id": 9999, "category": "INPATIENT"}]},
        )
        assert r.status_code == 404


class TestByCategoryQuery:
    def test_query_filters_by_category(self, client, make_user, login_token):
        pt = _patient(make_user, login_token)
        pid = client.get(
            "/api/auth/me", headers={"Authorization": f"Bearer {pt}"}
        ).json()["id"]
        h = _hospital(make_user, login_token)
        for cat in ["INPATIENT", "OUTPATIENT", "INPATIENT"]:
            client.post(
                "/api/records",
                headers={"Authorization": f"Bearer {h}"},
                json={
                    "patient_id": pid,
                    "title": cat,
                    "diagnosis": "d",
                    "content": "c",
                    "category": cat,
                },
            )
        adm = _admin(make_user, login_token)
        r = client.get(
            "/api/records/chain/by-category?category=INPATIENT",
            headers={"Authorization": f"Bearer {adm}"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["fetched_count"] == 2
