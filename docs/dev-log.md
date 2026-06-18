# 开发日志

## 2026-06-03

修复前端“加载数据失败”：

- 现象是前端病历列表加载失败，后端 `/api/records` 实际返回 500。
- 原因不是前端，也不是 Fabric/WSL 部署差异，而是 MySQL 旧数据卷还停留在早期表结构，`medical_records` 缺少 `anchor_batch_id`、`frozen`、`category` 等后续迭代字段，`access_requests` 缺少 `purpose`。
- 已对运行中的 `medshare` 库做非破坏性 `ALTER TABLE`，保留已有数据。
- 已同步补齐 `backend/sql/init.sql`，新环境初始化时会直接带上迭代 9、11、12 所需字段，以及 `governance_actions`、`merkle_anchor_batches` 表。

验证：
- `GET /api/records` 使用 hospital_a token 返回 200。
- `GET /api/credits/balance` 使用 hospital_a token 返回 200。
- `pytest backend/tests/test_anchor.py backend/tests/test_freeze.py backend/tests/test_v2_upgrade.py -q` 通过。

补齐 9-13 次升级里没有落到前端的入口：

- 全局右上角增加共享积分入口，余额、流水、转账都走 `/api/credits/*`，数据来自链码经 Gateway 返回的结果。
- 管理员审计页增加批量锚定、治理动作、链码 v2 信息和按分类链上查询入口。
- 患者病历列表增加紧急冻结按钮，调用 `/api/records/{id}/freeze`。
- 病历列表增加 Merkle 包含证明查看和链上验证。
- 上传病历增加 category，访问申请增加 purpose，下拉枚举来自 `/api/system/info`。
- 文件上传接口补了 category 透传，避免文件病历仍落成默认分类。

验证：

- `npm run build` 通过。

修复本机 Docker 启动问题：

- MySQL 宿主机端口从 `3306` 改为 `3307`，避免和本机已有 `mysqld` 冲突；容器内部仍然使用 `mysql:3306`。
- gateway 显式配置 Org1/Org2 admin 证书路径，匹配 Fabric 实际生成的 `Admin@org*.example.com-cert.pem`。
- 当前 Windows 项目目录缺少 Fabric runtime，已从 WSL 中同步 `organizations` 到 `fabric-network/runtime/...`，让 gateway 能读取 connection profile 和证书。

验证：

- `docker compose ps` 显示 mysql healthy、gateway healthy、backend/frontend running。
- `http://localhost:3000/ready` 返回 ready。
- `http://localhost:8000/health` 返回 ok。
- `http://localhost:5173` 返回 200。

修复链码版本不一致：

- 前端全局积分调用 `/api/credits/balance` 时，gateway 返回 `CreditBalance` 不存在。原因是业务容器连接到旧 Fabric 网络，peer 上部署的 `medshare` chaincode 还不是迭代 13 版本。
- 将 `fabric-bootstrap` 的 `FABRIC_DOCKER_HOST_ROOT` 改为当前项目路径 `/host_mnt/d/vsCode/python/blockchain`。
- bootstrap 启动 test-network 时增加 `-s couchdb`，保证第 7 次迭代富查询环境使用 CouchDB。
- 清理残缺的 `fabric-network/runtime/fabric-samples` 后重新执行 `docker compose --profile fabric-init up --build fabric-bootstrap`，完成当前链码重新部署。
- 新网络生成的 admin 证书名为 `cert.pem`，移除之前临时写死的 `Admin@org*.example.com-cert.pem` 环境变量。

验证：

- gateway `/ready` 返回 ready。
- gateway `/api/credits/HospitalA/balance` 返回 200。
- 后端 `/api/credits/balance` 通过 hospital_a token 返回 `{ user_id: "HospitalA", balance: 0 }`。
- `pytest tests/test_governance.py tests/test_freeze.py tests/test_v2_upgrade.py tests/test_anchor.py tests/test_credits.py -v` 通过。
- `pytest tests/test_credits.py tests/test_files.py -v` 通过。
- 链码 `npm test` 通过，85 条。

已知问题：

- Playwright 浏览器包下载中断，没完成浏览器截图验证。当前只做到了前端生产构建验证。
- `npm install` 后 audit 提示 2 个 moderate 漏洞，暂未使用 `npm audit fix --force`，避免破坏依赖版本。

补第 7 次迭代的链上富查询前端入口：

- 医院病历列表增加“链上本院数据”，调用 `/api/records/chain/by-hospital`。
- 管理员审计页增加“链上检索”页签，支持按医院和按时间范围查链上最新版病历。
- 患者待审批页增加“链上待审批”，调用 `/api/access-requests/chain/pending`。

验证：

- `npm run build` 通过。
## 2026-06-10

修复本机 Compose 启动失败：

- 现象：Docker Compose 启动时报 `dependency gateway failed to start`，`medshare-gateway` 退出。
- 实际原因不是 gateway 代码崩溃，而是 Fabric peer 在调用链码时找不到 CCAAS 容器：`peer0org1_medshare_ccaas` / `peer0org2_medshare_ccaas` 不存在。
- `fabric-bootstrap` 最近一次没有完整跑完，日志里有 Docker Hub DNS 解析失败；peer/orderer 仍在，但链码服务容器丢了。
- 未重建 Fabric 网络，避免清空链上数据；直接按当前 package id 补启动两个链码服务容器。

关键命令：

```powershell
docker run --rm -d --name peer0org1_medshare_ccaas --network fabric_test `
  -e CHAINCODE_SERVER_ADDRESS=0.0.0.0:9999 `
  -e CHAINCODE_ID=medshare_1.0:2a38d77afcdd1bf8e5ec60967045741a392bd97b0c951d09ea83e32a67f2814f `
  -e CORE_CHAINCODE_ID_NAME=medshare_1.0:2a38d77afcdd1bf8e5ec60967045741a392bd97b0c951d09ea83e32a67f2814f `
  medshare_ccaas_image:latest

docker run --rm -d --name peer0org2_medshare_ccaas --network fabric_test `
  -e CHAINCODE_SERVER_ADDRESS=0.0.0.0:9999 `
  -e CHAINCODE_ID=medshare_1.0:2a38d77afcdd1bf8e5ec60967045741a392bd97b0c951d09ea83e32a67f2814f `
  -e CORE_CHAINCODE_ID_NAME=medshare_1.0:2a38d77afcdd1bf8e5ec60967045741a392bd97b0c951d09ea83e32a67f2814f `
  medshare_ccaas_image:latest
```

验证：

- `http://127.0.0.1:3000/ready` 返回 ready，Org1/Org2 都 ready。
- `medshare-gateway` 和 `medshare-mysql` 已 healthy。
- 已重新启动 `backend` 和 `frontend`。
