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

  // --- 迭代 7：Mango selector 匹配器 ---
  function _matchOperator(fieldValue, opObj) {
    if (opObj === null || typeof opObj !== "object") {
      return fieldValue === opObj;
    }
    for (const [op, arg] of Object.entries(opObj)) {
      switch (op) {
        case "$eq":
          if (fieldValue !== arg) return false;
          break;
        case "$ne":
          if (fieldValue === arg) return false;
          break;
        case "$gt":
          if (!(fieldValue > arg)) return false;
          break;
        case "$gte":
          if (!(fieldValue >= arg)) return false;
          break;
        case "$lt":
          if (!(fieldValue < arg)) return false;
          break;
        case "$lte":
          if (!(fieldValue <= arg)) return false;
          break;
        case "$in":
          if (!Array.isArray(arg) || !arg.includes(fieldValue)) return false;
          break;
        default:
          // 未实现算子 → 保守拒绝
          return false;
      }
    }
    return true;
  }

  function _matchSelector(obj, selector) {
    if (!selector || typeof selector !== "object") return true;
    for (const [k, v] of Object.entries(selector)) {
      if (k === "$and") {
        if (!Array.isArray(v)) return false;
        if (!v.every((sub) => _matchSelector(obj, sub))) return false;
        continue;
      }
      if (k === "$or") {
        if (!Array.isArray(v)) return false;
        if (!v.some((sub) => _matchSelector(obj, sub))) return false;
        continue;
      }
      const fieldValue = obj == null ? undefined : obj[k];
      if (v !== null && typeof v === "object" && !Array.isArray(v)) {
        if (!_matchOperator(fieldValue, v)) return false;
      } else {
        if (fieldValue !== v) return false;
      }
    }
    return true;
  }

  function _sortResults(list, sort) {
    if (!sort || !Array.isArray(sort)) return list;
    const sorted = list.slice();
    sorted.sort((a, b) => {
      for (const rule of sort) {
        const [field, dir] = Object.entries(rule)[0] || [];
        if (!field) continue;
        const av = a.value ? a.value[field] : undefined;
        const bv = b.value ? b.value[field] : undefined;
        if (av === bv) continue;
        const mul = dir === "desc" ? -1 : 1;
        return av < bv ? -1 * mul : 1 * mul;
      }
      return 0;
    });
    return sorted;
  }

  function _runRichQuery(queryString) {
    let q;
    try {
      q = JSON.parse(queryString);
    } catch {
      return [];
    }
    const selector = q.selector || {};
    const matches = [];
    for (const [key, buf] of state.entries()) {
      let obj;
      try {
        obj = JSON.parse(buf.toString("utf8"));
      } catch {
        continue;
      }
      if (_matchSelector(obj, selector)) {
        matches.push({ key, value: obj });
      }
    }
    return _sortResults(matches, q.sort);
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
    // 迭代 7：Mock CouchDB 富查询（支持 Mango selector 子集：
    //   直接相等、$eq、$gte、$lte、$gt、$lt、$in、$and、$or）
    getQueryResult: sinon.stub().callsFake(async (queryString) => {
      const results = _runRichQuery(queryString);
      let idx = 0;
      return {
        next: async () => {
          if (idx >= results.length) return { value: null, done: true };
          const v = results[idx];
          idx += 1;
          return {
            value: { key: v.key, value: Buffer.from(JSON.stringify(v.value)) },
            done: idx >= results.length
          };
        },
        close: async () => {}
      };
    }),
    getQueryResultWithPagination: sinon.stub().callsFake(
      async (queryString, pageSize, bookmark) => {
        const all = _runRichQuery(queryString);
        const start = bookmark ? parseInt(bookmark, 10) || 0 : 0;
        const end = Math.min(all.length, start + Number(pageSize || 20));
        const page = all.slice(start, end);
        let idx = 0;
        const iterator = {
          next: async () => {
            if (idx >= page.length) return { value: null, done: true };
            const v = page[idx];
            idx += 1;
            return {
              value: { key: v.key, value: Buffer.from(JSON.stringify(v.value)) },
              done: idx >= page.length
            };
          },
          close: async () => {}
        };
        const metadata = {
          fetchedRecordsCount: page.length,
          bookmark: end < all.length ? String(end) : ""
        };
        return { iterator, metadata };
      }
    ),
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
