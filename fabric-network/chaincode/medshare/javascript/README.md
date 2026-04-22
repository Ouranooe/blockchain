# MedShare 链码（迭代 1 新增测试框架）

## 目录

```
chaincode/medshare/javascript/
├── index.js
├── lib/
│   └── medshare-contract.js      # 链码合约主体
├── test/
│   ├── helpers.js                # mock Context 工厂
│   └── medshare-contract.test.js # 迭代 1 单元测试
├── .mocharc.json
├── package.json
└── README.md
```

## 运行测试

```bash
cd fabric-network/chaincode/medshare/javascript
npm install           # 首次需安装 devDependencies（mocha/chai/sinon/nyc）
npm test              # 跑所有单元测试
npm run coverage      # 用 nyc 产生覆盖率报告（输出到 coverage/）
```

## 测试策略

链码单元测试**不启动真实 Fabric 网络**，而是用 `sinon` 构造 `ctx.stub` 的 mock。
- `getState/putState` 用内存 Map 模拟世界状态
- `getTxID` 返回可配置的固定值
- `setEvent` 捕获到 `stub._events`，便于后续迭代（迭代 6）断言事件

## 断言覆盖面（迭代 1）

| 分类 | 用例 |
|------|------|
| CreateMedicalRecordEvidence | 首次创建成功、重复抛错 |
| GetMedicalRecordEvidence    | 不存在抛错、存在时正确解析 |
| CreateAccessRequest         | 首次创建、重复抛错、status 默认值 |
| ApproveAccessRequest        | 状态转换、reviewTxId 写入、不存在抛错 |
| RejectAccessRequest         | 状态转换、不存在抛错 |
| QueryAccessRequest          | 不存在抛错、存在正确读取 |
| 端到端                      | 终态重复审批当前不拒（迭代 5 会收紧）、txId 与 ctx 绑定 |

## 后续迭代将扩展

- **迭代 2**：版本链相关方法（UpdateRecord、GetRecordLatest、GetRecordVersion）
- **迭代 3**：GetHistoryForKey 历史查询 mock 测试
- **迭代 5**：ClientIdentity 校验、过期 / 剩余次数控制
- **迭代 6**：setEvent 事件触发断言
- **迭代 7**：CouchDB 富查询 mock（getQueryResultWithPagination）
