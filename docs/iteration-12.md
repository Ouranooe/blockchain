# 迭代 12 设计与实现报告：链码 v2 升级 + 状态迁移 + 兼容读

> 对应 [项目迭代计划（5次升级）.md](../项目迭代计划（5次升级）.md) 第 12 次迭代
> 主线：演示 Fabric **链码生命周期升级**：给 record / request 加业务字段，做老数据兼容读与一次性迁移

## 一、本次迭代目标

迭代 1-11 写入的链上记录形态固定。当业务字段需要演进时（例如"病历科室分类 / 申请目的"），需要在不丢失老数据的前提下：

- 老 value 读出来仍可解析（**兼容读**）
- 新 value 写入新字段（**业务推进**）
- 老 value 可一次性迁移成新结构（**显式迁移**，产生 history 记录便于追溯）

这是真实 Fabric 项目"如何活到 v2"的核心问题。本迭代演示完整方案。

## 二、技术设计

### 2.1 新增字段
- `RecordEvidence.category` ∈ `{GENERAL, INPATIENT, OUTPATIENT, EMERGENCY}`（默认 GENERAL）
- `AccessRequest.purpose` ∈ `{TREATMENT, RESEARCH, AUDIT}`（默认 TREATMENT）

业务背景：科室分类便于审计统计；申请目的便于合规审查。

### 2.2 兼容读策略

链码层增 `_normalizeRecord(obj)` 与 `_normalizeRequest(obj)`：
- 在 `_getStateAsObject` 之后立即调用
- 缺省字段写默认值
- 不变更链上存储（只在内存里补）

所有 `GetXxx`、富查询、`AccessRecord` 等读路径走 normalize。

### 2.3 显式迁移方法
- `MigrateRecordsV2(batchJson)`（admin only）：入参 `[{recordId, category}]`
  - 对每条读 latest → 补 category（若 payload 给定）→ `putState` 回写
  - 在 history 上多一笔 v2.x 记录，可审计
- `MigrateRequestsV2(batchJson)`（admin only）：同理
- 事件 `SchemaMigrated`

### 2.4 SchemaVersion
- `GetSchemaVersion()` 返回 `"v2"`
- 后端启动时调用，记录到 `/api/system/info`

### 2.5 富查询索引
- `META-INF/statedb/couchdb/indexes/indexCategory.json`
- `META-INF/statedb/couchdb/indexes/indexPurpose.json`
- 链码新查询 `QueryRecordsByCategory(category, pageSize, bookmark)`

### 2.6 后端 / 前端
- 表 `medical_records` 增 `category VARCHAR(32)`
- 表 `access_requests` 增 `purpose VARCHAR(32)`
- API：
  - 创建病历 / 申请支持 category / purpose
  - `POST /api/admin/migrate/records-v2`（admin）
  - `GET /api/records/chain/by-category`
- 前端：
  - 上传 / 申请表单加下拉
  - 列表显示 category / purpose tag

## 三、测试

### 3.1 链码 mocha（≥6 条）
- 老 record (无 category) `GetRecordLatest` → category="GENERAL"
- 新 record 写入 category 持久化
- 富查询按 category 过滤
- `MigrateRecordsV2` 一次性更新 → 字段确实写入
- 非 admin MSP 调用 Migrate → 抛错
- `GetSchemaVersion()` 返回 "v2"

### 3.2 后端 pytest（≥6 条）
- 老数据兼容（mock 一条无 category 的 chain payload → 后端不报错）
- 新建带 category 写入
- 列表按 category 过滤
- 迁移 endpoint 非 admin 403
- 迁移 endpoint admin 200，DB 字段同步
- `/api/system/info` 返回 schemaVersion=v2

## 四、量化指标

| 指标 | 数值 |
|------|------|
| 链码 mocha 用例数 | 70 → **77**（+7） |
| 后端 pytest 用例数 | 133 → **142**（+9） |
| 老 record 兼容读成功率 | **100%**（`_normalizeRecord(obj)` 补齐 category=GENERAL） |
| MigrateRecordsV2 幂等性 | 二次同样输入 count=0 |
| 非 Org1MSP 调 Migrate | **100% 拒绝** |

## 五、反思

- 本迭代演示了 Fabric 链码 v2 升级的**最小可行方案**：
  - 在链码内部保留单一 contract class（实操中往往用 `peer lifecycle chaincode upgrade` 让 channel 升级到新的 contract package）
  - **加可选参数 + 默认值** 让旧调用方继续工作（向后兼容）
  - **`_normalize*()`** 给老数据补默认（兼容读）
  - **显式 Migrate 方法**（一次性把老数据写到新形态，留下 history 痕迹）
- 字段白名单（`V2_RECORD_CATEGORIES`、`V2_REQUEST_PURPOSES`）让链码做最严格的 schema 校验；后端 pydantic pattern 双层防御。
- 真正的 lifecycle 升级（`peer lifecycle chaincode package/install/approve/commit`）需要在真链上跑；本迭代用 `GetSchemaVersion()` 返回字符串 "v2" 作为一个简单的 schema 自描述接口。
- 风险：CouchDB 索引 `indexCategory` 必须在升级时一起部署，否则按 category 富查询会回退到全表扫描。

## 六、Done Definition

- [x] 链码 mocha：70 + 7 = **77 条**全部通过
- [x] 后端 pytest：133 + 9 = **142 条**全部通过
- [x] commit message：`迭代 12：链码 v2 升级 + 状态迁移`
