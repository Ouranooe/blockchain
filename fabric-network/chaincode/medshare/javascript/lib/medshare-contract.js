"use strict";

const { Contract } = require("fabric-contract-api");
const crypto = require("crypto");

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
    // 迭代 7：LATEST 上加 isLatest 标志，便于 CouchDB 富查询只命中最新版
    await this._putStateAsObject(ctx, latestKey, { ...evidence, isLatest: true });
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
    await this._putStateAsObject(ctx, latestKey, { ...newEvidence, isLatest: true });
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

  // ---------------- 迭代 7：CouchDB 富查询 ----------------

  async _iterateQuery(iterator) {
    const out = [];
    try {
      while (true) {
        const res = await iterator.next();
        if (res && res.value) {
          let parsed = null;
          if (res.value.value && res.value.value.length > 0) {
            try {
              parsed = JSON.parse(res.value.value.toString("utf8"));
            } catch (_e) {
              parsed = null;
            }
          }
          out.push({ key: res.value.key, value: parsed });
        }
        if (!res || res.done) break;
      }
    } finally {
      if (iterator && typeof iterator.close === "function") {
        await iterator.close();
      }
    }
    return out;
  }

  async _richQueryPaged(ctx, selector, pageSize, bookmark, useIndex, sort) {
    const query = { selector };
    if (sort) query.sort = sort;
    if (useIndex) query.use_index = useIndex;
    const ps = Math.max(1, Math.min(1000, Number(pageSize) || 20));
    const { iterator, metadata } = await ctx.stub.getQueryResultWithPagination(
      JSON.stringify(query),
      ps,
      bookmark || ""
    );
    const records = await this._iterateQuery(iterator);
    return {
      records: records.map((r) => r.value).filter((v) => v !== null),
      fetchedCount:
        (metadata && (metadata.fetchedRecordsCount || metadata.fetched_records_count)) ||
        records.length,
      bookmark: (metadata && metadata.bookmark) || "",
    };
  }

  /**
   * 迭代 7：按医院（uploaderHospital）查询最新版病历。分页。
   */
  async QueryRecordsByHospital(ctx, uploaderHospital, pageSize, bookmark) {
    const selector = {
      docType: "RecordEvidence",
      isLatest: true,
      uploaderHospital,
    };
    const out = await this._richQueryPaged(
      ctx,
      selector,
      pageSize,
      bookmark,
      ["_design/indexUploaderHospitalDoc", "indexUploaderHospital"]
    );
    return JSON.stringify(out);
  }

  /**
   * 迭代 7：按时间范围查询最新版病历（createdAt 介于 [fromIso, toIso]）。分页。
   */
  async QueryRecordsByDateRange(ctx, fromIso, toIso, pageSize, bookmark) {
    const selector = {
      docType: "RecordEvidence",
      isLatest: true,
      createdAt: { $gte: String(fromIso), $lte: String(toIso) },
    };
    const out = await this._richQueryPaged(
      ctx,
      selector,
      pageSize,
      bookmark,
      ["_design/indexCreatedAtDoc", "indexCreatedAt"],
      [{ createdAt: "asc" }]
    );
    return JSON.stringify(out);
  }

  /**
   * 迭代 7：查询某患者所有 PENDING 的访问申请。
   */
  async QueryPendingRequestsForPatient(ctx, patientId, pageSize, bookmark) {
    const selector = {
      docType: "AccessRequest",
      patientId: String(patientId),
      status: "PENDING",
    };
    const out = await this._richQueryPaged(
      ctx,
      selector,
      pageSize,
      bookmark,
      ["_design/indexPatientPendingDoc", "indexPatientPending"]
    );
    return JSON.stringify(out);
  }

  // ---------------- 迭代 9：Merkle 批量锚定 + 链上包含证明 ----------------

  _batchKey(batchId) {
    return `BATCH_${batchId}`;
  }

  _sha256Hex(buf) {
    return crypto.createHash("sha256").update(buf).digest("hex");
  }

  _hashPair(leftHex, rightHex) {
    // 左右拼接的二进制，再做一次 SHA-256（标准 Merkle）
    return this._sha256Hex(Buffer.concat([
      Buffer.from(String(leftHex), "hex"),
      Buffer.from(String(rightHex), "hex"),
    ]));
  }

  /**
   * 迭代 9：把一批叶子哈希聚合上链。
   * - 链码自己不重算根（叶子总集合不入链），调用方提交 (merkleRoot, leafCount)；
   *   链上只承诺"这个根在这个 batchId 下被锚定"
   * - 验证包含证明时不需要原始叶子集合，只需 (batchId, leafHash, proof) 即可重算根并比对
   *
   * 之所以"链下算根"：避免上链交易体积膨胀（链上交易只放 root，不放 N 个叶子）。
   * 这是经典 Merkle 锚定（Bitcoin/Ethereum 同思路）。
   */
  async AnchorRecordBatch(ctx, batchId, merkleRoot, leafCount, createdAt) {
    if (!batchId) throw new Error("batchId 必填");
    if (!merkleRoot || !/^[0-9a-f]{64}$/i.test(String(merkleRoot))) {
      throw new Error("merkleRoot 必须为 64 位十六进制（SHA-256）");
    }
    const count = Number(leafCount);
    if (!Number.isFinite(count) || count <= 0) {
      throw new Error("leafCount 必须为正整数");
    }
    const key = this._batchKey(batchId);
    const existing = await this._getStateAsObject(ctx, key);
    if (existing) {
      throw new Error(`Batch ${batchId} already anchored`);
    }
    const batch = {
      docType: "MerkleAnchorBatch",
      batchId: String(batchId),
      merkleRoot: String(merkleRoot).toLowerCase(),
      leafCount: count,
      createdAt: String(createdAt || ""),
      txId: ctx.stub.getTxID(),
      anchoredAt: this._isoFromSeconds(this._txTimestampSeconds(ctx)),
    };
    await this._putStateAsObject(ctx, key, batch);
    ctx.stub.setEvent(
      "BatchAnchored",
      Buffer.from(JSON.stringify({
        batchId: batch.batchId,
        merkleRoot: batch.merkleRoot,
        leafCount: batch.leafCount,
        txId: batch.txId,
      }))
    );
    return JSON.stringify(batch);
  }

  async GetAnchorBatch(ctx, batchId) {
    const batch = await this._getStateAsObject(ctx, this._batchKey(batchId));
    if (!batch) throw new Error(`Batch ${batchId} not found`);
    return JSON.stringify(batch);
  }

  /**
   * 迭代 9：在链上验证 (leafHash, proof) 是否能重算出 batch.merkleRoot。
   *
   * proofJson 形态：
   *   [{ "hash": "<hex>", "position": "left" | "right" }, ...]
   * 表示当前哈希在每一层中应与兄弟节点做什么样的拼接：
   *   position="right" → next = sha256(curr || sibling)
   *   position="left"  → next = sha256(sibling || curr)
   */
  async VerifyRecordInclusion(ctx, batchId, leafHash, proofJson) {
    const batch = await this._getStateAsObject(ctx, this._batchKey(batchId));
    if (!batch) throw new Error(`Batch ${batchId} not found`);
    if (!leafHash || !/^[0-9a-f]{64}$/i.test(String(leafHash))) {
      throw new Error("leafHash 必须为 64 位十六进制");
    }
    let proof;
    try {
      proof = JSON.parse(proofJson || "[]");
    } catch (_e) {
      throw new Error("proofJson 解析失败");
    }
    if (!Array.isArray(proof)) {
      throw new Error("proofJson 必须为数组");
    }
    let current = String(leafHash).toLowerCase();
    for (const step of proof) {
      if (!step || typeof step !== "object") {
        throw new Error("proof step 必须为 {hash,position} 对象");
      }
      const siblingHex = String(step.hash || "").toLowerCase();
      if (!/^[0-9a-f]{64}$/.test(siblingHex)) {
        throw new Error("proof.hash 必须为 64 位十六进制");
      }
      if (step.position === "right") {
        // 兄弟在右
        current = this._hashPair(current, siblingHex);
      } else if (step.position === "left") {
        // 兄弟在左
        current = this._hashPair(siblingHex, current);
      } else {
        throw new Error("proof.position 必须为 'left' 或 'right'");
      }
    }
    const ok = current === String(batch.merkleRoot).toLowerCase();
    return JSON.stringify({
      ok,
      recomputedRoot: current,
      anchoredRoot: batch.merkleRoot,
      batchId: batch.batchId,
      leafCount: batch.leafCount,
      txId: batch.txId,
    });
  }

  /**
   * 迭代 9：列出所有锚定批次（CouchDB 富查询）。
   */
  async ListAnchorBatches(ctx, pageSize, bookmark) {
    const selector = { docType: "MerkleAnchorBatch" };
    const out = await this._richQueryPaged(
      ctx,
      selector,
      pageSize,
      bookmark,
      ["_design/indexAnchorBatchDoc", "indexAnchorBatch"],
      [{ createdAt: "desc" }]
    );
    return JSON.stringify(out);
  }
}

module.exports = MedShareContract;
