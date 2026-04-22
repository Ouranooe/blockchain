"""迭代 3：Fabric 历史查询接口测试（病历 + 访问申请）+ 缓存行为。"""

from fastapi.testclient import TestClient


def _hospital(make_user, login_token, username="hospA", hosp="HospitalA", org="Org1MSP"):
    make_user(
        username,
        password="h123",
        role="hospital",
        real_name="医生",
        hospital_name=hosp,
        msp_org=org,
    )
    return login_token(username, "h123")


def _patient(make_user, login_token, username="patA"):
    make_user(username, password="p123", role="patient", real_name="患者")
    return login_token(username, "p123")


def _seed_record(client, make_user, login_token):
    patient_token = _patient(make_user, login_token)
    hosp_token = _hospital(make_user, login_token)
    # patient 用户 id
    pid_resp = client.get(
        "/api/auth/me", headers={"Authorization": f"Bearer {patient_token}"}
    )
    pid = pid_resp.json()["id"]
    rec = client.post(
        "/api/records",
        headers={"Authorization": f"Bearer {hosp_token}"},
        json={
            "patient_id": pid,
            "title": "t",
            "diagnosis": "d",
            "content": "c1",
        },
    ).json()
    return hosp_token, patient_token, rec


class TestRecordHistoryChain:
    def test_single_version_shape(self, client, make_user, login_token):
        hosp, _, rec = _seed_record(client, make_user, login_token)
        resp = client.get(
            f"/api/records/{rec['id']}/history",
            headers={"Authorization": f"Bearer {hosp}"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["latest_version"] == 1
        assert len(body["versions"]) == 1
        v1 = body["versions"][0]
        assert v1["version"] == 1
        assert v1["tx_id"] == rec["tx_id"]
        assert v1["previous_tx_id"] == ""

    def test_four_versions_ordered_and_chained(
        self, client, make_user, login_token
    ):
        hosp, _, rec = _seed_record(client, make_user, login_token)
        rid = rec["id"]
        txs = [rec["tx_id"]]
        for v in range(2, 5):
            r = client.post(
                f"/api/records/{rid}/revise",
                headers={"Authorization": f"Bearer {hosp}"},
                json={"content": f"c{v}"},
            ).json()
            txs.append(r["tx_id"])

        body = client.get(
            f"/api/records/{rid}/history",
            headers={"Authorization": f"Bearer {hosp}"},
        ).json()
        assert body["latest_version"] == 4
        assert [v["version"] for v in body["versions"]] == [1, 2, 3, 4]
        for i, v in enumerate(body["versions"]):
            assert v["tx_id"] == txs[i]
            assert v["previous_tx_id"] == ("" if i == 0 else txs[i - 1])


class TestRecordChainHistoryEndpoint:
    def test_chain_history_returns_reverse_chrono_and_cache_hit(
        self, client, make_user, login_token
    ):
        hosp, _, rec = _seed_record(client, make_user, login_token)
        rid = rec["id"]
        client.post(
            f"/api/records/{rid}/revise",
            headers={"Authorization": f"Bearer {hosp}"},
            json={"content": "c2"},
        )

        stats = client.app.state.chain_stats
        before = stats["history_chain_calls"]

        first = client.get(
            f"/api/records/{rid}/chain-history",
            headers={"Authorization": f"Bearer {hosp}"},
        )
        assert first.status_code == 200
        body1 = first.json()
        assert body1["cache"] == "miss"
        assert len(body1["entries"]) == 2
        # 倒序：最新在前
        assert body1["entries"][0]["value"]["version"] == 2
        assert body1["entries"][1]["value"]["version"] == 1
        assert stats["history_chain_calls"] == before + 1

        # 第 2 次请求应命中缓存，不再调链码
        second = client.get(
            f"/api/records/{rid}/chain-history",
            headers={"Authorization": f"Bearer {hosp}"},
        ).json()
        assert second["cache"] == "hit"
        assert stats["history_chain_calls"] == before + 1

    def test_revise_invalidates_cache(self, client, make_user, login_token):
        hosp, _, rec = _seed_record(client, make_user, login_token)
        rid = rec["id"]
        # 预热缓存
        client.get(
            f"/api/records/{rid}/chain-history",
            headers={"Authorization": f"Bearer {hosp}"},
        )
        stats = client.app.state.chain_stats
        calls_before = stats["history_chain_calls"]

        # 修订应 bust 缓存
        client.post(
            f"/api/records/{rid}/revise",
            headers={"Authorization": f"Bearer {hosp}"},
            json={"content": "c2"},
        )
        # 下一次查询应重新调链
        body = client.get(
            f"/api/records/{rid}/chain-history",
            headers={"Authorization": f"Bearer {hosp}"},
        ).json()
        assert body["cache"] == "miss"
        assert stats["history_chain_calls"] == calls_before + 1
        # 再来一次应再次命中
        body2 = client.get(
            f"/api/records/{rid}/chain-history",
            headers={"Authorization": f"Bearer {hosp}"},
        ).json()
        assert body2["cache"] == "hit"

    def test_cache_hit_rate_meets_target(self, client, make_user, login_token):
        """量化目标：100 次查询中命中率 ≥ 85%。"""
        hosp, _, rec = _seed_record(client, make_user, login_token)
        rid = rec["id"]

        hits = misses = 0
        for _ in range(100):
            body = client.get(
                f"/api/records/{rid}/chain-history",
                headers={"Authorization": f"Bearer {hosp}"},
            ).json()
            if body["cache"] == "hit":
                hits += 1
            else:
                misses += 1
        assert hits + misses == 100
        hit_rate = hits / 100
        # 首次 miss + 后续 99 次全 hit → 命中率应为 0.99
        assert hit_rate >= 0.85, f"命中率 {hit_rate:.2f} < 0.85"

    def test_chain_history_forbidden_for_unrelated_hospital(
        self, client, make_user, login_token
    ):
        _, _, rec = _seed_record(client, make_user, login_token)
        make_user(
            "hospB", password="hp", role="hospital", hospital_name="HospitalB"
        )
        other = login_token("hospB", "hp")
        resp = client.get(
            f"/api/records/{rec['id']}/chain-history",
            headers={"Authorization": f"Bearer {other}"},
        )
        assert resp.status_code == 403


class TestAccessRequestHistory:
    def _seed_access_flow(self, client, make_user, login_token):
        # 医院 A 上传病历
        hosp_a, patient_token, rec = _seed_record(client, make_user, login_token)
        # 医院 B 登录 + 发起申请
        make_user(
            "hospB",
            password="hp",
            role="hospital",
            hospital_name="HospitalB",
            msp_org="Org2MSP",
        )
        hosp_b = login_token("hospB", "hp")

        req = client.post(
            "/api/access-requests",
            headers={"Authorization": f"Bearer {hosp_b}"},
            json={"record_id": rec["id"], "reason": "for consult"},
        ).json()
        return hosp_a, hosp_b, patient_token, rec, req

    def test_history_contains_both_create_and_approve(
        self, client, make_user, login_token
    ):
        _, hosp_b, patient_token, _, req = self._seed_access_flow(
            client, make_user, login_token
        )

        # 患者审批
        client.post(
            f"/api/access-requests/{req['id']}/review",
            headers={"Authorization": f"Bearer {patient_token}"},
            json={"decision": "APPROVED", "duration_days": 7, "max_reads": 3},
        )

        # 申请方医院查看历史
        resp = client.get(
            f"/api/access-requests/{req['id']}/history",
            headers={"Authorization": f"Bearer {hosp_b}"},
        )
        assert resp.status_code == 200
        body = resp.json()
        # 倒序：最新（APPROVED）在前，PENDING 在后
        assert len(body["entries"]) == 2
        assert body["entries"][0]["value"]["status"] == "APPROVED"
        assert body["entries"][1]["value"]["status"] == "PENDING"
        assert body["cache"] == "miss"

    def test_patient_can_view_own_request_history(
        self, client, make_user, login_token
    ):
        _, _, patient_token, _, req = self._seed_access_flow(
            client, make_user, login_token
        )
        resp = client.get(
            f"/api/access-requests/{req['id']}/history",
            headers={"Authorization": f"Bearer {patient_token}"},
        )
        assert resp.status_code == 200

    def test_unrelated_user_forbidden(self, client, make_user, login_token):
        _, _, _, _, req = self._seed_access_flow(client, make_user, login_token)
        make_user(
            "patC", password="pc", role="patient", real_name="其他患者"
        )
        tok = login_token("patC", "pc")
        resp = client.get(
            f"/api/access-requests/{req['id']}/history",
            headers={"Authorization": f"Bearer {tok}"},
        )
        assert resp.status_code == 403

    def test_history_404_on_nonexistent(self, client, make_user, login_token):
        hosp = _hospital(make_user, login_token)
        resp = client.get(
            "/api/access-requests/9999/history",
            headers={"Authorization": f"Bearer {hosp}"},
        )
        assert resp.status_code == 404
