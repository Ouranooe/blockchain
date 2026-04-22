"use strict";

const sinon = require("sinon");

/**
 * 为链码测试构造 mock Context。
 *
 * stub 模拟的最小 API：
 *   - getState / putState / deleteState  基于内存 Map 的世界状态
 *   - getTxID / setTxID                  可变的当前交易 ID
 *   - setEvent                           事件捕获
 *   - getHistoryForKey                   迭代器形式的键历史（迭代 3 新增）
 *   - getTxTimestamp                     权威时间戳（迭代 5 预留）
 */
function makeMockContext({ txId = "tx-test-0001", existingState = {} } = {}) {
  const state = new Map(
    Object.entries(existingState).map(([k, v]) => [
      k,
      typeof v === "string" ? Buffer.from(v) : Buffer.from(JSON.stringify(v))
    ])
  );
  const historyByKey = new Map(); // key -> [{txId, timestamp, isDelete, value: Buffer}]
  let currentTxId = txId;
  // 从一个固定基准秒开始单调递增，模拟区块时间戳单调性
  let txSeconds = 1_714_000_000;

  function _appendHistory(key, buffer, isDelete) {
    const entry = {
      txId: currentTxId,
      timestamp: { seconds: { low: txSeconds, high: 0 }, nanos: 0 },
      isDelete,
      value: Buffer.from(buffer)
    };
    if (!historyByKey.has(key)) historyByKey.set(key, []);
    historyByKey.get(key).push(entry);
    txSeconds += 1;
  }

  const stub = {
    _state: state,
    _events: [],
    _history: historyByKey,
    getState: sinon.stub().callsFake(async (key) => {
      return state.has(key) ? state.get(key) : Buffer.from("");
    }),
    putState: sinon.stub().callsFake(async (key, value) => {
      state.set(key, Buffer.from(value));
      _appendHistory(key, value, false);
    }),
    deleteState: sinon.stub().callsFake(async (key) => {
      state.delete(key);
      _appendHistory(key, Buffer.from(""), true);
    }),
    getTxID: sinon.stub().callsFake(() => currentTxId),
    setTxID: (newTxId) => {
      currentTxId = newTxId;
    },
    setEvent: sinon.stub().callsFake((name, payload) => {
      stub._events.push({ name, payload });
    }),
    getHistoryForKey: sinon.stub().callsFake(async (key) => {
      const entries = (historyByKey.get(key) || []).slice();
      let idx = 0;
      return {
        next: async () => {
          if (idx >= entries.length) {
            return { value: null, done: true };
          }
          const value = entries[idx];
          idx += 1;
          const done = idx >= entries.length;
          return { value, done };
        },
        close: async () => {}
      };
    }),
    getTxTimestamp: sinon.stub().callsFake(() => ({
      seconds: { low: txSeconds, high: 0 },
      nanos: 0
    }))
  };

  const clientIdentity = {
    getMSPID: sinon.stub().returns("Org1MSP"),
    getID: sinon.stub().returns("x509::CN=test::CN=ca")
  };

  return { stub, clientIdentity };
}

function readState(ctx, key) {
  const bytes = ctx.stub._state.get(key);
  if (!bytes) return null;
  return JSON.parse(bytes.toString("utf8"));
}

module.exports = { makeMockContext, readState };
