"""迭代 8：全链路端到端冒烟测试。

一次 pytest 跑完 20 条核心业务流，覆盖迭代 1 至 7 的所有关键路径。
任何一条 fail 都意味着某个纵向链路坏了。
"""

import io

import pytest


def _make_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def setup_cast(client, make_user, login_token):
    """构造一组角色：admin / hospital_a / hospital_b / patient_a / patient_b。"""
    make_user("admin0", password="x", role="admin", real_name="超管")
    make_user(
        "hospital_a",
        password="p",
        role="hospital",
        hospital_name="HospitalA",
        msp_org="Org1MSP",
    )
    make_user(
        "hospital_b",
        password="p",
        role="hospital",
        hospital_name="HospitalB",
        msp_org="Org2MSP",
    )
    make_user("patient_a", password="p", role="patient", real_name="甲")
    make_user("patient_b", password="p", role="patient", real_name="乙")
    return {
        "admin": login_token("admin0", "x"),
        "hospA": login_token("hospital_a", "p"),
        "hospB": login_token("hospital_b", "p"),
        "patA": login_token("patient_a", "p"),
        "patB": login_token("patient_b", "p"),
    }


def test_e2e_full_user_journey(client, setup_cast):
    tokens = setup_cast

    # ---- 基础信息 ----
    pa_id = client.get("/api/auth/me", headers=_make_headers(tokens["patA"])).json()["id"]
    pb_id = client.get("/api/auth/me", headers=_make_headers(tokens["patB"])).json()["id"]

    # ============ #1 注册患者 ============
    reg = client.post(
        "/api/auth/register",
        json={"username": "pt_new", "password": "pwlong", "real_name": "新患者", "role": "patient"},
    )
    assert reg.status_code == 200

    # ============ #2 登录 / whoami ============
    tk = client.post(
        "/api/auth/login", json={"username": "pt_new", "password": "pwlong"}
    ).json()["token"]
    me = client.get("/api/auth/me", headers=_make_headers(tk)).json()
    assert me["role"] == "patient"
    assert me["username"] == "pt_new"

    # ============ #3 改密 + 用新密码登录 ============
    assert (
        client.post(
            "/api/auth/change-password",
            headers=_make_headers(tk),
            json={"old_password": "pwlong", "new_password": "pwlong2"},
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/api/auth/login", json={"username": "pt_new", "password": "pwlong2"}
        ).status_code
        == 200
    )

    # ============ #4 医院上传文本病历（迭代 1） ============
    rec_text = client.post(
        "/api/records",
        headers=_make_headers(tokens["hospA"]),
        json={
            "patient_id": pa_id,
            "title": "门诊-1",
            "diagnosis": "感冒",
            "content": "c-v1",
        },
    ).json()
    assert rec_text["version"] == 1 and rec_text["tx_id"]

    # ============ #5 修订 + 版本链校验（迭代 2） ============
    rev = client.post(
        f"/api/records/{rec_text['id']}/revise",
        headers=_make_headers(tokens["hospA"]),
        json={"content": "c-v2"},
    ).json()
    assert rev["version"] == 2 and rev["previous_tx_id"] == rec_text["tx_id"]

    # ============ #6 链上历史回溯（迭代 3 GetHistoryForKey） ============
    hist = client.get(
        f"/api/records/{rec_text['id']}/chain-history",
        headers=_make_headers(tokens["hospA"]),
    ).json()
    assert len(hist["entries"]) == 2
    assert hist["entries"][0]["value"]["version"] == 2  # 倒序

    # ============ #7 历史查询缓存命中（迭代 3） ============
    hist2 = client.get(
        f"/api/records/{rec_text['id']}/chain-history",
        headers=_make_headers(tokens["hospA"]),
    ).json()
    assert hist2["cache"] == "hit"

    # ============ #8 上传带文件的病历（迭代 4 AES-256-GCM） ============
    files = {"file": ("r.pdf", io.BytesIO(b"PDF-CONTENT-E2E" * 64), "application/pdf")}
    form = {
        "patient_id": str(pa_id),
        "title": "影像",
        "diagnosis": "正常",
        "description": "",
    }
    rec_file = client.post(
        "/api/records/upload",
        headers=_make_headers(tokens["hospA"]),
        files=files,
        data=form,
    ).json()
    assert rec_file["has_file"] is True and rec_file["tx_id"]

    # ============ #9 文件完整性校验（/verify） ============
    verify = client.get(
        f"/api/records/{rec_file['id']}/verify",
        headers=_make_headers(tokens["hospA"]),
    ).json()
    assert verify["hash_match"] is True

    # ============ #10 申请跨院访问（迭代 2/5） ============
    req = client.post(
        "/api/access-requests",
        headers=_make_headers(tokens["hospB"]),
        json={"record_id": rec_file["id"], "reason": "consult"},
    ).json()
    assert req["status"] == "PENDING"

    # ============ #11 患者审批（迭代 5 ABAC：duration+maxReads 上链） ============
    review = client.post(
        f"/api/access-requests/{req['id']}/review",
        headers=_make_headers(tokens["patA"]),
        json={"decision": "APPROVED", "duration_days": 7, "max_reads": 2},
    ).json()
    assert review["status"] == "APPROVED"
    assert review["max_reads"] == 2 and review["remaining_reads"] == 2

    # ============ #12 医院 B 下载 = 消费一次授权 + AccessRecorded 事件 ============
    dl = client.get(
        f"/api/records/{rec_file['id']}/download",
        headers=_make_headers(tokens["hospB"]),
    )
    assert dl.status_code == 200
    assert dl.headers["x-remaining-reads"] == "1"
    assert dl.headers["x-access-tx"]

    # ============ #13 Range 下载（断点续传） ============
    dl_range = client.get(
        f"/api/records/{rec_file['id']}/download",
        headers={
            **_make_headers(tokens["hospB"]),
            "Range": "bytes=0-99",
        },
    )
    # 又消费 1 次 → 耗尽
    assert dl_range.status_code == 206
    assert dl_range.headers["content-range"].startswith("bytes 0-99/")
    assert dl_range.headers["x-remaining-reads"] == "0"

    # ============ #14 第 3 次下载应被链码层拒绝（次数耗尽） ============
    dl3 = client.get(
        f"/api/records/{rec_file['id']}/download",
        headers=_make_headers(tokens["hospB"]),
    )
    assert dl3.status_code == 403

    # ============ #15 MSP 冒用直接调网关应被链码拒绝 ============
    from app.gateway import access_record_consume as fn_consume

    with pytest.raises(RuntimeError):
        fn_consume(
            hospital_name="HospitalA",  # Org1 冒用 Org2 授权
            request_id=req["id"],
            accessed_at="2026-04-22T12:00:00Z",
        )

    # ============ #16 患者撤销授权 + 再访问失败 ============
    # 新建第二条授权用来测撤销（第一条已耗尽）
    req2 = client.post(
        "/api/access-requests",
        headers=_make_headers(tokens["hospB"]),
        json={"record_id": rec_file["id"], "reason": "again"},
    ).json()
    client.post(
        f"/api/access-requests/{req2['id']}/review",
        headers=_make_headers(tokens["patA"]),
        json={"decision": "APPROVED", "duration_days": 7, "max_reads": 5},
    )
    rv = client.post(
        f"/api/access-requests/{req2['id']}/revoke",
        headers=_make_headers(tokens["patA"]),
    ).json()
    assert rv["status"] == "REVOKED"
    assert (
        client.get(
            f"/api/records/{rec_file['id']}/download",
            headers=_make_headers(tokens["hospB"]),
        ).status_code
        == 403
    )

    # ============ #17 管理员查审计表（迭代 6） ============
    events = client.get(
        "/api/audit/events?limit=500",
        headers=_make_headers(tokens["admin"]),
    ).json()
    kinds = {e["event_type"] for e in events}
    for expected in (
        "RecordCreated",
        "RecordUpdated",
        "AccessRequestCreated",
        "AccessApproved",
        "AccessRecorded",
        "AccessRevoked",
        "UnauthorizedAttempt",
    ):
        assert expected in kinds

    # ============ #18 WebSocket 实时通知（迭代 6） ============
    with client.websocket_connect(
        f"/ws/notifications?token={tokens['patA']}"
    ) as ws:
        ws.receive_json()  # _connected
        # 患者 A 上传触发 RecordCreated，但 actor 是医院，subject 是 patA，应收到
        files2 = {"file": ("r2.pdf", io.BytesIO(b"Y" * 40), "application/pdf")}
        form2 = {
            "patient_id": str(pa_id),
            "title": "e2e-ws",
            "diagnosis": "t",
            "description": "",
        }
        client.post(
            "/api/records/upload",
            headers=_make_headers(tokens["hospA"]),
            files=files2,
            data=form2,
        )
        msg = ws.receive_json()
        assert msg["event_type"] == "RecordCreated"

    # ============ #19 CouchDB 富查询：按医院（迭代 7） ============
    by_hosp = client.get(
        "/api/records/chain/by-hospital",
        headers=_make_headers(tokens["hospA"]),
    ).json()
    assert by_hosp["fetched_count"] >= 2  # 至少含 rec_text + rec_file
    assert all(r["uploader_hospital"] == "HospitalA" for r in by_hosp["records"])

    # ============ #20 运维：metrics + liveness/readiness（迭代 8） ============
    assert client.get("/health/live").json()["status"] == "alive"
    assert client.get("/health/ready").json()["status"] == "ready"
    metrics_body = client.get("/metrics").text
    assert "medshare_requests_total" in metrics_body
    assert "medshare_request_latency_seconds" in metrics_body
