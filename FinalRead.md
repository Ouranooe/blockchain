# MedShare — 最终说明（FinalRead）

> 基于 Hyperledger Fabric 的医疗数据共享平台 · 八次迭代收官
> 开发环境：**Windows**（IDE/编辑）；运行环境：**WSL2 Ubuntu + Docker Desktop**

---

## 一、这是什么

**MedShare** 是一个面向教学与实验的"医院—患者—管理员"三方医疗数据共享系统。三角色通过 FastAPI 后端 + Vue3 前端交互；业务状态（上传、修订、访问申请、审批、撤销、下载）**全部上链**到 Hyperledger Fabric 2.x 联盟链，真正体现"区块链的不可篡改与不可绕过"。

一句话概括：

> **MySQL 只保留当前视图，Fabric 保留唯一真相。**
>
> 文件在本地盘以 AES-256-GCM 密文存储；上链的只是 SHA-256 哈希。
> 访问控制策略（有效期 / 最大读取次数 / MSP 绑定 / 撤销）写进链码，**绕过后端也被链码拒绝**。

---

## 二、核心能力一览（八次迭代累计）

| 能力 | 位置 | 迭代 |
|---|---|---|
| 注册 / 登录（bcrypt 哈希 + JWT） | [backend/app/auth.py](backend/app/auth.py) | 1 |
| 病历版本链（`previous_tx_id` + `version`） | [medshare-contract.js](fabric-network/chaincode/medshare/javascript/lib/medshare-contract.js) | 2 |
| 链上历史查询（`GetHistoryForKey` + 30s 缓存） | [gateway/src/app.js](gateway/src/app.js) | 3 |
| 文件加密上传 / 下载 / 完整性校验（AES-256-GCM + SHA-256 上链） | [backend/app/files.py](backend/app/files.py) | 4 |
| 链上 ABAC（过期 / 次数 / 撤销 / MSP 绑定） | chaincode `AccessRecord/Revoke` | 5 |
| 链码事件 + WebSocket 实时通知 + 审计落库 | [backend/app/events.py](backend/app/events.py) + [NotificationBell.vue](frontend/src/components/NotificationBell.vue) | 6 |
| CouchDB 富查询（按医院/时间段/患者） | chaincode `QueryRecordsByHospital` 等 | 7 |
| Prometheus + Grafana + slowapi 限流 + Nginx+HTTPS + Caliper + Locust + 安全扫描 | [deploy/](deploy/) | 8 |

**测试规模**：链码 Mocha **49 条** + 后端 pytest **107 条** 全绿（Windows 本地可跑）。

---

## 三、架构总览

```
                 ┌─────────────────────────────────────┐
                 │           Nginx (443 HTTPS)         │  ← 迭代 8 生产栈
                 │      TLS + gzip + 安全头 + 反代     │
                 └─────────────┬──────────────────────┘
                               │
          ┌────────────────────┼────────────────────┐
          ▼                    ▼                    ▼
  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐
  │  Frontend    │    │  Backend     │    │  Backend WS  │
  │  Vue3 (:80)  │    │  FastAPI     │    │  /ws/notif   │
  │              │    │  (:8000)     │    │              │
  └──────────────┘    └─────┬────────┘    └──────────────┘
                            │
               ┌────────────┼────────────┐
               ▼            ▼            ▼
         ┌─────────┐  ┌─────────┐  ┌─────────────┐
         │  MySQL  │  │ Gateway │  │ Storage     │
         │  8.0    │  │ Node/   │  │ (AES 密文)  │
         │  (当前  │  │ Fabric  │  │ ./backend/  │
         │   视图) │  │ SDK     │  │  storage/   │
         └─────────┘  └────┬────┘  └─────────────┘
                           │
                    ┌──────┴──────┐
                    │ Fabric 2.x  │
                    │ test-network│
                    │ + CouchDB   │  ← 迭代 7
                    │ 索引文件    │
                    └─────────────┘

  ┌──────────────────────────────────────────────────┐
  │ 迭代 8 观测栈：Prometheus(:9090) + Grafana(:3001)│
  │           + MySQL 备份容器（每日）               │
  └──────────────────────────────────────────────────┘
```

**数据流要点**：

- 读写病历 / 审批 / 撤销 → 后端 → Gateway → Fabric 链码 → 世界状态（CouchDB） + 区块链账本
- 文件内容 → 后端流式加密 → 本地 `storage/` 落盘（密文）；链上**只存哈希**
- 链上事件 → Gateway `addContractListener`（真链）或后端 `event_bus`（demo/测试）→ WebSocket → 前端铃铛
- 前端通过 JWT 认证；WebSocket 也带 token

---

## 四、目录结构

```
blockchain/
├── backend/              FastAPI 后端 + SQLAlchemy + AES 加密
│   ├── app/              应用代码（14 个模块）
│   ├── sql/init.sql      数据库初始化（含迭代 1-6 的全部列）
│   ├── tests/            107 条 pytest 用例
│   └── storage/          加密文件落盘位置（.gitkeep 占位）
├── frontend/             Vue3 + Element Plus + Vite
│   └── src/
│       ├── views/        按角色拆分：admin/hospital/patient
│       └── components/   AppLayout + NotificationBell
├── gateway/              Node.js Fabric Gateway（含富查询缓存 / listener 骨架）
├── fabric-network/       Fabric test-network 管理脚本 + 链码
│   └── chaincode/medshare/javascript/
│       ├── lib/medshare-contract.js      14 个链码方法
│       ├── META-INF/statedb/couchdb/     迭代 7 索引文件
│       └── test/                         Mocha 49 条
├── deploy/               迭代 8 生产化部署文件
│   ├── docker-compose.prod.yml           prod overlay
│   ├── nginx.conf                        TLS + gzip + 安全头
│   ├── gen-self-signed-cert.sh           自签证书
│   ├── mysql-backup.sh                   每日备份
│   ├── prometheus.yml
│   └── grafana/provisioning/             数据源 + 仪表板
├── caliper/              迭代 8 Caliper 基准（三场景）
├── tools/
│   ├── locust/           100 并发 API 压测
│   └── security/         bandit + npm audit + ZAP 一键
├── docs/                 8 份迭代实验报告
├── docker-compose.yml    基础 compose（开发/演示）
└── 项目迭代计划（8次）.md  顶层路线图
```

---

## 五、环境说明：Windows 开发，WSL2 运行（非常重要）

### 5.1 为什么选择"开发在 Windows，跑在 WSL2"

| 操作 | 推荐环境 | 原因 |
|---|---|---|
| 编辑代码 / 跑单元测试 | **Windows** | VSCode + 中文输入法舒服；测试用 SQLite + mock，跨平台 |
| 跑 Docker 栈（所有服务） | **WSL2 Ubuntu** | Fabric test-network 脚本依赖 bash/openssl/certutil；卷挂载在 WSL 下性能优秀 |
| 跑 Caliper / Locust / 真链验证 | **WSL2 Ubuntu** | 真实 Fabric peer 只能在 Linux |

### 5.2 必须知道的三个坑

#### ① 换行符（CRLF vs LF）

Windows 默认 CRLF；Bash 脚本如果带 CRLF，在 WSL/Linux 下会报 `/bin/bash^M: bad interpreter`。

**解决**：在项目根建 `.gitattributes`：

```gitattributes
* text=auto eol=lf
*.sh text eol=lf
*.bat text eol=crlf
```

已有文件已经是 LF 的话，**把整个仓库 clone 到 WSL 侧的文件系统**（例如 `~/workspace/blockchain/`），Git 会自动规范化。不要在 `/mnt/d/` 下跑 Docker —— 跨文件系统性能和权限都会出问题。

#### ② 脚本可执行权限

Windows NTFS 不保留 Unix `x` 位。第一次到 WSL 下要：

```bash
cd ~/workspace/blockchain
chmod +x deploy/*.sh fabric-network/scripts/*.sh tools/security/*.sh
```

#### ③ Docker Desktop 必须开启 WSL2 integration

Docker Desktop → Settings → Resources → WSL Integration → Enable for Ubuntu。这样 `docker` / `docker compose` 命令在 WSL shell 里可用，且容器的数据卷挂载到 WSL 文件系统时性能最佳。

### 5.3 Python / Node 依赖差异

| 依赖 | Windows 开发（跑测试） | WSL / Docker |
|---|---|---|
| Python | **Anaconda / 系统 Python 3.12**；装 `backend/requirements.txt` + `requirements-dev.txt` 即可 | 容器内自动装 |
| Node | 本地 Node 20 跑 `npm test`（Mocha 链码测试） | 容器内 Node 18（Fabric SDK 兼容版本） |
| cryptography / bcrypt wheel | Windows 有官方 wheel，装 `passlib[bcrypt]` / `python-jose[cryptography]` 即可 | 容器 Dockerfile 已处理 |
| openssl | Git Bash 带，但 `gen-self-signed-cert.sh` 建议到 WSL 跑 | WSL Ubuntu 默认有 |

---

## 六、快速开始（三档）

### 档位 A：只跑自动化测试（Windows 即可，不需要 Docker）

这是最快的验证方式：

```powershell
# Windows PowerShell 或 Git Bash
cd d:\vsCode\python\blockchain\backend
pip install -r requirements-dev.txt
pytest tests/ -v         # 107 条后端测试，~1 分钟

cd ..\fabric-network\chaincode\medshare\javascript
npm install              # 首次安装 mocha / chai / sinon / nyc
npm test                 # 49 条链码测试，<1 秒
```

用这种模式可以复核**迭代 1-8 所有测试**的通过性（SQLite in-memory + mock Fabric 世界状态 + mock CouchDB Mango）。不需要 Docker，不需要 WSL。

### 档位 B：跑基础业务栈（WSL2 + Docker，Fabric test-network）

```bash
# 在 WSL2 Ubuntu 下
cd ~/workspace/blockchain

# 首次：初始化 Fabric（CouchDB 模式，迭代 7 + 8 已切换）
docker compose --profile fabric-init up --build fabric-bootstrap

# 启动业务服务
docker compose up -d --build

# 访问
# 前端：http://localhost:5173
# 后端 Swagger：http://localhost:8000/docs
# Gateway 健康：http://localhost:3000/health
```

测试账号（初次登录会把明文密码自动迁移为 bcrypt 哈希）：

| 角色 | 用户名 | 密码 |
|---|---|---|
| 管理员 | admin | 123456 |
| 患者 | patient1 / patient2 | 123456 |
| 医院 | hospital_a / hospital_b | 123456 |

停止 / 清理：

```bash
docker compose down
bash fabric-network/scripts/teardown.sh    # 彻底清掉 test-network
```

### 档位 C：生产栈（WSL2 + Docker + Nginx/HTTPS/监控）

迭代 8 新增的生产化部署：

```bash
cd ~/workspace/blockchain

# 1. 生成自签证书（实验用；生产建议 certbot + Let's Encrypt）
bash deploy/gen-self-signed-cert.sh medshare.local

# 2. 准备生产环境变量
cp deploy/.env.prod.example deploy/.env.prod
# 用编辑器打开 deploy/.env.prod，替换两项：
#   SECRET_KEY=<openssl rand -hex 32 生成>
#   MEDSHARE_FILE_KEY_BASE64=<openssl rand -base64 32 生成>

# 3. 一键拉起：基础 compose + 生产 overlay
docker compose -f docker-compose.yml -f deploy/docker-compose.prod.yml up -d

# 4. 访问入口
# 前端（HTTPS）：https://localhost   → 自签证书浏览器会告警，点"继续"即可
# Prometheus：http://localhost:9090
# Grafana：http://localhost:3001   （admin / changeme，改密）
```

生产 overlay 相比基础栈多出来：

- **Nginx** 反代 + TLS + gzip + 安全头 + WebSocket upgrade
- **gunicorn 4 workers** 跑后端（基础栈是 uvicorn 单进程）
- **资源限制**（CPU/内存）+ 健康检查 + `unless-stopped` 重启策略
- **MySQL 备份容器**（每日 00:00 `mysqldump` + 7 天轮转）
- **Prometheus + Grafana** 预配好数据源和"MedShare Overview"仪表板
- **slowapi 限流**自动开启（login 5/min，register 10/min）

---

## 七、主要功能演示路径

假设已经在档位 B 或 C 下跑起来了。

### 7.1 上传文件病历 + 链上哈希存证（迭代 4）

1. 登录 `hospital_a`
2. "数据上传" → 切换到"文件"模式 → 选一个 PDF → 提交
3. "数据列表" → 该行右侧出现 **"链上哈希 ✓"** 标签；点"下载"后出现 **"文件完整性 ✓"** 双勾

### 7.2 病历修订 + 版本链 + 链上时间线（迭代 2 + 3）

1. 同一份病历点"修订" → 改一处内容 → 提交
2. 列表里版本 Tag 从 v1 → v2
3. 点"版本链" → 抽屉打开，显示**倒序时间线**，每条带 `TxID` / 上一版 TxID / 数据哈希
4. 第二次打开抽屉，右上角出现"缓存：命中"

### 7.3 跨院申请 + 链上 ABAC（迭代 5）

1. `hospital_b` 登录 → "发起访问申请" → 选 HospitalA 的病历 → 填理由
2. `patient1` 登录 → "待审批申请" → 点"同意" → **必填"有效 7 天 / 最大 2 次"** → 批准
3. `hospital_b` → "已授权数据查看" → 点"下载" → 消息提示 `剩余次数：1`
4. 再下载 → `剩余次数：0`
5. **第三次下载 → 红色提示"链码层拒绝（访问次数已用尽）"**
6. `patient1` → "我的授权" → 点"撤销授权" → `hospital_b` 再下载仍 403

### 7.4 WebSocket 实时通知（迭代 6）

1. Tab 1 登录 `patient1`（右上角铃铛显示"在线"）
2. Tab 2 登录 `hospital_a` → 上传一份新病历指向 patient1
3. Tab 1 **秒级收到桌面弹窗**"新病历 | HospitalA 上传了一条新病历"；铃铛未读角标 +1
4. 点铃铛打开通知中心，看到完整事件列表（带 TxID / 时间戳）

### 7.5 非法访问尝试审计（迭代 5 + 6）

撤销后医院再试下载：

- 后端一侧返回 403
- 链码触发 `UnauthorizedAttempt` → `patient1` 和 `admin` 都收到红色推送
- `admin` 打开"审计事件"接口 `/api/audit/events` 可看到持久化记录

### 7.6 富查询（迭代 7）

登录 admin：

```bash
curl -H "Authorization: Bearer <ADMIN_TOKEN>" \
  "http://localhost:8000/api/records/chain/by-hospital?hospital=HospitalA&page_size=50"
```

返回该医院所有最新版病历（走 CouchDB Mango selector + 索引）。

---

## 八、测试矩阵

| 测试种类 | 命令 | 环境 | 用例数 | 时长 |
|---|---|---|---|---|
| 链码单元（Mocha + mock Fabric stub） | `npm test`（在 `fabric-network/chaincode/medshare/javascript/`） | Win / WSL 均可 | **49** | <1s |
| 后端单元 + 集成（pytest + SQLite） | `pytest tests/`（在 `backend/`） | Win / WSL 均可 | **107** | ~100s |
| 全链路冒烟（20 业务点 / 1 用例） | `pytest tests/test_smoke_e2e.py -v` | Win / WSL 均可 | 1 | <4s |
| 运维探针（metrics + 限流） | `pytest tests/test_ops.py -v` | Win / WSL 均可 | 7 | <12s |
| 加密性能（10MB GCM 吞吐） | `pytest tests/test_crypto.py -v -s` | Win / WSL 均可 | 12 | <0.5s |
| Caliper 链压测（TPS/延迟） | `caliper launch manager …` | **需真链，只能 WSL** | 3 场景 | ~3 min |
| Locust API 压测 | `locust -f locustfile.py -u 100 …` | 任一有后端运行的环境 | 100 并发 | 按配置 |
| 安全扫描 | `bash tools/security/run-scans.sh` | WSL 推荐 | 3 项 | 视服务大小 |

---

## 九、可观测性（迭代 8）

### 9.1 Prometheus 指标

- `GET /metrics` 暴露（生产栈 Prometheus 15s 抓一次）
- 关键指标：
  - `medshare_requests_total{method, path, status}` —— QPS
  - `medshare_request_latency_seconds_bucket{method, path}` —— 直方图做 P95
  - `medshare_ws_connections` —— 活跃 WebSocket 数
  - `medshare_audit_events_emitted` —— 累计审计事件

### 9.2 Grafana 仪表板

登录 `http://localhost:3001`（admin / changeme），"MedShare Overview" 仪表板有四个面板：
- 后端 QPS（stat）
- 错误率 5xx（stat）
- 请求延迟 P95（时序）
- WebSocket 连接 / 审计条数（时序）

### 9.3 健康探针

- `GET /health` —— 存活快检
- `GET /health/live` —— K8s liveness
- `GET /health/ready` —— readiness（会跑 `SELECT 1` 确保 DB 可读）

---

## 十、安全

已落地（迭代 1 / 4 / 5 / 8）：

| 层 | 措施 |
|---|---|
| 传输 | HTTPS 强制（HTTP 301 → 443）；HSTS / X-Frame-Options / X-Content-Type-Options / Referrer-Policy |
| 认证 | bcrypt 密码哈希 + JWT；`is_active` 禁用账号；登录自动迁移老明文 |
| 限流 | slowapi 按 IP，login 5/min、register 10/min（test 环境自动关闭） |
| 文件 | AES-256-GCM 加密落盘 + SHA-256 上链双层完整性；主密钥走 ENV |
| 授权 | 链上 ABAC（状态机 + 过期 + 次数 + **MSP 绑定**） |
| 审计 | 所有上链操作 + 失败尝试 都写入 `audit_events` |
| 备份 | MySQL 每日 `mysqldump` + 7 天轮转 |
| 扫描 | `tools/security/run-scans.sh` 一键跑 bandit + npm audit + OWASP ZAP |

---

## 十一、Windows → WSL 常见问题

| 问题 | 症状 | 解决 |
|---|---|---|
| 脚本换行符 | `bash: ./script.sh: /bin/bash^M: bad interpreter` | 在 WSL 下 `dos2unix deploy/*.sh fabric-network/scripts/*.sh` 或项目 clone 到 WSL 侧文件系统 |
| 文件权限 | `Permission denied` | `chmod +x deploy/*.sh fabric-network/scripts/*.sh` |
| 跨文件系统性能 | 在 `/mnt/d/` 下 `docker compose up` 特别慢、watcher 不生效 | 把仓库移到 WSL 原生路径 `~/workspace/blockchain/` |
| Docker 命令在 WSL shell 里找不到 | `docker: command not found` | Docker Desktop → Settings → WSL Integration → 打开 Ubuntu |
| 生产 compose 起不来 | `no such file: ./certs/server.crt` | 先跑 `bash deploy/gen-self-signed-cert.sh` |
| 前端 WebSocket 连不上 | 生产 HTTPS 下浏览器拒绝混合内容 | Nginx 已配置 `location /ws/ { proxy_http_version 1.1; Upgrade/Connection }`，前端 `VITE_WS_BASE_URL` 要设为 `wss://...` |
| Fabric bootstrap 被中断 | 再启动 chaincode 不可用 | `bash fabric-network/scripts/teardown.sh` 然后 `docker compose --profile fabric-init up --build fabric-bootstrap` 重来 |
| 老 DB 升级失败 | 新字段 `msp_org / version / expires_at / ...` 缺失 | 按每个 iteration-0X.md "升级 SQL 提示" 手动 `ALTER TABLE`；或直接 `docker compose down -v` 清卷重建（开发环境） |
| 中文输出乱码 | MySQL / FastAPI 返回 `???` | 容器环境默认 utf8mb4；首次建库用 `init.sql` 的 `SET NAMES utf8mb4`，若乱码重建 mysql 容器 `docker compose up -d --build mysql backend` |

---

## 十二、文档索引

| 文档 | 内容 |
|---|---|
| [项目迭代计划（8次）.md](项目迭代计划（8次）.md) | 顶层路线图（迭代 1-8 总览） |
| [docs/iteration-01.md](docs/iteration-01.md) | 工程基石：pytest + mocha + bcrypt |
| [docs/iteration-02.md](docs/iteration-02.md) | 病历版本链 |
| [docs/iteration-03.md](docs/iteration-03.md) | `GetHistoryForKey` + 缓存 |
| [docs/iteration-04.md](docs/iteration-04.md) | AES-256-GCM 文件加密 |
| [docs/iteration-05.md](docs/iteration-05.md) | 链上 ABAC |
| [docs/iteration-06.md](docs/iteration-06.md) | 链码事件 + WebSocket |
| [docs/iteration-07.md](docs/iteration-07.md) | CouchDB 富查询 |
| [docs/iteration-08.md](docs/iteration-08.md) | 生产化部署 + Caliper |
| [README.md](README.md) | 基础 compose 的"开发/演示"快速指引 |

---

## 十三、未来改进（不属于本次 8 次迭代，但合理的下一步）

1. **patient 专属 MSP** —— 当前链上 ABAC 的"归属患者"靠参数 `patientId` 比对；真正密码学级别需要给患者独立 MSP。这涉及 Fabric CA 配置。
2. **CouchDB 索引对比实测** —— 迭代 7 的"有索引 < 无索引 × 0.3"只能在真链上跑；可在迭代 8 Caliper 配置里加两组对比任务。
3. **事件流幂等** —— 迭代 6 gateway listener offset 持久化已就绪，但后端没做 `(tx_id, event_type)` 唯一索引；真链重启后会产生重复条目。一行 SQL 即可加。
4. **K8s Helm chart** —— 当前生产栈是 docker compose；K8s 下可以进一步做水平扩展 / 滚动更新 / secrets / 多副本 Backend。
5. **前端国际化** —— 当前纯中文 UI；若要对外演示可加 i18n。

---

## 十四、最终状态

- ✅ 链码 **49/49** Mocha 用例全绿（Windows 本地 `npm test`）
- ✅ 后端 **107/107** pytest 用例全绿（Windows 本地 `pytest`）
- ✅ 链码实现 Fabric 2.x 的 setEvent / getHistoryForKey / getQueryResultWithPagination 三大高级 API
- ✅ 生产栈一条命令启动：`docker compose -f docker-compose.yml -f deploy/docker-compose.prod.yml up -d`
- ✅ 八份迭代报告在 `docs/iteration-0{1..8}.md` 就位
- ✅ Caliper / Locust / 安全扫描 / 备份 / 监控配置全部就绪

**项目可作为区块链技术课八次实验的完整教学材料与答辩素材。**
