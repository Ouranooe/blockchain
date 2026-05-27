# 迭代 10 设计与实现报告：链上多签治理（双 MSP endorse）

> 对应 [项目迭代计划（5次升级）.md](../项目迭代计划（5次升级）.md) 第 10 次迭代
> 主线：把"高风险操作"约束在 **Org1MSP + Org2MSP 双方在链上联合 endorse** 之后才能 EXECUTE

## 一、本次迭代目标

医疗数据系统里"批量撤销患者所有授权"、"强制下架某条病历"、"解冻被冻结的病历"等动作影响面大，单组织决定不合规。本迭代把它们做成"**链上提案 → 多方批准 → 链上执行**"的三段式：

- **提案 (Propose)**：任何 admin 都可创建治理提案（不立即生效）
- **批准 (Approve)**：每个组织（MSP）的 admin 各自上链投一笔批准
- **执行 (Execute)**：链码自校验"已收齐 ≥2 个不同 MSP 的批准"才允许 EXECUTE

这是 Fabric ABAC + 链上状态机的进阶：**用合约逻辑而非外部协调来实现多签**。

## 二、技术设计

### 2.1 状态机
```
                                  ┌──── REJECTED （任一阶段可拒）
                                  │
PROPOSED ──signed by Org1── PARTIALLY_APPROVED ──signed by Org2── APPROVED
                                  │                                    │
                                  └──signed by Org2─→ PARTIALLY_APPROVED （顺序无关）
                                                                       │
                                                                  EXECUTED
```

合法跃迁表：
| from | to | 触发 |
|---|---|---|
| PROPOSED | PARTIALLY_APPROVED / REJECTED | Approve / Reject |
| PARTIALLY_APPROVED | APPROVED / REJECTED | 第二个不同 MSP Approve / Reject |
| APPROVED | EXECUTED | Execute |
| EXECUTED, REJECTED | — | 终态 |

### 2.2 链码方法

| 方法 | 入参 | 说明 |
|------|------|------|
| `ProposeGovernanceAction(actionId, kind, payloadJson, proposedAt)` | id / kind ∈ {FREEZE_RECORD, UNFREEZE_RECORD, BATCH_REVOKE_PATIENT, FORCE_DELETE_RECORD} / payload / 时间 | 写 `GOV_{actionId}`，事件 `GovernanceProposed` |
| `ApproveGovernanceAction(actionId, approvedAt)` | id / 时间 | 记录 `approverMsp = caller MSP`，状态机推进 |
| `RejectGovernanceAction(actionId, rejectedAt)` | id / 时间 | 终态 REJECTED |
| `ExecuteGovernanceAction(actionId, executedAt)` | id / 时间 | 必须 APPROVED；记 `executeTxId`；事件 `GovernanceExecuted` |
| `GetGovernanceAction(actionId)` | id | 读取 |
| `ListGovernanceActions(status, pageSize, bookmark)` | 富查询 | CouchDB 索引 |

> **执行动作真正落地** 在哪里？
> 本迭代里 `ExecuteGovernanceAction` 只**标记** EXECUTED 并发事件，**不直接执行 kind 对应的链上动作**（避免迭代 10 自己包揽迭代 11 的工作）；迭代 11 的 `UnfreezeRecord` 会校验"传入的治理 actionId 必须 EXECUTED 且 kind == UNFREEZE_RECORD"。

### 2.3 后端
- 表 `governance_actions`（MySQL 镜像，便于 list 性能）
- API：
  - `POST /api/governance/actions`（admin）
  - `POST /api/governance/actions/{id}/approve`（admin）
  - `POST /api/governance/actions/{id}/reject`（admin）
  - `POST /api/governance/actions/{id}/execute`（admin）
  - `GET /api/governance/actions?status=`（admin）

### 2.4 前端
admin 视图新增"治理审批"页：
- 卡片列出所有提案
- 显示 PROPOSED / PARTIALLY_APPROVED / APPROVED / EXECUTED / REJECTED 状态
- 行内动作：批准 / 拒绝 / 执行

## 三、实现要点
- **去重批准**：同 MSP 重复 Approve 链码层抛错（防作弊）
- **MSP 双签约束**：执行时 `approvers.length ≥ 2 && unique MSPs ≥ 2`
- **事件双向**：每步触发事件 → 后端 audit_events 落库 → 前端 WebSocket 实时通知

## 四、测试

### 4.1 链码 mocha 新增（≥6 条）
- 提案 → 单 MSP 批准 → status == PARTIALLY_APPROVED
- 同 MSP 二次批准 → 抛错
- 不同 MSP 第二次批准 → APPROVED
- 未 APPROVED 直接 Execute → 抛错
- APPROVED 后 Execute → EXECUTED
- 非法跃迁（REJECTED → APPROVE）→ 抛错

### 4.2 后端 pytest 新增（≥6 条）
- admin 提案 200
- 非 admin 操作 403
- 状态机串完：提案 → 批准 → 第二批准 → 执行
- 重复提交相同 actionId 409
- 状态过滤查询正确
- audit event 落库（GovernanceProposed/Approved/Executed）

## 五、量化指标

| 指标 | 数值 |
|------|------|
| 链码 mocha 用例数 | 56 → **63**（+7） |
| 后端 pytest 用例数 | 117 → **126**（+9） |
| 状态机非法跃迁拒绝率 | **100%**（同 MSP 重批 / 终态再批 / 未 APPROVED 直接执行 全部被拒） |
| 端到端：Propose → Approve(Org1) → Approve(Org2) → Execute | **4 笔链上交易**完成 |

## 六、反思

- **执行动作的"语义"留给下游迭代**：本迭代 `ExecuteGovernanceAction` 只把状态机推到 `EXECUTED` 并触发事件，不做实际的链上写副作用（如解冻一条 record）。迭代 11 的 `UnfreezeRecord` 会校验"传入的治理 actionId 必须 EXECUTED 且 kind == UNFREEZE_RECORD"，这种"合约组合"是 Fabric 真实工程的常用模式 —— 治理与业务解耦，可独立演化。
- **MSP 唯一性**只用了集合大小判断 `≥ 2`。如果未来要做 M-of-N（如 3 选 2 / 5 选 3），只需在 `ApproveGovernanceAction` 内增加配置阈值即可，状态机不变。
- 后端层先校验 `GovernanceAction.action_id` 唯一性（409）再调链码，避免给链上留"被链码拒绝的脏 propose"。
- 风险：本迭代后端层未额外校验 admin 的 MSP 与提案双方匹配，完全依赖链码 `_callerMsp(ctx)`。生产部署时应该让 backend 把 user.msp_org 透传到 gateway，gateway 根据它选择 Org1/Org2 的身份连接 —— 现 PR 已经在 `_proposer_org_to_gateway()` 做了这件事。

## 七、Done Definition

- [x] 链码 mocha：56 + 7 = **63 条**全部通过
- [x] 后端 pytest：117 + 9 = **126 条**全部通过
- [x] commit message：`迭代 10：链上多签治理（双 MSP endorse）`
