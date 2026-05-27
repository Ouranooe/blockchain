"""迭代 11：链上紧急冻结 + 治理解冻闭环 后端集成测试。"""


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


def _patient(make_user, login_token, username="patA"):
    make_user(username, password="p123", role="patient", real_name="患者")
    return login_token(username, "p123")


def _post_record(client, hosp_token, patient_id):
    return client.post(
        "/api/records",
        headers={"Authorization": f"Bearer {hosp_token}"},
        json={
            "patient_id": patient_id,
            "title": "t",
            "diagnosis": "d",
            "content": "病历内容",
        },
    )


def _create_governance_unfreeze(client, adm1, adm2, action_id, record_id):
    """走完迭代 10 的双签治理流程，返回 200 EXECUTED。"""
    r = client.post(
        "/api/governance/actions",
        headers={"Authorization": f"Bearer {adm1}"},
        json={
            "action_id": action_id,
            "kind": "UNFREEZE_RECORD",
            "payload": {"recordId": str(record_id)},
        },
    )
    assert r.status_code == 200, r.text
    assert (
        client.post(
            f"/api/governance/actions/{action_id}/approve",
            headers={"Authorization": f"Bearer {adm1}"},
        ).status_code
        == 200
    )
    assert (
        client.post(
            f"/api/governance/actions/{action_id}/approve",
            headers={"Authorization": f"Bearer {adm2}"},
        ).status_code
        == 200
    )
    return client.post(
        f"/api/governance/actions/{action_id}/execute",
        headers={"Authorization": f"Bearer {adm1}"},
    )


class TestFreeze:
    def test_patient_can_freeze_own_record(
        self, client, make_user, login_token
    ):
        pt = _patient(make_user, login_token)
        pid = client.get(
            "/api/auth/me", headers={"Authorization": f"Bearer {pt}"}
        ).json()["id"]
        h = _hospital(make_user, login_token)
        rid = _post_record(client, h, pid).json()["id"]

        r = client.post(
            f"/api/records/{rid}/freeze",
            headers={"Authorization": f"Bearer {pt}"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["frozen"] is True
        assert r.json()["freeze_tx_id"]

    def test_other_patient_cannot_freeze(
        self, client, make_user, login_token
    ):
        pt1 = _patient(make_user, login_token, "ptA")
        pid1 = client.get(
            "/api/auth/me", headers={"Authorization": f"Bearer {pt1}"}
        ).json()["id"]
        h = _hospital(make_user, login_token)
        rid = _post_record(client, h, pid1).json()["id"]
        pt2 = _patient(make_user, login_token, "ptB")
        r = client.post(
            f"/api/records/{rid}/freeze",
            headers={"Authorization": f"Bearer {pt2}"},
        )
        assert r.status_code == 403

    def test_hospital_cannot_revise_frozen_record(
        self, client, make_user, login_token
    ):
        pt = _patient(make_user, login_token)
        pid = client.get(
            "/api/auth/me", headers={"Authorization": f"Bearer {pt}"}
        ).json()["id"]
        h = _hospital(make_user, login_token)
        rid = _post_record(client, h, pid).json()["id"]
        client.post(
            f"/api/records/{rid}/freeze",
            headers={"Authorization": f"Bearer {pt}"},
        )
        r = client.post(
            f"/api/records/{rid}/revise",
            headers={"Authorization": f"Bearer {h}"},
            json={"content": "改了一下"},
        )
        # 链码层守卫 → 502
        assert r.status_code == 502
        assert "冻结" in r.json()["detail"]


class TestUnfreezeGovernance:
    def test_unfreeze_requires_governance_action(
        self, client, make_user, login_token
    ):
        pt = _patient(make_user, login_token)
        pid = client.get(
            "/api/auth/me", headers={"Authorization": f"Bearer {pt}"}
        ).json()["id"]
        h = _hospital(make_user, login_token)
        rid = _post_record(client, h, pid).json()["id"]
        client.post(
            f"/api/records/{rid}/freeze",
            headers={"Authorization": f"Bearer {pt}"},
        )
        adm = _admin(make_user, login_token)
        # 不传 governance_action_id → 422
        r = client.post(
            f"/api/records/{rid}/unfreeze",
            headers={"Authorization": f"Bearer {adm}"},
        )
        assert r.status_code == 422

    def test_unfreeze_rejects_non_executed_governance(
        self, client, make_user, login_token
    ):
        pt = _patient(make_user, login_token)
        pid = client.get(
            "/api/auth/me", headers={"Authorization": f"Bearer {pt}"}
        ).json()["id"]
        h = _hospital(make_user, login_token)
        rid = _post_record(client, h, pid).json()["id"]
        client.post(
            f"/api/records/{rid}/freeze",
            headers={"Authorization": f"Bearer {pt}"},
        )
        adm = _admin(make_user, login_token)
        # 提案但未走完审批
        client.post(
            "/api/governance/actions",
            headers={"Authorization": f"Bearer {adm}"},
            json={
                "action_id": "G-NOEX",
                "kind": "UNFREEZE_RECORD",
                "payload": {"recordId": str(rid)},
            },
        )
        r = client.post(
            f"/api/records/{rid}/unfreeze?governance_action_id=G-NOEX",
            headers={"Authorization": f"Bearer {adm}"},
        )
        assert r.status_code == 400
        assert "EXECUTED" in r.json()["detail"]

    def test_governance_unfreeze_round_trip(
        self, client, make_user, login_token
    ):
        pt = _patient(make_user, login_token)
        pid = client.get(
            "/api/auth/me", headers={"Authorization": f"Bearer {pt}"}
        ).json()["id"]
        h = _hospital(make_user, login_token)
        rid = _post_record(client, h, pid).json()["id"]
        client.post(
            f"/api/records/{rid}/freeze",
            headers={"Authorization": f"Bearer {pt}"},
        )

        adm1 = _admin(make_user, login_token, "adm1", msp="Org1MSP")
        adm2 = _admin(make_user, login_token, "adm2", msp="Org2MSP")
        exec_resp = _create_governance_unfreeze(
            client, adm1, adm2, "G-UF", rid
        )
        assert exec_resp.status_code == 200
        assert exec_resp.json()["status"] == "EXECUTED"

        r = client.post(
            f"/api/records/{rid}/unfreeze?governance_action_id=G-UF",
            headers={"Authorization": f"Bearer {adm1}"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["frozen"] is False
        assert r.json()["unfreeze_tx_id"]

        # 解冻后医院可以修订
        r2 = client.post(
            f"/api/records/{rid}/revise",
            headers={"Authorization": f"Bearer {h}"},
            json={"content": "解冻后修订"},
        )
        assert r2.status_code == 200

    def test_unfreeze_rejects_wrong_kind_governance(
        self, client, make_user, login_token
    ):
        pt = _patient(make_user, login_token)
        pid = client.get(
            "/api/auth/me", headers={"Authorization": f"Bearer {pt}"}
        ).json()["id"]
        h = _hospital(make_user, login_token)
        rid = _post_record(client, h, pid).json()["id"]
        client.post(
            f"/api/records/{rid}/freeze",
            headers={"Authorization": f"Bearer {pt}"},
        )
        adm1 = _admin(make_user, login_token, "adm1", msp="Org1MSP")
        adm2 = _admin(make_user, login_token, "adm2", msp="Org2MSP")
        # kind = FREEZE_RECORD（错的），即便 EXECUTED 也要被后端层拒绝
        client.post(
            "/api/governance/actions",
            headers={"Authorization": f"Bearer {adm1}"},
            json={
                "action_id": "G-WRONG",
                "kind": "FREEZE_RECORD",
                "payload": {"recordId": str(rid)},
            },
        )
        client.post(
            "/api/governance/actions/G-WRONG/approve",
            headers={"Authorization": f"Bearer {adm1}"},
        )
        client.post(
            "/api/governance/actions/G-WRONG/approve",
            headers={"Authorization": f"Bearer {adm2}"},
        )
        client.post(
            "/api/governance/actions/G-WRONG/execute",
            headers={"Authorization": f"Bearer {adm1}"},
        )
        r = client.post(
            f"/api/records/{rid}/unfreeze?governance_action_id=G-WRONG",
            headers={"Authorization": f"Bearer {adm1}"},
        )
        assert r.status_code == 400
        assert "UNFREEZE_RECORD" in r.json()["detail"]
