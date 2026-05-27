"""迭代 9：Merkle 树工具库 —— 链下计算根与包含证明。

设计与链码 `_hashPair` / `VerifyRecordInclusion` 严格保持一致：
- 叶子用 SHA-256 十六进制字符串
- 父 = SHA-256(left_bytes || right_bytes)，其中 left/right 是十六进制串解码得到的 32 字节
- 奇数节点补自身（Bitcoin 风格）
- 证明是 list[ {hash, position: "left"|"right"} ]，验证时按 position 与兄弟拼接
"""

from __future__ import annotations

import hashlib
from typing import Iterable, List


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def hash_pair(left_hex: str, right_hex: str) -> str:
    """对两个十六进制哈希字符串做 SHA-256(left_bytes || right_bytes)。"""
    return sha256_hex(bytes.fromhex(left_hex) + bytes.fromhex(right_hex))


def leaf_hash(record_id: int, content_hash: str) -> str:
    """病历叶子哈希：SHA-256(content_hash + "|" + record_id)。"""
    payload = f"{content_hash}|{int(record_id)}".encode("utf-8")
    return sha256_hex(payload)


def compute_merkle_root(leaves: List[str]) -> str:
    """给定叶子哈希数组（hex），返回 Merkle 根。"""
    if not leaves:
        raise ValueError("叶子集合不能为空")
    if len(leaves) == 1:
        return leaves[0]
    level = list(leaves)
    while len(level) > 1:
        if len(level) % 2 == 1:
            level.append(level[-1])
        nxt: List[str] = []
        for i in range(0, len(level), 2):
            nxt.append(hash_pair(level[i], level[i + 1]))
        level = nxt
    return level[0]


def compute_proof(leaves: List[str], index: int) -> List[dict]:
    """给定叶子哈希数组和目标叶子下标，返回该叶子的包含证明（list of {hash, position}）。"""
    if not leaves:
        raise ValueError("叶子集合不能为空")
    if index < 0 or index >= len(leaves):
        raise IndexError("index 越界")
    if len(leaves) == 1:
        return []
    proof: List[dict] = []
    level = list(leaves)
    idx = index
    while len(level) > 1:
        if len(level) % 2 == 1:
            level.append(level[-1])
        # 兄弟节点在 idx ^ 1 处
        sibling_idx = idx ^ 1
        if sibling_idx > idx:
            proof.append({"hash": level[sibling_idx], "position": "right"})
        else:
            proof.append({"hash": level[sibling_idx], "position": "left"})
        nxt = [
            hash_pair(level[i], level[i + 1]) for i in range(0, len(level), 2)
        ]
        idx //= 2
        level = nxt
    return proof


def verify_proof(root_hex: str, leaf_hex: str, proof: Iterable[dict]) -> bool:
    """链下验证：与链码 VerifyRecordInclusion 逻辑严格一致。"""
    current = leaf_hex.lower()
    for step in proof:
        sibling = str(step.get("hash", "")).lower()
        position = step.get("position")
        if len(sibling) != 64 or any(c not in "0123456789abcdef" for c in sibling):
            return False
        if position == "right":
            current = hash_pair(current, sibling)
        elif position == "left":
            current = hash_pair(sibling, current)
        else:
            return False
    return current == root_hex.lower()
