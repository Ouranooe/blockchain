"use strict";

const { Contract } = require("fabric-contract-api");

// 状态机（迭代 5 收紧）
//                 REVOKED (patient 撤销)
//               /
// PENDING -> APPROVED
//       \         \
//        \         EXPIRED (通过 AccessRecord 时隐式检测)
//         \
//          REJECTED
const ALLOWED_TRANSITIONS = {
  PENDING: new Set(["APPROVED", "REJECTED"]),
  APPROVED: new Set(["REVOKED"]),
  REJECTED: new Set([]),
  REVOKED: new Set([]),
};

class MedShareContract extends Contract {
  // ---------------- 键设计 ----------------
  _versionKey(recordId, version) {
    return `RECORD_${recordId}_v${version}`;
  }

  _latestKey(recordId) {
    return `RECORD_LATEST_${recordId}`;
  }

  _recordKey(recordId) {
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

  // ---------------- 时间戳与身份辅助（迭代 5） ----------------
  _txTimestampSeconds(ctx) {
    try {
      const ts = ctx.stub.getTxTimestamp();
      if (!ts) return Math.floor(Date.now() / 1000);
      const sec =
        typeof ts.seconds === "object" && ts.seconds !== null
          ? Number(ts.seconds.low || 0) + Number(ts.seconds.high || 0) * 2 ** 32
          : Number(ts.seconds || 0);
      return Number.isFinite(sec) && sec > 0
        ? sec
        : Math.floor(Date.now() / 1000);
    } catch (_e) {
      return Math.floor(Date.now() / 1000);
    }
  }

  _callerMsp(ctx) {
    try {
      if (ctx.clientIdentity && typeof ctx.clientIdentity.getMSPID === "function") {
        return ctx.clientIdentity.getMSPID() || "";
      }
    } catch (_e) {}
    return "";
  }

  _isoFromSeconds(sec) {
    return new Date(sec * 1000).toISOString();
  }

  // ---------------- 历史迭代方法 ----------------
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
            value: parsed,
          });
        }
        if (!res || res.done) break;
      }
    } finally {
      if (iterator && typeof iterator.close === "function") {
        await iterator.close();
      }
    }
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

  // ---------------- 病历版本链（沿用迭代 2 / 3） ----------------
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
      txId: ctx.stub.getTxID(),
    };

    await this._putStateAsObject(ctx, this._versionKey(recordId, 1), evidence);
    await this._putStateAsObject(ctx, latestKey, evidence);
    ctx.stub.setEvent(
      "RecordCreated",
      Buffer.from(
        JSON.stringify({
          recordId,
          patientId,
          uploaderHospital,
          dataHash,
          version: 1,
          txId: evidence.txId,
        })
      )
    );
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
      txId: ctx.stub.getTxID(),
    };

    await this._putStateAsObject(
      ctx,
      this._versionKey(recordId, newVersion),
      newEvidence
    );
    await this._putStateAsObject(ctx, latestKey, newEvidence);
    ctx.stub.setEvent(
      "RecordUpdated",
      Buffer.from(
        JSON.stringify({
          recordId,
          patientId: newEvidence.patientId,
          uploaderHospital: newEvidence.uploaderHospital,
          dataHash: newDataHash,
          version: newVersion,
          previousTxId: latest.txId,
          txId: newEvidence.txId,
        })
      )
    );
    return JSON.stringify(newEvidence);
  }

  async GetMedicalRecordEvidence(ctx, recordId) {
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

  // ---------------- 访问申请 ABAC（迭代 5 重写） ----------------

  async CreateAccessRequest(
    ctx,
    requestId,
    recordId,
    applicantHospital,
    patientId,
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
      applicantMsp: this._callerMsp(ctx), // 迭代 5：绑定申请方的 MSP
      patientId,                          // 迭代 5：记录归属患者
      reasonHash,
      status: status || "PENDING",
      createdAt,
      reviewedAt: "",
      revokedAt: "",
      expiresAt: "",
      expiresAtTs: 0,
      remainingReads: 0,
      readsUsed: 0,
      createTxId: ctx.stub.getTxID(),
      reviewTxId: "",
      revokeTxId: "",
      lastAccessTxId: "",
    };

    await this._putStateAsObject(ctx, key, request);
    ctx.stub.setEvent(
      "AccessRequestCreated",
      Buffer.from(JSON.stringify({ requestId, recordId, applicantHospital }))
    );
    return JSON.stringify(request);
  }

  async ApproveAccessRequest(ctx, requestId, reviewedAt, durationDays, maxReads) {
    const key = this._requestKey(requestId);
    const request = await this._getStateAsObject(ctx, key);
    if (!request) {
      throw new Error(`Access request ${requestId} not found`);
    }
    if (!ALLOWED_TRANSITIONS[request.status].has("APPROVED")) {
      throw new Error(
        `非法状态跃迁：${request.status} → APPROVED（访问申请 ${requestId}）`
      );
    }

    const duration = Number(durationDays || 0);
    const reads = Number(maxReads || 0);
    if (!Number.isFinite(duration) || duration <= 0) {
      throw new Error("durationDays 必须为正数");
    }
    if (!Number.isFinite(reads) || reads <= 0) {
      throw new Error("maxReads 必须为正数");
    }

    const nowTs = this._txTimestampSeconds(ctx);
    const expiresAtTs = nowTs + Math.floor(duration * 86400);

    request.status = "APPROVED";
    request.reviewedAt = reviewedAt;
    request.reviewTxId = ctx.stub.getTxID();
    request.expiresAtTs = expiresAtTs;
    request.expiresAt = this._isoFromSeconds(expiresAtTs);
    request.remainingReads = reads;
    request.readsUsed = 0;

    await this._putStateAsObject(ctx, key, request);
    ctx.stub.setEvent(
      "AccessApproved",
      Buffer.from(
        JSON.stringify({
          requestId,
          recordId: request.recordId,
          expiresAt: request.expiresAt,
          remainingReads: request.remainingReads,
        })
      )
    );
    return JSON.stringify(request);
  }

  async RejectAccessRequest(ctx, requestId, reviewedAt) {
    const key = this._requestKey(requestId);
    const request = await this._getStateAsObject(ctx, key);
    if (!request) {
      throw new Error(`Access request ${requestId} not found`);
    }
    if (!ALLOWED_TRANSITIONS[request.status].has("REJECTED")) {
      throw new Error(
        `非法状态跃迁：${request.status} → REJECTED（访问申请 ${requestId}）`
      );
    }
    request.status = "REJECTED";
    request.reviewedAt = reviewedAt;
    request.reviewTxId = ctx.stub.getTxID();
    await this._putStateAsObject(ctx, key, request);
    ctx.stub.setEvent(
      "AccessRejected",
      Buffer.from(JSON.stringify({ requestId, recordId: request.recordId }))
    );
    return JSON.stringify(request);
  }

  async RevokeAccessRequest(ctx, requestId, patientId, revokedAt) {
    const key = this._requestKey(requestId);
    const request = await this._getStateAsObject(ctx, key);
    if (!request) {
      throw new Error(`Access request ${requestId} not found`);
    }
    if (!ALLOWED_TRANSITIONS[request.status].has("REVOKED")) {
      throw new Error(
        `非法状态跃迁：${request.status} → REVOKED（访问申请 ${requestId}）`
      );
    }
    if (String(request.patientId) !== String(patientId)) {
      throw new Error("只有归属患者可以撤销授权");
    }

    request.status = "REVOKED";
    request.revokedAt = revokedAt;
    request.revokeTxId = ctx.stub.getTxID();
    await this._putStateAsObject(ctx, key, request);
    ctx.stub.setEvent(
      "AccessRevoked",
      Buffer.from(JSON.stringify({ requestId, recordId: request.recordId }))
    );
    return JSON.stringify(request);
  }

  /**
   * 迭代 5 核心：一次"读取访问"的链上校验与计数扣减。
   * 所有校验失败都抛错，链码层拒绝 —— 无论调用是否来自后端。
   *
   * 校验清单（全部需通过）：
   *   1) 请求存在
   *   2) status == APPROVED
   *   3) 未过期（getTxTimestamp < expiresAtTs）
   *   4) remainingReads > 0
   *   5) 调用方 MSP == state.applicantMsp（防止 Org2 盗用 Org1 的授权）
   */
  async AccessRecord(ctx, requestId, accessedAt) {
    const key = this._requestKey(requestId);
    const request = await this._getStateAsObject(ctx, key);
    if (!request) {
      throw new Error(`Access request ${requestId} not found`);
    }
    if (request.status !== "APPROVED") {
      throw new Error(
        `授权不可用：当前状态 ${request.status}（访问申请 ${requestId}）`
      );
    }
    const nowTs = this._txTimestampSeconds(ctx);
    if (request.expiresAtTs && nowTs >= request.expiresAtTs) {
      throw new Error(
        `授权已过期（expiresAt: ${request.expiresAt || "-"}）`
      );
    }
    if (!request.remainingReads || request.remainingReads <= 0) {
      throw new Error("访问次数已用尽");
    }
    const callerMsp = this._callerMsp(ctx);
    if (
      request.applicantMsp &&
      callerMsp &&
      request.applicantMsp !== callerMsp
    ) {
      throw new Error(
        `调用方 MSP (${callerMsp}) 与授权绑定 MSP (${request.applicantMsp}) 不一致`
      );
    }

    request.remainingReads -= 1;
    request.readsUsed = (request.readsUsed || 0) + 1;
    request.lastAccessTxId = ctx.stub.getTxID();

    await this._putStateAsObject(ctx, key, request);
    ctx.stub.setEvent(
      "AccessRecorded",
      Buffer.from(
        JSON.stringify({
          requestId,
          recordId: request.recordId,
          remainingReads: request.remainingReads,
          accessedAt,
          callerMsp,
          txId: ctx.stub.getTxID(),
        })
      )
    );
    return JSON.stringify({
      requestId,
      recordId: request.recordId,
      remainingReads: request.remainingReads,
      readsUsed: request.readsUsed,
      accessedAt,
      txId: ctx.stub.getTxID(),
    });
  }

  async QueryAccessRequest(ctx, requestId) {
    const key = this._requestKey(requestId);
    const request = await this._getStateAsObject(ctx, key);
    if (!request) {
      throw new Error(`Access request ${requestId} not found`);
    }
    // 若 APPROVED 但已过期，只做"视图"上的标记，不修改状态（状态要靠下一次 AccessRecord 才能感知到 EXPIRED 语义；这是纯粹的只读视图）
    return JSON.stringify(request);
  }
}

module.exports = MedShareContract;
