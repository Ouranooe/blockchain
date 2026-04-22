"""pytest 共享 fixtures：SQLite in-memory 测试库 + gateway 打桩。

迭代 1 首次引入后端自动化测试，所有测试在独立的 SQLite 内存库中运行，
不接触真实 MySQL 与 Fabric 网关。
"""

import os
import sys

# 必须在 import app 之前设置环境，避免读到生产默认值
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-pytest-only-0123456789")
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("GATEWAY_URL", "http://gateway-stub.invalid/api")
# 迭代 4：文件存储根目录指向 tmp，每个会话隔离
import tempfile as _tempfile
_TEST_STORAGE_DIR = os.path.join(
    _tempfile.gettempdir(), f"medshare-test-storage-{os.getpid()}"
)
os.makedirs(_TEST_STORAGE_DIR, exist_ok=True)
os.environ.setdefault("MEDSHARE_STORAGE_DIR", _TEST_STORAGE_DIR)

# 保证 tests 目录可直接运行：把 backend/ 加入 sys.path
_BACKEND_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _BACKEND_ROOT not in sys.path:
    sys.path.insert(0, _BACKEND_ROOT)

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import files as files_module
from app import gateway as gateway_module
from app import main as main_module
from app.database import Base, get_db
from app.main import app
from app.models import User
from app.security import hash_password


@pytest.fixture(scope="function")
def db_engine():
    # 使用单连接 StaticPool，让多线程 TestClient 与测试代码共享同一套表
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    try:
        yield engine
    finally:
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


@pytest.fixture(scope="function")
def db_session(db_engine):
    TestingSession = sessionmaker(bind=db_engine, autocommit=False, autoflush=False)
    session = TestingSession()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture(scope="function")
def client(db_engine, db_session, monkeypatch):
    TestingSession = sessionmaker(bind=db_engine, autocommit=False, autoflush=False)

    def override_get_db():
        s = TestingSession()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = override_get_db

    # 将所有网关调用打桩为固定返回值，避免出网
    def _stub_tx(prefix: str):
        def _inner(**kwargs):
            return {"txId": f"{prefix}-stub-tx"}
        return _inner

    # 迭代 2 / 3：_chain_store 模拟链上世界状态 + 历史追加。
    #  - records/requests：record_id -> list[snapshot]（按上链顺序追加）
    #  - cache：模拟网关 30s TTL 缓存，key -> (payload, created_at)
    #  - stats：调用次数，用于断言"缓存命中后不再调链"
    import time

    chain_store: dict = {
        "records": {},
        "requests": {},
        "cache": {},
        "stats": {"history_chain_calls": 0, "request_history_chain_calls": 0},
    }
    _CACHE_TTL = 30.0

    def _cache_get(key):
        entry = chain_store["cache"].get(key)
        if not entry:
            return None
        payload, ts = entry
        if time.time() - ts > _CACHE_TTL:
            chain_store["cache"].pop(key, None)
            return None
        return payload

    def _cache_set(key, payload):
        chain_store["cache"][key] = (payload, time.time())

    def _bust_record(rid):
        chain_store["cache"].pop(("record-history", int(rid)), None)

    def _bust_request(reqid):
        chain_store["cache"].pop(("request-history", int(reqid)), None)

    def stub_create_record(**kwargs):
        rid = int(kwargs["record_id"])
        tx = f"rec-{rid}-v1"
        snap = {
            "docType": "RecordEvidence",
            "recordId": str(rid),
            "patientId": str(kwargs["patient_id"]),
            "uploaderHospital": kwargs["hospital_name"],
            "dataHash": kwargs["data_hash"],
            "version": 1,
            "previousTxId": "",
            "createdAt": kwargs["created_at"],
            "updatedAt": kwargs["created_at"],
            "txId": tx,
        }
        chain_store["records"][rid] = [snap]
        _bust_record(rid)
        return {"txId": tx, "result": snap}

    def stub_revise_record(**kwargs):
        rid = int(kwargs["record_id"])
        if rid not in chain_store["records"]:
            raise RuntimeError(f"Record evidence {rid} not found")
        prev = chain_store["records"][rid][-1]
        new_version = prev["version"] + 1
        tx = f"rec-{rid}-v{new_version}"
        new_entry = {
            **prev,
            "dataHash": kwargs["new_data_hash"],
            "version": new_version,
            "previousTxId": prev["txId"],
            "updatedAt": kwargs["updated_at"],
            "txId": tx,
        }
        chain_store["records"][rid].append(new_entry)
        _bust_record(rid)
        return {"txId": tx, "result": new_entry}

    def stub_query_record_version(record_id: int, version: int):
        entries = chain_store["records"].get(int(record_id), [])
        for e in entries:
            if int(e["version"]) == int(version):
                return {"result": e}
        return {"result": None}

    def _fabric_history_entries(snapshots):
        # 模拟 Fabric GetHistoryForKey：按时间倒序（最新在前）
        base_ts = 1_714_000_000
        out = []
        for i, snap in enumerate(snapshots):
            ts_iso = f"2026-04-22T00:00:{i:02d}.000Z"
            out.append(
                {
                    "txId": snap.get("txId", ""),
                    "timestamp": ts_iso,
                    "isDelete": False,
                    "value": snap,
                }
            )
        return list(reversed(out))

    def stub_query_record_history(record_id: int):
        rid = int(record_id)
        key = ("record-history", rid)
        cached = _cache_get(key)
        if cached is not None:
            return {**cached, "cache": "hit"}
        chain_store["stats"]["history_chain_calls"] += 1
        snapshots = chain_store["records"].get(rid, [])
        payload = {"result": _fabric_history_entries(snapshots)}
        _cache_set(key, payload)
        return {**payload, "cache": "miss"}

    # 访问申请侧：迭代 5 增加 ABAC 字段与状态机守卫
    ALLOWED = {
        "PENDING": {"APPROVED", "REJECTED"},
        "APPROVED": {"REVOKED"},
        "REJECTED": set(),
        "REVOKED": set(),
    }

    def _now_ts():
        return int(time.time())

    def _hospital_to_msp(name):
        s = (name or "").strip().lower()
        if s in {"hospitala", "hospital_a", "hospital a", "org1", "org1msp"}:
            return "Org1MSP"
        if s in {"hospitalb", "hospital_b", "hospital b", "org2", "org2msp"}:
            return "Org2MSP"
        return "Org1MSP"

    def _base_snap(req_id, record_id, applicant, patient_id, tx_id):
        return {
            "docType": "AccessRequest",
            "requestId": str(req_id),
            "recordId": str(record_id),
            "applicantHospital": applicant,
            "applicantMsp": _hospital_to_msp(applicant),
            "patientId": str(patient_id),
            "reasonHash": "dummy-reason-hash",
            "status": "PENDING",
            "createdAt": "2026-04-22T00:00:00Z",
            "reviewedAt": "",
            "revokedAt": "",
            "expiresAt": "",
            "expiresAtTs": 0,
            "remainingReads": 0,
            "readsUsed": 0,
            "createTxId": tx_id,
            "reviewTxId": "",
            "revokeTxId": "",
            "lastAccessTxId": "",
        }

    def stub_create_access_request(**kwargs):
        rid = int(kwargs["request_id"])
        tx = f"req-{rid}-create"
        snap = _base_snap(
            rid,
            kwargs["record_id"],
            kwargs["hospital_name"],
            kwargs["patient_id"],
            tx,
        )
        chain_store["requests"][rid] = [snap]
        _bust_request(rid)
        return {"txId": tx, "result": snap}

    def _current_status(rid):
        entries = chain_store["requests"].get(rid, [])
        return entries[-1]["status"] if entries else None

    def stub_approve_access_request(**kwargs):
        rid = int(kwargs["request_id"])
        current = _current_status(rid)
        if current is None:
            raise RuntimeError(f"Access request {rid} not found")
        if "APPROVED" not in ALLOWED[current]:
            raise RuntimeError(
                f"非法状态跃迁：{current} → APPROVED（访问申请 {rid}）"
            )
        duration = int(kwargs.get("duration_days") or 0)
        reads = int(kwargs.get("max_reads") or 0)
        if duration <= 0 or reads <= 0:
            raise RuntimeError("durationDays / maxReads 必须为正数")
        prev = chain_store["requests"][rid][-1]
        tx = f"req-{rid}-approve"
        expires_ts = _now_ts() + duration * 86400
        snap = {
            **prev,
            "status": "APPROVED",
            "reviewedAt": kwargs["reviewed_at"],
            "reviewTxId": tx,
            "expiresAtTs": expires_ts,
            "expiresAt": f"{expires_ts}",  # 简化：保存秒数字符串
            "remainingReads": reads,
            "readsUsed": 0,
        }
        chain_store["requests"][rid].append(snap)
        _bust_request(rid)
        return {"txId": tx, "result": snap}

    def stub_reject_access_request(**kwargs):
        rid = int(kwargs["request_id"])
        current = _current_status(rid)
        if current is None:
            raise RuntimeError(f"Access request {rid} not found")
        if "REJECTED" not in ALLOWED[current]:
            raise RuntimeError(
                f"非法状态跃迁：{current} → REJECTED（访问申请 {rid}）"
            )
        tx = f"req-{rid}-reject"
        snap = {
            **chain_store["requests"][rid][-1],
            "status": "REJECTED",
            "reviewedAt": kwargs["reviewed_at"],
            "reviewTxId": tx,
        }
        chain_store["requests"][rid].append(snap)
        _bust_request(rid)
        return {"txId": tx, "result": snap}

    def stub_revoke_access_request(**kwargs):
        rid = int(kwargs["request_id"])
        current = _current_status(rid)
        if current is None:
            raise RuntimeError(f"Access request {rid} not found")
        if "REVOKED" not in ALLOWED[current]:
            raise RuntimeError(
                f"非法状态跃迁：{current} → REVOKED（访问申请 {rid}）"
            )
        prev = chain_store["requests"][rid][-1]
        if str(prev.get("patientId")) != str(kwargs["patient_id"]):
            raise RuntimeError("只有归属患者可以撤销授权")
        tx = f"req-{rid}-revoke"
        snap = {
            **prev,
            "status": "REVOKED",
            "revokedAt": kwargs["revoked_at"],
            "revokeTxId": tx,
        }
        chain_store["requests"][rid].append(snap)
        _bust_request(rid)
        return {"txId": tx, "result": snap}

    def stub_access_record_consume(**kwargs):
        rid = int(kwargs["request_id"])
        entries = chain_store["requests"].get(rid)
        if not entries:
            raise RuntimeError(f"Access request {rid} not found")
        prev = entries[-1]
        if prev.get("status") != "APPROVED":
            raise RuntimeError(
                f"授权不可用：当前状态 {prev.get('status')}（访问申请 {rid}）"
            )
        if prev.get("expiresAtTs", 0) and _now_ts() >= prev["expiresAtTs"]:
            raise RuntimeError("授权已过期")
        if not prev.get("remainingReads") or prev["remainingReads"] <= 0:
            raise RuntimeError("访问次数已用尽")
        caller_msp = _hospital_to_msp(kwargs["hospital_name"])
        if prev.get("applicantMsp") and prev["applicantMsp"] != caller_msp:
            raise RuntimeError(
                f"调用方 MSP ({caller_msp}) 与授权绑定 MSP ({prev['applicantMsp']}) 不一致"
            )
        tx = f"req-{rid}-access-{prev['readsUsed'] + 1}"
        snap = {
            **prev,
            "remainingReads": prev["remainingReads"] - 1,
            "readsUsed": prev["readsUsed"] + 1,
            "lastAccessTxId": tx,
        }
        chain_store["requests"][rid].append(snap)
        _bust_request(rid)
        return {
            "txId": tx,
            "result": {
                "requestId": str(rid),
                "recordId": prev.get("recordId"),
                "remainingReads": snap["remainingReads"],
                "readsUsed": snap["readsUsed"],
                "accessedAt": kwargs["accessed_at"],
                "txId": tx,
            },
        }

    def stub_query_access_request(request_id: int):
        entries = chain_store["requests"].get(int(request_id), [])
        latest = entries[-1] if entries else {}
        return {
            "result": latest,
            "txId": latest.get("reviewTxId")
            or latest.get("createTxId", "")
            or latest.get("revokeTxId", ""),
        }

    def stub_query_access_request_history(request_id: int):
        rid = int(request_id)
        key = ("request-history", rid)
        cached = _cache_get(key)
        if cached is not None:
            return {**cached, "cache": "hit"}
        chain_store["stats"]["request_history_chain_calls"] += 1
        snapshots = chain_store["requests"].get(rid, [])
        payload = {"result": _fabric_history_entries(snapshots)}
        _cache_set(key, payload)
        return {**payload, "cache": "miss"}

    for target in (gateway_module, main_module, files_module):
        if hasattr(target, "create_record_evidence"):
            monkeypatch.setattr(target, "create_record_evidence", stub_create_record)
        if hasattr(target, "revise_record_evidence"):
            monkeypatch.setattr(target, "revise_record_evidence", stub_revise_record)
        if hasattr(target, "query_record_version"):
            monkeypatch.setattr(target, "query_record_version", stub_query_record_version)
        if hasattr(target, "query_record_history"):
            monkeypatch.setattr(target, "query_record_history", stub_query_record_history)
        if hasattr(target, "create_access_request"):
            monkeypatch.setattr(target, "create_access_request", stub_create_access_request)
        if hasattr(target, "approve_access_request"):
            monkeypatch.setattr(target, "approve_access_request", stub_approve_access_request)
        if hasattr(target, "reject_access_request"):
            monkeypatch.setattr(target, "reject_access_request", stub_reject_access_request)
        if hasattr(target, "revoke_access_request"):
            monkeypatch.setattr(target, "revoke_access_request", stub_revoke_access_request)
        if hasattr(target, "access_record_consume"):
            monkeypatch.setattr(target, "access_record_consume", stub_access_record_consume)
        if hasattr(target, "query_access_request"):
            monkeypatch.setattr(target, "query_access_request", stub_query_access_request)
        if hasattr(target, "query_access_request_history"):
            monkeypatch.setattr(
                target, "query_access_request_history", stub_query_access_request_history
            )

    # 暴露 stats 与 store 供测试断言 / 篡改
    app.state.chain_stats = chain_store["stats"]
    app.state.chain_store = chain_store

    with TestClient(app) as c:
        yield c

    app.dependency_overrides.clear()


@pytest.fixture
def make_user(db_session):
    """工厂 fixture：按需创建用户。"""

    def _make(
        username: str,
        password: str = "123456",
        role: str = "patient",
        real_name: str = "测试用户",
        hospital_name=None,
        msp_org=None,
        is_active: bool = True,
        *,
        hashed: bool = True,
    ) -> User:
        stored = hash_password(password) if hashed else password
        user = User(
            username=username,
            password=stored,
            role=role,
            real_name=real_name,
            hospital_name=hospital_name,
            msp_org=msp_org,
            is_active=is_active,
        )
        db_session.add(user)
        db_session.commit()
        db_session.refresh(user)
        return user

    return _make


@pytest.fixture
def login_token(client):
    """工厂 fixture：登录并返回 Bearer token。"""

    def _login(username: str, password: str) -> str:
        resp = client.post(
            "/api/auth/login", json={"username": username, "password": password}
        )
        assert resp.status_code == 200, resp.text
        return resp.json()["token"]

    return _login
