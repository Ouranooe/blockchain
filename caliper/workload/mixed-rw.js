"use strict";

const { WorkloadModuleBase } = require("@hyperledger/caliper-core");

// 场景 3：混合读写 —— readRatio 默认 0.8
class MixedRWWorkload extends WorkloadModuleBase {
  constructor() {
    super();
    this.seeded = [];
    this.writeIndex = 0;
    this.readRatio = 0.8;
    this.hospital = "HospitalA";
    this.patientId = "2";
  }

  async initializeWorkloadModule(workerIndex, totalWorkers, roundIndex, roundArgs, sutAdapter, sutContext) {
    await super.initializeWorkloadModule(workerIndex, totalWorkers, roundIndex, roundArgs, sutAdapter, sutContext);
    this.readRatio = Number(roundArgs.readRatio || 0.8);
    this.hospital = roundArgs.hospital || "HospitalA";
    this.patientId = roundArgs.patientId || "2";

    // 初始种子：每个 worker 10 条
    for (let i = 0; i < 10; i++) {
      const id = `mixed-w${workerIndex}-seed-${i}`;
      this.seeded.push(id);
      try {
        await this.sutAdapter.sendRequests({
          contractId: "medshare",
          contractFunction: "CreateMedicalRecordEvidence",
          invokerIdentity: "admin.org1",
          contractArguments: [id, this.patientId, this.hospital, "seed", new Date().toISOString()],
          readOnly: false,
        });
      } catch (_e) {}
    }
  }

  async submitTransaction() {
    const isRead = Math.random() < this.readRatio;
    if (isRead && this.seeded.length > 0) {
      const target = this.seeded[Math.floor(Math.random() * this.seeded.length)];
      await this.sutAdapter.sendRequests({
        contractId: "medshare",
        contractFunction: "GetRecordLatest",
        invokerIdentity: "admin.org1",
        contractArguments: [target],
        readOnly: true,
      });
    } else {
      const id = `mixed-w${this.workerIndex}-new-${this.writeIndex}-${Date.now()}`;
      this.writeIndex += 1;
      this.seeded.push(id);
      await this.sutAdapter.sendRequests({
        contractId: "medshare",
        contractFunction: "CreateMedicalRecordEvidence",
        invokerIdentity: "admin.org1",
        contractArguments: [id, this.patientId, this.hospital, "mixed-hash", new Date().toISOString()],
        readOnly: false,
      });
    }
  }
}

module.exports.createWorkloadModule = () => new MixedRWWorkload();
