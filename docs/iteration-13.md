# 迭代 13 设计与实现报告：数据共享积分（FT）+ 经济激励闭环

> 对应 [项目迭代计划（5次升级）.md](../项目迭代计划（5次升级）.md) 第 13 次迭代
> 主线：在链上引入 **简单 FT 账户**，与业务自动联动，初步形成"共享积分"经济闭环

## 一、本次迭代目标

医疗数据共享的现实激励缺失：
- 上传方（医院）付出运维、合规、采集成本，没有"被使用就得分"的反馈
- 患者授权他人访问也未被显式记录贡献

本迭代把"共享行为 ↔ 积分"绑死在链上，让数据流动产生**可审计的链上凭证**：
- 医院新上传病历 → +5 分
- 医院被授权访问（`AccessRecord` 触发）→ 上传医院 +1 分
- 患者批准一次申请 → +1 分
- 任意账户间可链上 **转账** 积分

虽然简单，但完整覆盖 FT 的几个核心：**铸造 / 转账 / 余额 / 防双花 / 历史查询**。

## 二、技术设计

### 2.1 数据结构
- 键 `CREDIT_{userId}` → `{userId, balance, updatedAt}`
- 键 `CREDIT_LEDGER_{ledgerId}` → `{ledgerId, fromUserId, toUserId, amount, reasonCode, txId, createdAt}`（每笔流水）
- CouchDB 索引：`indexCreditLedgerUser`、`indexCreditLedgerTime`

### 2.2 链码方法

| 方法 | 入参 | 说明 |
|------|------|------|
| `CreditMint(toUserId, amount, reasonCode, mintedAt)` | string × 4 | admin only；amount > 0；写余额 + 流水 |
| `CreditTransfer(fromUserId, toUserId, amount, reasonCode, txAt)` | string × 5 | 原子扣加；余额不足抛错；自转抛错 |
| `CreditBalance(userId)` | userId | 返回余额（不存在返回 0） |
| `CreditHistory(userId, pageSize, bookmark)` | 富查询 | 按 userId 双向（from / to）流水 |

### 2.3 业务回调（自动 Mint）
- `CreateMedicalRecordEvidence`：成功后 `_creditMint(uploaderHospitalId, 5, "RECORD_UPLOAD")`
- `AccessRecord` 成功扣减后 `_creditMint(uploaderHospitalId, 1, "RECORD_ACCESSED")`
- `ApproveAccessRequest`：成功后 `_creditMint(patientId, 1, "REQUEST_APPROVED")`

> 注意：业务方法参数现已包含 uploaderHospitalId，但**老接口签名要保持兼容**（迭代 1-8 调用方传 hospital_name 字符串）；本迭代在后端层把 `numericUserId` 通过新参数 `uploaderUserId` 透传给链码。

### 2.4 后端
- 表 `credit_history`（id / user_id / delta / counterparty_id / reason_code / tx_id / created_at）
- API：
  - `GET /api/credits/balance`（当前用户）
  - `GET /api/credits/history`（当前用户）
  - `POST /api/credits/transfer`（任意角色，body: `{to_user_id, amount, reason}`）

### 2.5 前端
- 全角色右上角显示"积分：N"
- 点击展开抽屉：流水 + 转账按钮
- 上传 / 访问完成后通过 WebSocket `CreditMinted` 事件刷新

## 三、测试

### 3.1 链码 mocha（≥7 条）
- Mint 后 balance 增加
- 非 admin Mint → 抛错
- 不存在用户 transfer → 抛错
- 余额不足 transfer → 抛错（且双方余额不变 = 原子性）
- 自转 → 抛错
- 上传病历后 uploader 自动 +5
- AccessRecord 后 uploader 自动 +1

### 3.2 后端 pytest（≥7 条）
- 上传 5 份病历 → balance 25
- AccessRecord 后 uploader balance +1
- 转账成功后余额一致
- 余额不足转账 → 400
- 历史按时间倒序
- `/api/credits/transfer` 自转 → 400
- 非 admin 直接调 mint → 403

## 四、量化指标

| 指标 | 数值 |
|------|------|
| 链码 mocha 用例数 | 77 → **85**（+8） |
| 后端 pytest 用例数 | 142 → **149**（+7） |
| 上传病历奖励准确度 | **100%**（每次上传 uploaderHospital 准确 +5） |
| 余额不足转账拒绝率 | **100%**（链码层守卫） |
| 自转拒绝率 | **100%**（链码 + 后端双层守卫） |

## 五、反思

- 选用 **uploaderHospital 字符串** 作为医院的积分账户 ID，**str(user.id)** 作为患者的账户 ID —— 这与链码内已有的字符串标识对齐，避免再引入一套用户 ID 映射。生产场景下可以统一为去中心化身份（DID）。
- `_creditMint(ctx, ...)` 是链码内部辅助方法（不暴露为 contract function），让业务流程能在同一个交易里完成"业务动作 + 积分奖励"。这是 Fabric 单交易原子性的天然用法。
- `CreditTransfer` 的双方 `putState` 在同一交易内完成 → Fabric endorsement 模型保证原子性：要么两边都改、要么都不改。这是经典 FT 实现。
- 风险：当前积分账户没有"销毁"方法（如违规罚没）；如果运营需要可以再增加 `CreditBurn`（仅 Org1MSP）。本迭代故意保留最小可用范围。
- CouchDB 流水索引 `indexCreditLedger` 按 createdAt 排序；如果需要按 fromUserId 范围查询，需要补一个 partial index。

## 六、Done Definition

- [x] 链码 mocha：77 + 8 = **85 条**全部通过
- [x] 后端 pytest：142 + 7 = **149 条**全部通过
- [x] commit message：`迭代 13：数据共享积分（FT）+ 经济激励闭环`

---

## 五次升级总结

五次升级累计达成：

| # | 主题 | 链码方法增量 | 后端测试增量 |
|---|------|------------|------------|
| 9 | Merkle 批量锚定 | +4 (Anchor/Get/Verify/List) | +10 |
| 10 | 链上多签治理 | +6 (Propose/Approve/Reject/Execute/Get/List) | +9 |
| 11 | 链上紧急冻结 + 治理解冻 | +2 (Freeze/Unfreeze) + 2 处守卫 | +7 |
| 12 | 链码 v2 升级 + 状态迁移 | +4 (GetSchemaVersion / MigrateRecords / MigrateRequests / QueryByCategory) | +9 |
| 13 | 数据共享积分（FT） | +4 (Mint/Transfer/Balance/History) + 3 处自动 mint | +7 |
| 合计 | 5 次升级 | **+20 链码方法 / +6 守卫** | **+42 pytest 用例** |

**链码 mocha**：49 → **85** （+36 用例）
**后端 pytest**：107 → **149** （+42 用例）

五次升级完整覆盖：**哈希聚合存证 / 链上多签治理 / 合约组合 / 链码生命周期升级 / 链上 FT 账本**。
