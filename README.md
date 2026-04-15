# 基于 Vue3 + FastAPI + MySQL + Hyperledger Fabric + Docker 的医疗数据共享系统

## 1. 项目简介
这是一个“小型课程设计”项目，实现医疗数据共享核心闭环：

1. 医院上传病历，正文存 MySQL，摘要上 Fabric。  
2. 其他医院发起访问申请，申请信息上链。  
3. 患者审批（同意/拒绝），审批结果同步上链。  
4. 医院查看已授权数据。  
5. 管理员查看审计记录（上链 TxID、状态、时间）。  

项目目标是“能跑通、能演示、结构清晰”，不引入复杂企业级中间件。

## 2. 系统架构
- 前端：Vue3 + Vite + Element Plus
- 后端：FastAPI + SQLAlchemy + PyMySQL
- 数据库：MySQL
- 链上：Hyperledger Fabric（基于 `fabric-samples/test-network`）
- 链接链：Node.js Gateway（REST -> Fabric chaincode）
- 部署：Docker Compose

数据原则：
- 链下（MySQL）存病历正文和申请详情。
- 链上（Fabric）存哈希、申请状态、审批状态、交易信息。

## 3. 角色说明
- `hospital`（医院）：上传病历、发起申请、查看授权数据
- `patient`（患者）：查看个人病历、审批访问申请
- `admin`（管理员）：查看区块链审计记录

## 4. 项目目录结构
```text
design
├─ backend
│  ├─ app
│  │  ├─ auth.py
│  │  ├─ config.py
│  │  ├─ database.py
│  │  ├─ gateway.py
│  │  ├─ main.py
│  │  ├─ models.py
│  │  └─ schemas.py
│  ├─ sql
│  │  └─ init.sql
│  ├─ Dockerfile
│  └─ requirements.txt
├─ gateway
│  ├─ src
│  │  └─ app.js
│  ├─ Dockerfile
│  └─ package.json
├─ fabric-network
│  ├─ chaincode
│  │  └─ medshare
│  │     └─ javascript
│  │        ├─ lib
│  │        │  └─ medshare-contract.js
│  │        ├─ index.js
│  │        └─ package.json
│  └─ scripts
│     ├─ bootstrap.sh
│     └─ teardown.sh
├─ frontend
│  ├─ src
│  │  ├─ api/http.js
│  │  ├─ components/AppLayout.vue
│  │  ├─ router/index.js
│  │  ├─ views
│  │  │  ├─ admin/AuditView.vue
│  │  │  ├─ hospital/AuthorizedView.vue
│  │  │  ├─ hospital/RecordListView.vue
│  │  │  ├─ hospital/RequestView.vue
│  │  │  ├─ hospital/UploadView.vue
│  │  │  ├─ patient/MyRecordsView.vue
│  │  │  └─ patient/PendingApprovalsView.vue
│  │  ├─ App.vue
│  │  ├─ main.js
│  │  └─ styles.css
│  ├─ Dockerfile
│  ├─ nginx.conf
│  ├─ package.json
│  └─ vite.config.js
├─ docker-compose.yml
└─ README.md
```

## 5. 数据库表结构与初始化
完整 SQL 在 `backend/sql/init.sql`，包含：
- `users`
- `medical_records`
- `access_requests`
- 初始化账号与示例数据

初始化账号：
- 管理员：`admin / 123456`
- 患者：`patient1 / 123456`、`patient2 / 123456`
- 医院：`hospital_a / 123456`、`hospital_b / 123456`

初始化演示数据：
- `hospital_a` 已上传 `patient1` 一条病历
- `hospital_b` 可对该病历发起访问申请
- `patient1` 可审批
- `admin` 可审计

## 6. 后端接口文档（FastAPI）
接口前缀：`/api`

1. `POST /auth/login`
- 请求：`{ "username": "...", "password": "..." }`
- 返回：`{ "token": "...", "user": { ... } }`

2. `GET /users/patients`（医院）
- 返回患者列表

3. `GET /records`
- 医院：查看记录列表（未授权内容脱敏）
- 患者：查看本人记录
- 管理员：仅用于审计展示（正文脱敏）

4. `POST /records`（医院）
- 请求：`{ "patient_id": 2, "title": "...", "diagnosis": "...", "content": "..." }`
- 行为：写入 MySQL + `CreateMedicalRecordEvidence` 上链

5. `POST /access-requests`（医院）
- 请求：`{ "record_id": 1, "reason": "..." }`
- 行为：写入 MySQL + `CreateAccessRequest` 上链

6. `GET /access-requests/pending`（患者）
- 查询待审批申请

7. `POST /access-requests/{id}/review`（患者）
- 请求：`{ "decision": "APPROVED" | "REJECTED" }`
- 行为：更新 MySQL + 上链审批结果

8. `GET /authorized-records`（医院）
- 查看已授权数据（含正文）

9. `GET /audit`（管理员）
- 审计记录（上传上链、申请上链、审批上链）

10. `GET /access-requests/{id}/chain`
- 查询该申请链上状态

## 7. Fabric 链码功能
链码文件：`fabric-network/chaincode/medshare/javascript/lib/medshare-contract.js`

实现方法：
1. `CreateMedicalRecordEvidence`
2. `GetMedicalRecordEvidence`
3. `CreateAccessRequest`
4. `ApproveAccessRequest`
5. `RejectAccessRequest`
6. `QueryAccessRequest`

## 8. Gateway 服务接口
Gateway 地址：`http://localhost:3000/api`

1. `POST /records/evidence`
2. `GET /records/evidence/:recordId`
3. `POST /access-requests`
4. `POST /access-requests/:requestId/approve`
5. `POST /access-requests/:requestId/reject`
6. `GET /access-requests/:requestId`

## 9. 前端页面
1. 登录页：`/login`
2. 医院端：
- `/hospital/records` 数据列表
- `/hospital/upload` 数据上传
- `/hospital/requests` 发起访问申请
- `/hospital/authorized` 已授权数据查看
3. 患者端：
- `/patient/records` 我的医疗数据
- `/patient/reviews` 待审批申请
4. 管理员端：
- `/admin/audit` 区块链审计页

## 10. Docker 一键启动
在项目根目录执行：

```bash
docker compose up --build
```

说明：
1. `fabric-bootstrap` 会自动拉起 `fabric-samples/test-network` 并部署链码 `medshare`。  
2. `mysql` 自动执行 `backend/sql/init.sql`。  
3. 前后端与 Gateway 容器自动启动。  

访问地址：
- 前端：`http://localhost:5173`
- 后端 Swagger：`http://localhost:8000/docs`
- Gateway 健康检查：`http://localhost:3000/health`

停止并清理：
```bash
docker compose down
bash fabric-network/scripts/teardown.sh
```

## 11. 演示流程（答辩建议）
1. 管理员登录查看审计页（初始数据）。  
2. `hospital_b` 登录，在“发起访问申请”页对记录发起申请。  
3. `patient1` 登录，在“待审批申请”页点击同意。  
4. 再次 `hospital_b` 登录，在“已授权数据查看”页查看病历正文。  
5. 管理员刷新审计页，展示新产生的 `txId` 与状态。  

## 12. 注意事项
1. 这是课程设计最小可行实现，鉴权和密码策略做了简化。  
2. 若 Docker 环境无法挂载 `/var/run/docker.sock`，请先手动执行 Fabric 启动脚本：  
   `bash fabric-network/scripts/bootstrap.sh`  
   然后再 `docker compose up --build`。  
3. Fabric 初次启动会下载镜像与二进制，耗时较长是正常现象。  
