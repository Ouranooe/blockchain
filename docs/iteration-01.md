# 迭代 1 完成报告：工程基石 + 链码测试框架

> 对应 [ITERATION_PLAN.md](../ITERATION_PLAN.md) 第 1 次迭代

## 一、本次迭代目标

1. 补齐**后端 pytest** 与**链码 mocha** 两套自动化测试基础
2. 修复"密码明文存储 / 密钥硬编码"两个最严重的安全问题
3. 补齐"用户注册 / 修改密码 / 账号启用-禁用"这三项用户体系基础能力
4. 为 `User` 增加 **`msp_org`** 字段，为后续迭代的链上身份映射做铺垫

## 二、改动清单

### 2.1 后端（Python / FastAPI）

| 文件 | 改动 |
|------|------|
| `backend/requirements.txt` | 新增 `passlib[bcrypt]==1.7.4` |
| `backend/requirements-dev.txt` | 新文件：pytest / httpx 测试依赖 |
| `backend/.env.example` | 新文件：环境变量模板 |
| `backend/pytest.ini` | 新文件：pytest 配置 |
| `backend/app/config.py` | 引入环境标识 `ENVIRONMENT`；生产环境强制 SECRET_KEY；默认值告警 |
| `backend/app/database.py` | 按 DB 方言动态传 `connect_args`（MySQL/SQLite 通用） |
| `backend/app/security.py` | 新文件：bcrypt `hash_password / verify_password / is_hashed` |
| `backend/app/models.py` | User 新增 `msp_org` / `is_active` 字段 |
| `backend/app/schemas.py` | 新增 `RegisterRequest / ChangePasswordRequest / SimpleMessage`；`UserInfo` 补 `msp_org / is_active` |
| `backend/app/auth.py` | JWT 校验时拒绝已禁用账号 |
| `backend/app/main.py` | 登录改为 bcrypt 校验 + 明文→哈希自动迁移；新增 `/register`、`/change-password`、`/me` 三个端点 |
| `backend/sql/init.sql` | 建表补充 `msp_org / is_active`；种子账号填充 MSP 组织 |

### 2.2 链码（Node.js / Fabric Contract）

| 文件 | 改动 |
|------|------|
| `fabric-network/chaincode/medshare/javascript/package.json` | 新增 devDeps：mocha / chai / chai-as-promised / sinon / sinon-chai / nyc；新增 `test` / `test:watch` / `coverage` 脚本 |
| `fabric-network/chaincode/medshare/javascript/.mocharc.json` | 新文件：mocha 配置 |
| `fabric-network/chaincode/medshare/javascript/test/helpers.js` | 新文件：mock Context 工厂（stub.getState/putState/getTxID/setEvent） |
| `fabric-network/chaincode/medshare/javascript/test/medshare-contract.test.js` | 新文件：15 条链码单元测试 |
| `fabric-network/chaincode/medshare/javascript/README.md` | 新文件：测试运行说明 |

### 2.3 测试文档

| 文件 | 内容 |
|------|------|
| `backend/tests/conftest.py` | SQLite 内存库 + gateway 打桩 + 用户/登录工厂 fixtures |
| `backend/tests/test_auth.py` | 16 条用例：登录 / 注册 / 改密 / 禁用 / whoami |
| `backend/tests/README.md` | 测试运行说明 |

## 三、验证结果

### 3.1 后端测试

```
$ cd backend
$ pytest tests/ -v
================================ test session starts ================================
collected 16 items

tests/test_auth.py::TestLogin::test_login_success_returns_token_and_user        PASSED
tests/test_auth.py::TestLogin::test_login_wrong_password                        PASSED
tests/test_auth.py::TestLogin::test_login_user_not_found                        PASSED
tests/test_auth.py::TestLogin::test_login_disabled_account                      PASSED
tests/test_auth.py::TestLogin::test_legacy_plaintext_password_migrates_to_bcrypt PASSED
tests/test_auth.py::TestRegister::test_register_patient_success                 PASSED
tests/test_auth.py::TestRegister::test_register_duplicate_username_rejected     PASSED
tests/test_auth.py::TestRegister::test_register_non_patient_role_rejected       PASSED
tests/test_auth.py::TestRegister::test_register_short_password_rejected         PASSED
tests/test_auth.py::TestRegister::test_register_invalid_username_rejected       PASSED
tests/test_auth.py::TestChangePassword::test_change_password_success            PASSED
tests/test_auth.py::TestChangePassword::test_change_password_wrong_old          PASSED
tests/test_auth.py::TestChangePassword::test_change_password_same_as_old        PASSED
tests/test_auth.py::TestChangePassword::test_change_password_requires_auth      PASSED
tests/test_auth.py::TestWhoAmI::test_whoami_returns_current_user                PASSED
tests/test_auth.py::TestWhoAmI::test_whoami_rejected_when_disabled_after_token  PASSED

============================== 16 passed in 6.25s ==============================
```

**后端 16/16 通过，单轮 < 7 秒。**

### 3.2 链码测试

```
$ cd fabric-network/chaincode/medshare/javascript
$ npm test

  MedShareContract
    CreateMedicalRecordEvidence
      ✔ 首次创建应写入世界状态并返回带 txId 的 JSON
      ✔ 重复创建同一 recordId 应抛错
    GetMedicalRecordEvidence
      ✔ 查询不存在的记录应抛错
      ✔ 能读到已创建的证据
    CreateAccessRequest
      ✔ 首次创建应写入 PENDING 状态
      ✔ 重复创建应抛错
      ✔ 未指定 status 时默认为 PENDING
    ApproveAccessRequest
      ✔ 应把状态改为 APPROVED 并写入 reviewTxId
      ✔ 对不存在的申请审批应抛错
    RejectAccessRequest
      ✔ 应把状态改为 REJECTED
      ✔ 对不存在的申请拒绝应抛错
    QueryAccessRequest
      ✔ 不存在时抛错
      ✔ 存在时返回完整 JSON
    端到端：审批流状态机
      ✔ PENDING → APPROVED 后重复审批仍然成功（当前无状态守卫）
      ✔ 交易 ID 每次调用都来自 ctx.stub.getTxID()

  15 passing (19ms)
```

**链码 15/15 通过，单轮 < 20 毫秒。**

## 四、关键设计决策

### 4.1 历史明文密码的平滑迁移

种子库里 5 个账号的密码是明文 `123456`。为了**不影响现有部署**，采取了透明迁移方案：

```python
# backend/app/main.py login()
if not user or not verify_password(payload.password, user.password):
    raise 401
if not is_hashed(user.password):          # 旧明文
    user.password = hash_password(payload.password)  # 当场哈希
    db.commit()
```

`verify_password` 内部按前缀识别：若非 `$2a$ / $2b$ / $2y$` 则走明文比对。**首次登录成功即完成一次性迁移**，零停机、零感知。

### 4.2 SQLite 测试库

- 原 `database.py` 写死了 `connect_args={"charset": "utf8mb4"}`（MySQL 专属），会让 SQLite 报 `TypeError: 'charset' is an invalid keyword argument`
- 修复方案：按 `DATABASE_URL.startswith("mysql" | "sqlite")` 动态传参
- `conftest.py` 里用 `StaticPool` 让多线程 TestClient 与测试代码共享同一套内存表，避免 "database is locked" / "no such table"

### 4.3 链码 mock 的关键是 `stub._state: Map`

不依赖 `fabric-shim-mock`（其与 fabric 2.x 的兼容性并不稳定），而是直接手写一个 `sinon.stub()`，用 `Map<string, Buffer>` 充当世界状态。这样：
- 能精确控制读写顺序
- 能断言 `setEvent` 的调用（迭代 6 会用到）
- 能轻松构造 "已有数据" 场景

## 五、量化指标

| 指标 | 基线（迭代前） | 本次交付 | 变化 |
|------|----------------|----------|------|
| 后端自动化测试用例数 | 0 | **16** | +16 |
| 链码自动化测试用例数 | 0 | **15** | +15 |
| 链码方法覆盖率 | 0% | **6/6 = 100%** | +100% |
| 密码存储安全 | 明文 | **bcrypt + salt** | — |
| 关键密钥管理 | 硬编码 | **环境变量** | — |

## 六、已知不足 / 留给后续迭代

1. **前端未适配**：新的 `/register` 和 `/change-password` 接口尚无前端页面（迭代 2 会补）
2. **管理员启用/禁用账号的管理端点未实现**：`is_active` 字段已就位但暂无管理接口
3. **用户注册仅开放 `patient`**：医院/管理员创建仍需直接操作数据库
4. **Fabric 集成测试未启动**：目前仅链码单元测试，不涉及真实 peer。迭代 3 会首次加入真跑链的集成测试
5. **CI 尚未接入**：建议后续在 `.github/workflows/` 或自建 Runner 上启用两条流水线

## 七、如何复核本次迭代

```bash
# 1. 后端测试
cd backend
pip install -r requirements-dev.txt
pytest tests/ -v

# 2. 链码测试
cd fabric-network/chaincode/medshare/javascript
npm install
npm test

# 3. 容器重建后验证登录仍可用
docker compose up -d --build backend
# 首次登录 admin/123456 会触发明文→bcrypt 迁移
curl -X POST http://localhost:8000/api/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"123456"}'

# 4. 验证新接口
curl -X POST http://localhost:8000/api/auth/register \
  -H 'Content-Type: application/json' \
  -d '{"username":"newuser","password":"mypass1","real_name":"新用户","role":"patient"}'
```
