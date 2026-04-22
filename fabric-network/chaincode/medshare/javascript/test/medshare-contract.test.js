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
    it("首次创建应写入世界状态并返回带 txId 的 JSON", async () => {
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
      expect(evidence.txId).to.equal("tx-test-0001");
      expect(evidence.docType).to.equal("RecordEvidence");

      const stored = readState(ctx, "RECORD_1");
      expect(stored).to.deep.equal(evidence);
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

  describe("GetMedicalRecordEvidence", () => {
    it("查询不存在的记录应抛错", async () => {
      await expect(
        contract.GetMedicalRecordEvidence(ctx, "999")
      ).to.be.rejectedWith(/not found/);
    });

    it("能读到已创建的证据", async () => {
      await contract.CreateMedicalRecordEvidence(
        ctx, "1", "2", "HospitalA", "deadbeef", "2026-04-22T00:00:00Z"
      );
      const raw = await contract.GetMedicalRecordEvidence(ctx, "1");
      const evidence = JSON.parse(raw);
      expect(evidence.recordId).to.equal("1");
      expect(evidence.dataHash).to.equal("deadbeef");
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
