"use strict";

const { WorkloadModuleBase } = require("@hyperledger/caliper-core");

// 场景 2：纯查询 —— GetRecordLatest 为主
class QueryRecordsWorkload extends WorkloadModuleBase {
  constructor() {
    super();
    this.seeded = [];
  }

  async initializeWorkloadModule(workerIndex, totalWorkers, roundIndex, roundArgs, sutAdapter, sutContext) {
    await super.initializeWorkloadModule(workerIndex, totalWorkers, roundIndex, roundArgs, sutAdapter, sutContext);
    const seedCount = Number(roundArgs.seedRecords || 100);

    // 种子数据：每个 worker 种一部分，避免撞键
    const base = workerIndex * seedCount;
    for (let i = 0; i < seedCount; i++) {
      const id = `seed-w${workerIndex}-${base + i}`;
      this.seeded.push(id);
      try {
        await this.sutAdapter.sendRequests({
          contractId: "medshare",
          contractFunction: "CreateMedicalRecordEvidence",
          invokerIdentity: "admin.org1",
          contractArguments: [id, "2", "HospitalA", "hash-seed", new Date().toISOString()],
          readOnly: false,
        });
      } catch (_e) {
        // 幂等：若已种过，忽略
      }
    }
  }

  async submitTransaction() {
    const target = this.seeded[Math.floor(Math.random() * this.seeded.length)];
    await this.sutAdapter.sendRequests({
      contractId: "medshare",
      contractFunction: "GetRecordLatest",
      invokerIdentity: "admin.org1",
      contractArguments: [target],
      readOnly: true,
    });
  }
}

module.exports.createWorkloadModule = () => new QueryRecordsWorkload();
