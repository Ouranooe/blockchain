"use strict";

const chai = require("chai");
const chaiAsPromised = require("chai-as-promised");
chai.use(chaiAsPromised);
const { expect } = chai;

const MedShareContract = require("../lib/medshare-contract");
const { makeMockContext, readState } = require("./helpers");

describe("MedShareContract", () => {
  let contract;
  let ctx;

  beforeEach(() => {
    contract = new MedShareContract();
    ctx = makeMockContext();
  });

  describe("CreateMedicalRecordEvidence", () => {
    it("首次创建应写入 LATEST 与 v1 两个键，version=1 且 previousTxId 为空", async () => {
      const raw = await contract.CreateMedicalRecordEvidence(
        ctx,
        "1",
        "2",
        "HospitalA",
        "deadbeef",
        "2026-04-22T00:00:00Z"
      );
      const evidence = JSON.parse(raw);
      expect(evidence.recordId).to.equal("1");
      expect(evidence.patientId).to.equal("2");
      expect(evidence.uploaderHospital).to.equal("HospitalA");
      expect(evidence.dataHash).to.equal("deadbeef");
      expect(evidence.version).to.equal(1);
      expect(evidence.previousTxId).to.equal("");
      expect(evidence.createdAt).to.equal("2026-04-22T00:00:00Z");
      expect(evidence.updatedAt).to.equal("2026-04-22T00:00:00Z");
      expect(evidence.txId).to.equal("tx-test-0001");

      // 双键写入：v1 和 LATEST 应一致
      expect(readState(ctx, "RECORD_1_v1")).to.deep.equal(evidence);
      expect(readState(ctx, "RECORD_LATEST_1")).to.deep.equal(evidence);
    });

    it("重复创建同一 recordId 应抛错", async () => {
      await contract.CreateMedicalRecordEvidence(
        ctx, "1", "2", "HospitalA", "deadbeef", "2026-04-22T00:00:00Z"
      );
      await expect(
        contract.CreateMedicalRecordEvidence(
          ctx, "1", "2", "HospitalA", "cafe", "2026-04-22T01:00:00Z"
        )
      ).to.be.rejectedWith(/already exists/);
    });
  });

  describe("GetMedicalRecordEvidence（向后兼容 -> LATEST）", () => {
    it("查询不存在的记录应抛错", async () => {
      await expect(
        contract.GetMedicalRecordEvidence(ctx, "999")
      ).to.be.rejectedWith(/not found/);
    });

    it("能读到已创建的最新版证据", async () => {
      await contract.CreateMedicalRecordEvidence(
        ctx, "1", "2", "HospitalA", "deadbeef", "2026-04-22T00:00:00Z"
      );
      const raw = await contract.GetMedicalRecordEvidence(ctx, "1");
      const evidence = JSON.parse(raw);
      expect(evidence.recordId).to.equal("1");
      expect(evidence.dataHash).to.equal("deadbeef");
      expect(evidence.version).to.equal(1);
    });
  });

  describe("UpdateMedicalRecordEvidence（版本链）", () => {
    beforeEach(async () => {
      await contract.CreateMedicalRecordEvidence(
        ctx, "1", "2", "HospitalA", "hash-v1", "2026-04-22T00:00:00Z"
      );
    });

    it("首次修订应产生 v2，previousTxId 指向 v1 的 txId", async () => {
      const ctx2 = makeMockContext({ txId: "tx-v2" });
      // 把 ctx 的状态拷贝到 ctx2（模拟链持续累积）
      for (const [k, v] of ctx.stub._state.entries()) {
        ctx2.stub._state.set(k, Buffer.from(v));
      }

      const raw = await contract.UpdateMedicalRecordEvidence(
        ctx2, "1", "hash-v2", "2026-04-22T10:00:00Z"
      );
      const ev = JSON.parse(raw);
      expect(ev.version).to.equal(2);
      expect(ev.previousTxId).to.equal("tx-test-0001"); // v1 的 txId
      expect(ev.dataHash).to.equal("hash-v2");
      expect(ev.createdAt).to.equal("2026-04-22T00:00:00Z"); // 原创建时间保留
      expect(ev.updatedAt).to.equal("2026-04-22T10:00:00Z");
      expect(ev.txId).to.equal("tx-v2");
      expect(ev.patientId).to.equal("2");            // 继承
      expect(ev.uploaderHospital).to.equal("HospitalA"); // 继承

      // v2 键存在、LATEST 指向 v2、v1 键仍保留原样
      expect(readState(ctx2, "RECORD_1_v2").version).to.equal(2);
      expect(readState(ctx2, "RECORD_LATEST_1").version).to.equal(2);
      expect(readState(ctx2, "RECORD_1_v1").version).to.equal(1);
      expect(readState(ctx2, "RECORD_1_v1").dataHash).to.equal("hash-v1");
    });

    it("连续修订 5 次应形成长度为 5 的版本链，previousTxId 指向前一版", async () => {
      // 基线：ctx 已有 v1
      const txIds = ["tx-test-0001"]; // v1 的 txId
      let currentState = new Map();
      for (const [k, v] of ctx.stub._state.entries()) {
        currentState.set(k, Buffer.from(v));
      }

      for (let v = 2; v <= 5; v++) {
        const txId = `tx-v${v}`;
        const stepCtx = makeMockContext({ txId });
        // 累积状态
        for (const [k, val] of currentState.entries()) {
          stepCtx.stub._state.set(k, Buffer.from(val));
        }
        await contract.UpdateMedicalRecordEvidence(
          stepCtx, "1", `hash-v${v}`, `2026-04-22T${String(v).padStart(2,"0")}:00:00Z`
        );
        txIds.push(txId);
        // 更新累积状态
        currentState = new Map();
        for (const [k, val] of stepCtx.stub._state.entries()) {
          currentState.set(k, Buffer.from(val));
        }
      }

      // 最终 LATEST 应为 v5
      const finalCtx = makeMockContext();
      for (const [k, val] of currentState.entries()) {
        finalCtx.stub._state.set(k, Buffer.from(val));
      }

      const latest = JSON.parse(await contract.GetRecordLatest(finalCtx, "1"));
      expect(latest.version).to.equal(5);
      expect(latest.dataHash).to.equal("hash-v5");

      // 回溯版本链：每版 previousTxId 应指向前一版 txId
      for (let v = 1; v <= 5; v++) {
        const raw = await contract.GetRecordVersion(finalCtx, "1", String(v));
        const ev = JSON.parse(raw);
        expect(ev.version).to.equal(v);
        expect(ev.txId).to.equal(txIds[v - 1]);
        const expectedPrev = v === 1 ? "" : txIds[v - 2];
        expect(ev.previousTxId).to.equal(expectedPrev);
      }
    });

    it("修订不存在的记录应抛错", async () => {
      await expect(
        contract.UpdateMedicalRecordEvidence(
          ctx, "999", "anyhash", "2026-04-22T10:00:00Z"
        )
      ).to.be.rejectedWith(/not found/);
    });
  });

  describe("GetRecordVersion", () => {
    it("查询已存在的指定版本成功", async () => {
      await contract.CreateMedicalRecordEvidence(
        ctx, "1", "2", "HospitalA", "hash-v1", "2026-04-22T00:00:00Z"
      );
      const raw = await contract.GetRecordVersion(ctx, "1", "1");
      expect(JSON.parse(raw).version).to.equal(1);
    });

    it("查询不存在的版本应抛错", async () => {
      await contract.CreateMedicalRecordEvidence(
        ctx, "1", "2", "HospitalA", "hash-v1", "2026-04-22T00:00:00Z"
      );
      await expect(
        contract.GetRecordVersion(ctx, "1", "99")
      ).to.be.rejectedWith(/version 99 not found/);
    });
  });

  describe("CreateAccessRequest", () => {
    it("首次创建应写入 PENDING 状态", async () => {
      const raw = await contract.CreateAccessRequest(
        ctx, "10", "1", "HospitalB", "reasonhash", "PENDING", "2026-04-22T00:00:00Z"
      );
      const request = JSON.parse(raw);
      expect(request.requestId).to.equal("10");
      expect(request.status).to.equal("PENDING");
      expect(request.reviewedAt).to.equal("");
      expect(request.createTxId).to.equal("tx-test-0001");

      const stored = readState(ctx, "REQ_10");
      expect(stored.status).to.equal("PENDING");
    });

    it("重复创建应抛错", async () => {
      await contract.CreateAccessRequest(
        ctx, "10", "1", "HospitalB", "reasonhash", "PENDING", "2026-04-22T00:00:00Z"
      );
      await expect(
        contract.CreateAccessRequest(
          ctx, "10", "1", "HospitalB", "reasonhash", "PENDING", "2026-04-22T00:00:00Z"
        )
      ).to.be.rejectedWith(/already exists/);
    });

    it("未指定 status 时默认为 PENDING", async () => {
      const raw = await contract.CreateAccessRequest(
        ctx, "11", "1", "HospitalB", "hash", "", "2026-04-22T00:00:00Z"
      );
      expect(JSON.parse(raw).status).to.equal("PENDING");
    });
  });

  describe("ApproveAccessRequest", () => {
    it("应把状态改为 APPROVED 并写入 reviewTxId", async () => {
      await contract.CreateAccessRequest(
        ctx, "10", "1", "HospitalB", "hash", "PENDING", "2026-04-22T00:00:00Z"
      );
      const raw = await contract.ApproveAccessRequest(
        ctx, "10", "2026-04-22T10:00:00Z"
      );
      const request = JSON.parse(raw);
      expect(request.status).to.equal("APPROVED");
      expect(request.reviewedAt).to.equal("2026-04-22T10:00:00Z");
      expect(request.reviewTxId).to.equal("tx-test-0001");
    });

    it("对不存在的申请审批应抛错", async () => {
      await expect(
        contract.ApproveAccessRequest(ctx, "999", "2026-04-22T10:00:00Z")
      ).to.be.rejectedWith(/not found/);
    });
  });

  describe("RejectAccessRequest", () => {
    it("应把状态改为 REJECTED", async () => {
      await contract.CreateAccessRequest(
        ctx, "10", "1", "HospitalB", "hash", "PENDING", "2026-04-22T00:00:00Z"
      );
      const raw = await contract.RejectAccessRequest(
        ctx, "10", "2026-04-22T10:00:00Z"
      );
      const request = JSON.parse(raw);
      expect(request.status).to.equal("REJECTED");
    });

    it("对不存在的申请拒绝应抛错", async () => {
      await expect(
        contract.RejectAccessRequest(ctx, "404", "2026-04-22T10:00:00Z")
      ).to.be.rejectedWith(/not found/);
    });
  });

  describe("QueryAccessRequest", () => {
    it("不存在时抛错", async () => {
      await expect(
        contract.QueryAccessRequest(ctx, "404")
      ).to.be.rejectedWith(/not found/);
    });

    it("存在时返回完整 JSON", async () => {
      await contract.CreateAccessRequest(
        ctx, "10", "1", "HospitalB", "hash", "PENDING", "2026-04-22T00:00:00Z"
      );
      const raw = await contract.QueryAccessRequest(ctx, "10");
      const request = JSON.parse(raw);
      expect(request.requestId).to.equal("10");
    });
  });

  describe("GetRecordHistory（迭代 3：Fabric 原生历史查询）", () => {
    it("不存在的 recordId 应抛 not found", async () => {
      await expect(
        contract.GetRecordHistory(ctx, "404")
      ).to.be.rejectedWith(/not found/);
    });

    it("创建 + 连续修订 3 次应返回 4 条历史，按时间倒序", async () => {
      // v1 创建
      ctx.stub.setTxID("tx-v1");
      await contract.CreateMedicalRecordEvidence(
        ctx, "1", "2", "HospitalA", "hash-v1", "2026-04-22T00:00:00Z"
      );
      // v2
      ctx.stub.setTxID("tx-v2");
      await contract.UpdateMedicalRecordEvidence(
        ctx, "1", "hash-v2", "2026-04-22T10:00:00Z"
      );
      // v3
      ctx.stub.setTxID("tx-v3");
      await contract.UpdateMedicalRecordEvidence(
        ctx, "1", "hash-v3", "2026-04-22T11:00:00Z"
      );
      // v4
      ctx.stub.setTxID("tx-v4");
      await contract.UpdateMedicalRecordEvidence(
        ctx, "1", "hash-v4", "2026-04-22T12:00:00Z"
      );

      const raw = await contract.GetRecordHistory(ctx, "1");
      const history = JSON.parse(raw);
      expect(history).to.have.lengthOf(4);

      // 倒序：最近（tx-v4）在前，tx-v1 在末
      expect(history[0].txId).to.equal("tx-v4");
      expect(history[0].value.version).to.equal(4);
      expect(history[0].value.dataHash).to.equal("hash-v4");
      expect(history[0].isDelete).to.equal(false);

      expect(history[3].txId).to.equal("tx-v1");
      expect(history[3].value.version).to.equal(1);
      expect(history[3].value.previousTxId).to.equal("");

      // 每条都有 timestamp 且单调递增（倒序后前>=后）
      for (let i = 0; i < history.length - 1; i++) {
        expect(history[i].timestamp).to.be.a("string");
        expect(history[i].timestamp >= history[i + 1].timestamp).to.equal(true);
      }
    });

    it("本方法使用 LATEST 键的全量历史，而非逐版本 GetState", async () => {
      ctx.stub.setTxID("tx-v1");
      await contract.CreateMedicalRecordEvidence(
        ctx, "1", "2", "HospitalA", "hash-v1", "2026-04-22T00:00:00Z"
      );
      ctx.stub.setTxID("tx-v2");
      await contract.UpdateMedicalRecordEvidence(
        ctx, "1", "hash-v2", "2026-04-22T10:00:00Z"
      );

      ctx.stub.getHistoryForKey.resetHistory();
      ctx.stub.getState.resetHistory();

      await contract.GetRecordHistory(ctx, "1");

      // 只调用 1 次 getHistoryForKey，且目标键就是 LATEST
      expect(ctx.stub.getHistoryForKey.calledOnce).to.equal(true);
      expect(ctx.stub.getHistoryForKey.firstCall.args[0]).to.equal("RECORD_LATEST_1");
      // 不再逐版本读 GetState
      expect(ctx.stub.getState.called).to.equal(false);
    });
  });

  describe("GetAccessRequestHistory（迭代 3）", () => {
    it("不存在的请求应抛 not found", async () => {
      await expect(
        contract.GetAccessRequestHistory(ctx, "404")
      ).to.be.rejectedWith(/not found/);
    });

    it("创建→审批流应返回按时间倒序的 2 条历史", async () => {
      ctx.stub.setTxID("tx-create");
      await contract.CreateAccessRequest(
        ctx, "10", "1", "HospitalB", "reason-h", "PENDING", "2026-04-22T00:00:00Z"
      );
      ctx.stub.setTxID("tx-approve");
      await contract.ApproveAccessRequest(ctx, "10", "2026-04-22T10:00:00Z");

      const raw = await contract.GetAccessRequestHistory(ctx, "10");
      const history = JSON.parse(raw);
      expect(history).to.have.lengthOf(2);

      expect(history[0].txId).to.equal("tx-approve");
      expect(history[0].value.status).to.equal("APPROVED");
      expect(history[0].value.reviewTxId).to.equal("tx-approve");

      expect(history[1].txId).to.equal("tx-create");
      expect(history[1].value.status).to.equal("PENDING");
    });

    it("拒绝分支同样被记录到历史", async () => {
      ctx.stub.setTxID("tx-create");
      await contract.CreateAccessRequest(
        ctx, "11", "1", "HospitalB", "reason-h", "PENDING", "2026-04-22T00:00:00Z"
      );
      ctx.stub.setTxID("tx-reject");
      await contract.RejectAccessRequest(ctx, "11", "2026-04-22T10:00:00Z");

      const history = JSON.parse(await contract.GetAccessRequestHistory(ctx, "11"));
      expect(history[0].value.status).to.equal("REJECTED");
      expect(history[1].value.status).to.equal("PENDING");
    });
  });

  describe("端到端：审批流状态机", () => {
    it("PENDING → APPROVED 后重复审批仍然成功（当前无状态守卫）", async () => {
      await contract.CreateAccessRequest(
        ctx, "20", "1", "HospitalB", "hash", "PENDING", "2026-04-22T00:00:00Z"
      );
      await contract.ApproveAccessRequest(ctx, "20", "2026-04-22T01:00:00Z");

      // 注：当前链码未拒绝对已终态的重复审批，迭代 5（ABAC）会收紧该行为
      await contract.ApproveAccessRequest(ctx, "20", "2026-04-22T02:00:00Z");
      const stored = readState(ctx, "REQ_20");
      expect(stored.status).to.equal("APPROVED");
      expect(stored.reviewedAt).to.equal("2026-04-22T02:00:00Z");
    });

    it("交易 ID 每次调用都来自 ctx.stub.getTxID()", async () => {
      await contract.CreateAccessRequest(
        ctx, "30", "1", "HospitalB", "hash", "PENDING", "2026-04-22T00:00:00Z"
      );
      expect(readState(ctx, "REQ_30").createTxId).to.equal("tx-test-0001");

      const ctx2 = makeMockContext({ txId: "tx-test-0002" });
      // 把原状态手工复制过去（因为每个 ctx 有独立 state）
      ctx2.stub._state.set(
        "REQ_30",
        Buffer.from(JSON.stringify(readState(ctx, "REQ_30")))
      );
      await contract.ApproveAccessRequest(ctx2, "30", "2026-04-22T01:00:00Z");
      expect(readState(ctx2, "REQ_30").reviewTxId).to.equal("tx-test-0002");
    });
  });
});
