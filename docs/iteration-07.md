# 第七次迭代实验报告：CouchDB 富查询与链上条件检索

## 一、实验目的与内容

本次迭代主要解决前几次迭代留下的一个问题：病历和授权申请虽然已经上链，但很多列表查询仍然依赖后端数据库里的镜像数据。这样做开发方便，不过在审计场景下不够理想，因为数据库只是业务系统的缓存，真正有证明力的数据还是链上状态。

本次实验目的分为以下几点：

- 在链码层补充条件检索能力，让“按上传医院查询病历”“按创建时间范围查询病历”“按患者查询待审批申请”这些业务场景可以直接从链上查询。
- 为 CouchDB 世界状态库准备 Mango 索引文件，避免上线后富查询退化成全量扫描。
- 给病历最新版状态增加 `isLatest` 标志，避免富查询把历史版本键也当作当前版本返回。
- 在 Gateway 和 FastAPI 后端补齐链上查询接口，把链码能力暴露给业务系统。
- 补充链码单测和后端接口测试，重点验证三类查询的正确性、权限限制以及分页时不丢数据、不重复数据。
- 梳理链上直查和 MySQL 镜像查询的边界：普通业务列表仍然可以走数据库，但审计、追溯、患者待审批申请更适合直接以链上状态为准。

这次迭代的核心不是简单加几个接口，而是把“链上数据可查”往前推进了一步。前面几次迭代已经保证了写入、修订、授权和事件流，本次开始让链上的状态真正参与业务查询。

## 二、实验内容

### 1. 链码增加最新版标志

原来的病历状态同时保存了版本化键和最新版键，例如 `RECORD_1_v1`、`RECORD_1_v2`、`RECORD_LATEST_1`。它们的 `docType` 都是 `RecordEvidence`。如果直接用 `docType` 查询，历史版本和最新版都会被查出来，结果会混在一起。

所以本次在写入 `LATEST` 键时加了 `isLatest: true`，版本化键不加这个字段。这样 CouchDB selector 可以稳定过滤出最新版病历。

核心代码位置：[fabric-network/chaincode/medshare/javascript/lib/medshare-contract.js](../fabric-network/chaincode/medshare/javascript/lib/medshare-contract.js)

```js
await this._putStateAsObject(ctx, this._versionKey(recordId, 1), evidence);
await this._putStateAsObject(ctx, latestKey, { ...evidence, isLatest: true });
```

修订病历时同样只给最新版键写入 `isLatest`：

```js
await this._putStateAsObject(
  ctx,
  this._versionKey(recordId, newVersion),
  newEvidence
);
await this._putStateAsObject(ctx, latestKey, { ...newEvidence, isLatest: true });
```

这里没有改成新的 `docType`，主要是考虑到历史接口兼容性。病历证据本质上还是同一种数据，只是状态键不同；用 `isLatest` 做过滤比再拆一个 `RecordEvidenceLatest` 更轻一些，也不会影响已有的按版本查询。

### 2. 链码增加三类 CouchDB 富查询

本次封装了一个分页富查询方法，统一处理 `selector`、`use_index`、分页大小和 `bookmark`。Fabric 的分页不适合用传统 offset，`bookmark` 更接近游标，连续翻页时更稳定。

```js
async _richQueryPaged(ctx, selector, pageSize, bookmark, useIndex, sort) {
  const query = { selector };
  if (sort) query.sort = sort;
  if (useIndex) query.use_index = useIndex;

  const ps = Math.max(1, Math.min(1000, Number(pageSize) || 20));
  const { iterator, metadata } = await ctx.stub.getQueryResultWithPagination(
    JSON.stringify(query),
    ps,
    bookmark || ""
  );

  const records = await this._iterateQuery(iterator);
  return {
    records: records.map((r) => r.value).filter((v) => v !== null),
    fetchedCount:
      (metadata && (metadata.fetchedRecordsCount || metadata.fetched_records_count)) ||
      records.length,
    bookmark: (metadata && metadata.bookmark) || "",
  };
}
```

在这个基础上，新增了三个业务查询方法：

```js
async QueryRecordsByHospital(ctx, uploaderHospital, pageSize, bookmark) {
  const selector = {
    docType: "RecordEvidence",
    isLatest: true,
    uploaderHospital,
  };
  const out = await this._richQueryPaged(
    ctx,
    selector,
    pageSize,
    bookmark,
    ["_design/indexUploaderHospitalDoc", "indexUploaderHospital"]
  );
  return JSON.stringify(out);
}
```

```js
async QueryRecordsByDateRange(ctx, fromIso, toIso, pageSize, bookmark) {
  const selector = {
    docType: "RecordEvidence",
    isLatest: true,
    createdAt: { $gte: String(fromIso), $lte: String(toIso) },
  };
  const out = await this._richQueryPaged(
    ctx,
    selector,
    pageSize,
    bookmark,
    ["_design/indexCreatedAtDoc", "indexCreatedAt"],
    [{ createdAt: "asc" }]
  );
  return JSON.stringify(out);
}
```

```js
async QueryPendingRequestsForPatient(ctx, patientId, pageSize, bookmark) {
  const selector = {
    docType: "AccessRequest",
    patientId: String(patientId),
    status: "PENDING",
  };
  const out = await this._richQueryPaged(
    ctx,
    selector,
    pageSize,
    bookmark,
    ["_design/indexPatientPendingDoc", "indexPatientPending"]
  );
  return JSON.stringify(out);
}
```

这里特意显式写了 `use_index`。CouchDB 可以自动选择索引，但生产环境里如果 query planner 选错索引，性能会很难排查。直接声明索引更清楚，也方便后续压测时确认查询路径。

### 3. 新增 CouchDB 索引文件

本次在链码目录下增加了 CouchDB 索引定义：

- `fabric-network/chaincode/medshare/javascript/META-INF/statedb/couchdb/indexes/indexUploaderHospital.json`
- `fabric-network/chaincode/medshare/javascript/META-INF/statedb/couchdb/indexes/indexCreatedAt.json`
- `fabric-network/chaincode/medshare/javascript/META-INF/statedb/couchdb/indexes/indexPatientPending.json`

三个索引分别服务于按医院查询、按时间范围查询、按患者待审批申请查询。它们会在链码部署时随 chaincode package 一起被 Fabric 识别并建入 CouchDB。

需要注意的是，当前仓库里的 `fabric-network/scripts/bootstrap.sh` 实际启动命令仍是：

```sh
./network.sh up createChannel -ca -c "${CHANNEL_NAME}"
```

如果要在真实 Fabric 网络中验证 CouchDB 索引效果，需要把启动命令改成或手动执行：

```sh
./network.sh up createChannel -ca -c "${CHANNEL_NAME}" -s couchdb
```

也就是说，本次代码和索引已经准备好，但“有索引查询比无索引查询快多少”必须在真实 CouchDB peer 上测，不能只靠 mock 单测下结论。

### 4. Gateway 增加链上查询转发接口

Gateway 新增了三个 HTTP 接口，把前面的链码方法包装成 REST 查询：

- `GET /api/records/query/by-hospital?uploaderHospital=...&pageSize=&bookmark=`
- `GET /api/records/query/by-date?from=&to=&pageSize=&bookmark=`
- `GET /api/access-requests/query/pending-for-patient?patientId=&pageSize=&bookmark=`

Gateway 里沿用了 30 秒 TTL 缓存，避免前端或后端短时间重复点击时频繁访问 Fabric。

```js
async function _servePagedQuery(org, fnName, args, params, res) {
  const key = richKey(fnName, { org, ...params });
  const cached = richCache.get(key);
  if (cached) {
    richStats.hits += 1;
    return res.json({ ...cached, cache: "hit" });
  }

  richStats.misses += 1;
  try {
    const result = await evaluate(org, fnName, args);
    richCache.set(key, result);
    res.json({ ...result, cache: "miss" });
  } catch (error) {
    sendGatewayError(res, error);
  }
}
```

### 5. 后端增加业务接口和权限限制

FastAPI 后端新增了三个业务接口：

- `GET /api/records/chain/by-hospital`
- `GET /api/records/chain/by-date`
- `GET /api/access-requests/chain/pending`

权限设计上没有简单放开所有接口：

- 医院用户查询 `by-hospital` 时，默认查自己医院上传的病历。
- 管理员可以查指定医院，也可以按时间范围查全局病历。
- 患者只能查自己的 PENDING 授权申请。
- 患者不能调用按医院查病历，医院也不能调用按时间全局审计。

后端接口示例：

```python
@app.get(
    f"{settings.API_PREFIX}/records/chain/by-hospital",
    response_model=ChainRecordPage,
)
def chain_records_by_hospital(
    hospital: Optional[str] = None,
    page_size: int = Query(20, ge=1, le=200),
    bookmark: str = "",
    current_user: User = Depends(get_current_user),
):
    target = hospital
    if current_user.role == "hospital" and not target:
        target = current_user.hospital_name
    if current_user.role not in {"admin", "hospital"}:
        raise HTTPException(status_code=403, detail="仅 admin 与 hospital 可调用")
    if not target:
        raise HTTPException(status_code=400, detail="缺少 hospital 参数")

    payload = query_records_by_hospital(
        uploader_hospital=target,
        page_size=page_size,
        bookmark=bookmark,
    )
    return _chain_page_records(payload)
```

这部分我认为比较关键。链上数据虽然可信，但接口层仍然要按角色限制可见范围。否则链上查询做出来以后，反而可能变成越权读取的入口。

### 6. 补充单元测试

链码测试新增了 CouchDB 富查询分组，覆盖：

- 按医院查询只返回最新版 LATEST 记录。
- 查询另一个医院时只返回该医院上传的记录。
- 按 `createdAt` 闭区间查询，并验证升序结果。
- 患者 PENDING 申请查询会排除 APPROVED 记录。
- 1000 条 HospitalA 病历按 50 条一页遍历，最终 20 页，无重复、无丢失。
- 修订多次后，富查询只返回最新版，不返回版本化键。

其中分页测试是本次比较重要的边界用例：

```js
const pageSize = 50;
const seenIds = new Set();
let bookmark = "";
let pages = 0;

while (true) {
  const raw = await contract.QueryRecordsByHospital(
    ctx,
    "HospitalA",
    String(pageSize),
    bookmark
  );
  const out = JSON.parse(raw);
  for (const r of out.records) {
    const id = r.recordId;
    expect(seenIds.has(id)).to.equal(false, `记录 ${id} 重复出现`);
    seenIds.add(id);
  }
  pages += 1;
  if (!out.bookmark) break;
  bookmark = out.bookmark;
}

expect(seenIds.size).to.equal(1000);
expect(pages).to.equal(Math.ceil(1000 / pageSize));
```

后端新增 `backend/tests/test_rich_query.py`，覆盖 11 条接口级用例，包括权限、分页、缺参和 PENDING 状态过滤。

后端分页测试片段：

```python
seen = set()
bookmark = ""
pages = 0
while True:
    r = client.get(
        f"/api/records/chain/by-hospital?page_size=7&bookmark={bookmark}",
        headers={"Authorization": f"Bearer {hA}"},
    )
    body = r.json()
    for rec in body["records"]:
        assert rec["record_id"] not in seen
        seen.add(rec["record_id"])
    pages += 1
    if not body.get("bookmark"):
        break
    bookmark = body["bookmark"]

assert len(seen) == 25
assert pages == 4
```

## 三、实验结果

### 1. 单元测试结果

本次报告里建议只放第七次迭代相关测试截图，同时在文字里说明全量测试是否通过。

链码测试命令：

```powershell
cd fabric-network/chaincode/medshare/javascript
npm test
```

如果只想在截图里突出第七次迭代用例，可以截 `CouchDB 富查询（迭代 7）` 这一段。预期结果是链码测试全部通过，并能看到 6 条本次新增用例，其中包括 1000 条分页遍历。

当前仓库已经继续做到了后续迭代，所以现在运行 `npm test` 的总数会包含迭代 9 到迭代 13 的用例。本次实测结果是 `85 passing`，报告截图时重点截第七次迭代分组即可，不需要解释后续迭代的每一组测试。

后端全量测试命令：

```powershell
cd backend
pytest tests/ -v
```

后端第七次迭代专项测试命令：

```powershell
cd backend
pytest tests/test_rich_query.py -v
```

报告中建议放 `pytest tests/test_rich_query.py -v` 的截图，因为它最能说明本次迭代内容。本次实测结果是 `11 passed`。如果另外跑了全量测试，可以在正文补一句“全量后端测试通过”，但截图不一定要放全量输出，太长反而不清楚。

本次自动化测试能证明的内容：

- 三类链上富查询的筛选条件正确。
- `isLatest` 能把历史版本和最新版区分开。
- `bookmark` 分页没有重复数据，也没有漏数据。
- 后端接口的角色限制符合预期。
- PENDING 查询不会把已批准申请混入结果。

本次自动化测试暂时不能证明的内容：

- CouchDB 索引带来的真实性能提升。
- 真实 peer 中索引是否已经被 CouchDB 建成。

原因是当前链码单测使用的是 mock stub，mock 里的 Mango 查询本质上是全量扫描后过滤，没有真实索引路径。这个限制需要在 Docker + Fabric + CouchDB 实机环境下继续验证。

### 2. 实机截图建议

截图不需要堆很多，建议选能说明“系统真的跑起来了”和“第七次迭代接口真的可用”的画面。

建议截图一：链码单测结果

- 打开终端，运行 `npm test`。
- 截到 `CouchDB 富查询（迭代 7）` 分组和最后的 `passing` 汇总。
- 这张图用于证明链码层新增的富查询和分页逻辑通过测试。

建议截图二：后端专项测试结果

- 打开终端，运行 `pytest tests/test_rich_query.py -v`。
- 截到 11 条测试用例和 `11 passed`。
- 这张图用于证明后端接口权限、分页、状态过滤都通过。

建议截图三：Docker 容器运行状态

命令：

```powershell
docker compose ps
```

截图里至少要看到 `frontend`、`backend`、`gateway`、`mysql` 是运行状态。如果已经用 CouchDB 方式启动 Fabric，还应截 Fabric peer、orderer、couchdb 相关容器。

建议截图四：Swagger 或接口实测

打开：

```text
http://localhost:8000/docs
```

截图建议截这几个接口之一：

- `/api/records/chain/by-hospital`
- `/api/records/chain/by-date`
- `/api/access-requests/chain/pending`

如果已经拿到登录 token，也可以直接截 Swagger 的响应结果，重点显示 `records` 或 `requests`、`bookmark`、`fetched_count`、`cache` 字段。

建议截图五：前端登录后的业务页面

打开：

```text
http://localhost:5173
```

用以下账号任选两类截图：

- 医院账号 `hospital_a / 123456`：截病历列表或上传病历页面，说明医院端业务环境正常。
- 患者账号 `patient1 / 123456`：截待审批申请或我的病历页面，说明患者端业务环境正常。
- 管理员账号 `admin / 123456`：截审计页面，说明管理端能进入系统。

第七次迭代的前端还没有单独做“链上富查询页面”，所以前端截图主要用于证明项目实机运行状态。真正和本次迭代强相关的截图应以测试结果、Swagger 接口、Gateway/后端接口响应为主。

建议截图六：CouchDB 索引验证，只有实机启用 CouchDB 时再截

如果 Fabric 是通过 `-s couchdb` 启动的，可以执行：

```powershell
curl -s http://admin:adminpw@localhost:5984/medicalchannel_medshare/_index
```

截图中如果能看到 `indexUploaderHospital`、`indexCreatedAt`、`indexPatientPending`，就能说明索引文件已经被 CouchDB 识别。这个截图很有价值，但前提是本机 Fabric 网络确实已经启用 CouchDB。

### 3. 本次实验结论

第七次迭代完成后，系统已经具备基础的链上条件检索能力。按医院、按时间范围、按患者 PENDING 申请这三类查询不再只能依赖 MySQL 镜像，后端可以通过 Gateway 调用链码，直接从 Fabric 世界状态读取结果。

从实现上看，`isLatest` 是这次迭代里最重要的小改动。如果没有这个字段，富查询虽然能查，但结果会混入历史版本，业务上反而不可信。这个问题也说明，链上数据建模不能只考虑写入，还要提前考虑后续怎么查。

本次测试覆盖了查询正确性和分页稳定性，链码侧有 1000 条数据分页遍历用例，后端侧有 25 条数据分页为 4 页的接口用例。结果说明当前分页契约是可用的，`bookmark` 也能从链码、Gateway、后端一路传递。

遗留问题主要有两个。第一，当前自动化测试不能验证 CouchDB 索引性能，因为 mock 环境没有真实索引。第二，当前仓库的 Fabric 启动脚本需要再确认是否带 `-s couchdb`，否则真实网络仍可能使用 LevelDB。后续迭代如果要写性能结论，必须在真实 CouchDB peer 上跑接口压测，而不能只根据单测推断。

整体来看，这次迭代把系统从“数据上链但查询仍偏业务库”推进到“关键审计查询可以直接走链”。这对医疗数据共享场景是必要的一步，因为跨机构协作时，查询结果的可信来源比单纯查询速度更重要。
