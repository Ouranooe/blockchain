# 后端自动化测试（迭代 1 引入）

## 技术选型

- **pytest 8.x** — 测试运行器
- **httpx + FastAPI TestClient** — 接口级测试
- **SQLite in-memory** — 独立测试库，不接触 MySQL
- **monkeypatch** — 打桩 Fabric 网关调用，避免对真实链的依赖

## 运行方式

```bash
cd backend
pip install -r requirements-dev.txt

# 跑全部测试
pytest

# 详细输出
pytest -v

# 跑指定文件 / 指定类 / 指定用例
pytest tests/test_auth.py
pytest tests/test_auth.py::TestLogin
pytest tests/test_auth.py::TestLogin::test_login_success_returns_token_and_user
```

## 目录约定

```
backend/tests/
├── __init__.py
├── conftest.py          # 共享 fixtures（测试库、gateway 打桩、登录工厂）
├── test_auth.py         # 认证接口测试（登录/注册/改密/禁用）
└── README.md
```

## 常见 fixtures

- `client`：已绑定 SQLite 内存库 + gateway 打桩的 FastAPI TestClient
- `db_session`：直接操作测试库的 SQLAlchemy Session
- `make_user(username, password, role, ...)`：快速创建用户
- `login_token(username, password)`：登录后拿 JWT Token

## 后续迭代扩展思路

- **迭代 2**：新增 `test_records.py`、`test_access_requests.py` 覆盖病历 CRUD
- **迭代 3**：补状态机测试、覆盖申请撤销 / 过期流程
- **迭代 4**：文件上传加解密往返用例
- **迭代 5**：链上权限校验的端到端用例（配合链码测试）
