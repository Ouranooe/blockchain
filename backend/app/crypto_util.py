"""迭代 4：文件加解密与哈希工具。

设计要点：
- AES-256-GCM：每个文件一个 96-bit 随机 nonce，主密钥由 ENV 注入或从 SECRET_KEY 派生
- 流式处理：逐块读取，避免整文件加载到内存（单块默认 64KB）
- 完整性：GCM 自带 16 字节认证 tag；篡改密文或 tag 解密时会抛 InvalidTag
- 业务哈希：明文的 SHA-256，独立于 GCM 的 tag，用于上链存证
"""

from __future__ import annotations

import base64
import hashlib
import os
from dataclasses import dataclass
from typing import BinaryIO

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

_CHUNK = 64 * 1024
_NONCE_LEN = 12
_TAG_LEN = 16


@dataclass
class EncryptResult:
    nonce: bytes              # 12 bytes
    tag: bytes                # 16 bytes
    plaintext_size: int
    sha256_hex: str


def _derive_default_key(secret_key: str) -> bytes:
    """开发环境兜底：从 SECRET_KEY 派生 32 字节 key。生产环境必须显式配置。"""
    return hashlib.sha256(("medshare-file-key::" + secret_key).encode("utf-8")).digest()


def load_file_key(
    *, file_key_b64: str | None, secret_key: str, environment: str
) -> bytes:
    if file_key_b64:
        try:
            key = base64.b64decode(file_key_b64, validate=True)
        except Exception as exc:
            raise RuntimeError(f"MEDSHARE_FILE_KEY_BASE64 不是合法 base64：{exc}") from exc
        if len(key) != 32:
            raise RuntimeError("MEDSHARE_FILE_KEY_BASE64 必须解码为 32 字节 (AES-256)")
        return key
    if environment == "production":
        raise RuntimeError(
            "生产环境必须通过 MEDSHARE_FILE_KEY_BASE64 提供文件加密主密钥"
        )
    return _derive_default_key(secret_key)


def encrypt_stream(
    src: BinaryIO, dst: BinaryIO, key: bytes, *, chunk_size: int = _CHUNK
) -> EncryptResult:
    """流式加密：逐块读 src → AES-GCM → 写 dst。返回 nonce/tag/哈希/大小。"""
    if len(key) != 32:
        raise ValueError("AES-256 key 必须是 32 字节")

    nonce = os.urandom(_NONCE_LEN)
    cipher = Cipher(algorithms.AES(key), modes.GCM(nonce))
    encryptor = cipher.encryptor()
    hasher = hashlib.sha256()
    total = 0

    while True:
        chunk = src.read(chunk_size)
        if not chunk:
            break
        hasher.update(chunk)
        total += len(chunk)
        dst.write(encryptor.update(chunk))

    dst.write(encryptor.finalize())
    return EncryptResult(
        nonce=nonce,
        tag=encryptor.tag,
        plaintext_size=total,
        sha256_hex=hasher.hexdigest(),
    )


def decrypt_to_bytes(
    src: BinaryIO, key: bytes, nonce: bytes, tag: bytes, *, chunk_size: int = _CHUNK
) -> bytes:
    """流式解密：返回完整明文 bytes。若密文或 tag 被篡改，会抛 InvalidTag。"""
    if len(nonce) != _NONCE_LEN or len(tag) != _TAG_LEN:
        raise ValueError("非法 nonce / tag 长度")

    cipher = Cipher(algorithms.AES(key), modes.GCM(nonce, tag))
    decryptor = cipher.decryptor()
    out = bytearray()
    while True:
        chunk = src.read(chunk_size)
        if not chunk:
            break
        out.extend(decryptor.update(chunk))
    out.extend(decryptor.finalize())  # 校验 tag
    return bytes(out)


def sha256_of_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_of_file(path: str, *, chunk_size: int = _CHUNK) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(chunk_size)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def b64encode(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def b64decode(s: str) -> bytes:
    return base64.b64decode(s)
