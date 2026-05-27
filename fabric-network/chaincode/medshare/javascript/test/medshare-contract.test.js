"use strict";

const chai = require("chai");
const chaiAsPromised = require("chai-as-promised");
chai.use(chaiAsPromised);
const { expect } = chai;

const MedShareContract = require("../lib/medshare-contract");
const { makeMockContext, readState } = require("./helpers");

/** Helper: 种子一条 PENDING 申请，返回 ctx（已注入状态）。 */
async function seedPending(contract, ctx, { reqId = "10", recordId = "1",
  applicantHospital = "HospitalB", patientId = "2", reasonHash = "rh",
  createdAt = "2026-04-22T00:00:00Z" } = {}) {
  await contract.CreateAccessRequest(
    ctx, reqId, recordId, applicantHospital, patientId, reasonHash, "PENDING", createdAt
  );
}

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
        ctx, "1", "2", "HospitalA", "deadbeef", "2026-04-22T00:00:00Z"
      );
      const evidence = JSON.parse(raw);
      expect(evidence.recordId).to.equal("1");
      expect(evidence.patientId).to.equal("2");
      expect(evidence.uploaderHospital).to.equal("HospitalA");
      expect(evidence.dataHash).to.equal("deadbeef");
      expect(evidence.version).to.equal(1);
      expect(evidence.previousTxId).to.equal("");
      expect(evidence.txId).to.equal("tx-test-0001");

      // 迭代 7：LATEST 带 isLatest:true 标志；版本化键不带
      expect(readState(ctx, "RECORD_1_v1")).to.deep.equal(evidence);
      expect(readState(ctx, "RECORD_LATEST_1")).to.deep.equal({
        ...evidence,
        isLatest: true,
      });
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
      ctx.stub.setTxID("tx-v2");
      const raw = await contract.UpdateMedicalRecordEvidence(
        ctx, "1", "hash-v2", "2026-04-22T10:00:00Z"
      );
      const ev = JSON.parse(raw);
      expect(ev.version).to.equal(2);
      expect(ev.previousTxId).to.equal("tx-test-0001");
      expect(ev.dataHash).to.equal("hash-v2");
      expect(ev.updatedAt).to.equal("2026-04-22T10:00:00Z");
      expect(ev.txId).to.equal("tx-v2");

      expect(readState(ctx, "RECORD_1_v2").version).to.equal(2);
      expect(readState(ctx, "RECORD_LATEST_1").version).to.equal(2);
      expect(readState(ctx, "RECORD_1_v1").dataHash).to.equal("hash-v1");
    });

    it("连续修订 5 次应形成长度为 5 的版本链", async () => {
      const txIds = ["tx-test-0001"];
      for (let v = 2; v <= 5; v++) {
        ctx.stub.setTxID(`tx-v${v}`);
        await contract.UpdateMedicalRecordEvidence(
          ctx, "1", `hash-v${v}`, `2026-04-22T${String(v).padStart(2, "0")}:00:00Z`
        );
        txIds.push(`tx-v${v}`);
      }

      const latest = JSON.parse(await contract.GetRecordLatest(ctx, "1"));
      expect(latest.version).to.equal(5);

      for (let v = 1; v <= 5; v++) {
        const raw = await contract.GetRecordVersion(ctx, "1", String(v));
        const ev = JSON.parse(raw);
        expect(ev.version).to.equal(v);
        expect(ev.txId).to.equal(txIds[v - 1]);
        expect(ev.previousTxId).to.equal(v === 1 ? "" : txIds[v - 2]);
      }
    });

    it("修订不存在的记录应抛错", async () => {
      await expect(
        contract.UpdateMedicalRecordEvidence(ctx, "999", "anyhash", "2026-04-22T10:00:00Z")
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

  describe("CreateAccessRequest（迭代 5：签名加 patientId + 绑定 MSP）", () => {
    it("首次创建应写入 PENDING，并绑定申请方 MSP 与 patientId", async () => {
      ctx.clientIdentity.getMSPID.returns("Org2MSP");
      await seedPending(contract, ctx, { reqId: "10", applicantHospital: "HospitalB" });
      const stored = readState(ctx, "REQ_10");
      expect(stored.status).to.equal("PENDING");
      expect(stored.applicantMsp).to.equal("Org2MSP");
      expect(stored.patientId).to.equal("2");
      expect(stored.remainingReads).to.equal(0);
      expect(stored.expiresAtTs).to.equal(0);
      // 事件被触发
      expect(ctx.stub._events[0].name).to.equal("AccessRequestCreated");
    });

    it("重复创建应抛错", async () => {
      await seedPending(contract, ctx, { reqId: "10" });
      await expect(seedPending(contract, ctx, { reqId: "10" })).to.be.rejectedWith(
        /already exists/
      );
    });
  });

  describe("ApproveAccessRequest（迭代 5：有期限 + 次数上限）", () => {
    it("合法审批：写入 expiresAtTs / remainingReads / reviewTxId 并触发事件", async () => {
      ctx.stub.setTxID("tx-create");
      await seedPending(contract, ctx, { reqId: "10" });

      ctx.stub.setTxID("tx-approve");
      const raw = await contract.ApproveAccessRequest(
        ctx, "10", "2026-04-22T10:00:00Z", 7, 3
      );
      const request = JSON.parse(raw);
      expect(request.status).to.equal("APPROVED");
      expect(request.reviewTxId).to.equal("tx-approve");
      expect(request.remainingReads).to.equal(3);
      expect(request.expiresAtTs).to.be.greaterThan(0);
      expect(request.expiresAt).to.be.a("string");
      expect(
        ctx.stub._events.find((e) => e.name === "AccessApproved")
      ).to.not.be.undefined;
    });

    it("durationDays 非正数应抛错", async () => {
      await seedPending(contract, ctx, { reqId: "10" });
      await expect(
        contract.ApproveAccessRequest(ctx, "10", "ts", 0, 5)
      ).to.be.rejectedWith(/durationDays/);
    });

    it("maxReads 非正数应抛错", async () => {
      await seedPending(contract, ctx, { reqId: "10" });
      await expect(
        contract.ApproveAccessRequest(ctx, "10", "ts", 7, 0)
      ).to.be.rejectedWith(/maxReads/);
    });

    it("对不存在的申请审批应抛 not found", async () => {
      await expect(
        contract.ApproveAccessRequest(ctx, "999", "ts", 7, 3)
      ).to.be.rejectedWith(/not found/);
    });

    it("已是 APPROVED 时再次 Approve 应被状态机拒绝（迭代 5 收紧）", async () => {
      await seedPending(contract, ctx, { reqId: "20" });
      await contract.ApproveAccessRequest(ctx, "20", "t1", 7, 3);
      await expect(
        contract.ApproveAccessRequest(ctx, "20", "t2", 7, 3)
      ).to.be.rejectedWith(/非法状态跃迁/);
    });

    it("已 REJECTED 再 APPROVED 应被状态机拒绝", async () => {
      await seedPending(contract, ctx, { reqId: "21" });
      await contract.RejectAccessRequest(ctx, "21", "t1");
      await expect(
        contract.ApproveAccessRequest(ctx, "21", "t2", 7, 3)
      ).to.be.rejectedWith(/非法状态跃迁/);
    });
  });

  describe("RejectAccessRequest（迭代 5：状态机收紧）", () => {
    it("应把 PENDING 改为 REJECTED", async () => {
      await seedPending(contract, ctx, { reqId: "10" });
      const raw = await contract.RejectAccessRequest(ctx, "10", "2026-04-22T10:00:00Z");
      expect(JSON.parse(raw).status).to.equal("REJECTED");
    });

    it("不存在应抛 not found", async () => {
      await expect(
        contract.RejectAccessRequest(ctx, "404", "ts")
      ).to.be.rejectedWith(/not found/);
    });

    it("已 APPROVED 再 REJECTED 应被拒绝", async () => {
      await seedPending(contract, ctx, { reqId: "22" });
      await contract.ApproveAccessRequest(ctx, "22", "t1", 7, 3);
      await expect(
        contract.RejectAccessRequest(ctx, "22", "t2")
      ).to.be.rejectedWith(/非法状态跃迁/);
    });
  });

  describe("RevokeAccessRequest（迭代 5：链上撤销）", () => {
    beforeEach(async () => {
      ctx.clientIdentity.getMSPID.returns("Org2MSP");
      await seedPending(contract, ctx, { reqId: "30", patientId: "2" });
      await contract.ApproveAccessRequest(ctx, "30", "t1", 7, 5);
    });

    it("归属患者可撤销 APPROVED 授权", async () => {
      ctx.stub.setTxID("tx-revoke");
      const raw = await contract.RevokeAccessRequest(
        ctx, "30", "2", "2026-04-22T20:00:00Z"
      );
      const req = JSON.parse(raw);
      expect(req.status).to.equal("REVOKED");
      expect(req.revokeTxId).to.equal("tx-revoke");
      expect(req.revokedAt).to.equal("2026-04-22T20:00:00Z");
      expect(
        ctx.stub._events.find((e) => e.name === "AccessRevoked")
      ).to.not.be.undefined;
    });

    it("非归属患者尝试撤销应抛错", async () => {
      await expect(
        contract.RevokeAccessRequest(ctx, "30", "999", "ts")
      ).to.be.rejectedWith(/只有归属患者/);
    });

    it("对 PENDING 申请撤销应被状态机拒绝", async () => {
      await seedPending(contract, ctx, { reqId: "31", patientId: "2" });
      await expect(
        contract.RevokeAccessRequest(ctx, "31", "2", "ts")
      ).to.be.rejectedWith(/非法状态跃迁/);
    });

    it("已 REVOKED 再撤销应被拒绝", async () => {
      await contract.RevokeAccessRequest(ctx, "30", "2", "ts1");
      await expect(
        contract.RevokeAccessRequest(ctx, "30", "2", "ts2")
      ).to.be.rejectedWith(/非法状态跃迁/);
    });
  });

  describe("AccessRecord（迭代 5：链上授权消费与 ABAC 核心）", () => {
    it("正常消费一次：remainingReads 扣减 1，触发 AccessRecorded 事件", async () => {
      ctx.clientIdentity.getMSPID.returns("Org2MSP");
      await seedPending(contract, ctx, { reqId: "40" });
      await contract.ApproveAccessRequest(ctx, "40", "t1", 7, 3);

      ctx.stub.setTxID("tx-access-1");
      const raw = await contract.AccessRecord(ctx, "40", "2026-04-22T12:00:00Z");
      const out = JSON.parse(raw);
      expect(out.remainingReads).to.equal(2);
      expect(out.readsUsed).to.equal(1);
      expect(out.txId).to.equal("tx-access-1");

      const stored = readState(ctx, "REQ_40");
      expect(stored.remainingReads).to.equal(2);
      expect(stored.readsUsed).to.equal(1);
      expect(stored.lastAccessTxId).to.equal("tx-access-1");
      expect(
        ctx.stub._events.find((e) => e.name === "AccessRecorded")
      ).to.not.be.undefined;
    });

    it("次数用尽应被拒绝（remainingReads=0）", async () => {
      ctx.clientIdentity.getMSPID.returns("Org2MSP");
      await seedPending(contract, ctx, { reqId: "41" });
      await contract.ApproveAccessRequest(ctx, "41", "t1", 7, 1);

      await contract.AccessRecord(ctx, "41", "t-read-1"); // 消费 1 次
      await expect(
        contract.AccessRecord(ctx, "41", "t-read-2")      // 第 2 次应拒
      ).to.be.rejectedWith(/访问次数已用尽/);
    });

    it("授权已过期应被拒绝（使用 getTxTimestamp 权威时间）", async () => {
      ctx.clientIdentity.getMSPID.returns("Org2MSP");
      await seedPending(contract, ctx, { reqId: "42" });
      // 审批时 nowTs = 1_714_000_000 + N（helpers 内单调递增）
      await contract.ApproveAccessRequest(ctx, "42", "t1", 7, 5);
      // 把权威时间往后拉到过期之后：直接覆盖 getTxTimestamp 返回值
      const req = readState(ctx, "REQ_42");
      ctx.stub.getTxTimestamp.returns({
        seconds: { low: req.expiresAtTs + 1, high: 0 },
        nanos: 0,
      });
      await expect(
        contract.AccessRecord(ctx, "42", "t-late")
      ).to.be.rejectedWith(/授权已过期/);
    });

    it("status 非 APPROVED 应被拒绝（如已撤销）", async () => {
      ctx.clientIdentity.getMSPID.returns("Org2MSP");
      await seedPending(contract, ctx, { reqId: "43", patientId: "2" });
      await contract.ApproveAccessRequest(ctx, "43", "t1", 7, 3);
      await contract.RevokeAccessRequest(ctx, "43", "2", "t2");

      await expect(
        contract.AccessRecord(ctx, "43", "t3")
      ).to.be.rejectedWith(/当前状态 REVOKED/);
    });

    it("调用方 MSP 与绑定 MSP 不一致应被拒绝（防 MSP 盗用）", async () => {
      // 申请时 MSP 是 Org2
      ctx.clientIdentity.getMSPID.returns("Org2MSP");
      await seedPending(contract, ctx, { reqId: "44" });
      await contract.ApproveAccessRequest(ctx, "44", "t1", 7, 3);

      // 用 Org1 身份尝试消费（模拟另一医院拿到了 requestId）
      ctx.clientIdentity.getMSPID.returns("Org1MSP");
      await expect(
        contract.AccessRecord(ctx, "44", "t2")
      ).to.be.rejectedWith(/调用方 MSP/);
    });

    it("不存在的 requestId 应抛 not found", async () => {
      await expect(
        contract.AccessRecord(ctx, "999", "ts")
      ).to.be.rejectedWith(/not found/);
    });

    it("链码方法 getState 次数 ≤ 3（优化目标）", async () => {
      ctx.clientIdentity.getMSPID.returns("Org2MSP");
      await seedPending(contract, ctx, { reqId: "45" });
      await contract.ApproveAccessRequest(ctx, "45", "t1", 7, 3);

      ctx.stub.getState.resetHistory();
      await contract.AccessRecord(ctx, "45", "ts");
      expect(ctx.stub.getState.callCount).to.be.at.most(3);
    });
  });

  describe("QueryAccessRequest", () => {
    it("不存在时抛错", async () => {
      await expect(
        contract.QueryAccessRequest(ctx, "404")
      ).to.be.rejectedWith(/not found/);
    });

    it("存在时返回完整 JSON（含 ABAC 字段）", async () => {
      await seedPending(contract, ctx, { reqId: "10" });
      const raw = await contract.QueryAccessRequest(ctx, "10");
      const request = JSON.parse(raw);
      expect(request.requestId).to.equal("10");
      expect(request).to.have.property("remainingReads");
      expect(request).to.have.property("expiresAtTs");
      expect(request).to.have.property("applicantMsp");
    });
  });

  describe("GetRecordHistory（迭代 3：Fabric 原生历史查询）", () => {
    it("不存在的 recordId 应抛 not found", async () => {
      await expect(contract.GetRecordHistory(ctx, "404")).to.be.rejectedWith(
        /not found/
      );
    });

    it("创建 + 连续修订 3 次应返回 4 条历史，按时间倒序", async () => {
      ctx.stub.setTxID("tx-v1");
      await contract.CreateMedicalRecordEvidence(
        ctx, "1", "2", "HospitalA", "hash-v1", "2026-04-22T00:00:00Z"
      );
      for (let v = 2; v <= 4; v++) {
        ctx.stub.setTxID(`tx-v${v}`);
        await contract.UpdateMedicalRecordEvidence(
          ctx, "1", `hash-v${v}`, `2026-04-22T1${v}:00:00Z`
        );
      }
      const history = JSON.parse(await contract.GetRecordHistory(ctx, "1"));
      expect(history).to.have.lengthOf(4);
      expect(history[0].txId).to.equal("tx-v4");
      expect(history[3].txId).to.equal("tx-v1");
      for (let i = 0; i < history.length - 1; i++) {
        expect(history[i].timestamp >= history[i + 1].timestamp).to.equal(true);
      }
    });

    it("本方法使用 LATEST 键的全量历史", async () => {
      ctx.stub.setTxID("tx-v1");
      await contract.CreateMedicalRecordEvidence(
        ctx, "1", "2", "HospitalA", "hash-v1", "2026-04-22T00:00:00Z"
      );
      ctx.stub.getHistoryForKey.resetHistory();
      ctx.stub.getState.resetHistory();
      await contract.GetRecordHistory(ctx, "1");
      expect(ctx.stub.getHistoryForKey.firstCall.args[0]).to.equal("RECORD_LATEST_1");
      expect(ctx.stub.getState.called).to.equal(false);
    });
  });

  describe("GetAccessRequestHistory（迭代 3）", () => {
    it("不存在的请求应抛 not found", async () => {
      await expect(
        contract.GetAccessRequestHistory(ctx, "404")
      ).to.be.rejectedWith(/not found/);
    });

    it("创建→审批→撤销应返回按时间倒序的 3 条历史", async () => {
      ctx.clientIdentity.getMSPID.returns("Org2MSP");
      ctx.stub.setTxID("tx-create");
      await seedPending(contract, ctx, { reqId: "50", patientId: "2" });
      ctx.stub.setTxID("tx-approve");
      await contract.ApproveAccessRequest(ctx, "50", "t1", 7, 3);
      ctx.stub.setTxID("tx-revoke");
      await contract.RevokeAccessRequest(ctx, "50", "2", "t2");

      const history = JSON.parse(await contract.GetAccessRequestHistory(ctx, "50"));
      expect(history).to.have.lengthOf(3);
      expect(history[0].txId).to.equal("tx-revoke");
      expect(history[0].value.status).to.equal("REVOKED");
      expect(history[2].txId).to.equal("tx-create");
      expect(history[2].value.status).to.equal("PENDING");
    });
  });

  describe("链码事件（迭代 6）", () => {
    it("CreateMedicalRecordEvidence 触发 RecordCreated", async () => {
      await contract.CreateMedicalRecordEvidence(
        ctx, "e1", "2", "HospitalA", "h1", "2026-04-22T00:00:00Z"
      );
      const ev = ctx.stub._events.find((e) => e.name === "RecordCreated");
      expect(ev).to.not.be.undefined;
      const payload = JSON.parse(ev.payload.toString("utf8"));
      expect(payload.recordId).to.equal("e1");
      expect(payload.patientId).to.equal("2");
      expect(payload.version).to.equal(1);
      expect(payload.txId).to.be.a("string");
    });

    it("UpdateMedicalRecordEvidence 触发 RecordUpdated（带 version / previousTxId）", async () => {
      ctx.stub.setTxID("tx-c");
      await contract.CreateMedicalRecordEvidence(
        ctx, "e2", "2", "HospitalA", "h1", "2026-04-22T00:00:00Z"
      );
      ctx.stub.setTxID("tx-u");
      await contract.UpdateMedicalRecordEvidence(
        ctx, "e2", "h2", "2026-04-22T10:00:00Z"
      );
      const events = ctx.stub._events.filter((e) => e.name === "RecordUpdated");
      expect(events).to.have.lengthOf(1);
      const payload = JSON.parse(events[0].payload.toString("utf8"));
      expect(payload.recordId).to.equal("e2");
      expect(payload.version).to.equal(2);
      expect(payload.previousTxId).to.equal("tx-c");
      expect(payload.txId).to.equal("tx-u");
    });

    it("审批/撤销/消费各触发对应事件", async () => {
      ctx.clientIdentity.getMSPID.returns("Org2MSP");
      await seedPending(contract, ctx, { reqId: "e10", patientId: "2" });
      await contract.ApproveAccessRequest(ctx, "e10", "t1", 7, 3);
      await contract.AccessRecord(ctx, "e10", "t2");
      await contract.RevokeAccessRequest(ctx, "e10", "2", "t3");

      const names = ctx.stub._events.map((e) => e.name);
      expect(names).to.include("AccessRequestCreated");
      expect(names).to.include("AccessApproved");
      expect(names).to.include("AccessRecorded");
      expect(names).to.include("AccessRevoked");
    });
  });

  describe("CouchDB 富查询（迭代 7）", () => {
    // 种子数据：HospitalA 上传 5 条 + HospitalB 上传 3 条；各自修订 1 次（产生版本化键）
    async function seedRecords() {
      const now = (d) => `2026-04-${String(d).padStart(2, "0")}T00:00:00Z`;
      let n = 1;
      for (let i = 0; i < 5; i++) {
        ctx.stub.setTxID(`tx-A${i}`);
        await contract.CreateMedicalRecordEvidence(
          ctx, String(n), "2", "HospitalA", `hA${i}`, now(i + 1)
        );
        if (i === 0) {
          ctx.stub.setTxID(`tx-A${i}-v2`);
          await contract.UpdateMedicalRecordEvidence(
            ctx, String(n), `hA${i}-v2`, now(i + 2)
          );
        }
        n += 1;
      }
      for (let i = 0; i < 3; i++) {
        ctx.stub.setTxID(`tx-B${i}`);
        await contract.CreateMedicalRecordEvidence(
          ctx, String(n), "3", "HospitalB", `hB${i}`, now(i + 20)
        );
        n += 1;
      }
    }

    it("QueryRecordsByHospital 只返回最新版 LATEST 条目（不会包含版本化键）", async () => {
      await seedRecords();
      const raw = await contract.QueryRecordsByHospital(ctx, "HospitalA", "20", "");
      const out = JSON.parse(raw);
      expect(out.records).to.have.lengthOf(5);
      // 每条都应是 isLatest==true
      for (const r of out.records) {
        expect(r.isLatest).to.equal(true);
        expect(r.uploaderHospital).to.equal("HospitalA");
      }
      // 第一条被修订过，应是 v2
      const revised = out.records.find((r) => r.recordId === "1");
      expect(revised.version).to.equal(2);
    });

    it("QueryRecordsByHospital 对另一个医院只返回自己的 3 条", async () => {
      await seedRecords();
      const raw = await contract.QueryRecordsByHospital(ctx, "HospitalB", "20", "");
      const out = JSON.parse(raw);
      expect(out.records).to.have.lengthOf(3);
      expect(out.records.every((r) => r.uploaderHospital === "HospitalB")).to.equal(true);
    });

    it("QueryRecordsByDateRange 按 createdAt 闭区间过滤", async () => {
      await seedRecords();
      const raw = await contract.QueryRecordsByDateRange(
        ctx, "2026-04-01T00:00:00Z", "2026-04-03T00:00:00Z", "20", ""
      );
      const out = JSON.parse(raw);
      // HospitalA 的前 3 条创建日期 04-01..04-03
      expect(out.records).to.have.lengthOf(3);
      for (const r of out.records) {
        expect(r.createdAt >= "2026-04-01T00:00:00Z").to.equal(true);
        expect(r.createdAt <= "2026-04-03T00:00:00Z").to.equal(true);
      }
      // sort asc
      for (let i = 0; i < out.records.length - 1; i++) {
        expect(out.records[i].createdAt <= out.records[i + 1].createdAt).to.equal(true);
      }
    });

    it("QueryPendingRequestsForPatient 只返回 PENDING 的申请", async () => {
      // patient 2 有 2 条 PENDING，1 条 APPROVED，0 条 REJECTED
      ctx.clientIdentity.getMSPID.returns("Org2MSP");
      await seedPending(contract, ctx, { reqId: "P1", patientId: "2" });
      await seedPending(contract, ctx, { reqId: "P2", patientId: "2" });
      await seedPending(contract, ctx, { reqId: "P3", patientId: "2" });
      await contract.ApproveAccessRequest(ctx, "P3", "t", 7, 3);
      // 另一个患者的申请
      await seedPending(contract, ctx, { reqId: "X1", patientId: "3" });

      const raw = await contract.QueryPendingRequestsForPatient(ctx, "2", "10", "");
      const out = JSON.parse(raw);
      expect(out.records).to.have.lengthOf(2);
      expect(out.records.every((r) => r.status === "PENDING")).to.equal(true);
      expect(out.records.every((r) => r.patientId === "2")).to.equal(true);
    });

    it("分页 1000 条记录：按 50/页 遍历完成，无丢失无重复", async () => {
      // 种 1000 条 HospitalA 的病历
      for (let i = 0; i < 1000; i++) {
        ctx.stub.setTxID(`tx-${i}`);
        await contract.CreateMedicalRecordEvidence(
          ctx, String(i + 1), "2", "HospitalA",
          `hash-${i}`, `2026-04-22T${String(i % 24).padStart(2, "0")}:00:00Z`
        );
      }

      const pageSize = 50;
      const seenIds = new Set();
      let bookmark = "";
      let pages = 0;
      while (true) {
        const raw = await contract.QueryRecordsByHospital(
          ctx, "HospitalA", String(pageSize), bookmark
        );
        const out = JSON.parse(raw);
        for (const r of out.records) {
          const id = r.recordId;
          expect(seenIds.has(id)).to.equal(false, `记录 ${id} 重复出现`);
          seenIds.add(id);
        }
        pages += 1;
        if (!out.bookmark) break;
        bookmark = out.bookmark;
        if (pages > 30) break; // 安全阈
      }
      expect(seenIds.size).to.equal(1000);
      expect(pages).to.equal(Math.ceil(1000 / pageSize));
    });

    it("富查询只会命中 LATEST，不会把版本化键当成最新返回", async () => {
      // 创建 + 修订 3 次 → 4 份数据（v1/v2/v3/v4 + LATEST）
      ctx.stub.setTxID("c");
      await contract.CreateMedicalRecordEvidence(
        ctx, "9", "2", "HospitalA", "h1", "2026-04-22T00:00:00Z"
      );
      ctx.stub.setTxID("u2");
      await contract.UpdateMedicalRecordEvidence(ctx, "9", "h2", "2026-04-22T10:00:00Z");
      ctx.stub.setTxID("u3");
      await contract.UpdateMedicalRecordEvidence(ctx, "9", "h3", "2026-04-22T11:00:00Z");

      const raw = await contract.QueryRecordsByHospital(ctx, "HospitalA", "20", "");
      const out = JSON.parse(raw);
      expect(out.records).to.have.lengthOf(1);
      expect(out.records[0].version).to.equal(3);
      expect(out.records[0].isLatest).to.equal(true);
    });
  });

  describe("端到端：状态机表驱动测试", () => {
    it("合法跃迁矩阵全通过", async () => {
      // PENDING → APPROVED
      await seedPending(contract, ctx, { reqId: "100" });
      await contract.ApproveAccessRequest(ctx, "100", "t", 7, 3);
      expect(readState(ctx, "REQ_100").status).to.equal("APPROVED");

      // PENDING → REJECTED
      await seedPending(contract, ctx, { reqId: "101" });
      await contract.RejectAccessRequest(ctx, "101", "t");
      expect(readState(ctx, "REQ_101").status).to.equal("REJECTED");

      // APPROVED → REVOKED
      ctx.clientIdentity.getMSPID.returns("Org2MSP");
      await seedPending(contract, ctx, { reqId: "102", patientId: "2" });
      await contract.ApproveAccessRequest(ctx, "102", "t", 7, 3);
      await contract.RevokeAccessRequest(ctx, "102", "2", "t");
      expect(readState(ctx, "REQ_102").status).to.equal("REVOKED");
    });

    it("非法跃迁矩阵全被拒绝", async () => {
      // REJECTED → 任意 = 非法
      await seedPending(contract, ctx, { reqId: "200" });
      await contract.RejectAccessRequest(ctx, "200", "t");
      await expect(
        contract.ApproveAccessRequest(ctx, "200", "t", 7, 3)
      ).to.be.rejectedWith(/非法状态跃迁/);
      await expect(
        contract.RevokeAccessRequest(ctx, "200", "2", "t")
      ).to.be.rejectedWith(/非法状态跃迁/);

      // REVOKED → 任意 = 非法
      ctx.clientIdentity.getMSPID.returns("Org2MSP");
      await seedPending(contract, ctx, { reqId: "201", patientId: "2" });
      await contract.ApproveAccessRequest(ctx, "201", "t", 7, 3);
      await contract.RevokeAccessRequest(ctx, "201", "2", "t");
      await expect(
        contract.ApproveAccessRequest(ctx, "201", "t", 7, 3)
      ).to.be.rejectedWith(/非法状态跃迁/);
      await expect(
        contract.RejectAccessRequest(ctx, "201", "t")
      ).to.be.rejectedWith(/非法状态跃迁/);
    });
  });

  // ---------------- 迭代 9：Merkle 批量锚定 + 链上包含证明 ----------------
  describe("Merkle 批量锚定（迭代 9）", () => {
    const crypto = require("crypto");
    const sha256Hex = (buf) =>
      crypto.createHash("sha256").update(buf).digest("hex");
    const hashPair = (l, r) =>
      sha256Hex(Buffer.concat([Buffer.from(l, "hex"), Buffer.from(r, "hex")]));

    // 给定叶子哈希数组（hex），返回 {root, proofs[][]}
    function buildMerkleTree(leaves) {
      if (leaves.length === 0) return { root: "", proofs: [] };
      let level = leaves.slice();
      const proofs = leaves.map(() => []);
      const indices = leaves.map((_, i) => i);
      while (level.length > 1) {
        if (level.length % 2 === 1) {
          level.push(level[level.length - 1]); // 奇数补自身
        }
        const nextLevel = [];
        const nextIndices = [];
        for (let i = 0; i < level.length; i += 2) {
          const L = level[i];
          const R = level[i + 1];
          // 找到 indices 中映射到当前这两个 slot 的所有原叶子，
          // 给它们追加一步证明
          for (let k = 0; k < indices.length; k++) {
            if (indices[k] === i) {
              proofs[k].push({ hash: R, position: "right" });
            } else if (indices[k] === i + 1) {
              proofs[k].push({ hash: L, position: "left" });
            }
          }
          nextLevel.push(hashPair(L, R));
          // indices 折半
          for (let k = 0; k < indices.length; k++) {
            if (indices[k] === i || indices[k] === i + 1) {
              nextIndices[k] = i / 2;
            }
          }
        }
        // 把 indices 替换为折半后的值
        for (let k = 0; k < indices.length; k++) {
          indices[k] = nextIndices[k];
        }
        level = nextLevel;
      }
      return { root: level[0], proofs };
    }

    it("锚定一个 batch 后 GetAnchorBatch 能取回所有字段", async () => {
      const root = sha256Hex(Buffer.from("only-leaf"));
      const raw = await contract.AnchorRecordBatch(
        ctx, "B-001", root, "1", "2026-05-27T00:00:00Z"
      );
      const batch = JSON.parse(raw);
      expect(batch.batchId).to.equal("B-001");
      expect(batch.merkleRoot).to.equal(root);
      expect(batch.leafCount).to.equal(1);
      expect(batch.txId).to.equal("tx-test-0001");

      const got = JSON.parse(await contract.GetAnchorBatch(ctx, "B-001"));
      expect(got).to.deep.equal(batch);

      // 触发 BatchAnchored 事件
      const ev = ctx.stub._events.find((e) => e.name === "BatchAnchored");
      expect(ev).to.not.equal(undefined);
      expect(JSON.parse(ev.payload.toString("utf8")).leafCount).to.equal(1);
    });

    it("单叶子情况下根 == 叶子哈希，且空 proof 验证通过", async () => {
      const leaf = sha256Hex(Buffer.from("hello"));
      await contract.AnchorRecordBatch(ctx, "B-002", leaf, "1", "2026-05-27T00:00:00Z");
      const result = JSON.parse(
        await contract.VerifyRecordInclusion(ctx, "B-002", leaf, "[]")
      );
      expect(result.ok).to.equal(true);
      expect(result.recomputedRoot).to.equal(leaf);
    });

    it("2/3/8 叶子的 proof 全部 verify 为 true", async () => {
      // 2 叶子
      let leaves = [
        sha256Hex(Buffer.from("a")),
        sha256Hex(Buffer.from("b")),
      ];
      let tree = buildMerkleTree(leaves);
      await contract.AnchorRecordBatch(ctx, "B-2", tree.root, "2", "t");
      for (let i = 0; i < leaves.length; i++) {
        const r = JSON.parse(
          await contract.VerifyRecordInclusion(
            ctx, "B-2", leaves[i], JSON.stringify(tree.proofs[i])
          )
        );
        expect(r.ok).to.equal(true);
      }

      // 3 叶子（奇数补）
      leaves = [
        sha256Hex(Buffer.from("a")),
        sha256Hex(Buffer.from("b")),
        sha256Hex(Buffer.from("c")),
      ];
      tree = buildMerkleTree(leaves);
      await contract.AnchorRecordBatch(ctx, "B-3", tree.root, "3", "t");
      for (let i = 0; i < leaves.length; i++) {
        const r = JSON.parse(
          await contract.VerifyRecordInclusion(
            ctx, "B-3", leaves[i], JSON.stringify(tree.proofs[i])
          )
        );
        expect(r.ok).to.equal(true);
      }

      // 8 叶子
      leaves = Array.from({ length: 8 }, (_, i) =>
        sha256Hex(Buffer.from(`leaf-${i}`))
      );
      tree = buildMerkleTree(leaves);
      await contract.AnchorRecordBatch(ctx, "B-8", tree.root, "8", "t");
      for (let i = 0; i < leaves.length; i++) {
        const r = JSON.parse(
          await contract.VerifyRecordInclusion(
            ctx, "B-8", leaves[i], JSON.stringify(tree.proofs[i])
          )
        );
        expect(r.ok).to.equal(true, `leaf ${i} 验证失败`);
      }
    });

    it("篡改 proof 中任一兄弟 hash → verify 返回 false", async () => {
      const leaves = Array.from({ length: 4 }, (_, i) =>
        sha256Hex(Buffer.from(`x-${i}`))
      );
      const tree = buildMerkleTree(leaves);
      await contract.AnchorRecordBatch(ctx, "B-T", tree.root, "4", "t");

      // 篡改第一个 proof 的第一个兄弟（任意翻位）
      const tamperedProof = JSON.parse(JSON.stringify(tree.proofs[0]));
      const orig = tamperedProof[0].hash;
      tamperedProof[0].hash =
        (orig.startsWith("a") ? "b" : "a") + orig.slice(1);
      const r = JSON.parse(
        await contract.VerifyRecordInclusion(
          ctx, "B-T", leaves[0], JSON.stringify(tamperedProof)
        )
      );
      expect(r.ok).to.equal(false);
      expect(r.recomputedRoot).to.not.equal(tree.root);
    });

    it("未知 batchId → GetAnchorBatch / VerifyRecordInclusion 抛错", async () => {
      await expect(
        contract.GetAnchorBatch(ctx, "no-such")
      ).to.be.rejectedWith(/not found/);
      await expect(
        contract.VerifyRecordInclusion(
          ctx, "no-such", sha256Hex(Buffer.from("x")), "[]"
        )
      ).to.be.rejectedWith(/not found/);
    });

    it("重复锚定相同 batchId / 非法 merkleRoot / 非法 leafCount 全部抛错", async () => {
      const root = sha256Hex(Buffer.from("z"));
      await contract.AnchorRecordBatch(ctx, "B-D", root, "1", "t");
      await expect(
        contract.AnchorRecordBatch(ctx, "B-D", root, "1", "t")
      ).to.be.rejectedWith(/already anchored/);
      await expect(
        contract.AnchorRecordBatch(ctx, "B-X", "not-hex", "1", "t")
      ).to.be.rejectedWith(/64 位十六进制/);
      await expect(
        contract.AnchorRecordBatch(ctx, "B-Y", root, "0", "t")
      ).to.be.rejectedWith(/正整数/);
    });

    it("ListAnchorBatches 富查询能列出所有锚定批次（按 createdAt desc）", async () => {
      const root = sha256Hex(Buffer.from("a"));
      await contract.AnchorRecordBatch(ctx, "B-L1", root, "1", "2026-05-27T00:00:00Z");
      await contract.AnchorRecordBatch(ctx, "B-L2", root, "1", "2026-05-28T00:00:00Z");
      const out = JSON.parse(
        await contract.ListAnchorBatches(ctx, "20", "")
      );
      expect(out.records).to.have.lengthOf(2);
      expect(out.records[0].batchId).to.equal("B-L2"); // desc by createdAt
      expect(out.records[1].batchId).to.equal("B-L1");
    });
  });

  // ---------------- 迭代 10：链上多签治理（双 MSP endorse） ----------------
  describe("链上多签治理（迭代 10）", () => {
    it("Propose 后状态为 PROPOSED，含 proposerMsp", async () => {
      ctx.clientIdentity.getMSPID.returns("Org1MSP");
      const raw = await contract.ProposeGovernanceAction(
        ctx, "G-1", "UNFREEZE_RECORD", JSON.stringify({ recordId: "5" }), "2026-05-27"
      );
      const action = JSON.parse(raw);
      expect(action.status).to.equal("PROPOSED");
      expect(action.proposerMsp).to.equal("Org1MSP");
      expect(action.kind).to.equal("UNFREEZE_RECORD");
      expect(action.approvers).to.deep.equal([]);

      const ev = ctx.stub._events.find((e) => e.name === "GovernanceProposed");
      expect(ev).to.not.equal(undefined);
    });

    it("未知 kind 必须拒绝", async () => {
      await expect(
        contract.ProposeGovernanceAction(ctx, "G-2", "RANDOM_KIND", "{}", "t")
      ).to.be.rejectedWith(/未知治理动作 kind/);
    });

    it("单 MSP 批准 → PARTIALLY_APPROVED；同 MSP 二次批准 → 抛错", async () => {
      ctx.clientIdentity.getMSPID.returns("Org1MSP");
      await contract.ProposeGovernanceAction(
        ctx, "G-3", "BATCH_REVOKE_PATIENT", JSON.stringify({ patientId: "1" }), "t"
      );
      const after1 = JSON.parse(
        await contract.ApproveGovernanceAction(ctx, "G-3", "t")
      );
      expect(after1.status).to.equal("PARTIALLY_APPROVED");
      expect(after1.approvers).to.have.lengthOf(1);
      expect(after1.approvers[0].msp).to.equal("Org1MSP");
      // 同 MSP 再批
      await expect(
        contract.ApproveGovernanceAction(ctx, "G-3", "t")
      ).to.be.rejectedWith(/已批准过该提案/);
    });

    it("不同 MSP 第二次批准 → APPROVED；APPROVED 后再批被拒", async () => {
      ctx.clientIdentity.getMSPID.returns("Org1MSP");
      await contract.ProposeGovernanceAction(
        ctx, "G-4", "FORCE_DELETE_RECORD", JSON.stringify({ recordId: "9" }), "t"
      );
      await contract.ApproveGovernanceAction(ctx, "G-4", "t");
      ctx.clientIdentity.getMSPID.returns("Org2MSP");
      const after2 = JSON.parse(
        await contract.ApproveGovernanceAction(ctx, "G-4", "t")
      );
      expect(after2.status).to.equal("APPROVED");
      expect(new Set(after2.approvers.map((a) => a.msp)).size).to.equal(2);

      // APPROVED 后再批被拒
      ctx.clientIdentity.getMSPID.returns("Org1MSP");
      await expect(
        contract.ApproveGovernanceAction(ctx, "G-4", "t")
      ).to.be.rejectedWith(/已 APPROVED/);
    });

    it("未 APPROVED 直接 Execute → 抛错；APPROVED 后 Execute → EXECUTED", async () => {
      ctx.clientIdentity.getMSPID.returns("Org1MSP");
      await contract.ProposeGovernanceAction(
        ctx, "G-5", "FREEZE_RECORD", JSON.stringify({ recordId: "3" }), "t"
      );
      await expect(
        contract.ExecuteGovernanceAction(ctx, "G-5", "t")
      ).to.be.rejectedWith(/仅 APPROVED 可执行/);

      await contract.ApproveGovernanceAction(ctx, "G-5", "t");
      ctx.clientIdentity.getMSPID.returns("Org2MSP");
      await contract.ApproveGovernanceAction(ctx, "G-5", "t");

      const final = JSON.parse(
        await contract.ExecuteGovernanceAction(ctx, "G-5", "t-now")
      );
      expect(final.status).to.equal("EXECUTED");
      expect(final.executedAt).to.equal("t-now");

      // EXECUTED 后再批/执行都拒绝
      await expect(
        contract.ExecuteGovernanceAction(ctx, "G-5", "t")
      ).to.be.rejectedWith(/仅 APPROVED 可执行/);
      await expect(
        contract.ApproveGovernanceAction(ctx, "G-5", "t")
      ).to.be.rejectedWith(/处于终态/);
    });

    it("REJECTED 是终态：不能再批准 / 执行", async () => {
      ctx.clientIdentity.getMSPID.returns("Org1MSP");
      await contract.ProposeGovernanceAction(
        ctx, "G-6", "FREEZE_RECORD", "{}", "t"
      );
      await contract.RejectGovernanceAction(ctx, "G-6", "t");
      await expect(
        contract.ApproveGovernanceAction(ctx, "G-6", "t")
      ).to.be.rejectedWith(/处于终态/);
      await expect(
        contract.ExecuteGovernanceAction(ctx, "G-6", "t")
      ).to.be.rejectedWith(/仅 APPROVED 可执行/);
    });

    it("ListGovernanceActions 富查询能按 status 过滤", async () => {
      ctx.clientIdentity.getMSPID.returns("Org1MSP");
      await contract.ProposeGovernanceAction(
        ctx, "G-L1", "FREEZE_RECORD", "{}", "2026-05-27T00:00:00Z"
      );
      await contract.ProposeGovernanceAction(
        ctx, "G-L2", "UNFREEZE_RECORD", "{}", "2026-05-28T00:00:00Z"
      );
      await contract.RejectGovernanceAction(ctx, "G-L2", "t");

      const proposed = JSON.parse(
        await contract.ListGovernanceActions(ctx, "PROPOSED", "20", "")
      );
      expect(proposed.records).to.have.lengthOf(1);
      expect(proposed.records[0].actionId).to.equal("G-L1");

      const rejected = JSON.parse(
        await contract.ListGovernanceActions(ctx, "REJECTED", "20", "")
      );
      expect(rejected.records).to.have.lengthOf(1);
      expect(rejected.records[0].actionId).to.equal("G-L2");

      const all = JSON.parse(
        await contract.ListGovernanceActions(ctx, "", "20", "")
      );
      expect(all.records).to.have.lengthOf(2);
    });
  });

  // ---------------- 迭代 11：链上紧急冻结 + 治理解冻闭环 ----------------
  describe("链上紧急冻结（迭代 11）", () => {
    // 把 record 和 access request 准备好的 helper
    async function setupApprovedRequest(opts = {}) {
      const {
        recordId = "1",
        patientId = "2",
        hospital = "HospitalA",
        applicantHospital = "HospitalB",
        applicantMsp = "Org2MSP",
      } = opts;
      // record 由 Org1 上传
      ctx.clientIdentity.getMSPID.returns("Org1MSP");
      await contract.CreateMedicalRecordEvidence(
        ctx, recordId, patientId, hospital, "h0", "2026-05-27T00:00:00Z"
      );
      // request 由 Org2 申请
      ctx.clientIdentity.getMSPID.returns(applicantMsp);
      await contract.CreateAccessRequest(
        ctx, "10", recordId, applicantHospital, patientId,
        "rh", "PENDING", "t"
      );
      // 患者审批（链码不验证调用方）
      await contract.ApproveAccessRequest(ctx, "10", "t", 7, 3);
    }

    it("患者可冻结自己的病历；冻结后 record.frozen=true", async () => {
      ctx.clientIdentity.getMSPID.returns("Org1MSP");
      await contract.CreateMedicalRecordEvidence(
        ctx, "1", "2", "HospitalA", "h", "2026-05-27T00:00:00Z"
      );
      const raw = await contract.FreezeRecord(ctx, "1", "2", "reason-hash", "t");
      const after = JSON.parse(raw);
      expect(after.frozen).to.equal(true);
      expect(after.freezeReasonHash).to.equal("reason-hash");
      expect(after.freezeTxId).to.not.equal("");
      const ev = ctx.stub._events.find((e) => e.name === "RecordFrozen");
      expect(ev).to.not.equal(undefined);
    });

    it("非归属患者无法冻结", async () => {
      ctx.clientIdentity.getMSPID.returns("Org1MSP");
      await contract.CreateMedicalRecordEvidence(
        ctx, "1", "2", "HospitalA", "h", "2026-05-27T00:00:00Z"
      );
      await expect(
        contract.FreezeRecord(ctx, "1", "999", "rh", "t")
      ).to.be.rejectedWith(/只有归属患者可以冻结/);
    });

    it("冻结后 UpdateMedicalRecordEvidence 被拒（关键守卫）", async () => {
      ctx.clientIdentity.getMSPID.returns("Org1MSP");
      await contract.CreateMedicalRecordEvidence(
        ctx, "1", "2", "HospitalA", "h", "2026-05-27T00:00:00Z"
      );
      await contract.FreezeRecord(ctx, "1", "2", "rh", "t");
      await expect(
        contract.UpdateMedicalRecordEvidence(ctx, "1", "h2", "t2")
      ).to.be.rejectedWith(/已被冻结，无法修订/);
    });

    it("冻结后 AccessRecord 被拒（关键守卫）", async () => {
      await setupApprovedRequest();
      // patient 冻结
      ctx.clientIdentity.getMSPID.returns("Org1MSP");
      await contract.FreezeRecord(ctx, "1", "2", "rh", "t");
      // 医院 B 尝试消费授权
      ctx.clientIdentity.getMSPID.returns("Org2MSP");
      await expect(
        contract.AccessRecord(ctx, "10", "t-access")
      ).to.be.rejectedWith(/已被冻结，链码层拒绝访问/);
    });

    it("无治理 actionId → 解冻失败；治理未 EXECUTED → 解冻失败", async () => {
      ctx.clientIdentity.getMSPID.returns("Org1MSP");
      await contract.CreateMedicalRecordEvidence(
        ctx, "1", "2", "HospitalA", "h", "2026-05-27T00:00:00Z"
      );
      await contract.FreezeRecord(ctx, "1", "2", "rh", "t");

      await expect(
        contract.UnfreezeRecord(ctx, "1", "", "t")
      ).to.be.rejectedWith(/必须传入治理动作 ID/);

      // 只提案不批准
      await contract.ProposeGovernanceAction(
        ctx, "G-U1", "UNFREEZE_RECORD",
        JSON.stringify({ recordId: "1" }), "t"
      );
      await expect(
        contract.UnfreezeRecord(ctx, "1", "G-U1", "t")
      ).to.be.rejectedWith(/必须为 EXECUTED/);
    });

    it("治理 kind 不匹配 / payload.recordId 不匹配 → 解冻失败", async () => {
      ctx.clientIdentity.getMSPID.returns("Org1MSP");
      await contract.CreateMedicalRecordEvidence(
        ctx, "1", "2", "HospitalA", "h", "2026-05-27T00:00:00Z"
      );
      await contract.FreezeRecord(ctx, "1", "2", "rh", "t");

      // kind=FREEZE_RECORD（错误 kind），即便 EXECUTED 也拒
      await contract.ProposeGovernanceAction(
        ctx, "G-WRONG", "FREEZE_RECORD",
        JSON.stringify({ recordId: "1" }), "t"
      );
      await contract.ApproveGovernanceAction(ctx, "G-WRONG", "t");
      ctx.clientIdentity.getMSPID.returns("Org2MSP");
      await contract.ApproveGovernanceAction(ctx, "G-WRONG", "t");
      await contract.ExecuteGovernanceAction(ctx, "G-WRONG", "t");
      await expect(
        contract.UnfreezeRecord(ctx, "1", "G-WRONG", "t")
      ).to.be.rejectedWith(/kind=FREEZE_RECORD/);

      // 正确 kind 但 recordId 不一致
      ctx.clientIdentity.getMSPID.returns("Org1MSP");
      await contract.ProposeGovernanceAction(
        ctx, "G-MISS", "UNFREEZE_RECORD",
        JSON.stringify({ recordId: "999" }), "t"
      );
      await contract.ApproveGovernanceAction(ctx, "G-MISS", "t");
      ctx.clientIdentity.getMSPID.returns("Org2MSP");
      await contract.ApproveGovernanceAction(ctx, "G-MISS", "t");
      await contract.ExecuteGovernanceAction(ctx, "G-MISS", "t");
      await expect(
        contract.UnfreezeRecord(ctx, "1", "G-MISS", "t")
      ).to.be.rejectedWith(/payload\.recordId=999/);
    });

    it("正确治理 EXECUTED → 解冻成功，后续写入恢复", async () => {
      ctx.clientIdentity.getMSPID.returns("Org1MSP");
      await contract.CreateMedicalRecordEvidence(
        ctx, "1", "2", "HospitalA", "h", "2026-05-27T00:00:00Z"
      );
      await contract.FreezeRecord(ctx, "1", "2", "rh", "t");

      await contract.ProposeGovernanceAction(
        ctx, "G-OK", "UNFREEZE_RECORD",
        JSON.stringify({ recordId: "1" }), "t"
      );
      await contract.ApproveGovernanceAction(ctx, "G-OK", "t");
      ctx.clientIdentity.getMSPID.returns("Org2MSP");
      await contract.ApproveGovernanceAction(ctx, "G-OK", "t");
      await contract.ExecuteGovernanceAction(ctx, "G-OK", "t-now");

      ctx.clientIdentity.getMSPID.returns("Org1MSP");
      const after = JSON.parse(
        await contract.UnfreezeRecord(ctx, "1", "G-OK", "t")
      );
      expect(after.frozen).to.equal(false);
      expect(after.unfreezeGovTxId).to.not.equal("");

      // 恢复后可修订
      await contract.UpdateMedicalRecordEvidence(ctx, "1", "h2", "t2");
      const final = JSON.parse(await contract.GetRecordLatest(ctx, "1"));
      expect(final.version).to.equal(2);
    });
  });

  // ---------------- 迭代 12：链码 v2 升级 + 状态迁移 ----------------
  describe("链码 v2 升级（迭代 12）", () => {
    it("GetSchemaVersion 返回 v2", () => {
      expect(contract.GetSchemaVersion()).to.equal("v2");
    });

    it("v2 创建 record 时传入 category → 持久化", async () => {
      ctx.clientIdentity.getMSPID.returns("Org1MSP");
      await contract.CreateMedicalRecordEvidence(
        ctx, "1", "2", "HospitalA", "h", "2026-05-27T00:00:00Z",
        "EMERGENCY"
      );
      const got = JSON.parse(await contract.GetRecordLatest(ctx, "1"));
      expect(got.category).to.equal("EMERGENCY");
    });

    it("缺省 category → GENERAL；未知 category → 抛错", async () => {
      ctx.clientIdentity.getMSPID.returns("Org1MSP");
      await contract.CreateMedicalRecordEvidence(
        ctx, "2", "2", "HospitalA", "h", "t"
      );
      const got = JSON.parse(await contract.GetRecordLatest(ctx, "2"));
      expect(got.category).to.equal("GENERAL");

      await expect(
        contract.CreateMedicalRecordEvidence(
          ctx, "3", "2", "HospitalA", "h", "t", "DENTAL"
        )
      ).to.be.rejectedWith(/未知 category/);
    });

    it("兼容读：模拟一条老 record（无 category）→ GetRecordLatest 自动补 GENERAL", async () => {
      // 直接绕过 Create 写入老 schema（无 category）
      await ctx.stub.putState(
        "RECORD_LATEST_99",
        Buffer.from(JSON.stringify({
          docType: "RecordEvidence",
          recordId: "99",
          patientId: "2",
          uploaderHospital: "HospitalA",
          dataHash: "hold",
          version: 1,
          previousTxId: "",
          createdAt: "2026-01-01T00:00:00Z",
          updatedAt: "2026-01-01T00:00:00Z",
          txId: "old-tx",
          isLatest: true,
        }))
      );
      const got = JSON.parse(await contract.GetRecordLatest(ctx, "99"));
      expect(got.category).to.equal("GENERAL");
    });

    it("MigrateRecordsV2：Org1MSP 一次性迁移；幂等；非 Org1MSP 拒绝", async () => {
      ctx.clientIdentity.getMSPID.returns("Org1MSP");
      await contract.CreateMedicalRecordEvidence(
        ctx, "1", "2", "HospitalA", "h", "t"
      );
      await contract.CreateMedicalRecordEvidence(
        ctx, "2", "2", "HospitalA", "h", "t"
      );

      const r = JSON.parse(
        await contract.MigrateRecordsV2(
          ctx,
          JSON.stringify([
            { recordId: "1", category: "INPATIENT" },
            { recordId: "2", category: "OUTPATIENT" },
          ])
        )
      );
      expect(r.count).to.equal(2);
      const g1 = JSON.parse(await contract.GetRecordLatest(ctx, "1"));
      expect(g1.category).to.equal("INPATIENT");
      expect(g1._migratedAt).to.not.equal(undefined);

      // 幂等：再次相同迁移 → 0
      const r2 = JSON.parse(
        await contract.MigrateRecordsV2(
          ctx,
          JSON.stringify([{ recordId: "1", category: "INPATIENT" }])
        )
      );
      expect(r2.count).to.equal(0);

      // 非 Org1MSP 拒
      ctx.clientIdentity.getMSPID.returns("Org2MSP");
      await expect(
        contract.MigrateRecordsV2(
          ctx, JSON.stringify([{ recordId: "1", category: "INPATIENT" }])
        )
      ).to.be.rejectedWith(/仅 Org1MSP/);
    });

    it("QueryRecordsByCategory 按 category 过滤", async () => {
      ctx.clientIdentity.getMSPID.returns("Org1MSP");
      await contract.CreateMedicalRecordEvidence(
        ctx, "1", "2", "HospitalA", "h", "t", "INPATIENT"
      );
      await contract.CreateMedicalRecordEvidence(
        ctx, "2", "2", "HospitalA", "h", "t", "OUTPATIENT"
      );
      await contract.CreateMedicalRecordEvidence(
        ctx, "3", "2", "HospitalA", "h", "t", "INPATIENT"
      );

      const out = JSON.parse(
        await contract.QueryRecordsByCategory(ctx, "INPATIENT", "20", "")
      );
      expect(out.records).to.have.lengthOf(2);
      expect(out.records.every((r) => r.category === "INPATIENT")).to.equal(true);
    });

    it("v2 创建 request 时传入 purpose → 持久化；缺省 TREATMENT", async () => {
      await contract.CreateAccessRequest(
        ctx, "100", "1", "HospitalB", "2", "rh",
        "PENDING", "t", "RESEARCH"
      );
      const r = JSON.parse(await contract.QueryAccessRequest(ctx, "100"));
      expect(r.purpose).to.equal("RESEARCH");

      await contract.CreateAccessRequest(
        ctx, "101", "1", "HospitalB", "2", "rh", "PENDING", "t"
      );
      const r2 = JSON.parse(await contract.QueryAccessRequest(ctx, "101"));
      expect(r2.purpose).to.equal("TREATMENT");

      await expect(
        contract.CreateAccessRequest(
          ctx, "102", "1", "HospitalB", "2", "rh", "PENDING", "t", "DENTAL"
        )
      ).to.be.rejectedWith(/未知 purpose/);
    });
  });

  // ---------------- 迭代 13：数据共享积分（FT） ----------------
  describe("数据共享积分（迭代 13）", () => {
    beforeEach(() => {
      ctx.clientIdentity.getMSPID.returns("Org1MSP");
    });

    it("admin CreditMint 增加余额；非 Org1MSP 拒绝", async () => {
      const got = JSON.parse(
        await contract.CreditMint(ctx, "user-1", "10", "WELCOME", "t")
      );
      expect(got.balance).to.equal(10);

      ctx.clientIdentity.getMSPID.returns("Org2MSP");
      await expect(
        contract.CreditMint(ctx, "user-1", "1", "X", "t")
      ).to.be.rejectedWith(/仅 Org1MSP/);
    });

    it("CreditBalance 不存在用户返回 0", async () => {
      const got = JSON.parse(await contract.CreditBalance(ctx, "no-one"));
      expect(got.balance).to.equal(0);
    });

    it("CreditTransfer 余额不足 → 抛错；双方余额不变（原子性）", async () => {
      await contract.CreditMint(ctx, "alice", "5", "INIT", "t");
      await expect(
        contract.CreditTransfer(ctx, "alice", "bob", "100", "X", "t")
      ).to.be.rejectedWith(/余额不足/);
      const alice = JSON.parse(await contract.CreditBalance(ctx, "alice"));
      const bob = JSON.parse(await contract.CreditBalance(ctx, "bob"));
      expect(alice.balance).to.equal(5);
      expect(bob.balance).to.equal(0);
    });

    it("CreditTransfer 自转抛错", async () => {
      await contract.CreditMint(ctx, "alice", "5", "INIT", "t");
      await expect(
        contract.CreditTransfer(ctx, "alice", "alice", "1", "X", "t")
      ).to.be.rejectedWith(/不允许自转/);
    });

    it("CreditTransfer 成功扣加双方余额", async () => {
      await contract.CreditMint(ctx, "alice", "10", "INIT", "t");
      ctx.stub.setTxID("transfer-tx-1");
      const out = JSON.parse(
        await contract.CreditTransfer(ctx, "alice", "bob", "3", "GIFT", "t")
      );
      expect(out.fromBalance).to.equal(7);
      expect(out.toBalance).to.equal(3);
      expect(out.ledger.amount).to.equal(3);
      const ev = ctx.stub._events.find((e) => e.name === "CreditTransferred");
      expect(ev).to.not.equal(undefined);
    });

    it("CreateMedicalRecordEvidence 自动给 uploaderHospital +5 分", async () => {
      await contract.CreateMedicalRecordEvidence(
        ctx, "1", "2", "HospitalA", "h", "t"
      );
      const got = JSON.parse(await contract.CreditBalance(ctx, "HospitalA"));
      expect(got.balance).to.equal(5);
    });

    it("AccessRecord 成功消费后 uploaderHospital +1 分", async () => {
      // HospitalA 上传 → +5
      await contract.CreateMedicalRecordEvidence(
        ctx, "1", "2", "HospitalA", "h", "t"
      );
      // HospitalB 申请 → 患者 patient_2 审批 → 患者 +1
      ctx.clientIdentity.getMSPID.returns("Org2MSP");
      await contract.CreateAccessRequest(
        ctx, "10", "1", "HospitalB", "2", "rh", "PENDING", "t"
      );
      ctx.clientIdentity.getMSPID.returns("Org1MSP");
      await contract.ApproveAccessRequest(ctx, "10", "t", 7, 3);
      const patientBal = JSON.parse(await contract.CreditBalance(ctx, "2"));
      expect(patientBal.balance).to.equal(1);

      // HospitalB 消费授权 → HospitalA +1
      ctx.clientIdentity.getMSPID.returns("Org2MSP");
      await contract.AccessRecord(ctx, "10", "t-now");
      const hospABal = JSON.parse(await contract.CreditBalance(ctx, "HospitalA"));
      expect(hospABal.balance).to.equal(6);
    });

    it("CreditHistory 富查询返回双向流水", async () => {
      await contract.CreditMint(ctx, "alice", "10", "INIT", "t");
      ctx.stub.setTxID("t1");
      await contract.CreditTransfer(ctx, "alice", "bob", "3", "GIFT", "t");
      ctx.stub.setTxID("t2");
      await contract.CreditTransfer(ctx, "alice", "carol", "2", "GIFT", "t");
      const hist = JSON.parse(
        await contract.CreditHistory(ctx, "alice", "20", "")
      );
      // alice 应能看到至少 3 条：INIT mint + 2 transfer
      expect(hist.records.length).to.be.at.least(3);
    });
  });
});
