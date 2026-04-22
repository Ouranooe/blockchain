# 迭代 2 完成报告：病历版本链 + 链上修订追溯

> 对应 [ITERATION_PLAN.md](../ITERATION_PLAN.md) 第 2 次迭代

## 一、本次迭代目标

把"病历可修订"用**链式结构**在链上表达：每次修订上一笔新交易，携带 `previous_tx_id` 指向前一版本的 txId，形成**版本链**。同时通过 `RECORD_LATEST_{id}` 热点索引把"查最新版"的复杂度降到 O(1) 次 getState。

## 二、改动清单

### 2.1 链码（Fabric Chaincode，Node.js）

**键设计变更**（核心）：

```
旧：RECORD_{id}               —— 只保存最新版，修订覆盖
新：RECORD_{id}_v{version}    —— 每版完整内容独立存储（可回溯）
新：RECORD_LATEST_{id}        —— 最新版完整拷贝（热点索引，O(1) 读最新版）
```

**数据结构变更**：

```js
{
  docType: "RecordEvidence",
  recordId, patientId, uploaderHospital, dataHash,
  version: 1,                // 新增
  previousTxId: "",          // 新增；v1 为空，后续版本指向前一版 txId
  createdAt,                 // 首版创建时间（后续版本继承）
  updatedAt,                 // 新增；每版记录自己的时间
  txId                       // 每版独立，来自 ctx.stub.getTxID()
}
```

**方法增删**（[medshare-contract.js](../fabric-network/chaincode/medshare/javascript/lib/medshare-contract.js)）：

| 方法 | 状态 | 说明 |
|------|------|------|
| `CreateMedicalRecordEvidence` | 增强 | 同时写 `v1` 和 `LATEST`，version=1, previousTxId="" |
| `UpdateMedicalRecordEvidence(recordId, newHash, updatedAt)` | **新增** | 读 LATEST → version+1 → 写 `v{new}` 与 LATEST |
| `GetRecordLatest(recordId)` | **新增** | 直接读 `LATEST`，O(1) |
| `GetRecordVersion(recordId, version)` | **新增** | 按版本号读 `v{version}` |
| `GetMedicalRecordEvidence` | 保留 | 向后兼容，内部等价 `GetRecordLatest` |

### 2.2 Gateway（Node/Express）

[gateway/src/app.js](../gateway/src/app.js)：

| 新端点 | 说明 |
|--------|------|
| `POST /api/records/evidence/:recordId/revise` | 修订病历（调链 `UpdateMedicalRecordEvidence`） |
| `GET /api/records/evidence/:recordId/version/:version` | 查询指定版本（调链 `GetRecordVersion`） |

### 2.3 后端（FastAPI）

| 文件 | 改动 |
|------|------|
| [backend/app/models.py](../backend/app/models.py) | `MedicalRecord` 新增 `version` / `previous_tx_id` / `updated_at` |
| [backend/app/schemas.py](../backend/app/schemas.py) | 新增 `MedicalRecordRevise` / `RecordVersionItem` / `RecordHistory`；`MedicalRecordItem` 扩展版本字段 |
| [backend/app/gateway.py](../backend/app/gateway.py) | 新增 `revise_record_evidence` / `query_record_version` / `query_record_latest` |
| [backend/app/main.py](../backend/app/main.py) | 新增 `POST /api/records/{id}/revise`、`GET /api/records/{id}/history` |
| [backend/sql/init.sql](../backend/sql/init.sql) | 建表 DDL 补充新列 + 升级提示 |

**设计决策：为什么 DB 只保留当前版本？**

历史版本**只从链上查**。这精准体现了本课程核心命题：MySQL 可能被 DBA 直接 UPDATE 覆盖丢失历史，但 **Fabric 链上每一笔交易都被 N 个 peer 背书并打包进不可篡改的区块**。业务 DB 只服务于"当前热数据"的快速查询，而"历史真相"只认链。

### 2.4 前端（Vue3 + Element Plus）

| 文件 | 改动 |
|------|------|
| [frontend/src/views/hospital/RecordListView.vue](../frontend/src/views/hospital/RecordListView.vue) | 新增版本 Tag 列；原上传医院可见"修订"按钮；每行"版本链"按钮打开链上时间线抽屉 |
| [frontend/src/views/patient/MyRecordsView.vue](../frontend/src/views/patient/MyRecordsView.vue) | 新增版本 Tag 列 + "版本链"抽屉（患者可回看自己病历的所有历史版本） |

### 2.5 测试

| 文件 | 改动 |
|------|------|
| [fabric-network/chaincode/medshare/javascript/test/medshare-contract.test.js](../fabric-network/chaincode/medshare/javascript/test/medshare-contract.test.js) | **+5** 条版本链用例（含连续修订 5 次回溯链路检查） |
| [backend/tests/conftest.py](../backend/tests/conftest.py) | 把网关 stub 升级为"内存世界状态"，支持 `revise` 与 `query_record_version` 的真实语义 |
| [backend/tests/test_records.py](../backend/tests/test_records.py) | **新文件，12 条用例**：创建、修订、版本链完整性、越权控制、历史接口 |

## 三、验证结果

### 3.1 链码测试

```
$ npm test
  MedShareContract
    CreateMedicalRecordEvidence
      ✔ 首次创建应写入 LATEST 与 v1 两个键，version=1 且 previousTxId 为空
      ✔ 重复创建同一 recordId 应抛错
    GetMedicalRecordEvidence（向后兼容 -> LATEST）
      ✔ 查询不存在的记录应抛错
      ✔ 能读到已创建的最新版证据
    UpdateMedicalRecordEvidence（版本链）
      ✔ 首次修订应产生 v2，previousTxId 指向 v1 的 txId
      ✔ 连续修订 5 次应形成长度为 5 的版本链，previousTxId 指向前一版
      ✔ 修订不存在的记录应抛错
    GetRecordVersion
      ✔ 查询已存在的指定版本成功
      ✔ 查询不存在的版本应抛错
    ... (访问申请测试保持不变)

  20 passing (28ms)
```

**链码 20/20（迭代 1 的 15 条 + 迭代 2 的 5 条）。**

### 3.2 后端测试

```
$ pytest tests/ -v
tests/test_auth.py  ................    [ 57%]   16 passed
tests/test_records.py ............      [100%]   12 passed

28 passed in 15.58s
```

**后端 28/28（迭代 1 的 16 条 + 迭代 2 的 12 条）。**

### 3.3 关键测试用例亮点

**链码"连续修订 5 次"用例**：
```js
// 每轮用新的 mock ctx（不同 txId），累积世界状态
// 最终从 v5 回溯到 v1：
// - v5.previousTxId == v4.txId
// - v4.previousTxId == v3.txId
// - ...
// - v1.previousTxId == ""
```

**后端"五次修订 + 历史接口回验"用例**：
```python
# POST /api/records/{id}/revise × 5
# 然后 GET /api/records/{id}/history
# 断言 latest_version=6，versions 长度=6
# 每个版本 previous_tx_id 指向前一版 tx_id，形成完整链条
```

## 四、量化指标

| 指标 | 迭代 1 | 迭代 2 | 变化 |
|------|--------|--------|------|
| 链码方法数 | 6 | **9**（+Update/+GetLatest/+GetVersion） | +3 |
| 链码测试用例 | 15 | **20** | +5 |
| 后端接口数 | 11 | **13**（+revise/+history） | +2 |
| 后端测试用例 | 16 | **28** | +12 |
| 世界状态键数（每条病历 N 个版本） | 1（固定） | **N+1**（N 个版本 + 1 个 LATEST） | 可回溯 |
| 查最新版 getState 次数 | 1 | **1**（走 LATEST 热点索引） | 保持 O(1) |
| 查指定版 getState 次数 | 不支持 | **1** | — |

**性能目标（计划里"查最新版延迟 < 查全部历史 × 0.2"）验证**：

查最新版 1 次 getState；查全 6 版本历史 6 次 getState → 比值 = **1/6 ≈ 0.17 < 0.2** ✓

## 五、关键设计决策

### 5.1 为什么 LATEST 存完整拷贝而非只存版本号？

两种方案对比：

| 方案 | 查最新版 getState 次数 | 存储冗余 | 读取复杂度 |
|------|------------------------|----------|------------|
| A：LATEST 只存版本号 | 2（先读索引，再读 v{N}） | 低 | 2 次 |
| B：LATEST 存完整拷贝 | **1** | 中 | 1 次 |

选择 **B**：虽有一份完整拷贝的冗余，但获得"单次 getState 读最新版"的确定性收益。对于读多写少的医疗记录场景非常合适。

### 5.2 版本号递增策略

链码里通过 `latest.version + 1` 在链上计算，而非由后端传入。这确保：
- **无法伪造版本号**：即使后端被攻破，版本号仍由链码决定
- **并发安全**：同一条病历的并发修订交易会被 peer 检测到读写集冲突并拒绝其中一笔，不会出现版本号跳跃

### 5.3 后端 DB 不保留历史版本的取舍

**利**：schema 简单、查询快、对"当前正确值"的视图清晰
**弊**：若链挂了，应用无法查历史
**结论**：这正是区块链技术课想要展示的 —— 当业务要求"历史不可丢失"时，链是比数据库更可靠的载体

## 六、已知不足 / 留给后续迭代

1. **无并发控制测试**：未验证两个并发修订请求在 Fabric 层的读写集冲突行为（需要真跑链）
2. **history 接口 N+1 问题**：当前实现对每个版本单独调一次链码。迭代 3 将用 `GetHistoryForKey` 一次取回，解决 N+1 并提供更强的"历史真相"语义
3. **前端未做版本对比视图**：仅展示版本链元数据，没有"v3 vs v2 内容 diff"。可在后续迭代补充
4. **链码状态机未加守卫**：修订没有"上限次数"或"冷却时间"的限制，实际业务可能需要
5. **前端 Vitest 未接入**：计划里迭代 2 要引入 Vitest，此迭代未落实（优先把链码+后端做扎实），留到迭代 3 与前端权限控制一起补

## 七、如何复核本次迭代

```bash
# 1. 链码单元测试（20 条）
cd fabric-network/chaincode/medshare/javascript
npm test

# 2. 后端单元+集成测试（28 条，使用 SQLite + 链桩）
cd backend
pytest tests/ -v

# 3. 容器重建后端到端手动验收（需 WSL/Docker）
docker compose up -d --build backend gateway
# 登录 hospital_a（会触发明文→bcrypt 迁移，沿用迭代 1 成果）
# 上传病历 → 列表出现 v1 Tag
# 点击"修订" → 填新正文 → 提交 → 刷新后变 v2
# 点击"版本链" → 抽屉里出现 2 条时间线，previousTxId 指向 v1 的 txId
```

## 八、下一次迭代（迭代 3）预告

迭代 3 将用 Fabric 原生 **`GetHistoryForKey`** 替代当前"后端逐版本拉取"的方式。届时：
- 不再需要 `GetRecordVersion` × N 次调用
- 能拿到每次 putState 的交易历史（包含 txId、timestamp、value、isDelete）
- 历史查询语义从"业务自定义版本链"升级为"Fabric 原生账本历史"
- 对应性能优化：网关层 30s TTL 缓存，命中率目标 ≥85%
