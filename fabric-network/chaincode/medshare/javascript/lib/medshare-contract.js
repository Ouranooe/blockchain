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
    createdAt,
    category // 迭代 12 v2 新增可选参数；缺省 GENERAL
  ) {
    const latestKey = this._latestKey(recordId);
    const existing = await this._getStateAsObject(ctx, latestKey);
    if (existing) {
      throw new Error(`Record evidence ${recordId} already exists`);
    }
    const cat = category ? String(category) : "GENERAL";
    if (!MedShareContract.V2_RECORD_CATEGORIES.has(cat)) {
      throw new Error(`未知 category: ${cat}`);
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
      category: cat,
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
    // 迭代 13：医院上传病历 → 自动 +5 分（uploaderHospital 作为账户 ID）
    await this._creditMint(ctx, uploaderHospital, 5, "RECORD_UPLOAD");
    return JSON.stringify(evidence);
  }

  async UpdateMedicalRecordEvidence(ctx, recordId, newDataHash, updatedAt) {
    const latestKey = this._latestKey(recordId);
    const latest = await this._getStateAsObject(ctx, latestKey);
    if (!latest) {
      throw new Error(`Record evidence ${recordId} not found`);
    }
    // 迭代 11：冻结守卫
    if (latest.frozen) {
      throw new Error(`病历 ${recordId} 已被冻结，无法修订`);
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
    const evidence = this._normalizeRecord(
      await this._getStateAsObject(ctx, this._latestKey(recordId))
    );
    if (!evidence) {
      throw new Error(`Record evidence ${recordId} not found`);
    }
    return JSON.stringify(evidence);
  }

  async GetRecordVersion(ctx, recordId, version) {
    const evidence = this._normalizeRecord(
      await this._getStateAsObject(
        ctx,
        this._versionKey(recordId, version)
      )
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
    createdAt,
    purpose // 迭代 12 v2 新增可选参数；缺省 TREATMENT
  ) {
    const key = this._requestKey(requestId);
    const existing = await this._getStateAsObject(ctx, key);
    if (existing) {
      throw new Error(`Access request ${requestId} already exists`);
    }
    const pur = purpose ? String(purpose) : "TREATMENT";
    if (!MedShareContract.V2_REQUEST_PURPOSES.has(pur)) {
      throw new Error(`未知 purpose: ${pur}`);
    }

    const request = {
      docType: "AccessRequest",
      requestId,
      recordId,
      applicantHospital,
      applicantMsp: this._callerMsp(ctx), // 迭代 5：绑定申请方的 MSP
      patientId,                          // 迭代 5：记录归属患者
      reasonHash,
      purpose: pur,                       // 迭代 12 v2
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
    // 迭代 13：患者审批通过 → 患者 +1 分
    if (request.patientId) {
      await this._creditMint(ctx, request.patientId, 1, "REQUEST_APPROVED");
    }
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
    // 迭代 11：冻结守卫 —— 即使授权依然有效，被冻结的病历也不可被消费
    const latestRecord = await this._getStateAsObject(
      ctx,
      this._latestKey(request.recordId)
    );
    if (latestRecord && latestRecord.frozen) {
      throw new Error(
        `病历 ${request.recordId} 已被冻结，链码层拒绝访问`
      );
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
    // 迭代 13：被访问 → uploaderHospital +1 分（从 record 反查 uploader）
    if (latestRecord && latestRecord.uploaderHospital) {
      await this._creditMint(
        ctx,
        latestRecord.uploaderHospital,
        1,
        "RECORD_ACCESSED"
      );
    }
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

  // ---------------- 迭代 10：链上多签治理（双 MSP endorse） ----------------

  _governanceKey(actionId) {
    return `GOV_${actionId}`;
  }

  // 治理 kind 白名单：避免提案空间无限扩张；新增 kind 需要链码升级
  static GOV_KINDS = new Set([
    "FREEZE_RECORD",
    "UNFREEZE_RECORD",
    "BATCH_REVOKE_PATIENT",
    "FORCE_DELETE_RECORD",
  ]);

  /**
   * 迭代 10：提案高风险治理动作。
   * - 任何 MSP 调用方都可提案
   * - kind 必须在白名单
   * - payloadJson 由链码原样保存（业务方自行约定字段）
   */
  async ProposeGovernanceAction(ctx, actionId, kind, payloadJson, proposedAt) {
    if (!actionId) throw new Error("actionId 必填");
    if (!MedShareContract.GOV_KINDS.has(kind)) {
      throw new Error(`未知治理动作 kind: ${kind}`);
    }
    let payload;
    try {
      payload = JSON.parse(payloadJson || "{}");
    } catch (_e) {
      throw new Error("payloadJson 解析失败");
    }
    const key = this._governanceKey(actionId);
    const existing = await this._getStateAsObject(ctx, key);
    if (existing) {
      throw new Error(`Governance action ${actionId} already exists`);
    }
    const proposerMsp = this._callerMsp(ctx);
    const action = {
      docType: "GovernanceAction",
      actionId: String(actionId),
      kind,
      payload,
      proposerMsp,
      status: "PROPOSED",
      approvers: [], // [{msp, approvedAt, txId}]
      proposedAt: String(proposedAt || ""),
      proposeTxId: ctx.stub.getTxID(),
      executedAt: "",
      executeTxId: "",
      rejectedAt: "",
      rejectTxId: "",
    };
    await this._putStateAsObject(ctx, key, action);
    ctx.stub.setEvent(
      "GovernanceProposed",
      Buffer.from(JSON.stringify({
        actionId: action.actionId,
        kind,
        proposerMsp,
        txId: action.proposeTxId,
      }))
    );
    return JSON.stringify(action);
  }

  async _readGovernance(ctx, actionId) {
    const action = await this._getStateAsObject(ctx, this._governanceKey(actionId));
    if (!action) throw new Error(`Governance action ${actionId} not found`);
    return action;
  }

  async GetGovernanceAction(ctx, actionId) {
    const action = await this._readGovernance(ctx, actionId);
    return JSON.stringify(action);
  }

  /**
   * 迭代 10：批准治理动作。
   * - 调用方 MSP 不能与已批准 MSP 重复
   * - PROPOSED → PARTIALLY_APPROVED；PARTIALLY_APPROVED + 不同 MSP → APPROVED
   * - 终态（APPROVED/EXECUTED/REJECTED）不可再批
   */
  async ApproveGovernanceAction(ctx, actionId, approvedAt) {
    const action = await this._readGovernance(ctx, actionId);
    if (action.status === "EXECUTED" || action.status === "REJECTED") {
      throw new Error(`治理动作 ${actionId} 处于终态 ${action.status}，无法再批准`);
    }
    if (action.status === "APPROVED") {
      throw new Error(`治理动作 ${actionId} 已 APPROVED，无需再批`);
    }
    const callerMsp = this._callerMsp(ctx);
    if (!callerMsp) throw new Error("调用方 MSP 不可解析");
    for (const a of action.approvers) {
      if (a.msp === callerMsp) {
        throw new Error(`MSP ${callerMsp} 已批准过该提案`);
      }
    }
    action.approvers.push({
      msp: callerMsp,
      approvedAt: String(approvedAt || ""),
      txId: ctx.stub.getTxID(),
    });
    // 计算新状态
    const uniqueMsps = new Set(action.approvers.map((a) => a.msp));
    if (uniqueMsps.size >= 2) {
      action.status = "APPROVED";
    } else {
      action.status = "PARTIALLY_APPROVED";
    }
    await this._putStateAsObject(ctx, this._governanceKey(actionId), action);
    ctx.stub.setEvent(
      "GovernanceApproved",
      Buffer.from(JSON.stringify({
        actionId: action.actionId,
        approverMsp: callerMsp,
        status: action.status,
        approverCount: uniqueMsps.size,
      }))
    );
    return JSON.stringify(action);
  }

  async RejectGovernanceAction(ctx, actionId, rejectedAt) {
    const action = await this._readGovernance(ctx, actionId);
    if (action.status === "EXECUTED" || action.status === "REJECTED") {
      throw new Error(`治理动作 ${actionId} 处于终态 ${action.status}，无法再拒绝`);
    }
    const callerMsp = this._callerMsp(ctx);
    action.status = "REJECTED";
    action.rejectedAt = String(rejectedAt || "");
    action.rejectTxId = ctx.stub.getTxID();
    action.rejectorMsp = callerMsp;
    await this._putStateAsObject(ctx, this._governanceKey(actionId), action);
    ctx.stub.setEvent(
      "GovernanceRejected",
      Buffer.from(JSON.stringify({
        actionId: action.actionId,
        rejectorMsp: callerMsp,
      }))
    );
    return JSON.stringify(action);
  }

  /**
   * 迭代 10：执行治理动作。仅当 status == APPROVED 时允许。
   * 注意：本链码仅"标记"为 EXECUTED 并触发事件；具体的链上副作用（例如解冻一条
   * 病历）由迭代 11+ 的方法在校验 `executeTxId` 后落地。
   */
  async ExecuteGovernanceAction(ctx, actionId, executedAt) {
    const action = await this._readGovernance(ctx, actionId);
    if (action.status !== "APPROVED") {
      throw new Error(`仅 APPROVED 可执行：当前状态 ${action.status}`);
    }
    action.status = "EXECUTED";
    action.executedAt = String(executedAt || "");
    action.executeTxId = ctx.stub.getTxID();
    await this._putStateAsObject(ctx, this._governanceKey(actionId), action);
    ctx.stub.setEvent(
      "GovernanceExecuted",
      Buffer.from(JSON.stringify({
        actionId: action.actionId,
        kind: action.kind,
        executeTxId: action.executeTxId,
      }))
    );
    return JSON.stringify(action);
  }

  /**
   * 迭代 10：列出治理动作（可选按 status 过滤）。
   */
  async ListGovernanceActions(ctx, status, pageSize, bookmark) {
    const selector = { docType: "GovernanceAction" };
    if (status) selector.status = String(status);
    const out = await this._richQueryPaged(
      ctx,
      selector,
      pageSize,
      bookmark,
      ["_design/indexGovernanceStatusDoc", "indexGovernanceStatus"],
      [{ proposedAt: "desc" }]
    );
    return JSON.stringify(out);
  }

  // ---------------- 迭代 11：链上紧急冻结 + 治理解冻闭环 ----------------

  _normalizeRecord(obj) {
    // 老 record 缺省字段降级处理（向后兼容迭代 1-10）
    if (!obj) return obj;
    if (obj.frozen === undefined) obj.frozen = false;
    if (obj.frozenAt === undefined) obj.frozenAt = "";
    if (obj.freezeTxId === undefined) obj.freezeTxId = "";
    if (obj.freezeReasonHash === undefined) obj.freezeReasonHash = "";
    if (obj.unfreezeTxId === undefined) obj.unfreezeTxId = "";
    if (obj.unfreezeGovTxId === undefined) obj.unfreezeGovTxId = "";
    // 迭代 12 v2：科室分类（兼容读，缺省 GENERAL）
    if (obj.category === undefined) obj.category = "GENERAL";
    return obj;
  }

  _normalizeRequest(obj) {
    if (!obj) return obj;
    // 迭代 12 v2：申请目的（兼容读，缺省 TREATMENT）
    if (obj.purpose === undefined) obj.purpose = "TREATMENT";
    return obj;
  }

  // 迭代 12：v2 字段白名单
  static V2_RECORD_CATEGORIES = new Set([
    "GENERAL", "INPATIENT", "OUTPATIENT", "EMERGENCY",
  ]);
  static V2_REQUEST_PURPOSES = new Set([
    "TREATMENT", "RESEARCH", "AUDIT",
  ]);

  /**
   * 迭代 11：患者紧急冻结自己的病历。冻结后所有写动作 / 读消费动作均拒绝。
   * 校验：必须 record.patientId == patientId。
   */
  async FreezeRecord(ctx, recordId, patientId, reasonHash, frozenAt) {
    const latestKey = this._latestKey(recordId);
    const latest = this._normalizeRecord(
      await this._getStateAsObject(ctx, latestKey)
    );
    if (!latest) {
      throw new Error(`Record evidence ${recordId} not found`);
    }
    if (String(latest.patientId) !== String(patientId)) {
      throw new Error("只有归属患者可以冻结该病历");
    }
    if (latest.frozen) {
      throw new Error(`病历 ${recordId} 已处于冻结状态`);
    }
    latest.frozen = true;
    latest.frozenAt = String(frozenAt || "");
    latest.freezeTxId = ctx.stub.getTxID();
    latest.freezeReasonHash = String(reasonHash || "");
    // 解冻字段清零，便于下一次冻结
    latest.unfreezeTxId = "";
    latest.unfreezeGovTxId = "";

    await this._putStateAsObject(ctx, latestKey, { ...latest, isLatest: true });
    // 也在最新版本键上更新（保持两键一致）
    const versionKey = this._versionKey(recordId, latest.version);
    const versioned = this._normalizeRecord(
      await this._getStateAsObject(ctx, versionKey)
    );
    if (versioned) {
      versioned.frozen = true;
      versioned.frozenAt = latest.frozenAt;
      versioned.freezeTxId = latest.freezeTxId;
      versioned.freezeReasonHash = latest.freezeReasonHash;
      await this._putStateAsObject(ctx, versionKey, versioned);
    }

    ctx.stub.setEvent(
      "RecordFrozen",
      Buffer.from(JSON.stringify({
        recordId: String(recordId),
        patientId: String(patientId),
        frozenAt: latest.frozenAt,
        txId: latest.freezeTxId,
      }))
    );
    return JSON.stringify(latest);
  }

  /**
   * 迭代 11：解冻一条病历。
   * 必须传入一个已 EXECUTED 的治理动作 ID，且该治理动作的
   * kind == "UNFREEZE_RECORD" 且 payload.recordId == recordId。
   * 这是迭代 10 + 11 合约组合的核心：链码强制单方患者无法单独解冻，
   * 必须经过双 MSP 治理流程。
   */
  // ---------------- 迭代 12：链码 v2 升级 + 状态迁移 ----------------

  GetSchemaVersion() {
    return "v2";
  }

  /**
   * 迭代 12：把老 record 一次性迁移到 v2（写 category）。
   *
   * 仅 Org1MSP（管理员链上身份）可调用，避免医院随意改自己的 category。
   * 入参 batchJson: `[{"recordId": "1", "category": "INPATIENT"}, ...]`
   * 操作幂等：若 record 已有 category 且与传入相同则跳过；不同则覆盖并打 _migratedAt 痕迹。
   */
  async MigrateRecordsV2(ctx, batchJson) {
    if (this._callerMsp(ctx) !== "Org1MSP") {
      throw new Error("仅 Org1MSP 可执行链码迁移");
    }
    let items;
    try {
      items = JSON.parse(batchJson || "[]");
    } catch (_e) {
      throw new Error("batchJson 解析失败");
    }
    if (!Array.isArray(items)) {
      throw new Error("batchJson 必须为数组");
    }
    const migrated = [];
    for (const item of items) {
      const rid = String(item.recordId || "");
      const cat = String(item.category || "GENERAL");
      if (!rid) throw new Error("recordId 必填");
      if (!MedShareContract.V2_RECORD_CATEGORIES.has(cat)) {
        throw new Error(`未知 category: ${cat}`);
      }
      const latestKey = this._latestKey(rid);
      const latest = await this._getStateAsObject(ctx, latestKey);
      if (!latest) {
        throw new Error(`Record evidence ${rid} not found`);
      }
      if (latest.category === cat) {
        continue; // 幂等：相同跳过
      }
      latest.category = cat;
      latest._migratedAt = this._isoFromSeconds(this._txTimestampSeconds(ctx));
      latest._migrateTxId = ctx.stub.getTxID();
      await this._putStateAsObject(ctx, latestKey, { ...latest, isLatest: true });
      migrated.push(rid);
    }
    ctx.stub.setEvent(
      "SchemaMigrated",
      Buffer.from(JSON.stringify({
        kind: "RecordEvidence",
        migrated,
        count: migrated.length,
      }))
    );
    return JSON.stringify({ migrated, count: migrated.length });
  }

  /**
   * 迭代 12：把老 request 一次性迁移到 v2（写 purpose）。
   */
  async MigrateRequestsV2(ctx, batchJson) {
    if (this._callerMsp(ctx) !== "Org1MSP") {
      throw new Error("仅 Org1MSP 可执行链码迁移");
    }
    let items;
    try {
      items = JSON.parse(batchJson || "[]");
    } catch (_e) {
      throw new Error("batchJson 解析失败");
    }
    if (!Array.isArray(items)) {
      throw new Error("batchJson 必须为数组");
    }
    const migrated = [];
    for (const item of items) {
      const reqId = String(item.requestId || "");
      const pur = String(item.purpose || "TREATMENT");
      if (!reqId) throw new Error("requestId 必填");
      if (!MedShareContract.V2_REQUEST_PURPOSES.has(pur)) {
        throw new Error(`未知 purpose: ${pur}`);
      }
      const key = this._requestKey(reqId);
      const req = await this._getStateAsObject(ctx, key);
      if (!req) {
        throw new Error(`Access request ${reqId} not found`);
      }
      if (req.purpose === pur) continue;
      req.purpose = pur;
      req._migratedAt = this._isoFromSeconds(this._txTimestampSeconds(ctx));
      req._migrateTxId = ctx.stub.getTxID();
      await this._putStateAsObject(ctx, key, req);
      migrated.push(reqId);
    }
    ctx.stub.setEvent(
      "SchemaMigrated",
      Buffer.from(JSON.stringify({
        kind: "AccessRequest",
        migrated,
        count: migrated.length,
      }))
    );
    return JSON.stringify({ migrated, count: migrated.length });
  }

  // ---------------- 迭代 13：数据共享积分（FT）+ 经济激励 ----------------

  _creditKey(userId) {
    return `CREDIT_${userId}`;
  }

  _creditLedgerKey(ledgerId) {
    return `CREDIT_LEDGER_${ledgerId}`;
  }

  async _readCreditAccount(ctx, userId) {
    const obj = await this._getStateAsObject(ctx, this._creditKey(userId));
    if (obj) return obj;
    return {
      docType: "CreditAccount",
      userId: String(userId),
      balance: 0,
      updatedAt: "",
    };
  }

  async _writeCreditLedger(ctx, fromUserId, toUserId, amount, reasonCode) {
    // 流水键用 ctx.stub.getTxID() + 一个递增计数以保证一笔交易内多次落水不冲突
    const txid = ctx.stub.getTxID();
    // 用对象长度做次序，避免不同 tx 内并发冲突
    const ledgerId = `${txid}_${(this._ledgerSeqHint || 0).toString().padStart(4, "0")}`;
    this._ledgerSeqHint = (this._ledgerSeqHint || 0) + 1;
    const entry = {
      docType: "CreditLedger",
      ledgerId,
      fromUserId: fromUserId == null ? "" : String(fromUserId),
      toUserId: String(toUserId),
      amount: Number(amount),
      reasonCode: String(reasonCode || ""),
      txId: txid,
      createdAt: this._isoFromSeconds(this._txTimestampSeconds(ctx)),
    };
    await this._putStateAsObject(ctx, this._creditLedgerKey(ledgerId), entry);
    return entry;
  }

  async _creditMint(ctx, toUserId, amount, reasonCode) {
    // 内部用：业务方法触发的自动 mint。不做权限校验（业务上下文是封闭的）。
    const amt = Number(amount);
    if (!Number.isFinite(amt) || amt <= 0) return;
    const account = await this._readCreditAccount(ctx, toUserId);
    account.balance = Number(account.balance || 0) + amt;
    account.updatedAt = this._isoFromSeconds(this._txTimestampSeconds(ctx));
    await this._putStateAsObject(ctx, this._creditKey(toUserId), account);
    const ledger = await this._writeCreditLedger(
      ctx,
      "",
      toUserId,
      amt,
      reasonCode
    );
    ctx.stub.setEvent(
      "CreditMinted",
      Buffer.from(JSON.stringify({
        toUserId: String(toUserId),
        amount: amt,
        reasonCode,
        newBalance: account.balance,
        ledgerId: ledger.ledgerId,
      }))
    );
  }

  /**
   * 迭代 13：admin mint（仅 Org1MSP）。
   */
  async CreditMint(ctx, toUserId, amount, reasonCode, mintedAt) {
    if (this._callerMsp(ctx) !== "Org1MSP") {
      throw new Error("仅 Org1MSP 可执行 CreditMint");
    }
    const amt = Number(amount);
    if (!Number.isFinite(amt) || amt <= 0) {
      throw new Error("amount 必须为正数");
    }
    await this._creditMint(ctx, toUserId, amt, reasonCode || "ADMIN_MINT");
    const account = await this._readCreditAccount(ctx, toUserId);
    return JSON.stringify(account);
  }

  /**
   * 迭代 13：账户间转账。链上原子扣加；余额不足 / 自转 → 抛错。
   */
  async CreditTransfer(ctx, fromUserId, toUserId, amount, reasonCode, txAt) {
    if (String(fromUserId) === String(toUserId)) {
      throw new Error("不允许自转");
    }
    const amt = Number(amount);
    if (!Number.isFinite(amt) || amt <= 0) {
      throw new Error("amount 必须为正数");
    }
    const fromAcc = await this._readCreditAccount(ctx, fromUserId);
    if (Number(fromAcc.balance || 0) < amt) {
      throw new Error(`余额不足：${fromAcc.balance} < ${amt}`);
    }
    const toAcc = await this._readCreditAccount(ctx, toUserId);
    fromAcc.balance = Number(fromAcc.balance || 0) - amt;
    toAcc.balance = Number(toAcc.balance || 0) + amt;
    const nowIso = this._isoFromSeconds(this._txTimestampSeconds(ctx));
    fromAcc.updatedAt = nowIso;
    toAcc.updatedAt = nowIso;
    // 原子：同一 tx 内两次 putState（Fabric endorsement 单一 transaction）
    await this._putStateAsObject(ctx, this._creditKey(fromUserId), fromAcc);
    await this._putStateAsObject(ctx, this._creditKey(toUserId), toAcc);
    const ledger = await this._writeCreditLedger(
      ctx, fromUserId, toUserId, amt, reasonCode || "TRANSFER"
    );
    ctx.stub.setEvent(
      "CreditTransferred",
      Buffer.from(JSON.stringify({
        fromUserId: String(fromUserId),
        toUserId: String(toUserId),
        amount: amt,
        reasonCode,
        ledgerId: ledger.ledgerId,
      }))
    );
    return JSON.stringify({
      fromBalance: fromAcc.balance,
      toBalance: toAcc.balance,
      ledger,
    });
  }

  async CreditBalance(ctx, userId) {
    const account = await this._readCreditAccount(ctx, userId);
    return JSON.stringify(account);
  }

  async CreditHistory(ctx, userId, pageSize, bookmark) {
    // 富查询：所有 ledger 条目中，fromUserId == userId 或 toUserId == userId
    const selector = {
      docType: "CreditLedger",
      $or: [{ fromUserId: String(userId) }, { toUserId: String(userId) }],
    };
    const out = await this._richQueryPaged(
      ctx,
      selector,
      pageSize,
      bookmark,
      ["_design/indexCreditLedgerDoc", "indexCreditLedger"],
      [{ createdAt: "desc" }]
    );
    return JSON.stringify(out);
  }

  /**
   * 迭代 12：按 category 查询最新版病历。
   */
  async QueryRecordsByCategory(ctx, category, pageSize, bookmark) {
    if (!MedShareContract.V2_RECORD_CATEGORIES.has(String(category))) {
      throw new Error(`未知 category: ${category}`);
    }
    const selector = {
      docType: "RecordEvidence",
      isLatest: true,
      category: String(category),
    };
    const out = await this._richQueryPaged(
      ctx,
      selector,
      pageSize,
      bookmark,
      ["_design/indexCategoryDoc", "indexCategory"]
    );
    return JSON.stringify(out);
  }

  async UnfreezeRecord(ctx, recordId, governanceActionId, unfrozenAt) {
    const latestKey = this._latestKey(recordId);
    const latest = this._normalizeRecord(
      await this._getStateAsObject(ctx, latestKey)
    );
    if (!latest) {
      throw new Error(`Record evidence ${recordId} not found`);
    }
    if (!latest.frozen) {
      throw new Error(`病历 ${recordId} 未处于冻结状态`);
    }
    if (!governanceActionId) {
      throw new Error("解冻必须传入治理动作 ID");
    }
    const gov = await this._getStateAsObject(
      ctx,
      this._governanceKey(governanceActionId)
    );
    if (!gov) {
      throw new Error(`治理动作 ${governanceActionId} 不存在`);
    }
    if (gov.status !== "EXECUTED") {
      throw new Error(
        `治理动作 ${governanceActionId} 状态 ${gov.status}，必须为 EXECUTED 才能用于解冻`
      );
    }
    if (gov.kind !== "UNFREEZE_RECORD") {
      throw new Error(
        `治理动作 ${governanceActionId} 的 kind=${gov.kind}，期望 UNFREEZE_RECORD`
      );
    }
    const govRecordId = (gov.payload || {}).recordId;
    if (String(govRecordId) !== String(recordId)) {
      throw new Error(
        `治理动作 payload.recordId=${govRecordId} 与目标病历 ${recordId} 不一致`
      );
    }
    latest.frozen = false;
    latest.unfreezeTxId = ctx.stub.getTxID();
    latest.unfreezeGovTxId = gov.executeTxId || "";
    // frozenAt / freezeTxId / freezeReasonHash 留作历史痕迹（不清空）

    await this._putStateAsObject(ctx, latestKey, { ...latest, isLatest: true });
    const versionKey = this._versionKey(recordId, latest.version);
    const versioned = this._normalizeRecord(
      await this._getStateAsObject(ctx, versionKey)
    );
    if (versioned) {
      versioned.frozen = false;
      versioned.unfreezeTxId = latest.unfreezeTxId;
      versioned.unfreezeGovTxId = latest.unfreezeGovTxId;
      await this._putStateAsObject(ctx, versionKey, versioned);
    }

    ctx.stub.setEvent(
      "RecordUnfrozen",
      Buffer.from(JSON.stringify({
        recordId: String(recordId),
        governanceActionId: String(governanceActionId),
        unfreezeGovTxId: latest.unfreezeGovTxId,
        unfreezeTxId: latest.unfreezeTxId,
      }))
    );
    return JSON.stringify(latest);
  }
}

module.exports = MedShareContract;
