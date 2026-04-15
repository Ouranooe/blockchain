"use strict";

const { Contract } = require("fabric-contract-api");

class MedShareContract extends Contract {
  _recordKey(recordId) {
    return `RECORD_${recordId}`;
  }

  _requestKey(requestId) {
    return `REQ_${requestId}`;
  }

  async _getStateAsObject(ctx, key) {
    const bytes = await ctx.stub.getState(key);
    if (!bytes || bytes.length === 0) {
      return null;
    }
    return JSON.parse(bytes.toString("utf8"));
  }

  async CreateMedicalRecordEvidence(
    ctx,
    recordId,
    patientId,
    uploaderHospital,
    dataHash,
    createdAt
  ) {
    const key = this._recordKey(recordId);
    const existing = await this._getStateAsObject(ctx, key);
    if (existing) {
      throw new Error(`Record evidence ${recordId} already exists`);
    }

    const evidence = {
      docType: "RecordEvidence",
      recordId,
      patientId,
      uploaderHospital,
      dataHash,
      createdAt,
      txId: ctx.stub.getTxID()
    };

    await ctx.stub.putState(key, Buffer.from(JSON.stringify(evidence)));
    return JSON.stringify(evidence);
  }

  async GetMedicalRecordEvidence(ctx, recordId) {
    const key = this._recordKey(recordId);
    const evidence = await this._getStateAsObject(ctx, key);
    if (!evidence) {
      throw new Error(`Record evidence ${recordId} not found`);
    }
    return JSON.stringify(evidence);
  }

  async CreateAccessRequest(
    ctx,
    requestId,
    recordId,
    applicantHospital,
    reasonHash,
    status,
    createdAt
  ) {
    const key = this._requestKey(requestId);
    const existing = await this._getStateAsObject(ctx, key);
    if (existing) {
      throw new Error(`Access request ${requestId} already exists`);
    }

    const request = {
      docType: "AccessRequest",
      requestId,
      recordId,
      applicantHospital,
      reasonHash,
      status: status || "PENDING",
      createdAt,
      reviewedAt: "",
      createTxId: ctx.stub.getTxID(),
      reviewTxId: ""
    };

    await ctx.stub.putState(key, Buffer.from(JSON.stringify(request)));
    return JSON.stringify(request);
  }

  async ApproveAccessRequest(ctx, requestId, reviewedAt) {
    const key = this._requestKey(requestId);
    const request = await this._getStateAsObject(ctx, key);
    if (!request) {
      throw new Error(`Access request ${requestId} not found`);
    }
    request.status = "APPROVED";
    request.reviewedAt = reviewedAt;
    request.reviewTxId = ctx.stub.getTxID();
    await ctx.stub.putState(key, Buffer.from(JSON.stringify(request)));
    return JSON.stringify(request);
  }

  async RejectAccessRequest(ctx, requestId, reviewedAt) {
    const key = this._requestKey(requestId);
    const request = await this._getStateAsObject(ctx, key);
    if (!request) {
      throw new Error(`Access request ${requestId} not found`);
    }
    request.status = "REJECTED";
    request.reviewedAt = reviewedAt;
    request.reviewTxId = ctx.stub.getTxID();
    await ctx.stub.putState(key, Buffer.from(JSON.stringify(request)));
    return JSON.stringify(request);
  }

  async QueryAccessRequest(ctx, requestId) {
    const key = this._requestKey(requestId);
    const request = await this._getStateAsObject(ctx, key);
    if (!request) {
      throw new Error(`Access request ${requestId} not found`);
    }
    return JSON.stringify(request);
  }
}

module.exports = MedShareContract;
