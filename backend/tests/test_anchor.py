"""迭代 9：Merkle 批量锚定 + 链上包含证明 后端集成测试。"""

from app import merkle


def _hospital(make_user, login_token, username="hospA"):
    make_user(
        username,
        password="h123",
        role="hospital",
        real_name=f"{username} 医生",
        hospital_name="HospitalA",
        msp_org="Org1MSP",
    )
    return login_token(username, "h123")


def _patient(make_user, login_token, username="patA"):
    make_user(username, password="p123", role="patient", real_name="患者")
    return login_token(username, "p123")


def _admin(make_user, login_token):
    make_user("adm", password="apass", role="admin", real_name="A")
    return login_token("adm", "apass")


def _post_record(client, hosp_token, patient_id, title="t"):
    return client.post(
        "/api/records",
        headers={"Authorization": f"Bearer {hosp_token}"},
        json={
            "patient_id": patient_id,
            "title": title,
            "diagnosis": "d",
            "content": f"content-of-{title}",
        },
    )


class TestMerkleUtil:
    """链下 merkle 模块本身的正确性。"""

    def test_root_with_single_leaf(self):
        leaf = merkle.sha256_hex(b"only")
        assert merkle.compute_merkle_root([leaf]) == leaf
        assert merkle.compute_proof([leaf], 0) == []
        assert merkle.verify_proof(leaf, leaf, []) is True

    def test_root_and_proof_for_four_leaves(self):
        leaves = [merkle.sha256_hex(f"x-{i}".encode()) for i in range(4)]
        root = merkle.compute_merkle_root(leaves)
        for i in range(4):
            proof = merkle.compute_proof(leaves, i)
            assert merkle.verify_proof(root, leaves[i], proof) is True

    def test_odd_count_duplicates_last(self):
        leaves = [merkle.sha256_hex(f"y-{i}".encode()) for i in range(3)]
        root = merkle.compute_merkle_root(leaves)
        # 三叶子可被验证
        for i in range(3):
            proof = merkle.compute_proof(leaves, i)
            assert merkle.verify_proof(root, leaves[i], proof) is True

    def test_tampered_leaf_fails(self):
        leaves = [merkle.sha256_hex(f"z-{i}".encode()) for i in range(4)]
        root = merkle.compute_merkle_root(leaves)
        bad_leaf = merkle.sha256_hex(b"not-included")
        proof = merkle.compute_proof(leaves, 0)
        assert merkle.verify_proof(root, bad_leaf, proof) is False


class TestAnchorRun:
    def test_anchor_run_aggregates_all_unanchored_records(
        self, client, make_user, login_token
    ):
        pt = _patient(make_user, login_token)
        pid = client.get(
            "/api/auth/me", headers={"Authorization": f"Bearer {pt}"}
        ).json()["id"]
        h = _hospital(make_user, login_token)
        for i in range(5):
            r = _post_record(client, h, pid, title=f"t-{i}")
            assert r.status_code == 200

        adm = _admin(make_user, login_token)
        resp = client.post(
            "/api/anchor/run", headers={"Authorization": f"Bearer {adm}"}
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["anchored"] == 5
        assert body["batch"]["leaf_count"] == 5
        assert body["batch"]["tx_id"].startswith("anchor-")

        # 再调一次：已经无未锚定，应返回 0
        again = client.post(
            "/api/anchor/run", headers={"Authorization": f"Bearer {adm}"}
        ).json()
        assert again["anchored"] == 0
        assert again["batch"] is None

    def test_anchor_run_requires_admin(self, client, make_user, login_token):
        h = _hospital(make_user, login_token)
        resp = client.post(
            "/api/anchor/run", headers={"Authorization": f"Bearer {h}"}
        )
        assert resp.status_code == 403


class TestInclusionProof:
    def test_record_proof_verifies_on_chain(self, client, make_user, login_token):
        pt = _patient(make_user, login_token)
        pid = client.get(
            "/api/auth/me", headers={"Authorization": f"Bearer {pt}"}
        ).json()["id"]
        h = _hospital(make_user, login_token)
        record_ids = []
        for i in range(4):
            r = _post_record(client, h, pid, title=f"r-{i}")
            record_ids.append(r.json()["id"])

        adm = _admin(make_user, login_token)
        anchor_resp = client.post(
            "/api/anchor/run", headers={"Authorization": f"Bearer {adm}"}
        ).json()
        batch_id = anchor_resp["batch"]["batch_id"]

        # 每条 record 都能拿到 proof，并能链上验证为 true
        for rid in record_ids:
            proof_resp = client.get(
                f"/api/records/{rid}/proof",
                headers={"Authorization": f"Bearer {h}"},
            )
            assert proof_resp.status_code == 200, proof_resp.text
            proof_body = proof_resp.json()
            assert proof_body["batch"]["batch_id"] == batch_id
            # 链上验证
            verify_resp = client.post(
                "/api/anchor/verify",
                headers={"Authorization": f"Bearer {h}"},
                json={
                    "batch_id": batch_id,
                    "leaf_hash": proof_body["leaf_hash"],
                    "proof": proof_body["proof"],
                },
            )
            assert verify_resp.status_code == 200
            assert verify_resp.json()["ok"] is True

    def test_tampered_proof_fails_verification(
        self, client, make_user, login_token
    ):
        pt = _patient(make_user, login_token)
        pid = client.get(
            "/api/auth/me", headers={"Authorization": f"Bearer {pt}"}
        ).json()["id"]
        h = _hospital(make_user, login_token)
        first_id = None
        for i in range(3):
            r = _post_record(client, h, pid, title=f"r-{i}").json()
            if first_id is None:
                first_id = r["id"]
        adm = _admin(make_user, login_token)
        anchor_resp = client.post(
            "/api/anchor/run", headers={"Authorization": f"Bearer {adm}"}
        ).json()
        batch_id = anchor_resp["batch"]["batch_id"]

        proof_resp = client.get(
            f"/api/records/{first_id}/proof",
            headers={"Authorization": f"Bearer {h}"},
        ).json()
        assert proof_resp.get("proof"), "需要至少一步 proof 才能完成篡改测试"

        tampered = [step.copy() for step in proof_resp["proof"]]
        if tampered:
            # 翻转第一个 hex 字符
            orig = tampered[0]["hash"]
            tampered[0]["hash"] = ("b" if orig[0] != "b" else "a") + orig[1:]

        verify = client.post(
            "/api/anchor/verify",
            headers={"Authorization": f"Bearer {h}"},
            json={
                "batch_id": batch_id,
                "leaf_hash": proof_resp["leaf_hash"],
                "proof": tampered,
            },
        ).json()
        assert verify["ok"] is False

    def test_proof_404_when_not_anchored(self, client, make_user, login_token):
        pt = _patient(make_user, login_token)
        pid = client.get(
            "/api/auth/me", headers={"Authorization": f"Bearer {pt}"}
        ).json()["id"]
        h = _hospital(make_user, login_token)
        rid = _post_record(client, h, pid).json()["id"]
        # 未触发 anchor → 409
        resp = client.get(
            f"/api/records/{rid}/proof",
            headers={"Authorization": f"Bearer {h}"},
        )
        assert resp.status_code == 409

    def test_list_anchor_batches_admin_only(self, client, make_user, login_token):
        pt = _patient(make_user, login_token)
        pid = client.get(
            "/api/auth/me", headers={"Authorization": f"Bearer {pt}"}
        ).json()["id"]
        h = _hospital(make_user, login_token)
        _post_record(client, h, pid)
        adm = _admin(make_user, login_token)
        client.post("/api/anchor/run", headers={"Authorization": f"Bearer {adm}"})

        # admin 200
        resp = client.get(
            "/api/anchor/batches", headers={"Authorization": f"Bearer {adm}"}
        )
        assert resp.status_code == 200
        assert len(resp.json()) >= 1

        # 医院 403
        resp_h = client.get(
            "/api/anchor/batches", headers={"Authorization": f"Bearer {h}"}
        )
        assert resp_h.status_code == 403
