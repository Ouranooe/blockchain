# 迭代 4 完成报告：文件哈希上链 + 链下加密存储

> 对应 [项目迭代计划（8次）.md](../项目迭代计划（8次）.md) 第 4 次迭代

## 一、本次迭代目标

落地"**链上存证、链下存文件**"的经典区块链范式：

1. **上传**：病历文件（PDF / JPG / PNG ≤10MB）经 **AES-256-GCM** 加密后落盘，**SHA-256(plaintext)** 上链存证
2. **下载**：读密文 → 解密 → 重新计算 SHA-256 → **与链上哈希比对** → 不一致一律拒绝
3. **流式处理**：加密走 64KB 分块，不把整文件塞进内存
4. **断点续传**：下载支持 `Range` 请求，返回 `206 Partial Content`
5. **双层完整性**：GCM tag（对称加密层）+ SHA-256 上链（业务层）

## 二、改动清单

### 2.1 后端（Python / FastAPI）

| 文件 | 改动 |
|------|------|
| [backend/requirements.txt](../backend/requirements.txt) | 无需新增（`cryptography` 由 `python-jose` 引入；`python-multipart` 已在 FastAPI 环境存在） |
| [backend/app/crypto_util.py](../backend/app/crypto_util.py) | **新文件** —— `encrypt_stream` / `decrypt_to_bytes` / `sha256_of_bytes` / `sha256_of_file` / `load_file_key`；`load_file_key` 生产环境强制 ENV 注入，dev 从 SECRET_KEY 派生 |
| [backend/app/config.py](../backend/app/config.py) | 新增 `FILE_KEY_BASE64` / `STORAGE_DIR` / `MAX_FILE_SIZE_BYTES` / `ALLOWED_MIME_TYPES` 四项配置 |
| [backend/app/models.py](../backend/app/models.py) | `MedicalRecord` 新增 6 列：`file_name` / `file_mime` / `file_size` / `file_path` / `file_nonce_b64` / `file_tag_b64` |
| [backend/app/schemas.py](../backend/app/schemas.py) | `MedicalRecordItem` 扩展 `has_file` / `file_name` / `file_mime` / `file_size`；新增 `FileVerifyResult` |
| [backend/app/files.py](../backend/app/files.py) | **新文件** —— 独立 APIRouter，含 `POST /api/records/upload` / `GET /api/records/{id}/download`（支持 Range）/ `GET /api/records/{id}/verify`；权限与现有下载路径一致（本院医生 / 已授权医生 / 患者本人 / 管理员） |
| [backend/app/main.py](../backend/app/main.py) | `include_router(files_router)`；`_record_to_item` 补齐新字段 |
| [backend/sql/init.sql](../backend/sql/init.sql) | 建表 DDL 追加 6 列 + 升级提示 ALTER |
| [backend/storage/.gitkeep](../backend/storage/.gitkeep) | 占位目录 |

### 2.2 前端（Vue3）

| 文件 | 改动 |
|------|------|
| [frontend/src/views/hospital/UploadView.vue](../frontend/src/views/hospital/UploadView.vue) | 新增"文本 / 文件"模式切换；文件模式走 multipart；成功提示展示哈希前 12 位与 TxID |
| [frontend/src/views/hospital/RecordListView.vue](../frontend/src/views/hospital/RecordListView.vue) | 新增"文件"列，展示 **"链上哈希 ✓" + "文件完整性 ✓"** 双勾；新增"下载"按钮，下载成功后记录 `verifiedIds` 驱动完整性勾标 |
| [frontend/src/views/patient/MyRecordsView.vue](../frontend/src/views/patient/MyRecordsView.vue) | 同上，患者侧可下载自己病历的文件 |

### 2.3 测试

| 文件 | 改动 |
|------|------|
| [backend/tests/conftest.py](../backend/tests/conftest.py) | 引入 `tempfile` 隔离 `MEDSHARE_STORAGE_DIR`；把 `files_module` 纳入 monkeypatch 循环；暴露 `app.state.chain_store` 供篡改测试 |
| [backend/tests/test_crypto.py](../backend/tests/test_crypto.py) | **新文件，12 条用例** —— 往返 / 空输入 / 1MB blob / 篡改密文 / 篡改 tag / 换错 key / 流式 SHA-256 / key loader 四种场景 / **10MB 吞吐** |
| [backend/tests/test_files.py](../backend/tests/test_files.py) | **新文件，16 条用例** —— 上传（成功 / 非法 MIME / 超大 / 空 / 非医院身份）/ 下载（往返 / 篡改密文 / 篡改链上哈希 / 越权 / 患者本人 / Range 206 / 开放式 Range / 416）/ 校验（通过 / 失败 / 非文件病历） |

## 三、验证结果

### 3.1 crypto 单元测试

```
$ pytest tests/test_crypto.py -v -s
  ...
  10MB GCM encrypt throughput: 351.7 MB/s (elapsed 28.4 ms)
  ...
12 passed in 0.33s
```

### 3.2 文件端到端测试

```
$ pytest tests/test_files.py -v
tests/test_files.py::TestUpload::test_upload_success_stores_encrypted_and_chain_hash    PASSED
tests/test_files.py::TestUpload::test_upload_unsupported_mime_rejected                  PASSED
tests/test_files.py::TestUpload::test_upload_too_large_rejected                         PASSED
tests/test_files.py::TestUpload::test_upload_empty_file_rejected                        PASSED
tests/test_files.py::TestUpload::test_patient_cannot_upload                             PASSED
tests/test_files.py::TestDownload::test_download_round_trip                             PASSED
tests/test_files.py::TestDownload::test_download_detects_tampered_ciphertext            PASSED
tests/test_files.py::TestDownload::test_download_detects_chain_hash_mismatch            PASSED
tests/test_files.py::TestDownload::test_download_forbidden_for_unrelated_hospital       PASSED
tests/test_files.py::TestDownload::test_patient_can_download_own_record                 PASSED
tests/test_files.py::TestDownload::test_download_range_returns_206_and_slice            PASSED
tests/test_files.py::TestDownload::test_download_open_ended_range                       PASSED
tests/test_files.py::TestDownload::test_download_unsatisfiable_range                    PASSED
tests/test_files.py::TestVerify::test_verify_passes_on_untouched_file                   PASSED
tests/test_files.py::TestVerify::test_verify_fails_when_ciphertext_tampered             PASSED
tests/test_files.py::TestVerify::test_verify_fails_on_non_file_record                   PASSED

16 passed in 15.27s
```

### 3.3 全量回归

```
66 passed, 214 warnings in 40.84s

# 分解
tests/test_auth.py      16 passed   (迭代 1)
tests/test_records.py   12 passed   (迭代 2)
tests/test_history.py   10 passed   (迭代 3)
tests/test_crypto.py    12 passed   (迭代 4 新增)
tests/test_files.py     16 passed   (迭代 4 新增)
```

**迭代 4 净增 28 条，累计后端 66/66 全过。**

## 四、量化指标（对应计划验证条目）

| 指标 | 目标 | 实测 | 结论 |
|------|------|------|------|
| 篡改密文 → 检出 | 100% | **100%** 检出（GCM InvalidTag → 422） | ✓ |
| 篡改链上哈希 → 检出 | 100% | **100%** 检出（SHA-256 重算不匹配 → 422） | ✓ |
| 10MB 文件加密吞吐 | ≥ 30 MB/s | **351.7 MB/s** | ✓ |
| 支持 Range 断点续传 | — | `bytes=100-199` → 206 + 100B 切片 | ✓ |
| 链上只存哈希 | — | 链码仅持有 `dataHash`，文件不触链 | ✓ |
| 链下密文与明文区分 | — | 读盘原始字节 ≠ 明文 bytes | ✓ |

## 五、核心设计决策

### 5.1 双层完整性保护

**第一层（对称加密层）：AES-256-GCM 自带 128-bit 认证 tag**
- 防篡改：任何对密文 / nonce / tag 的修改都会让 `decryptor.finalize()` 抛 `InvalidTag`
- 本层保护的是"文件在磁盘上被篡改"的场景
- 不依赖链：即使链挂了，本层仍能判断密文是否可信

**第二层（业务层）：SHA-256(plaintext) 上链**
- 防攻击面扩展：攻击者如果同时拿到主密钥，可以重新生成合法的密文 + tag，但他**改不了链上的哈希**
- 本层保护的是"连密钥一起被攻破"的极端场景 —— 此时链仍是真相
- 对应代码：`_verify_and_get_plaintext` 先 GCM 解密（第一层），再重算 SHA-256 与 DB 值（= 链上值）比对（第二层）

这是区块链 + 密码学组合的经典范式：密码学保证"本地完整性"，链保证"权威真相"。

### 5.2 主密钥来源分层

```python
load_file_key(
    file_key_b64=settings.FILE_KEY_BASE64,   # 优先 ENV
    secret_key=settings.SECRET_KEY,          # 兜底派生
    environment=settings.ENVIRONMENT,        # 生产强制 ENV
)
```

- **生产**：必须 `MEDSHARE_FILE_KEY_BASE64` ENV 注入 32 字节 base64；不给就启动报错
- **开发**：`sha256("medshare-file-key::" + SECRET_KEY)`，保证重启后密钥一致，能解出之前的密文
- **测试**：走开发路径（conftest 设了固定 SECRET_KEY），完全可重现

### 5.3 逐块加密 + 内存友好下载

**加密写盘**：逐块 `encryptor.update()`，空间复杂度 O(64KB) 而非 O(file)。虽然 `cryptography.Cipher` 的 GCM 实际上是在流式处理，认证 tag 只有等 `finalize()` 时才产生；但全程内存占用仍只有单块大小。

**下载**：当前实现在内存里持有完整明文再切片给 `Range`，这是因为 GCM 的 tag 校验必须基于完整密文。对 ≤10MB 文件无压力。对 > 100MB 的大文件需要改为"分段加密 + 逐段校验"设计（不在本迭代范围）。

### 5.4 为什么不直接把文件塞进 MySQL（LONGBLOB）？

- **规模扩展**：10MB × 10 万条病历 = 1TB，MySQL 难管理；文件系统对此友好
- **灾备分离**：密文与密钥、与数据库分离，提高攻击者"同时拿到三者"的难度
- **流式下载**：FastAPI `StreamingResponse` + 文件对象天然适配
- **备份策略**：数据库用 `mysqldump`，密文目录用 `rsync`，分别做冷热存

### 5.5 兼容老文本病历

- 现有 `POST /api/records`（JSON 文本）**保持不变**，iteration 1–3 的测试与业务全部兼容
- 新 multipart 走 `POST /api/records/upload`，两条路径互不干扰
- `MedicalRecordItem.has_file` 告诉前端"这是文本 or 文件"

## 六、关键代码片段

### 加密上链流程

```python
# backend/app/files.py - upload_record_file (要点)
with open(tmp_abs, "wb") as dst:
    enc = encrypt_stream(file.file, dst, _FILE_KEY)   # ① 流式 AES-256-GCM
record = MedicalRecord(
    content_hash=enc.sha256_hex,                       # ② SHA-256(plaintext) 即将上链
    file_nonce_b64=b64encode(enc.nonce),
    file_tag_b64=b64encode(enc.tag),
    ...
)
chain_result = create_record_evidence(
    data_hash=enc.sha256_hex,                          # ③ 上链存证
    ...
)
```

### 下载校验流程

```python
# backend/app/files.py - download / verify
plaintext = decrypt_to_bytes(fh, _FILE_KEY, nonce, tag)  # ① GCM 验 tag
actual = sha256_of_bytes(plaintext)                      # ② 重算业务哈希
if actual != record.content_hash:                        # ③ 与"链上真相"对比
    raise HTTPException(422, detail="文件哈希与链上存证不一致")
```

## 七、已知不足 / 留给后续迭代

1. **下载仍全量解密**：大文件的 `Range` 目前是"整解后切片"，并非真正意义上的"只解密所需段"。>100MB 时需要改为分段 GCM（比如每 1MB 一块，各自独立 tag）
2. **明文落日志风险**：加密走内存，但 `uvicorn` 访问日志会记 Content-Length 等元数据；敏感场景需额外过滤
3. **密钥轮换未做**：所有文件用同一主密钥；生产应做"主密钥加密数据密钥"分层（KEK + DEK）
4. **无病毒扫描**：`file.file` 直接写盘，生产前需接 ClamAV 或类似
5. **前端没做文件 MIME 白名单**：后端已拦截，但前端 `accept` 仅是提示性，低版本浏览器不严格
6. **chain hash 与 DB hash 绑定**：当前 "DB 改了哈希" 等同于 "链上哈希被改"（测试中这样模拟）。真实链场景下还需验证一次 chaincode 返回值，属迭代 5（ABAC）与迭代 8（压测）的工作

## 八、如何复核本次迭代

```bash
# 1. 后端加密 + 文件测试
cd backend
pytest tests/test_crypto.py tests/test_files.py -v -s

# 2. 全量回归
pytest tests/ -v

# 3. 端到端手动（需容器环境）
docker compose up -d --build backend frontend
# 浏览器 http://localhost:5173 登录 hospital_a → 上传 → 下载
# 观察：
#   - 上传后列表行出现"链上哈希 ✓"
#   - 点"下载"后本地拿到 PDF，出现"文件完整性 ✓"
#   - 可用 curl -H "Range: bytes=0-99" 取前 100 字节，返回 206

# 4. 篡改验证（可选手动）
# 找到 backend/storage/record-N.enc，用 hex editor 改一个字节
# 再点下载，前端弹红色提示"文件完整性校验未通过"
```

## 九、下一次迭代（迭代 5）预告

**迭代 5：链上访问控制精细化（ABAC）** —— 把"有期限授权 / 链上撤销 / 访问次数限制"的策略**写进链码**。

- `ApproveAccessRequest(duration_days, max_reads)` 上链写入 `expires_at` / `remaining_reads`
- `AccessRecord(recordId, requesterId)` 原子检查 + 计数扣减 + 访问事件记录
- `RevokeAccessRequest(reqId, patientId)` 链上撤销 + ClientIdentity 校验
- 量化目标：
  - 即使绕过后端直接调网关，**非法访问 100% 被链码拒绝**
  - 链码方法平均 `getState` 次数 ≤3
