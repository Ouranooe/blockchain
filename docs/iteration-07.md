# 迭代 7 完成报告：CouchDB 富查询 + 链上条件检索

> 对应 [项目迭代计划（8次）.md](../项目迭代计划（8次）.md) 第 7 次迭代

## 一、本次迭代目标

把 Fabric peer 的世界状态库从默认的 **LevelDB** 换成 **CouchDB**，启用 Mango 富查询；链码新增 3 个按条件检索的方法，配合 `META-INF/statedb/couchdb/indexes/` 下的索引文件。让"按医院 / 按时间段 / 按患者的 PENDING 申请"这类业务查询能**直接走链**，不再依赖 MySQL 镜像。

量化目标（计划定义）：
- 1000 条记录下，**有索引查询 < 无索引查询 × 0.3**
- 分页遍历 1000 条 **无丢失 / 无重复**

## 二、改动清单

### 2.1 链码（Node.js / Fabric Contract）

[fabric-network/chaincode/medshare/javascript/lib/medshare-contract.js](../fabric-network/chaincode/medshare/javascript/lib/medshare-contract.js)：

**① 给 LATEST 键的写入加 `isLatest: true` 标志**：

```js
await this._putStateAsObject(ctx, this._versionKey(recordId, 1), evidence);
// 新增：LATEST 上带 isLatest 标志
await this._putStateAsObject(ctx, latestKey, { ...evidence, isLatest: true });
```

为什么需要？—— 版本化键 `RECORD_{id}_v{n}` 和 LATEST 键 `RECORD_LATEST_{id}` 存的都是同一 `docType: "RecordEvidence"` 结构。富查询要只命中 LATEST，必须用额外字段过滤。

**② 3 个富查询方法**，都使用 `getQueryResultWithPagination` + `use_index`：

```js
async QueryRecordsByHospital(ctx, uploaderHospital, pageSize, bookmark) {
  // selector: { docType, isLatest: true, uploaderHospital }
  // use_index: ["_design/indexUploaderHospitalDoc", "indexUploaderHospital"]
}

async QueryRecordsByDateRange(ctx, fromIso, toIso, pageSize, bookmark) {
  // selector: { docType, isLatest: true, createdAt: { $gte, $lte } }
  // sort: [{ createdAt: "asc" }]
}

async QueryPendingRequestsForPatient(ctx, patientId, pageSize, bookmark) {
  // selector: { docType: "AccessRequest", patientId, status: "PENDING" }
}
```

**③ 通用辅助 `_richQueryPaged`**：统一封装查询 → 迭代器 → 解析 → 分页 metadata（bookmark）。

### 2.2 CouchDB 索引文件

新建 3 个 JSON 索引定义，位于 [chaincode/medshare/javascript/META-INF/statedb/couchdb/indexes/](../fabric-network/chaincode/medshare/javascript/META-INF/statedb/couchdb/indexes/)：

| 文件 | 覆盖字段 | 对应查询 |
|------|---------|---------|
| `indexUploaderHospital.json` | `docType`, `isLatest`, `uploaderHospital` | 按医院查病历 |
| `indexCreatedAt.json` | `docType`, `isLatest`, `createdAt` | 按时间段查病历 |
| `indexPatientPending.json` | `docType`, `patientId`, `status` | 按患者查 PENDING 申请 |

这些文件会在 chaincode 部署时被 Fabric 自动读取并**建入 CouchDB**。

### 2.3 Fabric 网络启动切到 CouchDB

[fabric-network/scripts/bootstrap.sh](../fabric-network/scripts/bootstrap.sh)：

```sh
# 迭代 7：-s couchdb 让 peer 世界状态库使用 CouchDB
./network.sh up createChannel -ca -c "${CHANNEL_NAME}" -s couchdb
```

### 2.4 Gateway（Node/Express）

[gateway/src/app.js](../gateway/src/app.js) 新增 3 个查询端点，沿用迭代 3 的 TTL 缓存套路（richCache，30s）：

```
GET /api/records/query/by-hospital?uploaderHospital=...&pageSize=&bookmark=
GET /api/records/query/by-date?from=&to=&pageSize=&bookmark=
GET /api/access-requests/query/pending-for-patient?patientId=&pageSize=&bookmark=
```

响应体里带 `{ records, bookmark, fetchedCount, cache: "hit"|"miss" }`。

### 2.5 后端（FastAPI）

| 文件 | 改动 |
|------|------|
| [backend/app/gateway.py](../backend/app/gateway.py) | 新增 `query_records_by_hospital` / `query_records_by_date` / `query_pending_requests_for_patient` |
| [backend/app/schemas.py](../backend/app/schemas.py) | 新增 `ChainRecordBrief / ChainRecordPage / ChainPendingRequestBrief / ChainPendingRequestPage` |
| [backend/app/main.py](../backend/app/main.py) | 新增 3 个端点：`GET /api/records/chain/by-hospital`（admin 全局、hospital 默认本院）/ `GET /api/records/chain/by-date`（admin 专属）/ `GET /api/access-requests/chain/pending`（patient 专属） |

### 2.6 测试

| 文件 | 改动 |
|------|------|
| [test/helpers.js](../fabric-network/chaincode/medshare/javascript/test/helpers.js) | **新增 Mango selector 匹配器**（`$eq/$ne/$gt/$gte/$lt/$lte/$in/$and/$or`）+ `_runRichQuery` 实现 state 全扫描 + `getQueryResult` / `getQueryResultWithPagination` stub |
| [test/medshare-contract.test.js](../fabric-network/chaincode/medshare/javascript/test/medshare-contract.test.js) | **+6** 条富查询用例：3 种查询的正确性 / 非法 LATEST 过滤 / 修订版不会冒充最新 / **1000 条分页遍历无丢失无重复** |
| [backend/tests/conftest.py](../backend/tests/conftest.py) | 为富查询添加 3 个 stub，基于 `chain_store["records"]` / `chain_store["requests"]` 共享状态 |
| [backend/tests/test_rich_query.py](../backend/tests/test_rich_query.py) | **新文件，11 条用例** —— by-hospital（本院默认 / admin 指定 / admin 必填 / patient 禁止 / 分页 25→4 页无重复）/ by-date（admin 查 / 非 admin 禁 / 缺参 422）/ pending（本人只看自己 / APPROVED 剔除 / 非 patient 禁） |

## 三、验证结果

### 3.1 链码测试

```
$ npm test

  MedShareContract
    ... (前 43 条保留)
    CouchDB 富查询（迭代 7）
      ✔ QueryRecordsByHospital 只返回最新版 LATEST 条目（不会包含版本化键）
      ✔ QueryRecordsByHospital 对另一个医院只返回自己的 3 条
      ✔ QueryRecordsByDateRange 按 createdAt 闭区间过滤
      ✔ QueryPendingRequestsForPatient 只返回 PENDING 的申请
      ✔ 分页 1000 条记录：按 50/页 遍历完成，无丢失无重复 (161ms)
      ✔ 富查询只会命中 LATEST，不会把版本化键当成最新返回
    ...

  49 passing (270ms)
```

**链码 49/49（迭代 6 的 43 + 迭代 7 的 6）。**

### 3.2 后端测试

```
$ pytest tests/ -v
tests/test_auth.py         16 passed
tests/test_records.py      12 passed
tests/test_history.py      10 passed
tests/test_crypto.py       12 passed
tests/test_files.py        16 passed
tests/test_abac.py         13 passed
tests/test_events.py        9 passed
tests/test_rich_query.py   11 passed   ← 本次新增

99 passed in 82.71s
```

**后端 99/99（迭代 6 的 88 + 迭代 7 的 11）。**

### 3.3 关键测试亮点

**① 1000 条分页，无丢失 / 无重复**（`链码`）：

```js
for (let i = 0; i < 1000; i++) {
  await contract.CreateMedicalRecordEvidence(ctx, String(i + 1), ...);
}
const seenIds = new Set();
let bookmark = "";
let pages = 0;
while (true) {
  const raw = await contract.QueryRecordsByHospital(
    ctx, "HospitalA", "50", bookmark
  );
  const out = JSON.parse(raw);
  for (const r of out.records) {
    expect(seenIds.has(r.recordId)).to.equal(false);   // ← 无重复
    seenIds.add(r.recordId);
  }
  pages += 1;
  if (!out.bookmark) break;
  bookmark = out.bookmark;
}
expect(seenIds.size).to.equal(1000);                   // ← 无丢失
expect(pages).to.equal(20);                            // ceil(1000/50)
```

**② 富查询只命中 LATEST**：

```js
// 创建 + 修订 2 次 → 4 个状态键（v1 / v2 / v3 / LATEST）
// 但 QueryRecordsByHospital 应只返回 1 条：version==3 && isLatest==true
const out = JSON.parse(await contract.QueryRecordsByHospital(ctx, "HospitalA", "20", ""));
expect(out.records).to.have.lengthOf(1);
expect(out.records[0].version).to.equal(3);
expect(out.records[0].isLatest).to.equal(true);
```

**③ 后端 25 条分页 → 4 页完整遍历**（`后端`）：

```python
for _ in range(25): _upload(client, hA, pid)
seen = set()
bookmark = ""
pages = 0
while True:
    body = client.get(f"/api/records/chain/by-hospital?page_size=7&bookmark={bookmark}").json()
    for rec in body["records"]:
        assert rec["record_id"] not in seen   # 无重复
        seen.add(rec["record_id"])
    pages += 1
    if not body.get("bookmark"): break
    bookmark = body["bookmark"]
assert len(seen) == 25                         # 无丢失
assert pages == 4                              # ceil(25/7)
```

## 四、量化指标

| 指标 | 目标 | 本次交付 | 说明 |
|------|------|---------|------|
| 分页遍历 1000 条无丢失/重复 | 100% | **1000/1000** 且 20 页完整遍历 | ✓ |
| 三类富查询的正确性 | 全部通过 | 6 条链码 + 11 条后端 = **17 条** 专属用例 | ✓ |
| CouchDB 索引文件就绪 | 3 个 | `indexUploaderHospital` / `indexCreatedAt` / `indexPatientPending` | ✓ |
| 有索引 vs 无索引比值 | < 0.3 | **只能在真实 CouchDB peer 上测**；mock 用全扫模拟无索引路径 | ⚠ 部分（见下节） |

### 4.1 关于"有索引查询 < 无索引查询 × 0.3" 的说明

本项目的自动化测试运行在 **mock stub** 下，Mango selector 的实现就是 "全扫描 + 过滤"，本质上**没有索引加速可以模拟**。要验证这条指标，必须在**真实 CouchDB peer** 上部署：

```sh
# 1) 启用 CouchDB
bash fabric-network/scripts/bootstrap.sh     # 已带 -s couchdb

# 2) 部署链码：Fabric 会读取 META-INF/statedb/couchdb/indexes/*.json 自动建索引
# 3) 通过 docker exec 进入 peer 容器，对比：
#    a) 查询时在 selector 里指定 use_index → 索引命中
#    b) 临时删除索引后查询 → warning.log 会出现 "no_usable_index"
# 4) 用 ab / hey 或 locust 压测两种场景，比较 P50/P95
```

迭代 8（生产化）会做这部分压测。本迭代已经备齐：
- 索引 JSON 文件
- 链码 `use_index` 语法正确
- Fabric 网络脚本切到 CouchDB

## 五、核心设计决策

### 5.1 `isLatest` 标志 vs 独立 docType

两种方案：
- **方案 A**：LATEST 用独立 `docType: "RecordEvidenceLatest"`
- **方案 B**：保持同一 `docType`，加 `isLatest: true`（本次采用）

选择 B：
- 版本化键和 LATEST 键**本质是同一种数据**（同一个病历的某一瞬态快照），用同一 docType 更自然
- 向后兼容：迭代 2 建立的 `GetRecordLatest / GetRecordVersion` 不受影响
- CouchDB 索引可以复合字段，`(docType, isLatest, uploaderHospital)` 本身就是 B-tree 的一级过滤

**代价**：需要更新迭代 2 的一条 `deep.equal` 断言。这是单点改动。

### 5.2 链上查询 vs MySQL 镜像：何时用哪个？

本迭代**新增了** "链上直查" 能力，但**没有去掉**原有 MySQL 列表接口。这是有意的：

| 场景 | 源 | 原因 |
|------|----|------|
| 病历列表（快速浏览） | MySQL | DB 关联 users 拿 real_name 等"业务语义字段" |
| 跨医院统计 / 按时间段审计 | **链上** | 源头真实性高，即使 DB 被篡改仍可信 |
| 患者看自己的 PENDING 申请 | **链上** | 数据归属患者，应让链成为最终仲裁 |
| 下载 / 加解密 / 修订 | MySQL + 链 | 文件在本地磁盘；但状态更新要上链 |

后续 UI 可以把"普通列表"与"链上权威视图"作为两个 tab，或者让 admin 的审计界面优先选链上路径。

### 5.3 bookmark 分页 vs offset 分页

CouchDB 和 Fabric 都原生支持 `bookmark`（不透明游标）。本迭代从头到尾都透传 bookmark：
- 链码：`getQueryResultWithPagination(query, pageSize, bookmark)` 直接返回下一页 bookmark
- Gateway：作为字符串查询参数
- 后端：URL encoded
- Mock 里用 offset 的字符串模拟 bookmark，保持接口契约一致

好处：分页对"数据插入"稳定（即使遍历过程中有新插入，bookmark 方式更不容易漏/重），相比 offset 是**物联网 / 审计类应用的标配**。

### 5.4 `use_index` 显式声明

```js
const query = {
  selector: {...},
  use_index: ["_design/indexUploaderHospitalDoc", "indexUploaderHospital"],
};
```

CouchDB 会**自动尝试选择索引**，但在生产中显式指定避免 query planner 选择"次优索引"导致性能退化。Fabric 2.x 推荐这种写法。

## 六、已知不足 / 留给后续迭代

1. **Mock vs 真 CouchDB 语义差异**：我们的 mock 支持 Mango 子集，但并不 100% 等价于真 CouchDB（例如 `$regex` / `$exists` / 数组字段查询未实现）。真实部署仍需迭代 8 的压测验证
2. **性能比值（< 0.3）未自动化**：如前文所述，需真实 peer
3. **查询结果未加权限二次过滤**：链上查询返回的是"全量匹配"，后端拿到后只做 schema 转换。若要做"某医院只能看到与自己相关的记录"，需要在 selector 中额外加 `$or`（`uploaderHospital == me` 或 `approvedTo $contains me`）
4. **`isLatest` 对遗留数据**：若升级前已有 v1/LATEST 数据（没有 `isLatest` 字段），富查询会查不到。真实升级需要运行一次"补标"交易遍历所有 LATEST 键回填标志
5. **前端未暴露富查询入口**：当前只有后端 + gateway 就绪；前端列表页仍走老 MySQL 路径。这部分留到迭代 8 做"审计/统计大盘"时统一加入

## 七、如何复核本次迭代

```bash
# 1. 链码测试（49 条，含 6 条富查询 + 1000 条分页）
cd fabric-network/chaincode/medshare/javascript
npm test

# 2. 后端测试（99 条，含 11 条富查询集成）
cd backend
pytest tests/ -v
pytest tests/test_rich_query.py -v   # 单独跑迭代 7 的后端用例

# 3. 真实 CouchDB 下的端到端（需 WSL/Docker）
bash fabric-network/scripts/bootstrap.sh    # -s couchdb 已嵌入脚本
docker compose up -d --build gateway backend

# 进 peer 容器看索引是否建成
docker exec peer0.org1.example.com ls -la /var/hyperledger/production/ledgersData/stateLeveldb
# 或直接看 CouchDB 管理界面（对应 host 端口由 network.sh 映射）
curl -s http://admin:adminpw@localhost:5984/medicalchannel_medshare/_index | jq .

# 查 API
curl "http://localhost:8000/api/records/chain/by-hospital" \
  -H "Authorization: Bearer <HOSPITAL_A_TOKEN>"
```

## 八、下一次迭代（迭代 8）预告

**迭代 8：性能压测（Caliper）+ 生产化部署**（收官）

- Hyperledger Caliper 三场景（纯查 / 纯写 / 80% 查+20% 写）+ TPS/延迟/错误率报告
- Nginx 反向代理 + HTTPS
- `docker-compose.prod.yml`（资源限制、健康检查）
- Prometheus `/metrics` + 简单 Grafana 仪表板
- 安全扫描：bandit / npm audit / OWASP ZAP
- 全链路集成测试：一键启动 → 自动跑 20 条核心业务流
- **补齐本迭代的"有索引 < 无索引 × 0.3" 实测**
- 量化目标：
  - Caliper 查询 TPS ≥ 200
  - Caliper 上链 TPS ≥ 30
  - API P95 < 500ms（100 并发）
  - 安全扫描 0 高危漏洞
