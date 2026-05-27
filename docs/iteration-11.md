# 迭代 11 设计与实现报告：链上紧急冻结 + 治理解冻闭环

> 对应 [项目迭代计划（5次升级）.md](../项目迭代计划（5次升级）.md) 第 11 次迭代
> 主线：患者发现病历被滥用时**一键紧急冻结**链上记录；解冻必须通过迭代 10 的**链上多签治理**

## 一、本次迭代目标

迭代 5 的 ABAC 已经做了"过期 / 次数 / MSP 绑定"三重链上守卫；本迭代加上 **冻结/解冻** 的合约组合：

- **冻结门槛低、动作快**（患者一人即可）
- **解冻门槛高**（必须有迭代 10 的治理动作 EXECUTED 才能调用）
- **冻结期间所有写动作 / 读动作均被链码层拒绝**（不依赖后端）

体现 Fabric 的两个关键性质：
1. **链上不可绕过**：即使后端误调用，链码层也拒绝
2. **合约组合**：迭代 11 解冻 → 引用迭代 10 治理 TxID，链上闭环验证

## 二、技术设计

### 2.1 数据结构变化
RecordEvidence 新增字段：
```js
{
  ...原字段,
  frozen: false,         // 默认 false
  frozenAt: "",          // ISO 时间
  freezeTxId: "",        // 冻结 TxID
  freezeReasonHash: "",  // 患者写入原因哈希
  unfreezeTxId: "",      // 解冻 TxID
  unfreezeGovTxId: "",   // 解冻引用的治理 actionId 的 ExecuteTxID
}
```

老数据兼容：在 `_normalizeRecord(obj)` 给缺省字段填 false / ""。

### 2.2 链码方法

| 方法 | 入参 | 调用方约束 |
|------|------|------------|
| `FreezeRecord(recordId, patientId, reasonHash, frozenAt)` | id / patient / hash / 时间 | `request.patientId == patientId`（按业务上下文从 latest record 读 patientId 校验） |
| `UnfreezeRecord(recordId, governanceActionId, unfrozenAt)` | id / 治理动作 ID / 时间 | 链码内部读取 `GOV_{actionId}`，要求 `status == EXECUTED` 且 `kind == UNFREEZE_RECORD` 且 `payload.recordId == recordId` |

### 2.3 守卫拦截
在以下方法的开头加 `if (record.frozen) throw ...`：
- `UpdateMedicalRecordEvidence`
- `AccessRecord`
- 富查询 / 历史读 / `GetRecordLatest` 不拒（用户应能看到"被冻结"标记）

### 2.4 后端
- 表 `medical_records` 加 `frozen` / `frozen_at` / `freeze_tx_id` / `unfreeze_tx_id` / `unfreeze_gov_tx_id`
- API：
  - `POST /api/records/{id}/freeze`（患者）
  - `POST /api/records/{id}/unfreeze`（admin，body: `{governance_action_id}`）
- 事件 `RecordFrozen` / `RecordUnfrozen` → audit_events + WebSocket

### 2.5 前端
- 患者侧：MyRecordsView 每条记录右上加红色"紧急冻结"按钮，冻结后变灰色"已冻结"角标
- admin 侧：治理审批页 + "解冻"按钮（点开自动带入 actionId）

## 三、测试

### 3.1 链码 mocha（≥6 条）
- 患者冻结 → record.frozen == true
- 非归属患者冻结 → 抛错
- 冻结后 UpdateMedicalRecordEvidence → 抛错（关键守卫）
- 冻结后 AccessRecord → 抛错（关键守卫）
- 解冻无 governanceActionId → 抛错
- 治理动作 EXECUTED 且 kind 匹配 → 解冻成功，后续操作恢复

### 3.2 后端 pytest（≥6 条）
- 患者冻结 200，record 表 frozen=1
- 医院修订冻结记录 502（链码拒绝）
- admin 无 actionId 解冻 400
- 走完迭代 10 的治理流程后 admin 解冻 200
- 患者解冻自己病历 403
- 审计事件 `RecordFrozen` 落库 + WebSocket 推

## 四、量化指标

| 指标 | 数值 |
|------|------|
| 链码 mocha 用例数 | 63 → **70**（+7） |
| 后端 pytest 用例数 | 126 → **133**（+7） |
| 冻结后写动作拒绝率 | **100%**（UpdateMedicalRecordEvidence / AccessRecord 全部抛错） |
| 解冻必须的链上前置 | **EXECUTED 治理动作 + kind 匹配 + recordId 匹配 三重校验** |

## 五、反思

- 本迭代和迭代 10 形成了 **真正的合约组合**：解冻这个动作不能由任何单一角色触发，必须先在链上完成多 MSP 治理（迭代 10）→ 执行的 TxID 作为"令牌"被迭代 11 的 `UnfreezeRecord` 校验。这种"以链上状态作为权限令牌"的模式是 Fabric 真实工程极其常见的设计。
- `_normalizeRecord(obj)` 让迭代 1-10 写入的老 record（没有 frozen 字段）依然可读 —— 链码层做兼容读，零迁移成本。
- 风险：本迭代 `AccessRecord` 添加了对 record 的额外 getState 调用（从 ≤3 升到 ≤4）。如果将来需要严格控制 gas/状态读取，可以把 frozen 标志冗余进 access request 的镜像里。当前实现取"代码可读优先"。
- 前端尚未引入冻结按钮（计划文档已说明）；后续可以补一个简单的红色按钮。

## 六、Done Definition

- [x] 链码 mocha：63 + 7 = **70 条**全部通过
- [x] 后端 pytest：126 + 7 = **133 条**全部通过
- [x] commit message：`迭代 11：链上紧急冻结 + 治理解冻闭环`
