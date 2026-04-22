"""迭代 4：加解密工具单元测试（底层原语，独立于 FastAPI）。"""

import io
import os
import time

import pytest

from app.crypto_util import (
    b64decode,
    b64encode,
    decrypt_to_bytes,
    encrypt_stream,
    load_file_key,
    sha256_of_bytes,
    sha256_of_file,
)


def _encrypt_roundtrip(plaintext: bytes, key: bytes):
    src = io.BytesIO(plaintext)
    dst = io.BytesIO()
    info = encrypt_stream(src, dst, key)
    ct_src = io.BytesIO(dst.getvalue())
    decrypted = decrypt_to_bytes(ct_src, key, info.nonce, info.tag)
    return decrypted, info


class TestRoundTrip:
    def test_round_trip_matches(self):
        key = os.urandom(32)
        plaintext = b"hello medshare, this is a test for GCM encryption" * 13
        out, info = _encrypt_roundtrip(plaintext, key)
        assert out == plaintext
        assert info.plaintext_size == len(plaintext)
        assert info.sha256_hex == sha256_of_bytes(plaintext)
        assert len(info.nonce) == 12
        assert len(info.tag) == 16

    def test_empty_input_produces_only_tag(self):
        key = os.urandom(32)
        src, dst = io.BytesIO(b""), io.BytesIO()
        info = encrypt_stream(src, dst, key)
        assert info.plaintext_size == 0
        assert info.sha256_hex == (
            # SHA-256("") 常量
            "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        )
        # 密文部分应为空，仅 tag 单独保存
        assert dst.getvalue() == b""

    def test_large_blob_roundtrip_1mb(self):
        key = os.urandom(32)
        plaintext = os.urandom(1024 * 1024)
        out, _ = _encrypt_roundtrip(plaintext, key)
        assert out == plaintext


class TestTamperDetection:
    def test_tampered_ciphertext_raises(self):
        key = os.urandom(32)
        plaintext = b"sensitive medical record content"
        src, dst = io.BytesIO(plaintext), io.BytesIO()
        info = encrypt_stream(src, dst, key)

        ciphertext = bytearray(dst.getvalue())
        if ciphertext:
            ciphertext[0] ^= 0x01  # 翻转 1 bit

        with pytest.raises(Exception):
            decrypt_to_bytes(
                io.BytesIO(bytes(ciphertext)), key, info.nonce, info.tag
            )

    def test_tampered_tag_raises(self):
        key = os.urandom(32)
        src, dst = io.BytesIO(b"abc"), io.BytesIO()
        info = encrypt_stream(src, dst, key)
        bad_tag = bytearray(info.tag)
        bad_tag[0] ^= 0xFF
        with pytest.raises(Exception):
            decrypt_to_bytes(io.BytesIO(dst.getvalue()), key, info.nonce, bytes(bad_tag))

    def test_wrong_key_raises(self):
        key = os.urandom(32)
        key2 = os.urandom(32)
        src, dst = io.BytesIO(b"hello"), io.BytesIO()
        info = encrypt_stream(src, dst, key)
        with pytest.raises(Exception):
            decrypt_to_bytes(io.BytesIO(dst.getvalue()), key2, info.nonce, info.tag)


class TestSha256:
    def test_sha256_of_file_equals_one_shot(self, tmp_path):
        data = os.urandom(123_456)
        p = tmp_path / "t.bin"
        p.write_bytes(data)
        assert sha256_of_file(str(p)) == sha256_of_bytes(data)


class TestKeyLoader:
    def test_loads_from_base64(self):
        raw = os.urandom(32)
        key = load_file_key(
            file_key_b64=b64encode(raw), secret_key="x", environment="development"
        )
        assert key == raw

    def test_rejects_wrong_length(self):
        with pytest.raises(RuntimeError):
            load_file_key(
                file_key_b64=b64encode(b"too short"),
                secret_key="x",
                environment="development",
            )

    def test_rejects_missing_in_production(self):
        with pytest.raises(RuntimeError):
            load_file_key(file_key_b64=None, secret_key="x", environment="production")

    def test_derives_default_from_secret_in_dev(self):
        key = load_file_key(
            file_key_b64=None, secret_key="secret-A", environment="development"
        )
        key2 = load_file_key(
            file_key_b64=None, secret_key="secret-A", environment="development"
        )
        assert key == key2
        assert len(key) == 32


class TestThroughput:
    """非硬性阈值：仅记录测量值，防止性能严重退化。"""

    def test_10mb_encrypt_throughput(self):
        key = os.urandom(32)
        plaintext = os.urandom(10 * 1024 * 1024)
        t0 = time.perf_counter()
        src, dst = io.BytesIO(plaintext), io.BytesIO()
        info = encrypt_stream(src, dst, key)
        elapsed = time.perf_counter() - t0
        mb_per_sec = 10.0 / elapsed if elapsed > 0 else float("inf")
        # 只要 >= 30MB/s 即算达标；Anaconda Python 在普通笔记本上轻松达到 100+MB/s
        print(f"\n  10MB GCM encrypt throughput: {mb_per_sec:.1f} MB/s "
              f"(elapsed {elapsed*1000:.1f} ms)")
        assert info.plaintext_size == len(plaintext)
        assert mb_per_sec >= 30.0, f"吞吐 {mb_per_sec:.1f} MB/s < 30 MB/s 目标"
