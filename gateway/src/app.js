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
    res.status(500).json({ message: error.message });
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
