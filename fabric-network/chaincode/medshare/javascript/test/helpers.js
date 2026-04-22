"use strict";

const sinon = require("sinon");

/**
 * 为链码测试构造 mock Context。
 * 模拟 ctx.stub 的最小 API：getState / putState / getTxID。
 * 用一个内存 Map 充当世界状态。
 */
function makeMockContext({ txId = "tx-test-0001", existingState = {} } = {}) {
  const state = new Map(
    Object.entries(existingState).map(([k, v]) => [
      k,
      typeof v === "string" ? Buffer.from(v) : Buffer.from(JSON.stringify(v))
    ])
  );

  const stub = {
    _state: state,
    _events: [],
    getState: sinon.stub().callsFake(async (key) => {
      return state.has(key) ? state.get(key) : Buffer.from("");
    }),
    putState: sinon.stub().callsFake(async (key, value) => {
      state.set(key, Buffer.from(value));
    }),
    deleteState: sinon.stub().callsFake(async (key) => {
      state.delete(key);
    }),
    getTxID: sinon.stub().returns(txId),
    setEvent: sinon.stub().callsFake((name, payload) => {
      stub._events.push({ name, payload });
    })
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
