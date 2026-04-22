"use strict";

const { Contract } = require("fabric-contract-api");

class MedShareContract extends Contract {
  // ---------------- 键设计（迭代 2 引入版本链） ----------------
  // RECORD_{id}_v{version}  每版的完整内容
  // RECORD_LATEST_{id}      最新版完整内容的冗余拷贝（热点索引，O(1) 读最新版）
  // REQ_{requestId}         访问申请

  _versionKey(recordId, version) {
    return `RECORD_${recordId}_v${version}`;
  }

  _latestKey(recordId) {
    return `RECORD_LATEST_${recordId}`;
  }

  _recordKey(recordId) {
    // 向后兼容：原 _recordKey 被等价为指向最新版的 LATEST 键
    return this._latestKey(recordId);
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

  async _putStateAsObject(ctx, key, value) {
    await ctx.stub.putState(key, Buffer.from(JSON.stringify(value)));
  }

  // ---------------- 迭代 3：Fabric 原生历史查询 ----------------

  _formatTimestamp(ts) {
    if (!ts) return null;
    const secondsField = ts.seconds;
    const seconds =
      typeof secondsField === "object" && secondsField !== null
        ? Number(secondsField.low || 0)
        : Number(secondsField || 0);
    const nanos = Number(ts.nanos || 0);
    if (!seconds && !nanos) return null;
    const ms = seconds * 1000 + Math.floor(nanos / 1e6);
    return new Date(ms).toISOString();
  }

  async _collectHistory(ctx, key) {
    const iterator = await ctx.stub.getHistoryForKey(key);
    const entries = [];
    try {
      while (true) {
        const res = await iterator.next();
        if (res && res.value) {
          const v = res.value;
          let parsed = null;
          if (v.value && v.value.length > 0) {
            try {
              parsed = JSON.parse(v.value.toString("utf8"));
            } catch (_err) {
              parsed = v.value.toString("utf8");
            }
          }
          entries.push({
            txId: v.txId || v.tx_id || "",
            timestamp: this._formatTimestamp(v.timestamp),
            isDelete: Boolean(v.isDelete || v.is_delete),
            value: parsed
          });
        }
        if (!res || res.done) break;
      }
    } finally {
      if (iterator && typeof iterator.close === "function") {
        await iterator.close();
      }
    }
    // 按时间倒序（最近的在前）
    entries.sort((a, b) => {
      if (!a.timestamp) return 1;
      if (!b.timestamp) return -1;
      if (a.timestamp === b.timestamp) return 0;
      return a.timestamp < b.timestamp ? 1 : -1;
    });
    return entries;
  }

  async GetRecordHistory(ctx, recordId) {
    const entries = await this._collectHistory(ctx, this._latestKey(recordId));
    if (entries.length === 0) {
      throw new Error(`Record evidence ${recordId} not found`);
    }
    return JSON.stringify(entries);
  }

  async GetAccessRequestHistory(ctx, requestId) {
    const entries = await this._collectHistory(ctx, this._requestKey(requestId));
    if (entries.length === 0) {
      throw new Error(`Access request ${requestId} not found`);
    }
    return JSON.stringify(entries);
  }

  // ---------------- 病历版本链 ----------------

  async CreateMedicalRecordEvidence(
    ctx,
    recordId,
    patientId,
    uploaderHospital,
    dataHash,
    createdAt
  ) {
    const latestKey = this._latestKey(recordId);
    const existing = await this._getStateAsObject(ctx, latestKey);
    if (existing) {
      throw new Error(`Record evidence ${recordId} already exists`);
    }

    const evidence = {
      docType: "RecordEvidence",
      recordId,
      patientId,
      uploaderHospital,
      dataHash,
      version: 1,
      previousTxId: "",
      createdAt,
      updatedAt: createdAt,
      txId: ctx.stub.getTxID()
    };

    await this._putStateAsObject(ctx, this._versionKey(recordId, 1), evidence);
    await this._putStateAsObject(ctx, latestKey, evidence);
    return JSON.stringify(evidence);
  }

  async UpdateMedicalRecordEvidence(ctx, recordId, newDataHash, updatedAt) {
    const latestKey = this._latestKey(recordId);
    const latest = await this._getStateAsObject(ctx, latestKey);
    if (!latest) {
      throw new Error(`Record evidence ${recordId} not found`);
    }

    const newVersion = latest.version + 1;
    const newEvidence = {
      docType: "RecordEvidence",
      recordId,
      patientId: latest.patientId,
      uploaderHospital: latest.uploaderHospital,
      dataHash: newDataHash,
      version: newVersion,
      previousTxId: latest.txId,
      createdAt: latest.createdAt,
      updatedAt,
      txId: ctx.stub.getTxID()
    };

    await this._putStateAsObject(
      ctx,
      this._versionKey(recordId, newVersion),
      newEvidence
    );
    await this._putStateAsObject(ctx, latestKey, newEvidence);
    return JSON.stringify(newEvidence);
  }

  async GetMedicalRecordEvidence(ctx, recordId) {
    // 向后兼容：返回最新版
    return this.GetRecordLatest(ctx, recordId);
  }

  async GetRecordLatest(ctx, recordId) {
    const evidence = await this._getStateAsObject(ctx, this._latestKey(recordId));
    if (!evidence) {
      throw new Error(`Record evidence ${recordId} not found`);
    }
    return JSON.stringify(evidence);
  }

  async GetRecordVersion(ctx, recordId, version) {
    const evidence = await this._getStateAsObject(
      ctx,
      this._versionKey(recordId, version)
    );
    if (!evidence) {
      throw new Error(`Record ${recordId} version ${version} not found`);
    }
    return JSON.stringify(evidence);
  }

  // ---------------- 访问申请（迭代 1 保留，未改动） ----------------

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

    await this._putStateAsObject(ctx, key, request);
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
    await this._putStateAsObject(ctx, key, request);
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
    await this._putStateAsObject(ctx, key, request);
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
