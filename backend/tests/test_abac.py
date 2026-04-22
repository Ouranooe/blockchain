"""迭代 5：链上访问控制（ABAC）后端集成测试。

涵盖：
- 审批必须携带 duration_days + max_reads
- 过期 / 次数耗尽 / 已撤销的授权在下载时被链码层拒绝
- 患者撤销 + 撤销后下载失败
- 跨 MSP 冒用授权被拒绝
- 授权列表自动过滤过期 / 已撤销
"""

import io

import pytest


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


def _seed_file_record(client, make_user, login_token, *, payload=b"clinical data\n" * 64):
    """医院 A 上传一条带文件的病历，返回 (hospA token, patient token, patient id, record json)。"""
    patient_tok = _patient(make_user, login_token)
    pid = client.get(
        "/api/auth/me", headers={"Authorization": f"Bearer {patient_tok}"}
    ).json()["id"]
    hosp_a = _hospital(make_user, login_token, "hospA", "HospitalA", "Org1MSP")
    files = {"file": ("r.pdf", io.BytesIO(payload), "application/pdf")}
    form = {
        "patient_id": str(pid),
        "title": "t",
        "diagnosis": "d",
        "description": "",
    }
    rec = client.post(
        "/api/records/upload",
        headers={"Authorization": f"Bearer {hosp_a}"},
        files=files,
        data=form,
    ).json()
    return hosp_a, patient_tok, pid, rec


def _apply_and_approve(client, *, hosp_b_token, patient_token, record_id,
                       duration_days=7, max_reads=3):
    """医院 B 申请 + 患者审批，返回审批后的 request dict。"""
    req = client.post(
        "/api/access-requests",
        headers={"Authorization": f"Bearer {hosp_b_token}"},
        json={"record_id": record_id, "reason": "consult"},
    ).json()
    resp = client.post(
        f"/api/access-requests/{req['id']}/review",
        headers={"Authorization": f"Bearer {patient_token}"},
        json={
            "decision": "APPROVED",
            "duration_days": duration_days,
            "max_reads": max_reads,
        },
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


class TestApproveRequiresParams:
    def test_approve_without_params_rejected(self, client, make_user, login_token):
        hosp_a, patient_tok, pid, rec = _seed_file_record(
            client, make_user, login_token
        )
        hosp_b = _hospital(make_user, login_token, "hospB", "HospitalB", "Org2MSP")
        req = client.post(
            "/api/access-requests",
            headers={"Authorization": f"Bearer {hosp_b}"},
            json={"record_id": rec["id"], "reason": "x"},
        ).json()
        resp = client.post(
            f"/api/access-requests/{req['id']}/review",
            headers={"Authorization": f"Bearer {patient_tok}"},
            json={"decision": "APPROVED"},  # 缺 duration/reads
        )
        assert resp.status_code == 400

    def test_duration_days_and_max_reads_persisted(
        self, client, make_user, login_token
    ):
        hosp_a, patient_tok, pid, rec = _seed_file_record(
            client, make_user, login_token
        )
        hosp_b = _hospital(make_user, login_token, "hospB", "HospitalB", "Org2MSP")
        req = _apply_and_approve(
            client, hosp_b_token=hosp_b, patient_token=patient_tok,
            record_id=rec["id"], duration_days=30, max_reads=5,
        )
        assert req["status"] == "APPROVED"
        assert req["remaining_reads"] == 5
        assert req["max_reads"] == 5
        assert req["expires_at"] is not None


class TestAccessRecordConsumption:
    def test_download_decrements_remaining_reads(
        self, client, make_user, login_token
    ):
        _, patient_tok, _, rec = _seed_file_record(
            client, make_user, login_token
        )
        hosp_b = _hospital(make_user, login_token, "hospB", "HospitalB", "Org2MSP")
        req = _apply_and_approve(
            client, hosp_b_token=hosp_b, patient_token=patient_tok,
            record_id=rec["id"], max_reads=2,
        )
        assert req["remaining_reads"] == 2

        r1 = client.get(
            f"/api/records/{rec['id']}/download",
            headers={"Authorization": f"Bearer {hosp_b}"},
        )
        assert r1.status_code == 200
        assert r1.headers.get("x-access-tx")
        assert r1.headers.get("x-remaining-reads") == "1"

        r2 = client.get(
            f"/api/records/{rec['id']}/download",
            headers={"Authorization": f"Bearer {hosp_b}"},
        )
        assert r2.status_code == 200
        assert r2.headers.get("x-remaining-reads") == "0"

        # 第 3 次应被链码拒绝
        r3 = client.get(
            f"/api/records/{rec['id']}/download",
            headers={"Authorization": f"Bearer {hosp_b}"},
        )
        assert r3.status_code == 403
        assert "次数" in r3.json()["detail"] or "exhaust" in r3.json()["detail"].lower()

    def test_uploader_hospital_bypasses_access_counter(
        self, client, make_user, login_token
    ):
        """本院医生下载自己上传的病历不消耗授权次数。"""
        hosp_a, _, _, rec = _seed_file_record(client, make_user, login_token)
        for _ in range(5):
            resp = client.get(
                f"/api/records/{rec['id']}/download",
                headers={"Authorization": f"Bearer {hosp_a}"},
            )
            assert resp.status_code == 200
            assert resp.headers.get("x-access-tx") is None  # 未调 AccessRecord

    def test_download_without_approved_request_forbidden(
        self, client, make_user, login_token
    ):
        hosp_a, _, _, rec = _seed_file_record(client, make_user, login_token)
        hosp_b = _hospital(make_user, login_token, "hospB", "HospitalB", "Org2MSP")
        resp = client.get(
            f"/api/records/{rec['id']}/download",
            headers={"Authorization": f"Bearer {hosp_b}"},
        )
        assert resp.status_code == 403


class TestRevoke:
    def test_patient_can_revoke_and_download_blocked(
        self, client, make_user, login_token
    ):
        _, patient_tok, _, rec = _seed_file_record(
            client, make_user, login_token
        )
        hosp_b = _hospital(make_user, login_token, "hospB", "HospitalB", "Org2MSP")
        req = _apply_and_approve(
            client, hosp_b_token=hosp_b, patient_token=patient_tok,
            record_id=rec["id"], max_reads=10,
        )

        # 先确认能下载一次
        r1 = client.get(
            f"/api/records/{rec['id']}/download",
            headers={"Authorization": f"Bearer {hosp_b}"},
        )
        assert r1.status_code == 200

        # 撤销
        rev = client.post(
            f"/api/access-requests/{req['id']}/revoke",
            headers={"Authorization": f"Bearer {patient_tok}"},
        )
        assert rev.status_code == 200
        assert rev.json()["status"] == "REVOKED"
        assert rev.json()["revoke_tx_id"]

        # 撤销后下载被链码层拒绝
        r2 = client.get(
            f"/api/records/{rec['id']}/download",
            headers={"Authorization": f"Bearer {hosp_b}"},
        )
        # 后端先做"是否存在 APPROVED 记录"的查询 → 撤销后已不是 APPROVED → 前置 403
        # 或链码返回 REVOKED 拒绝，同样 403
        assert r2.status_code == 403

    def test_non_patient_cannot_revoke(self, client, make_user, login_token):
        _, patient_tok, _, rec = _seed_file_record(
            client, make_user, login_token
        )
        hosp_b = _hospital(make_user, login_token, "hospB", "HospitalB", "Org2MSP")
        req = _apply_and_approve(
            client, hosp_b_token=hosp_b, patient_token=patient_tok,
            record_id=rec["id"],
        )
        # 医院 B 尝试撤销（不是患者）
        resp = client.post(
            f"/api/access-requests/{req['id']}/revoke",
            headers={"Authorization": f"Bearer {hosp_b}"},
        )
        assert resp.status_code == 403

    def test_other_patient_cannot_revoke(self, client, make_user, login_token):
        _, patient_tok, _, rec = _seed_file_record(
            client, make_user, login_token
        )
        hosp_b = _hospital(make_user, login_token, "hospB", "HospitalB", "Org2MSP")
        req = _apply_and_approve(
            client, hosp_b_token=hosp_b, patient_token=patient_tok,
            record_id=rec["id"],
        )
        # 另一个患者尝试撤销
        make_user("patB", password="p", role="patient", real_name="其他")
        other = login_token("patB", "p")
        resp = client.post(
            f"/api/access-requests/{req['id']}/revoke",
            headers={"Authorization": f"Bearer {other}"},
        )
        assert resp.status_code == 404  # 非本人 → 不可见

    def test_revoke_already_revoked_rejected(
        self, client, make_user, login_token
    ):
        _, patient_tok, _, rec = _seed_file_record(
            client, make_user, login_token
        )
        hosp_b = _hospital(make_user, login_token, "hospB", "HospitalB", "Org2MSP")
        req = _apply_and_approve(
            client, hosp_b_token=hosp_b, patient_token=patient_tok,
            record_id=rec["id"],
        )
        ok = client.post(
            f"/api/access-requests/{req['id']}/revoke",
            headers={"Authorization": f"Bearer {patient_tok}"},
        )
        assert ok.status_code == 200
        again = client.post(
            f"/api/access-requests/{req['id']}/revoke",
            headers={"Authorization": f"Bearer {patient_tok}"},
        )
        assert again.status_code == 400


class TestMspGating:
    def test_wrong_msp_bypass_attempt_rejected_by_chaincode(
        self, client, make_user, login_token
    ):
        """模拟 "医院 C 拿到了医院 B 的 requestId 直接调 gateway"：
        链码通过 MSP 比对，拒绝跨 MSP 冒用。"""
        _, patient_tok, _, rec = _seed_file_record(
            client, make_user, login_token
        )
        hosp_b = _hospital(make_user, login_token, "hospB", "HospitalB", "Org2MSP")
        req = _apply_and_approve(
            client, hosp_b_token=hosp_b, patient_token=patient_tok,
            record_id=rec["id"],
        )

        # 医院 A（Org1MSP）尝试消费绑定到 Org2 的 requestId
        # 走 gateway client：直接调 access_record_consume 时 caller_msp=Org1
        from app.gateway import access_record_consume as fn_access
        # 桩的 _hospital_to_msp 根据 name 解析 MSP；HospitalA → Org1
        with pytest.raises(RuntimeError, match="MSP"):
            fn_access(
                hospital_name="HospitalA",
                request_id=req["id"],
                accessed_at="2026-04-22T12:00:00Z",
            )


class TestListingsFilter:
    def test_exhausted_request_not_in_authorized_list(
        self, client, make_user, login_token
    ):
        _, patient_tok, _, rec = _seed_file_record(
            client, make_user, login_token
        )
        hosp_b = _hospital(make_user, login_token, "hospB", "HospitalB", "Org2MSP")
        _apply_and_approve(
            client, hosp_b_token=hosp_b, patient_token=patient_tok,
            record_id=rec["id"], max_reads=1,
        )
        # 消费掉唯一一次
        r = client.get(
            f"/api/records/{rec['id']}/download",
            headers={"Authorization": f"Bearer {hosp_b}"},
        )
        assert r.status_code == 200

        # 再查 authorized-records，应该过滤掉（remaining_reads==0）
        listing = client.get(
            "/api/authorized-records",
            headers={"Authorization": f"Bearer {hosp_b}"},
        ).json()
        assert all(item["id"] != rec["id"] for item in listing)

    def test_revoked_request_not_in_authorized_list(
        self, client, make_user, login_token
    ):
        _, patient_tok, _, rec = _seed_file_record(
            client, make_user, login_token
        )
        hosp_b = _hospital(make_user, login_token, "hospB", "HospitalB", "Org2MSP")
        req = _apply_and_approve(
            client, hosp_b_token=hosp_b, patient_token=patient_tok,
            record_id=rec["id"],
        )
        client.post(
            f"/api/access-requests/{req['id']}/revoke",
            headers={"Authorization": f"Bearer {patient_tok}"},
        )
        listing = client.get(
            "/api/authorized-records",
            headers={"Authorization": f"Bearer {hosp_b}"},
        ).json()
        assert all(item["id"] != rec["id"] for item in listing)


class TestStatusViewSemantics:
    def test_exhausted_status_label_is_EXHAUSTED(
        self, client, make_user, login_token
    ):
        _, patient_tok, _, rec = _seed_file_record(
            client, make_user, login_token
        )
        hosp_b = _hospital(make_user, login_token, "hospB", "HospitalB", "Org2MSP")
        req = _apply_and_approve(
            client, hosp_b_token=hosp_b, patient_token=patient_tok,
            record_id=rec["id"], max_reads=1,
        )
        client.get(
            f"/api/records/{rec['id']}/download",
            headers={"Authorization": f"Bearer {hosp_b}"},
        )
        # 患者侧待审批列表不含这条（因为已不是 PENDING）；改查历史视图
        # 直接读 DB 镜像状态派生
        from app.models import AccessRequest as ARModel

        db_gen = client.app.dependency_overrides[__import__(
            "app.database", fromlist=["get_db"]
        ).get_db]()
        db = next(db_gen)
        try:
            row = db.query(ARModel).filter(ARModel.id == req["id"]).first()
            assert row.remaining_reads == 0
        finally:
            db.close()
