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

    # 访问申请侧：保留 v1 的语义 + 迭代 3 的 history
    def _req_snapshot(req_id, record_id, applicant, status, tx_id, reviewed_at=""):
        return {
            "docType": "AccessRequest",
            "requestId": str(req_id),
            "recordId": str(record_id),
            "applicantHospital": applicant,
            "reasonHash": "dummy-reason-hash",
            "status": status,
            "createdAt": "2026-04-22T00:00:00Z",
            "reviewedAt": reviewed_at,
            "createTxId": tx_id if status == "PENDING" else "",
            "reviewTxId": tx_id if status != "PENDING" else "",
        }

    def stub_create_access_request(**kwargs):
        rid = int(kwargs["request_id"])
        tx = f"req-{rid}-create"
        snap = _req_snapshot(
            rid, kwargs["record_id"], kwargs["hospital_name"], "PENDING", tx
        )
        chain_store["requests"][rid] = [snap]
        _bust_request(rid)
        return {"txId": tx, "result": snap}

    def stub_approve_access_request(**kwargs):
        rid = int(kwargs["request_id"])
        prev = chain_store["requests"].get(rid, [None])[-1] or {}
        tx = f"req-{rid}-approve"
        snap = {**prev, "status": "APPROVED", "reviewedAt": kwargs["reviewed_at"], "reviewTxId": tx}
        chain_store["requests"].setdefault(rid, []).append(snap)
        _bust_request(rid)
        return {"txId": tx, "result": snap}

    def stub_reject_access_request(**kwargs):
        rid = int(kwargs["request_id"])
        prev = chain_store["requests"].get(rid, [None])[-1] or {}
        tx = f"req-{rid}-reject"
        snap = {**prev, "status": "REJECTED", "reviewedAt": kwargs["reviewed_at"], "reviewTxId": tx}
        chain_store["requests"].setdefault(rid, []).append(snap)
        _bust_request(rid)
        return {"txId": tx, "result": snap}

    def stub_query_access_request(request_id: int):
        entries = chain_store["requests"].get(int(request_id), [])
        latest = entries[-1] if entries else {}
        return {"result": latest, "txId": latest.get("reviewTxId") or latest.get("createTxId", "")}

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
