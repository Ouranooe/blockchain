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

# 保证 tests 目录可直接运行：把 backend/ 加入 sys.path
_BACKEND_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _BACKEND_ROOT not in sys.path:
    sys.path.insert(0, _BACKEND_ROOT)

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

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

    monkeypatch.setattr(gateway_module, "create_record_evidence", _stub_tx("rec"))
    monkeypatch.setattr(gateway_module, "create_access_request", _stub_tx("req"))
    monkeypatch.setattr(gateway_module, "approve_access_request", _stub_tx("apr"))
    monkeypatch.setattr(gateway_module, "reject_access_request", _stub_tx("rej"))
    monkeypatch.setattr(
        gateway_module, "query_access_request", lambda request_id: {"result": {}, "txId": ""}
    )
    # main.py 里以 `from .gateway import ...` 导入了引用，需要一并替换
    monkeypatch.setattr(main_module, "create_record_evidence", _stub_tx("rec"))
    monkeypatch.setattr(main_module, "create_access_request", _stub_tx("req"))
    monkeypatch.setattr(main_module, "approve_access_request", _stub_tx("apr"))
    monkeypatch.setattr(main_module, "reject_access_request", _stub_tx("rej"))
    monkeypatch.setattr(
        main_module, "query_access_request", lambda request_id: {"result": {}, "txId": ""}
    )

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
