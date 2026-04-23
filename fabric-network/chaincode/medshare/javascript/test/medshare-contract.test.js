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
});
