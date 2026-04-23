"use strict";

const { WorkloadModuleBase } = require("@hyperledger/caliper-core");

// 场景 1：纯写入 —— CreateMedicalRecordEvidence 批量上链
class CreateRecordsWorkload extends WorkloadModuleBase {
  constructor() {
    super();
    this.txIndex = 0;
    this.hospital = "HospitalA";
    this.patientId = "2";
  }

  async initializeWorkloadModule(workerIndex, totalWorkers, roundIndex, roundArgs, sutAdapter, sutContext) {
    await super.initializeWorkloadModule(workerIndex, totalWorkers, roundIndex, roundArgs, sutAdapter, sutContext);
    this.hospital = roundArgs.hospital || "HospitalA";
    this.patientId = roundArgs.patientId || "2";
  }

  async submitTransaction() {
    // 每个 worker 的 recordId 命名空间隔离：worker-N-idx
    const recordId = `bench-w${this.workerIndex}-${this.txIndex}-${Date.now()}`;
    this.txIndex += 1;
    const createdAt = new Date().toISOString();
    const args = {
      contractId: "medshare",
      contractFunction: "CreateMedicalRecordEvidence",
      invokerIdentity: "admin.org1",
      contractArguments: [recordId, this.patientId, this.hospital, "hash-bench", createdAt],
      readOnly: false,
    };
    await this.sutAdapter.sendRequests(args);
  }
}

module.exports.createWorkloadModule = () => new CreateRecordsWorkload();
