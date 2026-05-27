"""迭代 13：数据共享积分（FT）+ 经济激励 后端集成测试。"""


def _hospital(make_user, login_token, username="hospA", hosp_name="HospitalA", org="Org1MSP"):
    make_user(
        username,
        password="h123",
        role="hospital",
        real_name="医生",
        hospital_name=hosp_name,
        msp_org=org,
    )
    return login_token(username, "h123")


def _patient(make_user, login_token, username="patA"):
    make_user(username, password="p123", role="patient", real_name="患者")
    return login_token(username, "p123")


def _admin(make_user, login_token):
    make_user("adm", password="a", role="admin", real_name="A", msp_org="Org1MSP")
    return login_token("adm", "a")


def _create_record(client, hosp_token, pid, title="t"):
    return client.post(
        "/api/records",
        headers={"Authorization": f"Bearer {hosp_token}"},
        json={
            "patient_id": pid,
            "title": title,
            "diagnosis": "d",
            "content": f"c-{title}",
        },
    )


def _balance(client, token):
    return client.get(
        "/api/credits/balance", headers={"Authorization": f"Bearer {token}"}
    )


class TestAutoMint:
    def test_upload_record_mints_5_to_hospital(
        self, client, make_user, login_token
    ):
        pt = _patient(make_user, login_token)
        pid = client.get(
            "/api/auth/me", headers={"Authorization": f"Bearer {pt}"}
        ).json()["id"]
        h = _hospital(make_user, login_token)
        for i in range(3):
            _create_record(client, h, pid, title=f"t-{i}")
        b = _balance(client, h)
        assert b.status_code == 200
        # 3 上传 × 5 分
        assert b.json()["balance"] == 15

    def test_balance_initial_zero(self, client, make_user, login_token):
        pt = _patient(make_user, login_token)
        b = _balance(client, pt)
        assert b.status_code == 200
        assert b.json()["balance"] == 0


class TestTransfer:
    def _setup(self, client, make_user, login_token):
        pt = _patient(make_user, login_token)
        pid = client.get(
            "/api/auth/me", headers={"Authorization": f"Bearer {pt}"}
        ).json()["id"]
        h = _hospital(make_user, login_token)
        _create_record(client, h, pid)  # h 余额 +5
        return pt, pid, h

    def test_transfer_success(self, client, make_user, login_token):
        pt, pid, h = self._setup(client, make_user, login_token)
        # 医院 → 患者 转 3
        r = client.post(
            "/api/credits/transfer",
            headers={"Authorization": f"Bearer {h}"},
            json={"to_user_id": str(pid), "amount": 3, "reason_code": "GIFT"},
        )
        assert r.status_code == 200, r.text
        # 转账后医院余额 2
        assert r.json()["balance"] == 2

        # 患者余额 +3
        bp = _balance(client, pt).json()["balance"]
        assert bp == 3

    def test_transfer_insufficient_balance(
        self, client, make_user, login_token
    ):
        pt, pid, h = self._setup(client, make_user, login_token)
        # 医院余额 5；想转 100 → 400
        r = client.post(
            "/api/credits/transfer",
            headers={"Authorization": f"Bearer {h}"},
            json={"to_user_id": str(pid), "amount": 100},
        )
        assert r.status_code == 400
        assert "余额不足" in r.json()["detail"]

    def test_self_transfer_rejected(self, client, make_user, login_token):
        h = _hospital(make_user, login_token)
        # 医院 → 自己（hospital_name）
        r = client.post(
            "/api/credits/transfer",
            headers={"Authorization": f"Bearer {h}"},
            json={"to_user_id": "HospitalA", "amount": 1},
        )
        assert r.status_code == 400

    def test_transfer_amount_must_be_positive(
        self, client, make_user, login_token
    ):
        h = _hospital(make_user, login_token)
        r = client.post(
            "/api/credits/transfer",
            headers={"Authorization": f"Bearer {h}"},
            json={"to_user_id": "Some", "amount": 0},
        )
        # pydantic schema: amount > 0 → 422
        assert r.status_code == 422


class TestHistory:
    def test_history_shows_recent_ledger(
        self, client, make_user, login_token
    ):
        pt = _patient(make_user, login_token)
        pid = client.get(
            "/api/auth/me", headers={"Authorization": f"Bearer {pt}"}
        ).json()["id"]
        h = _hospital(make_user, login_token)
        _create_record(client, h, pid)
        _create_record(client, h, pid)

        r = client.get(
            "/api/credits/history",
            headers={"Authorization": f"Bearer {h}"},
        )
        assert r.status_code == 200
        body = r.json()
        # 至少 2 笔上传 mint
        assert body["fetched_count"] >= 2
        # 每笔都是流入 HospitalA
        for item in body["items"]:
            assert item["to_user_id"] == "HospitalA"
            assert item["amount"] == 5
