const express = require("express");
const cors = require("cors");
const fs = require("fs");
const http = require("http");
const path = require("path");
const NodeCache = require("node-cache");
const { Gateway, Wallets } = require("fabric-network");

const app = express();
app.use(cors());
app.use(express.json());

const PORT = process.env.PORT || 3000;
const CHANNEL_NAME = process.env.CHANNEL_NAME || "medicalchannel";
const CHAINCODE_NAME = process.env.CHAINCODE_NAME || "medshare";
const DISCOVERY_AS_LOCALHOST =
  (process.env.FABRIC_DISCOVERY_AS_LOCALHOST || "true").toLowerCase() === "true";
const FABRIC_ENDPOINT_HOST = (process.env.FABRIC_ENDPOINT_HOST || "").trim();
const READY_ORGS = (process.env.GATEWAY_READY_ORGS || "org1,org2")
  .split(",")
  .map((org) => normalizeOrg(org))
  .filter((org, index, orgs) => orgs.indexOf(org) === index);

// 迭代 3：链上历史查询 TTL 缓存（30s）。命中/未命中计数暴露到 /health 便于观察。
const HISTORY_TTL_SECONDS = Number(process.env.HISTORY_CACHE_TTL || 30);
const historyCache = new NodeCache({ stdTTL: HISTORY_TTL_SECONDS, checkperiod: 60 });
const cacheStats = { hits: 0, misses: 0, invalidations: 0 };

function cacheKey(kind, id) {
  return `${kind}:${id}`;
}

function invalidateRecordCache(recordId) {
  if (historyCache.del(cacheKey("record-history", recordId))) {
    cacheStats.invalidations += 1;
  }
}

function invalidateRequestCache(requestId) {
  if (historyCache.del(cacheKey("request-history", requestId))) {
    cacheStats.invalidations += 1;
  }
}

const orgConfigs = {
  org1: {
    mspId: process.env.FABRIC_ORG1_MSPID || "Org1MSP",
    ccpPath:
      process.env.FABRIC_ORG1_CCP ||
      "/fabric-network/runtime/fabric-samples/test-network/organizations/peerOrganizations/org1.example.com/connection-org1.json",
    certPath:
      process.env.FABRIC_ORG1_CERT ||
      "/fabric-network/runtime/fabric-samples/test-network/organizations/peerOrganizations/org1.example.com/users/Admin@org1.example.com/msp/signcerts/cert.pem",
    keyDir:
      process.env.FABRIC_ORG1_KEY_DIR ||
      "/fabric-network/runtime/fabric-samples/test-network/organizations/peerOrganizations/org1.example.com/users/Admin@org1.example.com/msp/keystore"
  },
  org2: {
    mspId: process.env.FABRIC_ORG2_MSPID || "Org2MSP",
    ccpPath:
      process.env.FABRIC_ORG2_CCP ||
      "/fabric-network/runtime/fabric-samples/test-network/organizations/peerOrganizations/org2.example.com/connection-org2.json",
    certPath:
      process.env.FABRIC_ORG2_CERT ||
      "/fabric-network/runtime/fabric-samples/test-network/organizations/peerOrganizations/org2.example.com/users/Admin@org2.example.com/msp/signcerts/cert.pem",
    keyDir:
      process.env.FABRIC_ORG2_KEY_DIR ||
      "/fabric-network/runtime/fabric-samples/test-network/organizations/peerOrganizations/org2.example.com/users/Admin@org2.example.com/msp/keystore"
  }
};

function normalizeOrg(org) {
  if (!org) return "org1";
  const value = String(org).trim().toLowerCase();
  return value === "org2" ? "org2" : "org1";
}

function assertReadableFile(filePath, label) {
  if (!fs.existsSync(filePath)) {
    throw new Error(`${label} not found: ${filePath}`);
  }
  if (!fs.statSync(filePath).isFile()) {
    throw new Error(`${label} is not a file: ${filePath}`);
  }
}

function assertReadableDir(dirPath, label) {
  if (!fs.existsSync(dirPath)) {
    throw new Error(`${label} not found: ${dirPath}`);
  }
  if (!fs.statSync(dirPath).isDirectory()) {
    throw new Error(`${label} is not a directory: ${dirPath}`);
  }
}

function readFirstKeyFile(keyDir) {
  assertReadableDir(keyDir, "Fabric private key directory");
  const files = fs.readdirSync(keyDir)
    .filter((name) => !name.startsWith("."))
    .map((name) => path.join(keyDir, name))
    .filter((filePath) => fs.statSync(filePath).isFile())
    .sort((a, b) => path.basename(a).localeCompare(path.basename(b)));
  const keyPath = files.find((filePath) =>
    fs.readFileSync(filePath, "utf8").includes("PRIVATE KEY")
  );
  if (!keyPath) {
    throw new Error(`No private key file found in ${keyDir}`);
  }
  return keyPath;
}

function parseResult(buffer) {
  if (!buffer) return null;
  const text = buffer.toString("utf8");
  if (!text) return null;
  try {
    return JSON.parse(text);
  } catch (err) {
    return text;
  }
}

function rewriteEndpointHost(url) {
  if (!FABRIC_ENDPOINT_HOST) return url;
  return String(url).replace(
    /^([a-z][a-z0-9+.-]*:\/\/)(\[[^\]]+\]|[^/:]+)(:\d+)?(.*)$/i,
    (_match, scheme, _host, port = "", rest = "") => `${scheme}${FABRIC_ENDPOINT_HOST}${port}${rest}`
  );
}

function rewriteConnectionProfile(ccp) {
  if (!FABRIC_ENDPOINT_HOST) return ccp;
  const copy = JSON.parse(JSON.stringify(ccp));
  ["peers", "orderers", "certificateAuthorities"].forEach((section) => {
    Object.values(copy[section] || {}).forEach((endpoint) => {
      if (endpoint.url) {
        endpoint.url = rewriteEndpointHost(endpoint.url);
      }
    });
  });
  return copy;
}

function loadOrgMaterial(org) {
  const normalized = normalizeOrg(org);
  const config = orgConfigs[normalized];
  assertReadableFile(config.ccpPath, `${normalized} connection profile`);
  assertReadableFile(config.certPath, `${normalized} certificate`);
  const keyPath = readFirstKeyFile(config.keyDir);
  return { normalized, config, keyPath };
}

async function withContract(org, action) {
  const { config, keyPath } = loadOrgMaterial(org);
  const ccp = rewriteConnectionProfile(JSON.parse(fs.readFileSync(config.ccpPath, "utf8")));
  const cert = fs.readFileSync(config.certPath, "utf8");
  const privateKey = fs.readFileSync(keyPath, "utf8");

  const wallet = await Wallets.newInMemoryWallet();
  await wallet.put("appUser", {
    credentials: { certificate: cert, privateKey },
    mspId: config.mspId,
    type: "X.509"
  });

  const gateway = new Gateway();
  try {
    await gateway.connect(ccp, {
      wallet,
      identity: "appUser",
      discovery: { enabled: true, asLocalhost: DISCOVERY_AS_LOCALHOST }
    });
    const network = await gateway.getNetwork(CHANNEL_NAME);
    const contract = network.getContract(CHAINCODE_NAME);
    return await action(contract);
  } finally {
    gateway.disconnect();
  }
}

async function submit(org, fnName, args) {
  return withContract(org, async (contract) => {
    const tx = contract.createTransaction(fnName);
    const result = await tx.submit(...args);
    return { txId: tx.getTransactionId(), result: parseResult(result) };
  });
}

async function evaluate(org, fnName, args) {
  return withContract(org, async (contract) => {
    const result = await contract.evaluateTransaction(fnName, ...args);
    return { result: parseResult(result) };
  });
}

function statusForError(error) {
  const message = String(error && error.message ? error.message : "");
  if (/not found/i.test(message)) return 404;
  if (/already exists/i.test(message)) return 409;
  return 502;
}

function sendGatewayError(res, error) {
  const message = error && error.message ? error.message : "Fabric Gateway request failed";
  res.status(statusForError(error)).json({
    message,
    error: "fabric_gateway_error"
  });
}

function isReadinessProbeMiss(error) {
  return /Record evidence __gateway_ready__ not found/i.test(String(error && error.message ? error.message : ""));
}

async function checkOrgReady(org) {
  const normalized = normalizeOrg(org);
  try {
    await withContract(normalized, async (contract) => {
      try {
        await contract.evaluateTransaction("GetMedicalRecordEvidence", "__gateway_ready__");
      } catch (error) {
        if (!isReadinessProbeMiss(error)) {
          throw error;
        }
      }
    });
    return { org: normalized, status: "ready" };
  } catch (error) {
    return { org: normalized, status: "unready", message: error.message };
  }
}

app.get("/health", (_req, res) => {
  const total = cacheStats.hits + cacheStats.misses;
  const hitRate = total > 0 ? cacheStats.hits / total : 0;
  res.json({
    status: "ok",
    historyCache: {
      ttlSeconds: HISTORY_TTL_SECONDS,
      hits: cacheStats.hits,
      misses: cacheStats.misses,
      invalidations: cacheStats.invalidations,
      hitRate: Number(hitRate.toFixed(4)),
      size: historyCache.keys().length
    }
  });
});

app.get("/ready", async (_req, res) => {
  const checks = await Promise.all(READY_ORGS.map((org) => checkOrgReady(org)));
  const ready = checks.every((check) => check.status === "ready");
  res.status(ready ? 200 : 503).json({
    status: ready ? "ready" : "unready",
    channel: CHANNEL_NAME,
    chaincode: CHAINCODE_NAME,
    discoveryAsLocalhost: DISCOVERY_AS_LOCALHOST,
    endpointHostOverride: FABRIC_ENDPOINT_HOST || null,
    checks
  });
});

app.post("/api/records/evidence", async (req, res) => {
  const { org, recordId, patientId, uploaderHospital, dataHash, createdAt, category } = req.body;
  if (!recordId || !patientId || !uploaderHospital || !dataHash || !createdAt) {
    return res.status(400).json({ message: "missing required fields" });
  }
  try {
    // 迭代 12：category 为可选第 6 参数
    const args = [
      String(recordId),
      String(patientId),
      String(uploaderHospital),
      String(dataHash),
      String(createdAt),
    ];
    if (category) args.push(String(category));
    const result = await submit(org, "CreateMedicalRecordEvidence", args);
    res.json(result);
  } catch (error) {
    sendGatewayError(res, error);
  }
});

app.get("/api/records/evidence/:recordId", async (req, res) => {
  const org = req.query.org || "org1";
  try {
    const result = await evaluate(org, "GetMedicalRecordEvidence", [String(req.params.recordId)]);
    res.json(result);
  } catch (error) {
    sendGatewayError(res, error);
  }
});

// 迭代 2：病历修订（生成新版本）
app.post("/api/records/evidence/:recordId/revise", async (req, res) => {
  const { org, newDataHash, updatedAt } = req.body;
  if (!newDataHash || !updatedAt) {
    return res.status(400).json({ message: "missing required fields" });
  }
  try {
    const result = await submit(org, "UpdateMedicalRecordEvidence", [
      String(req.params.recordId),
      String(newDataHash),
      String(updatedAt)
    ]);
    // 迭代 3：写操作后使对应缓存失效
    invalidateRecordCache(req.params.recordId);
    res.json(result);
  } catch (error) {
    sendGatewayError(res, error);
  }
});

// 迭代 3：调 Fabric GetHistoryForKey 获取病历完整历史（TTL 缓存）
app.get("/api/records/evidence/:recordId/history", async (req, res) => {
  const org = req.query.org || "org1";
  const key = cacheKey("record-history", req.params.recordId);
  const cached = historyCache.get(key);
  if (cached) {
    cacheStats.hits += 1;
    return res.json({ ...cached, cache: "hit" });
  }
  cacheStats.misses += 1;
  try {
    const result = await evaluate(org, "GetRecordHistory", [
      String(req.params.recordId)
    ]);
    historyCache.set(key, result);
    res.json({ ...result, cache: "miss" });
  } catch (error) {
    sendGatewayError(res, error);
  }
});

app.get("/api/access-requests/:requestId/history", async (req, res) => {
  const org = req.query.org || "org1";
  const key = cacheKey("request-history", req.params.requestId);
  const cached = historyCache.get(key);
  if (cached) {
    cacheStats.hits += 1;
    return res.json({ ...cached, cache: "hit" });
  }
  cacheStats.misses += 1;
  try {
    const result = await evaluate(org, "GetAccessRequestHistory", [
      String(req.params.requestId)
    ]);
    historyCache.set(key, result);
    res.json({ ...result, cache: "miss" });
  } catch (error) {
    sendGatewayError(res, error);
  }
});

// 迭代 2：查询指定版本
app.get("/api/records/evidence/:recordId/version/:version", async (req, res) => {
  const org = req.query.org || "org1";
  try {
    const result = await evaluate(org, "GetRecordVersion", [
      String(req.params.recordId),
      String(req.params.version)
    ]);
    res.json(result);
  } catch (error) {
    sendGatewayError(res, error);
  }
});

app.post("/api/access-requests", async (req, res) => {
  const { org, requestId, recordId, applicantHospital, patientId, reasonHash, status, createdAt, purpose } = req.body;
  if (!requestId || !recordId || !applicantHospital || !patientId || !reasonHash || !createdAt) {
    return res.status(400).json({ message: "missing required fields" });
  }
  try {
    // 迭代 5：传入 patientId，链码记录 applicantMsp
    // 迭代 12：purpose 为可选第 8 参数
    const args = [
      String(requestId),
      String(recordId),
      String(applicantHospital),
      String(patientId),
      String(reasonHash),
      String(status || "PENDING"),
      String(createdAt),
    ];
    if (purpose) args.push(String(purpose));
    const result = await submit(org, "CreateAccessRequest", args);
    invalidateRequestCache(requestId);
    res.json(result);
  } catch (error) {
    sendGatewayError(res, error);
  }
});

app.post("/api/access-requests/:requestId/approve", async (req, res) => {
  const org = req.body.org || "org1";
  const reviewedAt = req.body.reviewedAt || new Date().toISOString();
  const durationDays = req.body.durationDays;
  const maxReads = req.body.maxReads;
  if (!durationDays || !maxReads) {
    return res.status(400).json({ message: "durationDays 与 maxReads 为必填" });
  }
  try {
    // 迭代 5：带入有效期和次数上限
    const result = await submit(org, "ApproveAccessRequest", [
      String(req.params.requestId),
      String(reviewedAt),
      String(durationDays),
      String(maxReads)
    ]);
    invalidateRequestCache(req.params.requestId);
    res.json(result);
  } catch (error) {
    sendGatewayError(res, error);
  }
});

// 迭代 5：患者撤销已批准授权
app.post("/api/access-requests/:requestId/revoke", async (req, res) => {
  const org = req.body.org || "org1";
  const patientId = req.body.patientId;
  const revokedAt = req.body.revokedAt || new Date().toISOString();
  if (!patientId) {
    return res.status(400).json({ message: "patientId 为必填" });
  }
  try {
    const result = await submit(org, "RevokeAccessRequest", [
      String(req.params.requestId),
      String(patientId),
      String(revokedAt)
    ]);
    invalidateRequestCache(req.params.requestId);
    res.json(result);
  } catch (error) {
    sendGatewayError(res, error);
  }
});

// 迭代 5：一次访问消费（原子校验 + 计数扣减 + 事件）
app.post("/api/access-requests/:requestId/access", async (req, res) => {
  const org = req.body.org || "org1";
  const accessedAt = req.body.accessedAt || new Date().toISOString();
  try {
    const result = await submit(org, "AccessRecord", [
      String(req.params.requestId),
      String(accessedAt)
    ]);
    invalidateRequestCache(req.params.requestId);
    res.json(result);
  } catch (error) {
    sendGatewayError(res, error);
  }
});

app.post("/api/access-requests/:requestId/reject", async (req, res) => {
  const org = req.body.org || "org1";
  const reviewedAt = req.body.reviewedAt || new Date().toISOString();
  try {
    const result = await submit(org, "RejectAccessRequest", [
      String(req.params.requestId),
      String(reviewedAt)
    ]);
    invalidateRequestCache(req.params.requestId);
    res.json(result);
  } catch (error) {
    sendGatewayError(res, error);
  }
});

app.get("/api/access-requests/:requestId", async (req, res) => {
  const org = req.query.org || "org1";
  try {
    const result = await evaluate(org, "QueryAccessRequest", [String(req.params.requestId)]);
    res.json(result);
  } catch (error) {
    sendGatewayError(res, error);
  }
});

// 迭代 7：CouchDB 富查询（带 30s TTL 缓存）
const richCache = new NodeCache({ stdTTL: HISTORY_TTL_SECONDS, checkperiod: 60 });
const richStats = { hits: 0, misses: 0 };

function richKey(name, params) {
  return `rich:${name}:${JSON.stringify(params)}`;
}

async function _servePagedQuery(org, fnName, args, params, res) {
  const key = richKey(fnName, { org, ...params });
  const cached = richCache.get(key);
  if (cached) {
    richStats.hits += 1;
    return res.json({ ...cached, cache: "hit" });
  }
  richStats.misses += 1;
  try {
    const result = await evaluate(org, fnName, args);
    richCache.set(key, result);
    res.json({ ...result, cache: "miss" });
  } catch (error) {
    sendGatewayError(res, error);
  }
}

app.get("/api/records/query/by-hospital", async (req, res) => {
  const org = req.query.org || "org1";
  const uploaderHospital = String(req.query.uploaderHospital || "");
  const pageSize = String(req.query.pageSize || "20");
  const bookmark = String(req.query.bookmark || "");
  if (!uploaderHospital) {
    return res.status(400).json({ message: "uploaderHospital 必填" });
  }
  await _servePagedQuery(
    org,
    "QueryRecordsByHospital",
    [uploaderHospital, pageSize, bookmark],
    { uploaderHospital, pageSize, bookmark },
    res
  );
});

app.get("/api/records/query/by-date", async (req, res) => {
  const org = req.query.org || "org1";
  const from = String(req.query.from || "");
  const to = String(req.query.to || "");
  const pageSize = String(req.query.pageSize || "20");
  const bookmark = String(req.query.bookmark || "");
  if (!from || !to) {
    return res.status(400).json({ message: "from / to 必填（ISO8601）" });
  }
  await _servePagedQuery(
    org,
    "QueryRecordsByDateRange",
    [from, to, pageSize, bookmark],
    { from, to, pageSize, bookmark },
    res
  );
});

app.get("/api/access-requests/query/pending-for-patient", async (req, res) => {
  const org = req.query.org || "org1";
  const patientId = String(req.query.patientId || "");
  const pageSize = String(req.query.pageSize || "20");
  const bookmark = String(req.query.bookmark || "");
  if (!patientId) {
    return res.status(400).json({ message: "patientId 必填" });
  }
  await _servePagedQuery(
    org,
    "QueryPendingRequestsForPatient",
    [patientId, pageSize, bookmark],
    { patientId, pageSize, bookmark },
    res
  );
});

// ---------- 迭代 9：Merkle 批量锚定 + 链上包含证明 ----------

app.post("/api/anchor/batches", async (req, res) => {
  const { org, batchId, merkleRoot, leafCount, createdAt } = req.body || {};
  if (!batchId || !merkleRoot || !leafCount) {
    return res.status(400).json({ message: "batchId / merkleRoot / leafCount 必填" });
  }
  try {
    const result = await submit(org, "AnchorRecordBatch", [
      String(batchId),
      String(merkleRoot),
      String(leafCount),
      String(createdAt || new Date().toISOString()),
    ]);
    res.json(result);
  } catch (error) {
    res.status(500).json({ message: error.message });
  }
});

app.get("/api/anchor/batches/:batchId", async (req, res) => {
  const org = req.query.org || "org1";
  try {
    const result = await evaluate(org, "GetAnchorBatch", [String(req.params.batchId)]);
    res.json(result);
  } catch (error) {
    res.status(500).json({ message: error.message });
  }
});

app.post("/api/anchor/verify", async (req, res) => {
  const { org, batchId, leafHash, proof } = req.body || {};
  if (!batchId || !leafHash || proof === undefined) {
    return res.status(400).json({ message: "batchId / leafHash / proof 必填" });
  }
  try {
    const proofJson = typeof proof === "string" ? proof : JSON.stringify(proof);
    const result = await evaluate(org, "VerifyRecordInclusion", [
      String(batchId),
      String(leafHash),
      proofJson,
    ]);
    res.json(result);
  } catch (error) {
    res.status(500).json({ message: error.message });
  }
});

app.get("/api/anchor/batches", async (req, res) => {
  const org = req.query.org || "org1";
  const pageSize = String(req.query.pageSize || "20");
  const bookmark = String(req.query.bookmark || "");
  await _servePagedQuery(
    org,
    "ListAnchorBatches",
    [pageSize, bookmark],
    { pageSize, bookmark },
    res
  );
});

// ---------- 迭代 10：链上多签治理 ----------

app.post("/api/governance/actions", async (req, res) => {
  const { org, actionId, kind, payloadJson, proposedAt } = req.body || {};
  if (!actionId || !kind) {
    return res.status(400).json({ message: "actionId / kind 必填" });
  }
  try {
    const result = await submit(org, "ProposeGovernanceAction", [
      String(actionId),
      String(kind),
      String(payloadJson || "{}"),
      String(proposedAt || new Date().toISOString()),
    ]);
    res.json(result);
  } catch (error) {
    res.status(500).json({ message: error.message });
  }
});

app.post("/api/governance/actions/:actionId/approve", async (req, res) => {
  try {
    const result = await submit(req.body.org, "ApproveGovernanceAction", [
      String(req.params.actionId),
      String(req.body.approvedAt || new Date().toISOString()),
    ]);
    res.json(result);
  } catch (error) {
    res.status(500).json({ message: error.message });
  }
});

app.post("/api/governance/actions/:actionId/reject", async (req, res) => {
  try {
    const result = await submit(req.body.org, "RejectGovernanceAction", [
      String(req.params.actionId),
      String(req.body.rejectedAt || new Date().toISOString()),
    ]);
    res.json(result);
  } catch (error) {
    res.status(500).json({ message: error.message });
  }
});

app.post("/api/governance/actions/:actionId/execute", async (req, res) => {
  try {
    const result = await submit(req.body.org, "ExecuteGovernanceAction", [
      String(req.params.actionId),
      String(req.body.executedAt || new Date().toISOString()),
    ]);
    res.json(result);
  } catch (error) {
    res.status(500).json({ message: error.message });
  }
});

app.get("/api/governance/actions/:actionId", async (req, res) => {
  const org = req.query.org || "org1";
  try {
    const result = await evaluate(org, "GetGovernanceAction", [
      String(req.params.actionId),
    ]);
    res.json(result);
  } catch (error) {
    res.status(500).json({ message: error.message });
  }
});

app.get("/api/governance/actions", async (req, res) => {
  const org = req.query.org || "org1";
  const status = String(req.query.status || "");
  const pageSize = String(req.query.pageSize || "20");
  const bookmark = String(req.query.bookmark || "");
  await _servePagedQuery(
    org,
    "ListGovernanceActions",
    [status, pageSize, bookmark],
    { status, pageSize, bookmark },
    res
  );
});

// ---------- 迭代 11：链上紧急冻结 + 治理解冻闭环 ----------

app.post("/api/records/evidence/:recordId/freeze", async (req, res) => {
  const { org, patientId, reasonHash, frozenAt } = req.body || {};
  if (!patientId) {
    return res.status(400).json({ message: "patientId 必填" });
  }
  try {
    const result = await submit(org, "FreezeRecord", [
      String(req.params.recordId),
      String(patientId),
      String(reasonHash || ""),
      String(frozenAt || new Date().toISOString()),
    ]);
    // 迭代 3：写操作后让 history 缓存失效
    invalidateRecordCache(req.params.recordId);
    res.json(result);
  } catch (error) {
    res.status(500).json({ message: error.message });
  }
});

app.post("/api/records/evidence/:recordId/unfreeze", async (req, res) => {
  const { org, governanceActionId, unfrozenAt } = req.body || {};
  if (!governanceActionId) {
    return res.status(400).json({ message: "governanceActionId 必填" });
  }
  try {
    const result = await submit(org, "UnfreezeRecord", [
      String(req.params.recordId),
      String(governanceActionId),
      String(unfrozenAt || new Date().toISOString()),
    ]);
    invalidateRecordCache(req.params.recordId);
    res.json(result);
  } catch (error) {
    res.status(500).json({ message: error.message });
  }
});

// ---------- 迭代 12：链码 v2 ----------

app.get("/api/system/schema-version", async (req, res) => {
  const org = req.query.org || "org1";
  try {
    // GetSchemaVersion 是同步纯函数（无 ctx），evaluate 仍可调
    const result = await evaluate(org, "GetSchemaVersion", []);
    res.json(result);
  } catch (error) {
    res.status(500).json({ message: error.message });
  }
});

app.post("/api/admin/migrate/records-v2", async (req, res) => {
  const { org, batchJson } = req.body || {};
  if (!batchJson) {
    return res.status(400).json({ message: "batchJson 必填" });
  }
  try {
    const result = await submit(org, "MigrateRecordsV2", [String(batchJson)]);
    res.json(result);
  } catch (error) {
    res.status(500).json({ message: error.message });
  }
});

// ---------- 迭代 13：积分（FT） ----------

app.get("/api/credits/:userId/balance", async (req, res) => {
  const org = req.query.org || "org1";
  try {
    const result = await evaluate(org, "CreditBalance", [String(req.params.userId)]);
    res.json(result);
  } catch (error) {
    res.status(500).json({ message: error.message });
  }
});

app.post("/api/credits/mint", async (req, res) => {
  const { org, toUserId, amount, reasonCode, mintedAt } = req.body || {};
  if (!toUserId || !amount) {
    return res.status(400).json({ message: "toUserId / amount 必填" });
  }
  try {
    const result = await submit(org, "CreditMint", [
      String(toUserId),
      String(amount),
      String(reasonCode || ""),
      String(mintedAt || new Date().toISOString()),
    ]);
    res.json(result);
  } catch (error) {
    res.status(500).json({ message: error.message });
  }
});

app.post("/api/credits/transfer", async (req, res) => {
  const { org, fromUserId, toUserId, amount, reasonCode, txAt } = req.body || {};
  if (!fromUserId || !toUserId || !amount) {
    return res.status(400).json({ message: "fromUserId / toUserId / amount 必填" });
  }
  try {
    const result = await submit(org, "CreditTransfer", [
      String(fromUserId),
      String(toUserId),
      String(amount),
      String(reasonCode || ""),
      String(txAt || new Date().toISOString()),
    ]);
    res.json(result);
  } catch (error) {
    res.status(500).json({ message: error.message });
  }
});

app.get("/api/credits/history", async (req, res) => {
  const org = req.query.org || "org1";
  const userId = String(req.query.userId || "");
  const pageSize = String(req.query.pageSize || "20");
  const bookmark = String(req.query.bookmark || "");
  if (!userId) {
    return res.status(400).json({ message: "userId 必填" });
  }
  await _servePagedQuery(
    org,
    "CreditHistory",
    [userId, pageSize, bookmark],
    { userId, pageSize, bookmark },
    res
  );
});

app.get("/api/records/query/by-category", async (req, res) => {
  const org = req.query.org || "org1";
  const category = String(req.query.category || "");
  const pageSize = String(req.query.pageSize || "20");
  const bookmark = String(req.query.bookmark || "");
  if (!category) {
    return res.status(400).json({ message: "category 必填" });
  }
  await _servePagedQuery(
    org,
    "QueryRecordsByCategory",
    [category, pageSize, bookmark],
    { category, pageSize, bookmark },
    res
  );
});

// ---------- 迭代 6：链码事件订阅（真实 Fabric 下启用） ----------
//
// 设计：
//   - 对每个组织启动一个独立监听器（Org1/Org2）
//   - 监听器 offset 持久化到磁盘（block + txIndex），断线重连可从断点恢复
//   - 收到事件后 POST 给后端的 /internal/events（本项目当前通过 backend 内嵌总线
//     直接 emit 避免双通道；真实部署时把 BACKEND_EVENT_URL 指到后端即可启用）。
//
// 控制环境变量：
//   ENABLE_CHAINCODE_LISTENER=1        启用
//   BACKEND_EVENT_URL=http://backend:8000/internal/events   （可选）
//   LISTENER_OFFSET_PATH=/data/listener-offset.json

const LISTENER_ENABLED =
  (process.env.ENABLE_CHAINCODE_LISTENER || "0") === "1";
const BACKEND_EVENT_URL = process.env.BACKEND_EVENT_URL || "";
const OFFSET_PATH =
  process.env.LISTENER_OFFSET_PATH || path.join(__dirname, "listener-offset.json");

function loadOffsets() {
  try {
    if (fs.existsSync(OFFSET_PATH)) {
      return JSON.parse(fs.readFileSync(OFFSET_PATH, "utf8"));
    }
  } catch (e) {
    console.warn("[listener] offset 读取失败：", e.message);
  }
  return {};
}

function saveOffsets(offsets) {
  try {
    fs.writeFileSync(OFFSET_PATH, JSON.stringify(offsets, null, 2), "utf8");
  } catch (e) {
    console.warn("[listener] offset 写入失败：", e.message);
  }
}

function forwardEventToBackend(event) {
  if (!BACKEND_EVENT_URL) return;
  const body = JSON.stringify(event);
  try {
    const req = http.request(BACKEND_EVENT_URL, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Content-Length": Buffer.byteLength(body),
      },
      timeout: 2000,
    });
    req.on("error", (err) =>
      console.warn("[listener] 转发到后端失败：", err.message)
    );
    req.write(body);
    req.end();
  } catch (e) {
    console.warn("[listener] 构造转发请求失败：", e.message);
  }
}

async function startChaincodeListener(org) {
  const offsets = loadOffsets();
  const startBlock = offsets[org] !== undefined ? BigInt(offsets[org]) : undefined;
  console.log(
    `[listener][${org}] 启动；startBlock=${startBlock === undefined ? "latest" : startBlock}`
  );

  await withContract(org, async (contract) => {
    const options = startBlock !== undefined ? { startBlock } : undefined;
    const listener = await contract.addContractListener(
      async (event) => {
        try {
          const payload = {
            eventName: event.eventName,
            chaincodeName: event.chaincodeName,
            txId: event.transactionId,
            blockNumber: event.getBlockEvent
              ? String(event.getBlockEvent().blockNumber)
              : undefined,
            data: event.payload ? JSON.parse(event.payload.toString("utf8")) : null,
            caughtAt: new Date().toISOString(),
            org,
          };
          forwardEventToBackend(payload);
          // 持久化 offset
          const nextBlock =
            event.getBlockEvent && event.getBlockEvent().blockNumber !== undefined
              ? BigInt(event.getBlockEvent().blockNumber) + 1n
              : undefined;
          if (nextBlock !== undefined) {
            const o = loadOffsets();
            o[org] = String(nextBlock);
            saveOffsets(o);
          }
        } catch (e) {
          console.warn("[listener] 事件处理异常：", e.message);
        }
      },
      options
    );
    // 保持监听器不退出：withContract 的 gateway.disconnect() 会在函数 return 后触发，
    // 所以这里返回一个永远不 resolve 的 Promise 让 contract 保活。
    console.log(`[listener][${org}] 已挂载；将持续监听事件`);
    await new Promise(() => {});
    return listener; // unreachable
  }).catch((e) => {
    console.error(`[listener][${org}] 异常退出，5s 后重连：`, e.message);
    setTimeout(() => startChaincodeListener(org), 5000);
  });
}

if (LISTENER_ENABLED) {
  setTimeout(() => {
    startChaincodeListener("org1").catch((e) =>
      console.error("[listener][org1] 启动失败：", e.message)
    );
    startChaincodeListener("org2").catch((e) =>
      console.error("[listener][org2] 启动失败：", e.message)
    );
  }, 3000);
}

app.listen(PORT, () => {
  console.log(`Gateway listening on :${PORT}`);
  if (LISTENER_ENABLED) {
    console.log(
      `[listener] 已启用；offset 文件：${OFFSET_PATH}；转发地址：${BACKEND_EVENT_URL || "(未配置，仅本地打印)"}`
    );
  } else {
    console.log("[listener] 未启用（设 ENABLE_CHAINCODE_LISTENER=1 开启）");
  }
});
