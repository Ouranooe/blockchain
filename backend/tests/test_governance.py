"""迭代 10：链上多签治理（双 MSP endorse）后端集成测试。"""


def _admin(make_user, login_token, username="adm", *, msp="Org1MSP"):
    make_user(
        username,
        password="apass",
        role="admin",
        real_name=f"管理员 {username}",
        msp_org=msp,
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


def _propose(client, token, action_id, kind="UNFREEZE_RECORD", payload=None):
    return client.post(
        "/api/governance/actions",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "action_id": action_id,
            "kind": kind,
            "payload": payload or {"recordId": "5"},
        },
    )


class TestPropose:
    def test_admin_can_propose(self, client, make_user, login_token):
        token = _admin(make_user, login_token)
        resp = _propose(client, token, "G-100")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["action_id"] == "G-100"
        assert body["status"] == "PROPOSED"
        assert body["propose_tx_id"]
        assert body["proposer_msp"] == "Org1MSP"

    def test_hospital_cannot_propose(self, client, make_user, login_token):
        token = _hospital(make_user, login_token)
        resp = _propose(client, token, "G-101")
        assert resp.status_code == 403

    def test_unknown_kind_rejected_at_validation(
        self, client, make_user, login_token
    ):
        token = _admin(make_user, login_token)
        resp = client.post(
            "/api/governance/actions",
            headers={"Authorization": f"Bearer {token}"},
            json={"action_id": "G-X", "kind": "FAKE_KIND", "payload": {}},
        )
        # pydantic 模式正则会先拒，返回 422
        assert resp.status_code in (400, 422)

    def test_duplicate_action_id_rejected(self, client, make_user, login_token):
        token = _admin(make_user, login_token)
        _propose(client, token, "G-DUP")
        resp = _propose(client, token, "G-DUP")
        assert resp.status_code == 409


class TestApprovalFlow:
    def test_two_msp_approve_then_execute(self, client, make_user, login_token):
        adm1 = _admin(make_user, login_token, "adm1", msp="Org1MSP")
        adm2 = _admin(make_user, login_token, "adm2", msp="Org2MSP")

        _propose(client, adm1, "G-EX", payload={"recordId": "9"})

        r1 = client.post(
            "/api/governance/actions/G-EX/approve",
            headers={"Authorization": f"Bearer {adm1}"},
        )
        assert r1.status_code == 200
        assert r1.json()["status"] == "PARTIALLY_APPROVED"

        # 同 MSP 的 admin 重复批准 → 502
        r1b = client.post(
            "/api/governance/actions/G-EX/approve",
            headers={"Authorization": f"Bearer {adm1}"},
        )
        assert r1b.status_code == 502

        # 第二个 MSP 批准
        r2 = client.post(
            "/api/governance/actions/G-EX/approve",
            headers={"Authorization": f"Bearer {adm2}"},
        )
        assert r2.status_code == 200
        assert r2.json()["status"] == "APPROVED"

        # 执行
        r3 = client.post(
            "/api/governance/actions/G-EX/execute",
            headers={"Authorization": f"Bearer {adm1}"},
        )
        assert r3.status_code == 200
        assert r3.json()["status"] == "EXECUTED"
        assert r3.json()["execute_tx_id"]

    def test_execute_before_approved_fails(
        self, client, make_user, login_token
    ):
        token = _admin(make_user, login_token)
        _propose(client, token, "G-EARLY")
        r = client.post(
            "/api/governance/actions/G-EARLY/execute",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 502  # 链码拒

    def test_rejected_terminal(self, client, make_user, login_token):
        token = _admin(make_user, login_token)
        _propose(client, token, "G-REJ")
        r = client.post(
            "/api/governance/actions/G-REJ/reject",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200
        assert r.json()["status"] == "REJECTED"

        # 再批准 / 执行均被拒
        r1 = client.post(
            "/api/governance/actions/G-REJ/approve",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r1.status_code == 502


class TestListing:
    def test_list_filters_by_status(self, client, make_user, login_token):
        token = _admin(make_user, login_token)
        _propose(client, token, "G-L1")
        _propose(client, token, "G-L2")
        client.post(
            "/api/governance/actions/G-L2/reject",
            headers={"Authorization": f"Bearer {token}"},
        )

        all_resp = client.get(
            "/api/governance/actions",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert all_resp.status_code == 200
        assert len(all_resp.json()) == 2

        rej = client.get(
            "/api/governance/actions?status=REJECTED",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert len(rej.json()) == 1
        assert rej.json()[0]["action_id"] == "G-L2"

    def test_get_404_unknown(self, client, make_user, login_token):
        token = _admin(make_user, login_token)
        r = client.get(
            "/api/governance/actions/NOPE",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 404
