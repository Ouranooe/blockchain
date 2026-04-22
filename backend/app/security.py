"""密码哈希工具（迭代 1 引入 bcrypt）。"""

from passlib.context import CryptContext

_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(raw: str) -> str:
    return _pwd_context.hash(raw)


def verify_password(raw: str, stored: str) -> bool:
    """校验密码。兼容历史明文：若存储值不是 bcrypt 哈希，则按明文比对。"""
    if not stored:
        return False
    if stored.startswith("$2a$") or stored.startswith("$2b$") or stored.startswith("$2y$"):
        try:
            return _pwd_context.verify(raw, stored)
        except Exception:
            return False
    return raw == stored


def is_hashed(stored: str) -> bool:
    return bool(stored) and (
        stored.startswith("$2a$") or stored.startswith("$2b$") or stored.startswith("$2y$")
    )
