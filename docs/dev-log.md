# 开发日志

## 2026-06-03

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
