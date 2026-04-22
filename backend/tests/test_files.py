"""迭代 4：文件上传 / 下载 / 哈希完整性校验端到端测试。"""

import io
import os

import pytest

from app.config import settings
from app.crypto_util import sha256_of_bytes


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


def _upload(client, hosp_token, *, patient_id, payload_bytes, filename="report.pdf",
            mime="application/pdf", title="t", diagnosis="d", description=""):
    files = {"file": (filename, io.BytesIO(payload_bytes), mime)}
    form = {
        "patient_id": str(patient_id),
        "title": title,
        "diagnosis": diagnosis,
        "description": description,
    }
    return client.post(
        "/api/records/upload",
        headers={"Authorization": f"Bearer {hosp_token}"},
        files=files,
        data=form,
    )


def _seed_uploaded(client, make_user, login_token, payload_bytes=b"PDF-CONTENT-123"):
    patient_token = _patient(make_user, login_token)
    hosp_token = _hospital(make_user, login_token)
    patient_id = client.get(
        "/api/auth/me", headers={"Authorization": f"Bearer {patient_token}"}
    ).json()["id"]
    resp = _upload(
        client, hosp_token, patient_id=patient_id, payload_bytes=payload_bytes
    )
    assert resp.status_code == 200, resp.text
    return hosp_token, patient_token, resp.json()


class TestUpload:
    def test_upload_success_stores_encrypted_and_chain_hash(
        self, client, make_user, login_token
    ):
        plaintext = b"demo file bytes\n" * 500
        hosp, _, rec = _seed_uploaded(
            client, make_user, login_token, payload_bytes=plaintext
        )
        assert rec["has_file"] is True
        assert rec["file_name"] == "report.pdf"
        assert rec["file_mime"] == "application/pdf"
        assert rec["file_size"] == len(plaintext)
        assert rec["content_hash"] == sha256_of_bytes(plaintext)
        assert rec["tx_id"] and rec["tx_id"].startswith("rec-")

        # 密文文件应存在，且内容不是明文（已被加密）
        from app.models import MedicalRecord

        rec_id = rec["id"]
        with next(client.app.dependency_overrides[__import__(
            "app.database", fromlist=["get_db"]
        ).get_db]()) as db:
            row = db.query(MedicalRecord).filter(MedicalRecord.id == rec_id).first()
            assert row.file_path
            abs_path = os.path.join(settings.STORAGE_DIR, row.file_path)
            assert os.path.exists(abs_path)
            with open(abs_path, "rb") as fh:
                raw = fh.read()
            assert raw != plaintext  # 密文与明文不同

    def test_upload_unsupported_mime_rejected(self, client, make_user, login_token):
        pt = _patient(make_user, login_token)
        ht = _hospital(make_user, login_token)
        pid = client.get(
            "/api/auth/me", headers={"Authorization": f"Bearer {pt}"}
        ).json()["id"]

        resp = _upload(
            client, ht, patient_id=pid,
            payload_bytes=b"fake",
            filename="x.exe",
            mime="application/x-msdownload",
        )
        assert resp.status_code == 400

    def test_upload_too_large_rejected(self, client, make_user, login_token, monkeypatch):
        # 临时收紧上限，避免实际真上 11MB
        monkeypatch.setattr(settings, "MAX_FILE_SIZE_BYTES", 1024)
        pt = _patient(make_user, login_token)
        ht = _hospital(make_user, login_token)
        pid = client.get(
            "/api/auth/me", headers={"Authorization": f"Bearer {pt}"}
        ).json()["id"]

        resp = _upload(
            client, ht, patient_id=pid,
            payload_bytes=b"a" * 5000,  # > 1KB
        )
        assert resp.status_code == 413

    def test_upload_empty_file_rejected(self, client, make_user, login_token):
        pt = _patient(make_user, login_token)
        ht = _hospital(make_user, login_token)
        pid = client.get(
            "/api/auth/me", headers={"Authorization": f"Bearer {pt}"}
        ).json()["id"]
        resp = _upload(client, ht, patient_id=pid, payload_bytes=b"")
        assert resp.status_code == 400

    def test_patient_cannot_upload(self, client, make_user, login_token):
        pt = _patient(make_user, login_token)
        ht = _hospital(make_user, login_token)
        pid = client.get(
            "/api/auth/me", headers={"Authorization": f"Bearer {pt}"}
        ).json()["id"]
        files = {"file": ("x.pdf", io.BytesIO(b"abc"), "application/pdf")}
        form = {
            "patient_id": str(pid),
            "title": "x",
            "diagnosis": "x",
            "description": "",
        }
        resp = client.post(
            "/api/records/upload",
            headers={"Authorization": f"Bearer {pt}"},
            files=files,
            data=form,
        )
        assert resp.status_code == 403


class TestDownload:
    def test_download_round_trip(self, client, make_user, login_token):
        plaintext = b"x-ray report bytes" * 1000
        hosp, _, rec = _seed_uploaded(
            client, make_user, login_token, payload_bytes=plaintext
        )
        resp = client.get(
            f"/api/records/{rec['id']}/download",
            headers={"Authorization": f"Bearer {hosp}"},
        )
        assert resp.status_code == 200
        assert resp.content == plaintext
        assert resp.headers.get("x-content-hash") == rec["content_hash"]
        assert resp.headers.get("x-hash-verified") == "1"
        assert resp.headers.get("accept-ranges") == "bytes"

    def test_download_detects_tampered_ciphertext(
        self, client, make_user, login_token
    ):
        plaintext = b"highly sensitive medical text"
        hosp, _, rec = _seed_uploaded(
            client, make_user, login_token, payload_bytes=plaintext
        )
        # 篡改磁盘上的密文（翻转第 0 字节）
        from app.models import MedicalRecord

        with next(client.app.dependency_overrides[__import__(
            "app.database", fromlist=["get_db"]
        ).get_db]()) as db:
            row = db.query(MedicalRecord).filter(
                MedicalRecord.id == rec["id"]
            ).first()
            abs_path = os.path.join(settings.STORAGE_DIR, row.file_path)
        with open(abs_path, "r+b") as fh:
            data = bytearray(fh.read())
            data[0] ^= 0xFF
            fh.seek(0)
            fh.write(bytes(data))

        resp = client.get(
            f"/api/records/{rec['id']}/download",
            headers={"Authorization": f"Bearer {hosp}"},
        )
        assert resp.status_code == 422
        assert "篡改" in resp.json()["detail"] or "tag" in resp.json()["detail"].lower()

    def test_download_detects_chain_hash_mismatch(
        self, client, make_user, login_token
    ):
        """模拟链上哈希被篡改（或数据库哈希与链码不一致）：DB 里的 content_hash 对应链上值。
        这里直接改 DB 的 content_hash，模拟"链上真相 != 实际文件"的情况。
        """
        plaintext = b"correct content"
        hosp, _, rec = _seed_uploaded(
            client, make_user, login_token, payload_bytes=plaintext
        )
        from app.models import MedicalRecord

        db_gen = client.app.dependency_overrides[__import__(
            "app.database", fromlist=["get_db"]
        ).get_db]()
        db = next(db_gen)
        try:
            row = (
                db.query(MedicalRecord)
                .filter(MedicalRecord.id == rec["id"])
                .first()
            )
            row.content_hash = "0" * 64  # 伪造链上哈希
            db.commit()
        finally:
            db.close()

        resp = client.get(
            f"/api/records/{rec['id']}/download",
            headers={"Authorization": f"Bearer {hosp}"},
        )
        assert resp.status_code == 422
        assert "链上" in resp.json()["detail"] or "不一致" in resp.json()["detail"]

    def test_download_forbidden_for_unrelated_hospital(
        self, client, make_user, login_token
    ):
        _, _, rec = _seed_uploaded(client, make_user, login_token)
        make_user("hospB", password="hp", role="hospital", hospital_name="HospitalB")
        other = login_token("hospB", "hp")
        resp = client.get(
            f"/api/records/{rec['id']}/download",
            headers={"Authorization": f"Bearer {other}"},
        )
        assert resp.status_code == 403

    def test_patient_can_download_own_record(self, client, make_user, login_token):
        plaintext = b"my report"
        _, patient_tok, rec = _seed_uploaded(
            client, make_user, login_token, payload_bytes=plaintext
        )
        resp = client.get(
            f"/api/records/{rec['id']}/download",
            headers={"Authorization": f"Bearer {patient_tok}"},
        )
        assert resp.status_code == 200
        assert resp.content == plaintext

    def test_download_range_returns_206_and_slice(
        self, client, make_user, login_token
    ):
        plaintext = bytes(range(256)) * 4  # 1024 bytes 固定模式
        hosp, _, rec = _seed_uploaded(
            client, make_user, login_token, payload_bytes=plaintext
        )
        resp = client.get(
            f"/api/records/{rec['id']}/download",
            headers={
                "Authorization": f"Bearer {hosp}",
                "Range": "bytes=100-199",
            },
        )
        assert resp.status_code == 206
        assert resp.content == plaintext[100:200]
        assert resp.headers["content-range"] == f"bytes 100-199/{len(plaintext)}"
        assert resp.headers["content-length"] == "100"

    def test_download_open_ended_range(self, client, make_user, login_token):
        plaintext = b"X" * 500
        hosp, _, rec = _seed_uploaded(
            client, make_user, login_token, payload_bytes=plaintext
        )
        resp = client.get(
            f"/api/records/{rec['id']}/download",
            headers={
                "Authorization": f"Bearer {hosp}",
                "Range": "bytes=400-",
            },
        )
        assert resp.status_code == 206
        assert resp.content == plaintext[400:]
        assert resp.headers["content-range"] == "bytes 400-499/500"

    def test_download_unsatisfiable_range(self, client, make_user, login_token):
        hosp, _, rec = _seed_uploaded(
            client, make_user, login_token, payload_bytes=b"short"
        )
        resp = client.get(
            f"/api/records/{rec['id']}/download",
            headers={
                "Authorization": f"Bearer {hosp}",
                "Range": "bytes=10000-20000",
            },
        )
        assert resp.status_code == 416


class TestVerify:
    def test_verify_passes_on_untouched_file(self, client, make_user, login_token):
        hosp, _, rec = _seed_uploaded(
            client, make_user, login_token, payload_bytes=b"data\n" * 10
        )
        resp = client.get(
            f"/api/records/{rec['id']}/verify",
            headers={"Authorization": f"Bearer {hosp}"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["hash_match"] is True
        assert body["chain_hash"] == body["decrypted_hash"] == rec["content_hash"]

    def test_verify_fails_when_ciphertext_tampered(
        self, client, make_user, login_token
    ):
        hosp, _, rec = _seed_uploaded(
            client, make_user, login_token, payload_bytes=b"orig"
        )
        from app.models import MedicalRecord

        db_gen = client.app.dependency_overrides[__import__(
            "app.database", fromlist=["get_db"]
        ).get_db]()
        db = next(db_gen)
        try:
            row = db.query(MedicalRecord).filter(MedicalRecord.id == rec["id"]).first()
            abs_path = os.path.join(settings.STORAGE_DIR, row.file_path)
        finally:
            db.close()
        with open(abs_path, "r+b") as fh:
            data = bytearray(fh.read())
            data[-1] ^= 0x55  # 改最后一个字节（可能在 tag 区）
            fh.seek(0)
            fh.write(bytes(data))

        resp = client.get(
            f"/api/records/{rec['id']}/verify",
            headers={"Authorization": f"Bearer {hosp}"},
        )
        assert resp.status_code == 422

    def test_verify_fails_on_non_file_record(self, client, make_user, login_token):
        """旧的纯文本病历不应该走下载/校验。"""
        pt = _patient(make_user, login_token)
        ht = _hospital(make_user, login_token)
        pid = client.get(
            "/api/auth/me", headers={"Authorization": f"Bearer {pt}"}
        ).json()["id"]
        rec = client.post(
            "/api/records",
            headers={"Authorization": f"Bearer {ht}"},
            json={"patient_id": pid, "title": "t", "diagnosis": "d", "content": "c"},
        ).json()
        resp = client.get(
            f"/api/records/{rec['id']}/verify",
            headers={"Authorization": f"Bearer {ht}"},
        )
        assert resp.status_code == 400
