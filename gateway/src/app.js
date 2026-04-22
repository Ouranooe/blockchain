const express = require("express");
const cors = require("cors");
const fs = require("fs");
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

function readFirstKeyFile(keyDir) {
  const files = fs.readdirSync(keyDir).filter((name) => !name.startsWith("."));
  if (!files.length) {
    throw new Error(`No key file found in ${keyDir}`);
  }
  return path.join(keyDir, files[0]);
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

async function withContract(org, action) {
  const config = orgConfigs[normalizeOrg(org)];
  const ccp = JSON.parse(fs.readFileSync(config.ccpPath, "utf8"));
  const cert = fs.readFileSync(config.certPath, "utf8");
  const keyPath = readFirstKeyFile(config.keyDir);
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

app.post("/api/records/evidence", async (req, res) => {
  const { org, recordId, patientId, uploaderHospital, dataHash, createdAt } = req.body;
  if (!recordId || !patientId || !uploaderHospital || !dataHash || !createdAt) {
    return res.status(400).json({ message: "missing required fields" });
  }
  try {
    const result = await submit(org, "CreateMedicalRecordEvidence", [
      String(recordId),
      String(patientId),
      String(uploaderHospital),
      String(dataHash),
      String(createdAt)
    ]);
    res.json(result);
  } catch (error) {
    res.status(500).json({ message: error.message });
  }
});

app.get("/api/records/evidence/:recordId", async (req, res) => {
  const org = req.query.org || "org1";
  try {
    const result = await evaluate(org, "GetMedicalRecordEvidence", [String(req.params.recordId)]);
    res.json(result);
  } catch (error) {
    res.status(500).json({ message: error.message });
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
    res.status(500).json({ message: error.message });
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
    res.status(500).json({ message: error.message });
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
    res.status(500).json({ message: error.message });
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
    res.status(500).json({ message: error.message });
  }
});

app.post("/api/access-requests", async (req, res) => {
  const { org, requestId, recordId, applicantHospital, patientId, reasonHash, status, createdAt } = req.body;
  if (!requestId || !recordId || !applicantHospital || !patientId || !reasonHash || !createdAt) {
    return res.status(400).json({ message: "missing required fields" });
  }
  try {
    // 迭代 5：传入 patientId，链码记录 applicantMsp
    const result = await submit(org, "CreateAccessRequest", [
      String(requestId),
      String(recordId),
      String(applicantHospital),
      String(patientId),
      String(reasonHash),
      String(status || "PENDING"),
      String(createdAt)
    ]);
    invalidateRequestCache(requestId);
    res.json(result);
  } catch (error) {
    res.status(500).json({ message: error.message });
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
    res.status(500).json({ message: error.message });
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
    res.status(500).json({ message: error.message });
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
    res.status(500).json({ message: error.message });
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
    res.status(500).json({ message: error.message });
  }
});

app.get("/api/access-requests/:requestId", async (req, res) => {
  const org = req.query.org || "org1";
  try {
    const result = await evaluate(org, "QueryAccessRequest", [String(req.params.requestId)]);
    res.json(result);
  } catch (error) {
    res.status(500).json({ message: error.message });
  }
});

app.listen(PORT, () => {
  console.log(`Gateway listening on :${PORT}`);
});
