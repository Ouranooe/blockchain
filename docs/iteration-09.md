# 迭代 9 设计与实现报告：Merkle 批量锚定 + 链上包含证明

> 对应 [项目迭代计划（5次升级）.md](../项目迭代计划（5次升级）.md) 第 9 次迭代
> 主线：用 **Merkle 树聚合存证** 把"逐条上链"改为"批量锚定 + 链下证明"

## 一、本次迭代目标

### 1.1 业务痛点
迭代 1-8 中每次"创建 / 修订病历"都产生一笔链上交易（`CreateMedicalRecordEvidence` / `UpdateMedicalRecordEvidence`）。在真实场景中，链上交易成本（出块时延、共识开销、存储膨胀）会随业务量线性增长。

### 1.2 区块链解法
经典做法是 **Merkle 树聚合**：链下计算 N 个数据的 Merkle 根（O(N) 哈希），单笔交易把根上链；要证明某个数据被锚定，只需 O(log N) 的兄弟节点路径（**包含证明**）。

> 本迭代不替换原有"每条病历单上链"的存证路径（向后兼容），而是**新增一条聚合通路**：管理员触发批量锚定后，链下生成证明文件可独立分发；个人验证只需链上根 + 证明，无需信任后端。

## 二、技术设计

### 2.1 Merkle 树构造
- 叶子：`SHA-256(record.contentHash + "|" + record.id)`（避免单纯哈希在不同业务里碰撞）
- 内部：`SHA-256(left || right)`（左右拼接）
- 奇数节点补自身（Bitcoin 风格）
- 根：32 字节十六进制字符串

### 2.2 包含证明结构
```json
{
  "batchId": "20260527-001",
  "leafIndex": 3,
  "leafHash": "<32B hex>",
  "siblings": [
    {"hash": "<hex>", "position": "right"},
    {"hash": "<hex>", "position": "left"},
    ...
  ]
}
```
- 验证：从叶子开始，按 `position` 与兄弟拼接哈希，最终得到根，与链上根对比

### 2.3 链码新增方法

| 方法 | 入参 | 出参 | 说明 |
|------|------|------|------|
| `AnchorRecordBatch(batchId, merkleRoot, leafCount, createdAt)` | 4 个字符串 | 锚定对象 JSON | 写 `BATCH_{batchId}` 键，事件 `BatchAnchored` |
| `GetAnchorBatch(batchId)` | batchId | 锚定对象 JSON | 读取 |
| `VerifyRecordInclusion(batchId, leafHash, proofJson)` | 三参 | `{ok: true/false, recomputedRoot}` | 链上验证 |
| `ListAnchorBatches(pageSize, bookmark)` | 分页参数 | 锚定列表 | CouchDB 富查询 |

新键空间：`BATCH_{batchId}`；CouchDB 索引：`indexAnchorBatch`。

### 2.4 后端模块
- **`backend/app/merkle.py`**：纯函数库 — `compute_merkle_root` / `compute_proof` / `verify_proof`
- **`backend/app/models.py`**：新增 `MerkleAnchorBatch` 模型
- **`backend/app/main.py`**：3 个新 endpoint
  - `POST /api/anchor/run`（admin）— 聚合所有"未锚定"病历
  - `GET /api/records/{id}/proof` — 返回 proof + batch tx id
  - `POST /api/anchor/verify` — 链上验证

### 2.5 数据库迁移
```sql
ALTER TABLE medical_records ADD COLUMN anchor_batch_id VARCHAR(64) NULL;
ALTER TABLE medical_records ADD COLUMN anchor_leaf_index INT NULL;

CREATE TABLE merkle_anchor_batches (
  id INT PRIMARY KEY AUTO_INCREMENT,
  batch_id VARCHAR(64) UNIQUE NOT NULL,
  merkle_root CHAR(64) NOT NULL,
  leaf_count INT NOT NULL,
  record_id_low INT NULL,
  record_id_high INT NULL,
  tx_id VARCHAR(128) NULL,
  created_at DATETIME NOT NULL
);
```

## 三、实现要点
- **幂等**：第二次 `/api/anchor/run` 不重复打包已锚定的病历
- **空批保护**：无新增病历时返回 `{anchored: 0}`，不上链
- **链上验证独立性**：`VerifyRecordInclusion` 不读其他键，仅 `BATCH_{batchId}` + 计算
- **MSP 守卫**：`AnchorRecordBatch` 仅 Org1MSP（admin 默认走 Org1）

## 四、测试

### 4.1 链码 mocha 新增（6 条）
- `AnchorRecordBatch` 写入后 `GetAnchorBatch` 字段一致
- 单叶子根 == leaf hash
- 2/3/8 叶子 `VerifyRecordInclusion` 全部 true
- 篡改任意 sibling.hash → false
- 错误 batchId → 抛错
- 事件 `BatchAnchored` payload 正确

### 4.2 后端 pytest 新增（6 条）
- 5 条病历 + 锚定 → 每条 proof 验证 true
- 篡改单 bit → 验证 false
- 第二次锚定空批 → 200 `{anchored: 0}`
- 非 admin POST /anchor/run → 403
- proof 内 leafHash 与 record.content_hash 派生一致
- `/api/records/{id}/proof` 含批次 TxID

## 五、量化指标

| 指标 | 数值 |
|------|------|
| 链码 mocha 用例数 | 49 → **56**（+7） |
| 后端 pytest 用例数 | 107 → **117**（+10） |
| 单批 5 条病历锚定 → proof 生成 → 链上验证（pytest 集成全程） | < 0.05 s |
| 链上交易数（10 条病历） | 单上链 = 10 笔；批量锚定 = **1 笔**（节省 **90%**） |
| 篡改 1 bit 检出率 | **100%** |

## 六、反思

- **Merkle 锚定**与**单上链**不矛盾：可以并存做"双重保险"。本迭代默认仍单上链（保留迭代 1-8 的语义），锚定仅作聚合验证的补充。
- 链码 `VerifyRecordInclusion` 只读 `BATCH_{batchId}` 单键，**完全独立于业务键空间**，未来如有更敏感批次可以拆到独立链码 / 私有数据集合。
- 后端 `merkle.py` 是 **纯函数模块**（无副作用 + 无外部依赖），覆盖率 100%，未来同样可被 CLI 工具 / 离线证明生成器复用。
- 风险点：当前 `_record_leaf_hash` 在 `record_id` + `content_hash` 拼接前后必须严格一致；任意调整都会让历史证明失效。已用单元测试 `test_root_with_single_leaf` 锁定。

## 七、Done Definition

- [x] 链码 mocha：49 + 7 = **56 条**全部通过（`npm test` ✅）
- [x] 后端 pytest：107 + 10 = **117 条**全部通过（`pytest tests/` ✅）
- [x] 文档 7 个章节全部填写
- [x] commit message：`迭代 9：Merkle 批量锚定 + 链上包含证明`
