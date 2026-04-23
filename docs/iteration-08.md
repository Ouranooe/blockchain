# 迭代 8 完成报告（收官）：性能压测 + 生产化部署

> 对应 [项目迭代计划（8次）.md](../项目迭代计划（8次）.md) 第 8 次迭代

## 一、本次迭代目标

给前 7 次迭代做收尾：把 demo 打磨到**可以放公网运行**。三条主线：

1. **性能量化** —— Hyperledger Caliper 三场景 + Locust 100 并发 API 压测
2. **生产化部署** —— Nginx 反代 + HTTPS + `docker-compose.prod.yml` + MySQL 每日备份
3. **可观测 + 可守护** —— Prometheus `/metrics` + Grafana 仪表板 + slowapi 限流 + liveness/readiness 探针 + bandit/npm-audit/OWASP ZAP 安全扫描

同时交付一份**全链路冒烟测试**：20 条业务流在 1 个 pytest 用例里串完，任何纵向断裂都暴露。

## 二、改动清单

### 2.1 后端（FastAPI）—— 可观测 + 限流 + 健康探针

| 文件 | 改动 |
|------|------|
| [backend/requirements.txt](../backend/requirements.txt) | 新增 `prometheus-client==0.21.0`、`slowapi==0.1.9` |
| [backend/app/config.py](../backend/app/config.py) | 新增 `RATE_LIMIT_ENABLED/LOGIN/REGISTER`（test 环境默认关） |
| [backend/app/metrics.py](../backend/app/metrics.py) | **新文件** —— 自定义 `CollectorRegistry` + 5 个指标（`requests_total` 计数、`request_latency_seconds` 直方图、`chain_calls_total`、`ws_connections`、`audit_events_emitted`）+ `/metrics` endpoint + 请求埋点中间件（路径归一化避免 label 爆炸） |
| [backend/app/main.py](../backend/app/main.py) | 启用 `install_metrics(app)` + `Limiter(enabled=settings.RATE_LIMIT_ENABLED)` + 429 统一异常处理；`/auth/login` `@limiter.limit("5/minute")`、`/auth/register` `@limiter.limit("10/minute")`；新增 `/health/live`、`/health/ready`（后者跑 `SELECT 1`） |

### 2.2 部署文件（都在 [deploy/](../deploy/)）

| 文件 | 作用 |
|------|------|
| [docker-compose.prod.yml](../deploy/docker-compose.prod.yml) | 在基础 compose 之上叠加：Nginx、资源限制（CPU/内存）、健康检查、gunicorn 4 workers、MySQL 备份容器、Prometheus、Grafana |
| [nginx.conf](../deploy/nginx.conf) | HTTP → HTTPS 301；TLS1.2/1.3；gzip；安全头（HSTS/X-Frame-Options/X-Content-Type-Options/Referrer-Policy）；WebSocket upgrade；静态资源 7 天 `Cache-Control: public,immutable` |
| [gen-self-signed-cert.sh](../deploy/gen-self-signed-cert.sh) | 自签证书（开发/实验）；注释引导生产用 certbot + Let's Encrypt |
| [mysql-backup.sh](../deploy/mysql-backup.sh) | 每日 `mysqldump --single-transaction` + gzip；`RETENTION_DAYS=7` 自动清理 |
| [prometheus.yml](../deploy/prometheus.yml) | 3 个 scrape job（backend/gateway/self），15s interval |
| [grafana/provisioning/datasources/prometheus.yaml](../deploy/grafana/provisioning/datasources/prometheus.yaml) | 自动注入 Prometheus 数据源 |
| [grafana/provisioning/dashboards/medshare-dashboard.json](../deploy/grafana/provisioning/dashboards/medshare-dashboard.json) | 4 个面板：QPS / 错误率 / P95 / WS 连接 + 审计条数 |
| [.env.prod.example](../deploy/.env.prod.example) | 生产 ENV 模板（SECRET_KEY / MEDSHARE_FILE_KEY_BASE64 / 限流参数） |

### 2.3 Caliper 压测（[caliper/](../caliper/)）

| 文件 | 作用 |
|------|------|
| [networks/medshare-network.yaml](../caliper/networks/medshare-network.yaml) | Fabric 2.2 SUT binding + 连接配置指向 test-network 证书 |
| [benchmarks/medshare-benchmark.yaml](../caliper/benchmarks/medshare-benchmark.yaml) | 3 轮：`query-records`（60s/50 并发）、`create-records`（500 笔/30 TPS）、`mixed-rw`（90s/80% 读） |
| [workload/create-records.js](../caliper/workload/create-records.js) | `CreateMedicalRecordEvidence` 批量上链（每 worker 独立命名空间） |
| [workload/query-records.js](../caliper/workload/query-records.js) | 种子 + `GetRecordLatest` 随机读 |
| [workload/mixed-rw.js](../caliper/workload/mixed-rw.js) | 可配置 `readRatio` 的混合 |
| [README.md](../caliper/README.md) | 运行步骤、验证目标、索引对比指引 |

### 2.4 Locust API 压测（[tools/locust/](../tools/locust/))

[locustfile.py](../tools/locust/locustfile.py) 定义两类并发用户：
- `MedShareUser`（患者视角）：`GET /patient/records`、`GET /access-requests/chain/pending`、`GET /auth/me`、`GET /audit/events`
- `HospitalUser`（医院视角，占比 1:3）：`GET /records`、`GET /records/chain/by-hospital`、偶尔 `POST /records`

### 2.5 安全扫描（[tools/security/run-scans.sh](../tools/security/run-scans.sh)）

一键跑三项：`bandit -r backend/app`（Python 静态）、`npm audit --json`（gateway/frontend/chaincode 三个项目）、OWASP ZAP `zap-baseline.py`（需要正在运行的 HTTPS 服务）。报告落到 `tools/security/reports/`。

### 2.6 测试新增

| 文件 | 用例数 | 说明 |
|------|-------|------|
| [backend/tests/test_ops.py](../backend/tests/test_ops.py) | **7** | `/health` / `/health/live` / `/health/ready` / `/metrics` Prometheus 格式 / 登录指标计数 / 限流默认关 / 限流开启后 429 |
| [backend/tests/test_smoke_e2e.py](../backend/tests/test_smoke_e2e.py) | **1 用例 / 20 业务点** | 注册→登录→改密→文本上传→修订→链上历史→缓存命中→文件上传→完整性校验→申请→审批→下载扣减→Range→次数耗尽→**MSP 冒用被链码拒**→撤销→审计矩阵→**WebSocket 实时通知**→富查询→运维探针与 metrics |
| [backend/tests/conftest.py](../backend/tests/conftest.py) | — | 新增 `client_with_limiter` fixture：临时开启 limiter 并清零计数，用于 429 断言 |

## 三、验证结果

### 3.1 链码测试

```
49 passing
```

（迭代 7 收尾状态保持，本迭代未对链码做功能改动）

### 3.2 后端测试（累计）

```
$ pytest tests/ -v
tests/test_auth.py        16 passed
tests/test_records.py     12 passed
tests/test_history.py     10 passed
tests/test_crypto.py      12 passed
tests/test_files.py       16 passed
tests/test_abac.py        13 passed
tests/test_events.py       9 passed
tests/test_rich_query.py  11 passed
tests/test_ops.py          7 passed   ← 本次新增
tests/test_smoke_e2e.py    1 passed   ← 本次新增（20 业务点）

107 passed in 99.24s
```

**后端累计 107/107。**

### 3.3 全链路冒烟测试亮点

一个 `test_e2e_full_user_journey` 依次串起这 20 个检查点：

| # | 业务点 | 对应迭代 |
|---|---|---|
| 1 | `POST /auth/register` 新患者 | 1 |
| 2 | `POST /auth/login` + `GET /auth/me` | 1 |
| 3 | `POST /auth/change-password` → 新密码登录 | 1 |
| 4 | `POST /records` 文本上传 | 1 |
| 5 | `POST /records/{id}/revise` + 版本链 `previous_tx_id` 链接 | 2 |
| 6 | `GET /records/{id}/chain-history` 倒序 2 条 | 3 |
| 7 | 二次查询 `cache == "hit"` | 3 |
| 8 | `POST /records/upload` 文件上传（AES-GCM 加密） | 4 |
| 9 | `GET /records/{id}/verify` 双层哈希校验通过 | 4 |
| 10 | `POST /access-requests` | 2/5 |
| 11 | `review decision=APPROVED` 必填 `duration_days + max_reads` | 5 |
| 12 | `GET /records/{id}/download` 首次下载：`X-Remaining-Reads: 1` | 5 |
| 13 | `Range: bytes=0-99` 返回 206 + 再消费一次 | 4/5 |
| 14 | 第 3 次下载 → 403（次数耗尽） | 5 |
| 15 | **直接调 gateway 传 HospitalA 冒用 Org2 授权 → 链码 MSP 守卫抛错** | 5 |
| 16 | 新建第二条授权 → 患者撤销 → 再下载 403 | 5 |
| 17 | `/audit/events` 出现全部 7 类事件 | 6 |
| 18 | WebSocket `/ws/notifications` 实时收到 `RecordCreated` 推送 | 6 |
| 19 | `/records/chain/by-hospital` 富查询只返 HospitalA | 7 |
| 20 | `/health/live`、`/health/ready`、`/metrics` Prometheus 格式 | 8 |

单次运行 **<4 秒**。

## 四、量化指标

### 4.1 本迭代自动化测试可验证的部分

| 指标 | 目标 | 实测 | 结论 |
|------|------|------|------|
| `/metrics` Prometheus 格式有效 | — | 通过 | ✓ |
| 限流生效 | 超阈返回 429 | `test_rate_limiter_blocks_when_enabled` ✓ | ✓ |
| Liveness/Readiness | 200 + DB SELECT 1 | ✓ | ✓ |
| 全链路冒烟 20 点通过 | 全绿 | 1 用例通吃 | ✓ |

### 4.2 需真链 / 真环境验证的部分

| 指标 | 目标 | 本迭代交付 | 验证方式 |
|------|------|------|----------|
| Caliper 场景 2（query）TPS | ≥ 200 | 完整 workload + network + benchmark 配置 | `caliper launch manager …` |
| Caliper 场景 1（create）TPS | ≥ 30 | 同上 | 同上 |
| API P95 (100 并发) | < 500 ms | `locustfile.py` + 运行指引 | `locust -u 100 -r 20 -t 2m` |
| 安全扫描 0 高危 | — | `run-scans.sh` 一键跑 bandit/npm audit/ZAP | `bash tools/security/run-scans.sh` |
| 有索引查询 < 无索引 × 0.3（迭代 7 遗留） | — | Caliper 配置里留了对比方案（去掉 `use_index` 再跑） | 两轮 Caliper 对比 |

## 五、核心设计决策

### 5.1 限流只在生产开启

slowapi 按 IP 限流（`get_remote_address`）。测试环境 TestClient 都从同一 "testclient" IP 出发，开启限流会瞬间触发 429 污染所有用例。解决：配置化 `RATE_LIMIT_ENABLED=0` 在 `ENVIRONMENT=test` 时默认关，生产 `.env.prod` 开启。

### 5.2 Prometheus 指标的路径归一化

直接用 `request.url.path` 作为 label 会因为 `/records/1/revise`、`/records/2/revise` 等产生无限多 label → 高基数爆炸。解决：

```python
route = request.scope.get("route")
return route.path if route else fallback_with_digit_stripping
```

优先用 FastAPI 路由模板 `/records/{record_id}/revise`，模板匹配不上再按数字段降级归一。

### 5.3 全链路测试 vs 分模块测试

分模块测试（test_auth/test_abac/…）验证**正确性**；全链路测试验证**接缝**。迭代 1-7 累计 99 条用例各自覆盖一个接口，但"登录→上传→申请→审批→下载→撤销→再下载 403"这条**垂直链路**首次被一根测试串起。任何接口单测通过但链路断裂（比如权限检查和事件广播打架）都会被这个测试暴露。

### 5.4 生产部署为何分成 `docker-compose.yml` + `docker-compose.prod.yml`

基础 compose 保持"一键跑 demo"能力（开发者 / 答辩演示用）；prod overlay 叠加资源限制、Nginx、备份容器、监控栈。合并命令：

```sh
docker compose -f docker-compose.yml -f deploy/docker-compose.prod.yml up -d
```

这样开发环境不必每次启动一堆 Prometheus/Grafana/Nginx，生产可以启用。

### 5.5 "生产化"的底线：5 条安全默认

- HTTPS 强制（HTTP 301 → 443）
- HSTS + X-Frame-Options + X-Content-Type-Options + Referrer-Policy
- 敏感接口限流（login 5/min、register 10/min）
- 密钥全部走 ENV（`.env.prod` 模板就位）
- MySQL 每日备份 + 7 天轮转

这 5 条在 `deploy/` 目录里是完整落地的，不是"后续可加"的 TODO。

## 六、项目全景（迭代 1-8 累计）

| 迭代 | 链码测试 | 后端测试 | 关键交付 |
|-----|---------|---------|---------|
| 1 | 15 | 16 | 工程基石：pytest + mocha + bcrypt + 环境变量化 |
| 2 | 20 (+5) | 28 (+12) | 版本链：RECORD_LATEST_{id} 热点索引 + previous_tx_id |
| 3 | 26 (+6) | 38 (+10) | Fabric `GetHistoryForKey` + 30s 网关缓存 |
| 4 | 26 | 66 (+28) | AES-256-GCM 文件加密 + SHA-256 上链 |
| 5 | 40 (+14) | 79 (+13) | ABAC：链上过期 / 次数 / MSP 守卫 |
| 6 | 43 (+3) | 88 (+9) | 链码事件 + WebSocket 实时通知 |
| 7 | 49 (+6) | 99 (+11) | CouchDB 富查询 + bookmark 分页 |
| 8 | 49 | **107 (+8)** | 监控 + 限流 + 生产部署 + Caliper + 全链路冒烟 |

**最终状态**：**链码 49 / 后端 107 全绿**。

## 七、如何复核本次迭代

```bash
# 1. 全量回归（107 条后端 + 49 条链码）
cd backend && pytest tests/ -v
cd ../fabric-network/chaincode/medshare/javascript && npm test

# 2. 运维与限流专项
cd backend && pytest tests/test_ops.py -v

# 3. 全链路冒烟（20 业务点 / 1 用例）
pytest tests/test_smoke_e2e.py -v

# 4. 生成证书 + 一键起生产栈（需 Linux/WSL + Docker）
bash deploy/gen-self-signed-cert.sh medshare.local
cp deploy/.env.prod.example deploy/.env.prod
vi deploy/.env.prod   # 替换 SECRET_KEY / MEDSHARE_FILE_KEY_BASE64
docker compose -f docker-compose.yml -f deploy/docker-compose.prod.yml up -d
# 访问：https://localhost（自签证书会提示"不安全"，实验环境接受即可）
# 监控：http://localhost:9090（Prometheus）/ http://localhost:3001（Grafana, admin/changeme）

# 5. Caliper 压测（需真实 Fabric + Caliper CLI）
npm install -g @hyperledger/caliper-cli@0.5
caliper bind --caliper-bind-sut fabric:2.2
caliper launch manager \
  --caliper-workspace . \
  --caliper-benchconfig caliper/benchmarks/medshare-benchmark.yaml \
  --caliper-networkconfig caliper/networks/medshare-network.yaml

# 6. Locust 压测（接真实后端）
pip install locust==2.31.5
cd tools/locust
locust -f locustfile.py --host http://localhost:8000 -u 100 -r 20 -t 2m --headless --csv report

# 7. 安全扫描
pip install bandit==1.7.10
bash tools/security/run-scans.sh
```

## 八、收官 —— 八次迭代都解决了什么问题

| 课程所期望的"区块链能力" | 在 MedShare 里是怎么做的 |
|-----|---|
| 链码开发 | 迭代 1 建脚手架；迭代 2-7 持续扩展到 14 个方法 |
| 交易与 txId 溯源 | 迭代 2 版本链每版绑定 txId + `previous_tx_id` |
| 世界状态（World State） | 迭代 2 `LATEST` 热点索引 + 版本键设计 |
| 历史溯源（History） | 迭代 3 `GetHistoryForKey` 一次拉全 |
| 资产/证据上链 | 迭代 4 文件 SHA-256 上链 + GCM 链下加密 |
| ABAC / 链上权限 | 迭代 5 状态机守卫 + MSP 绑定 + 过期/次数/撤销 |
| 事件机制（Chaincode Events） | 迭代 6 `setEvent` + gateway listener + WebSocket |
| 富查询 + 索引（CouchDB） | 迭代 7 Mango selector + `META-INF/statedb/couchdb/indexes` |
| TPS / 性能量化 | 迭代 8 Caliper 三场景 + Locust 100 并发 |
| 生产化（TLS/限流/备份/监控） | 迭代 8 Nginx+HTTPS + slowapi + mysql-backup + Prometheus/Grafana |

每次迭代都产出**代码 + 测试 + 实验报告**；累计 8 份 md 文档在 `docs/iteration-0{1..8}.md` 下，可以直接作为 8 次实验报告的基础。
