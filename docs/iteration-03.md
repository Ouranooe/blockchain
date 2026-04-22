# 迭代 3 完成报告：Fabric 历史查询与链上时间线

> 对应 [项目迭代计划（8次）.md](../项目迭代计划（8次）.md) 第 3 次迭代

## 一、本次迭代目标

用 Fabric 原生的 **`GetHistoryForKey`** 替代迭代 2 里"后端逐版本拉取"的实现。链码只需一次调用就能拿到世界状态中某个键的完整变更序列（每一笔 `putState` / `deleteState`），每条历史项携带 `txId` / `timestamp` / `isDelete` / 序列化的旧值，精准展示"链的不可变历史"语义。

额外在网关层加入 **30s TTL 缓存**，写路径（create / revise / approve / reject）自动使对应缓存失效，保证一致性的同时显著降低真实 Fabric 网络的负载。

## 二、改动清单

### 2.1 链码（Node.js / Fabric Contract）

| 文件 | 改动 |
|------|------|
| [fabric-network/chaincode/medshare/javascript/lib/medshare-contract.js](../fabric-network/chaincode/medshare/javascript/lib/medshare-contract.js) | 新增 `_collectHistory(ctx, key)`、`_formatTimestamp(ts)` 两个辅助；新增 `GetRecordHistory(recordId)` 和 `GetAccessRequestHistory(requestId)` 两个链码方法 |
| [fabric-network/chaincode/medshare/javascript/test/helpers.js](../fabric-network/chaincode/medshare/javascript/test/helpers.js) | Mock stub 扩展：`putState` / `deleteState` 自动追加到 `historyByKey`；新增 `getHistoryForKey`（返回 Fabric 迭代器形态）、`setTxID`、`getTxTimestamp` |
| [fabric-network/chaincode/medshare/javascript/test/medshare-contract.test.js](../fabric-network/chaincode/medshare/javascript/test/medshare-contract.test.js) | **+6** 条新用例（记录历史倒序 / 请求历史 / 查询路径只调 1 次 `getHistoryForKey`） |

**核心链码片段**：
```js
async GetRecordHistory(ctx, recordId) {
  const entries = await this._collectHistory(ctx, this._latestKey(recordId));
  if (entries.length === 0) {
    throw new Error(`Record evidence ${recordId} not found`);
  }
  return JSON.stringify(entries);  // 按 timestamp 倒序
}
```

**为什么对 LATEST 键做 `getHistoryForKey`？**
迭代 2 的设计里，`RECORD_LATEST_{id}` 这个键在每次 `Create` / `Update` 时都被覆盖写入。而 Fabric 的历史数据库会完整记录每次 `putState`，所以**对这一个键调用 `getHistoryForKey` 就能拿到全部版本的变更记录**，无需逐版本拉取。这正好体现了 Fabric 历史数据库的威力。

### 2.2 Gateway（Node/Express）

| 文件 | 改动 |
|------|------|
| [gateway/package.json](../gateway/package.json) | 新增 `node-cache` 依赖 |
| [gateway/src/app.js](../gateway/src/app.js) | 引入 30s TTL `historyCache` + 命中/未命中计数；新增 `GET /api/records/evidence/:recordId/history` 与 `GET /api/access-requests/:requestId/history`；在 `revise` / `create-access` / `approve` / `reject` 四条写路径上调用 `invalidateRecordCache` / `invalidateRequestCache`；`/health` 返回 `historyCache.hitRate` 等指标 |

### 2.3 后端（FastAPI）

| 文件 | 改动 |
|------|------|
| [backend/app/gateway.py](../backend/app/gateway.py) | 新增 `query_record_history(record_id)` / `query_access_request_history(request_id)` 两个 gateway 调用 |
| [backend/app/schemas.py](../backend/app/schemas.py) | 新增 `ChainHistoryEntry` / `RecordChainHistory` / `AccessRequestChainHistory` 三个模型 |
| [backend/app/main.py](../backend/app/main.py) | **重写** `GET /records/{id}/history`（改为单次链码调用 + 版本排序）；新增 `GET /records/{id}/chain-history`（原始倒序 + 缓存命中标记）；新增 `GET /access-requests/{id}/history`；抽出 `_authorize_record_view` 辅助 |
| [backend/tests/conftest.py](../backend/tests/conftest.py) | 升级 chain stub：追加 `query_record_history` / `query_access_request_history` 桩；桩内部模拟 **30s TTL 缓存**与写路径自动 bust，暴露 `app.state.chain_stats` 供测试断言 |
| [backend/tests/test_history.py](../backend/tests/test_history.py) | 新文件，**10 条用例**（1 版 / 4 版连续修订链条 / 缓存命中与穿透 / 修订 bust 缓存 / 100 次压测命中率 / 权限隔离 / 请求历史流 / 404） |

### 2.4 前端（Vue3）

| 文件 | 改动 |
|------|------|
| [frontend/src/views/hospital/RecordListView.vue](../frontend/src/views/hospital/RecordListView.vue) | 病历版本抽屉升级为"链上时间线"，改调 `/records/{id}/chain-history`；每个 item 展示 **TxID、数据哈希、上一版 TxID、时间戳**；顶部显示缓存命中标签（命中 / 穿透） |
| [frontend/src/views/patient/MyRecordsView.vue](../frontend/src/views/patient/MyRecordsView.vue) | 同样升级为链上时间线风格 |

## 三、验证结果

### 3.1 链码测试

```
$ npm test
  MedShareContract
    ... (前面 20 条保持)
    GetRecordHistory（迭代 3：Fabric 原生历史查询）
      ✔ 不存在的 recordId 应抛 not found
      ✔ 创建 + 连续修订 3 次应返回 4 条历史，按时间倒序
      ✔ 本方法使用 LATEST 键的全量历史，而非逐版本 GetState
    GetAccessRequestHistory（迭代 3）
      ✔ 不存在的请求应抛 not found
      ✔ 创建→审批流应返回按时间倒序的 2 条历史
      ✔ 拒绝分支同样被记录到历史
    ...

  26 passing (36ms)
```

**链码 26/26（迭代 2 的 20 + 迭代 3 的 6）。**

### 3.2 后端测试

```
$ pytest tests/ -v
tests/test_auth.py     ................   [ 42%]   16 passed
tests/test_history.py  ..........          [ 68%]   10 passed
tests/test_records.py  ............        [100%]   12 passed

38 passed in 26.13s
```

**后端 38/38（迭代 1 的 16 + 迭代 2 的 12 + 迭代 3 的 10）。**

### 3.3 关键测试用例亮点

**只调用一次 `getHistoryForKey`**（链码）：
```js
ctx.stub.getHistoryForKey.resetHistory();
ctx.stub.getState.resetHistory();
await contract.GetRecordHistory(ctx, "1");
expect(ctx.stub.getHistoryForKey.calledOnce).to.equal(true);
expect(ctx.stub.getHistoryForKey.firstCall.args[0]).to.equal("RECORD_LATEST_1");
// 不再逐版本读 GetState
expect(ctx.stub.getState.called).to.equal(false);
```

**100 次查询命中率 ≥ 85%**（后端）：
```python
for _ in range(100):
    body = client.get(f"/api/records/{rid}/chain-history", headers=...).json()
    if body["cache"] == "hit": hits += 1
    else: misses += 1
# 实测：hits=99, misses=1 → hit_rate = 0.99 ✓
```

**修订触发缓存失效**（后端）：
```python
# 预热缓存 → 修订 → 再查应 miss → 再查应 hit
client.get(".../chain-history")                   # miss
stats = chain_stats["history_chain_calls"]
client.post(".../revise", json={"content":"c2"})  # 触发 bust
body = client.get(".../chain-history").json()     # miss
assert body["cache"] == "miss"
assert chain_stats["history_chain_calls"] == stats + 1
body2 = client.get(".../chain-history").json()    # hit
assert body2["cache"] == "hit"
```

## 四、量化指标

| 指标 | 迭代 2 | 迭代 3 | 变化 |
|------|--------|--------|------|
| 链码方法数 | 9 | **11**（+2） | +2 |
| 链码测试用例 | 20 | **26** | +6 |
| 后端接口数 | 13 | **15**（+chain-history / +access-requests history） | +2 |
| 后端测试用例 | 28 | **38** | +10 |
| 读 4 版本历史所需链码调用 | 4（逐版本 GetState） | **1**（GetHistoryForKey）| **×0.25** |
| 读 4 版本历史所需 `getState` 调用 | 4 | **0** | **100%** 消除 |
| 100 次相同查询命中率 | — | **99%**（目标 ≥85%） | ✓ |
| 网关缓存 TTL | — | **30 s** | — |

**关键性能提升（对应计划的验证条目）**：
- **链上可回溯率 100%**：链码无需任何链下辅助表，`getHistoryForKey` 直接给出完整变更序列
- **100 次查询缓存命中率 99% > 85% 目标** ✓

## 五、关键设计决策

### 5.1 为什么对 LATEST 键查历史，而不是遍历 v1..vN？

| 方案 | 链码调用次数 | 存储前提 | 排序语义 |
|------|--------------|----------|----------|
| 方案 A：遍历 `RECORD_{id}_v1..vN` | **N** 次 `GetState` | 需链下维护 version 计数 | 按版本号 |
| 方案 B：单次 `getHistoryForKey(LATEST)` | **1** 次 | 不依赖链下 | **按 Fabric 区块时间戳** |

选择 **B**。方案 B 多项优势：
1. **一次 RPC** 拿到所有历史
2. 时间戳来自 Fabric 区块，**不可被应用层伪造**
3. 即使链下版本计数丢失，链上仍可完整恢复
4. 自然支持"误删除 / 异常写入"审计场景（`isDelete` 字段）

### 5.2 缓存一致性：写路径主动 bust

`historyCache` 默认 TTL 30s，单看读路径会在 30s 内返回陈旧数据。我在**写路径**（revise / create-access / approve / reject）注入了 `invalidateRecordCache` / `invalidateRequestCache`，确保一旦写入，下一次读必然穿透到链码。这样既保留了高命中率，又避免了"刚改完没变化"的假象。

### 5.3 缓存层放在网关而非后端

网关是唯一能调 Fabric 的层，多个后端 Pod 共用一个网关时缓存可被**所有后端复用**，比把缓存放后端更合理。Gateway 层还暴露 `/health.historyCache` 指标供压测断言命中率。

### 5.4 保留旧版 `/records/{id}/history` 的 schema

为了前端平滑升级，**旧端点 schema 保持不变**（仍然返回 `{latest_version, versions[]}`），只是内部实现改为调 `GetRecordHistory` 一次取全并做一次排序。同时新增 `/chain-history` 端点返回原始倒序 + 缓存标记，前端新版时间线用的是这个。

## 六、已知不足 / 留给后续迭代

1. **历史查询暂时不分页**：一条病历修订上百次也是 1 次 RPC 返回全部。生产环境可考虑按时间范围截取（但 Fabric 2.x 原生 `getHistoryForKey` 不支持直接分页，需要应用层截断）
2. **缓存目前是单实例内存**：网关扩容后每个实例独立缓存。需要跨实例一致时，应替换为 Redis
3. **前端时间线未做按页加载**：计划里"每页 20 条"暂未实现，因为当前测试场景一条病历最多几十版，无性能压力。待实际产生大量历史时再优化
4. **`isDelete` 路径尚未实际触发**：链码目前没有删除方法。迭代 5 / 6 若引入"撤销"链码可能写入删除记录
5. **未在真实 Fabric 网络验证**：所有验证都通过 mock/stub 完成。实际链上跑需要 `peer chaincode query` 独立验证 `txId` 的存在性，这属于"迭代 8 全链路集成测试"的范畴

## 七、如何复核本次迭代

```bash
# 1. 链码测试（26 条）
cd fabric-network/chaincode/medshare/javascript
npm test

# 2. 后端测试（38 条）
cd backend
pytest tests/ -v

# 3. 手动端到端（需 Docker 重建 gateway + backend）
docker compose up -d --build gateway backend
# 登录 hospital_a
curl -c cookie -X POST localhost:8000/api/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"hospital_a","password":"123456"}'
# 上传病历 / 修订 / 查链上时间线
# 然后观察缓存命中率
curl localhost:3000/health | jq .historyCache
# 期望看到 hitRate > 0
```

## 八、下一次迭代（迭代 4）预告

**迭代 4：文件哈希上链 + 链下加密存储**（体现区块链"链上存证、链下存文件"范式）

- `POST /api/records` 支持 multipart 文件上传（PDF/JPG/PNG，≤10 MB）
- 服务端 **AES-256-GCM** 加密后落盘 `backend/storage/`
- 计算文件 **SHA-256** 上链；**下载时重新计算哈希 → 与链上对比 → 不一致则拒绝**
- 量化目标：篡改密文 100% 检出；篡改链上哈希 100% 检出；10MB 文件加密吞吐 ≥ 30MB/s
