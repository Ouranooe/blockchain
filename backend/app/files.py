"""迭代 4：文件上传 / 下载 / 完整性校验端点。

- 上传：multipart → 计算 SHA-256（明文）+ AES-256-GCM 加密 → 落盘
        → 调链写入 content_hash（与文本上传走同一条链码方法）
- 下载：读 DB → 解密 → 重新计算 SHA-256 → 与链上 content_hash 比对
        → 不一致则 422；一致则流式下发（支持 Range）
"""

from __future__ import annotations

import io
import logging
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import Response, StreamingResponse
from sqlalchemy.orm import Session

from .auth import get_current_user, require_role
from .config import settings
from .crypto_util import (
    b64decode,
    b64encode,
    decrypt_to_bytes,
    encrypt_stream,
    load_file_key,
    sha256_of_bytes,
)
from .database import get_db
from .events import AuditEvent as AuditPayload, bus
from .gateway import access_record_consume, create_record_evidence
from .models import AccessRequest, MedicalRecord, User
from .schemas import FileVerifyResult, MedicalRecordItem

router = APIRouter()

_FILE_KEY = load_file_key(
    file_key_b64=settings.FILE_KEY_BASE64,
    secret_key=settings.SECRET_KEY,
    environment=settings.ENVIRONMENT,
)


def _ensure_storage_dir() -> str:
    os.makedirs(settings.STORAGE_DIR, exist_ok=True)
    return settings.STORAGE_DIR


def _authorize_file_access(current_user: User, record: MedicalRecord, db: Session) -> bool:
    if current_user.role == "admin":
        return True
    if current_user.role == "patient" and record.patient_id == current_user.id:
        return True
    if current_user.role == "hospital":
        if record.uploader_hospital_id == current_user.id:
            return True
        approved = (
            db.query(AccessRequest)
            .filter(
                AccessRequest.record_id == record.id,
                AccessRequest.applicant_hospital_id == current_user.id,
                AccessRequest.status == "APPROVED",
            )
            .first()
        )
        return approved is not None
    return False


def _user_map(db: Session, ids):
    if not ids:
        return {}
    users = db.query(User).filter(User.id.in_(set(ids))).all()
    return {u.id: u for u in users}


def _record_to_item(record: MedicalRecord, users: Dict[int, User], can_view: bool) -> MedicalRecordItem:
    # 与 main.py 里的等价实现保持一致（导入 main 会造成循环依赖，故此处重写）
    patient = users.get(record.patient_id)
    hospital = users.get(record.uploader_hospital_id)
    return MedicalRecordItem(
        id=record.id,
        patient_id=record.patient_id,
        patient_name=patient.real_name if patient else f"patient_{record.patient_id}",
        uploader_hospital=hospital.hospital_name
        if hospital
        else f"hospital_{record.uploader_hospital_id}",
        title=record.title,
        diagnosis=record.diagnosis,
        content_hash=record.content_hash,
        tx_id=record.tx_id,
        version=record.version or 1,
        previous_tx_id=record.previous_tx_id,
        updated_at=record.updated_at,
        created_at=record.created_at,
        can_view_content=can_view,
        content=record.content if can_view else None,
        has_file=bool(record.file_path),
        file_name=record.file_name,
        file_mime=record.file_mime,
        file_size=record.file_size,
    )


@router.post(f"{settings.API_PREFIX}/records/upload", response_model=MedicalRecordItem)
def upload_record_file(
    patient_id: int = Form(...),
    title: str = Form(..., min_length=1, max_length=255),
    diagnosis: str = Form(..., min_length=1, max_length=255),
    description: str = Form(""),
    file: UploadFile = File(...),
    current_user: User = Depends(require_role("hospital")),
    db: Session = Depends(get_db),
):
    patient = (
        db.query(User)
        .filter(User.id == patient_id, User.role == "patient")
        .first()
    )
    if not patient:
        raise HTTPException(status_code=400, detail="patient_id 无效")
    if file.content_type and file.content_type not in settings.ALLOWED_MIME_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的文件类型：{file.content_type}",
        )

    storage_dir = _ensure_storage_dir()
    tmp_rel = f"tmp-{uuid.uuid4().hex}.enc"
    tmp_abs = os.path.join(storage_dir, tmp_rel)

    plain_size = 0
    try:
        with open(tmp_abs, "wb") as dst:
            enc = encrypt_stream(file.file, dst, _FILE_KEY)
        plain_size = enc.plaintext_size
        if plain_size == 0:
            raise HTTPException(status_code=400, detail="文件为空")
        if plain_size > settings.MAX_FILE_SIZE_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"文件超出上限 ({settings.MAX_FILE_SIZE_BYTES} 字节)",
            )
    except HTTPException:
        if os.path.exists(tmp_abs):
            os.remove(tmp_abs)
        raise
    except Exception as exc:
        if os.path.exists(tmp_abs):
            os.remove(tmp_abs)
        raise HTTPException(status_code=500, detail=f"加密写盘失败：{exc}") from exc

    record = MedicalRecord(
        patient_id=patient_id,
        uploader_hospital_id=current_user.id,
        title=title,
        diagnosis=diagnosis,
        content=description or f"[文件] {file.filename}",
        content_hash=enc.sha256_hex,
        version=1,
        previous_tx_id=None,
        file_name=file.filename or "unnamed",
        file_mime=file.content_type or "application/octet-stream",
        file_size=plain_size,
        file_nonce_b64=b64encode(enc.nonce),
        file_tag_b64=b64encode(enc.tag),
    )
    db.add(record)
    db.flush()  # 取 record.id

    final_rel = f"record-{record.id}.enc"
    final_abs = os.path.join(storage_dir, final_rel)
    try:
        os.replace(tmp_abs, final_abs)
    except Exception as exc:
        db.rollback()
        if os.path.exists(tmp_abs):
            os.remove(tmp_abs)
        raise HTTPException(status_code=500, detail=f"重命名密文失败：{exc}") from exc
    record.file_path = final_rel

    now_iso = datetime.now(timezone.utc).isoformat()
    try:
        chain_result = create_record_evidence(
            hospital_name=current_user.hospital_name or current_user.username,
            record_id=record.id,
            patient_id=record.patient_id,
            data_hash=enc.sha256_hex,
            created_at=now_iso,
        )
        record.tx_id = chain_result.get("txId")
    except RuntimeError as exc:
        db.rollback()
        if os.path.exists(final_abs):
            os.remove(final_abs)
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    db.commit()
    db.refresh(record)

    try:
        bus.emit_sync(
            AuditPayload(
                event_type="RecordCreated",
                actor_id=current_user.id,
                actor_role=current_user.role,
                subject_user_id=record.patient_id,
                record_id=record.id,
                tx_id=record.tx_id,
                message=f"{current_user.hospital_name or current_user.username} 上传了一份带文件的病历",
                payload={
                    "title": record.title,
                    "file_name": record.file_name,
                    "file_size": record.file_size,
                    "version": 1,
                },
            )
        )
    except Exception:
        logger.exception("emit RecordCreated failed")

    users = _user_map(db, [record.patient_id, record.uploader_hospital_id])
    return _record_to_item(record, users, True)


def _parse_range_header(header: Optional[str], size: int) -> Optional[Tuple[int, int]]:
    """解析 bytes=start-end；失败返回 None 表示按全量下发。"""
    if not header:
        return None
    match = re.match(r"bytes=(\d*)-(\d*)", header.strip())
    if not match:
        return None
    start_s, end_s = match.group(1), match.group(2)
    if start_s == "" and end_s == "":
        return None
    if start_s == "":
        # 后缀 range: 最后 N 字节
        length = int(end_s)
        if length <= 0:
            return None
        start = max(size - length, 0)
        end = size - 1
    else:
        start = int(start_s)
        end = int(end_s) if end_s else size - 1
    if start > end or start >= size:
        raise HTTPException(status_code=416, detail="Range 无法满足")
    end = min(end, size - 1)
    return start, end


def _load_and_decrypt(record: MedicalRecord) -> bytes:
    path_abs = os.path.join(settings.STORAGE_DIR, record.file_path)
    if not os.path.exists(path_abs):
        raise HTTPException(status_code=404, detail="密文文件不存在")
    try:
        nonce = b64decode(record.file_nonce_b64 or "")
        tag = b64decode(record.file_tag_b64 or "")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"非法 nonce/tag: {exc}") from exc
    with open(path_abs, "rb") as fh:
        try:
            return decrypt_to_bytes(fh, _FILE_KEY, nonce, tag)
        except Exception as exc:
            # InvalidTag / ValueError / OSError
            raise HTTPException(
                status_code=422,
                detail=f"文件完整性校验失败（GCM tag 或密文被篡改）：{exc}",
            ) from exc


def _verify_and_get_plaintext(record: MedicalRecord) -> Tuple[bytes, str]:
    plaintext = _load_and_decrypt(record)
    actual_hash = sha256_of_bytes(plaintext)
    if actual_hash != record.content_hash:
        raise HTTPException(
            status_code=422,
            detail="文件哈希与链上存证不一致，疑似遭到篡改",
        )
    return plaintext, actual_hash


@router.get(f"{settings.API_PREFIX}/records/{{record_id}}/verify", response_model=FileVerifyResult)
def verify_record_file(
    record_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    record = db.query(MedicalRecord).filter(MedicalRecord.id == record_id).first()
    if not record:
        raise HTTPException(status_code=404, detail="病历不存在")
    if not record.file_path:
        raise HTTPException(status_code=400, detail="该病历没有附件文件")
    if not _authorize_file_access(current_user, record, db):
        raise HTTPException(status_code=403, detail="无权限访问该文件")

    plaintext, actual = _verify_and_get_plaintext(record)
    return FileVerifyResult(
        record_id=record.id,
        chain_hash=record.content_hash,
        decrypted_hash=actual,
        hash_match=True,
        file_size=len(plaintext),
    )


@router.get(f"{settings.API_PREFIX}/records/{{record_id}}/download")
def download_record_file(
    record_id: int,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    record = db.query(MedicalRecord).filter(MedicalRecord.id == record_id).first()
    if not record:
        raise HTTPException(status_code=404, detail="病历不存在")
    if not record.file_path:
        raise HTTPException(status_code=400, detail="该病历没有附件文件")

    # 迭代 6：权限检查分派 —— admin / 患者本人 / 本院医生直接放行；
    #         其他角色走"需要链上授权消费"路径
    is_privileged = (
        current_user.role == "admin"
        or (current_user.role == "patient" and record.patient_id == current_user.id)
        or (
            current_user.role == "hospital"
            and record.uploader_hospital_id == current_user.id
        )
    )
    if not is_privileged and current_user.role != "hospital":
        raise HTTPException(status_code=403, detail="无权限下载该文件")

    # 迭代 5：非本院医生下载 = 消费一次链上授权（链码内做守卫 + 扣减 remainingReads）
    consume_tx = None
    consume_remaining = None
    if (
        current_user.role == "hospital"
        and record.uploader_hospital_id != current_user.id
    ):
        req = (
            db.query(AccessRequest)
            .filter(
                AccessRequest.record_id == record.id,
                AccessRequest.applicant_hospital_id == current_user.id,
                AccessRequest.status == "APPROVED",
            )
            .order_by(AccessRequest.id.desc())
            .first()
        )
        if not req:
            # 迭代 6：后端已能判定无有效授权，也记录一次未授权尝试
            try:
                bus.emit_sync(
                    AuditPayload(
                        event_type="UnauthorizedAttempt",
                        actor_id=current_user.id,
                        actor_role=current_user.role,
                        subject_user_id=record.patient_id,
                        record_id=record.id,
                        message=(
                            f"{current_user.hospital_name or current_user.username} "
                            f"尝试下载病历 #{record.id}，但无有效授权"
                        ),
                        payload={"reason": "no-approved-request"},
                    )
                )
            except Exception:
                logger.exception("emit UnauthorizedAttempt failed")
            raise HTTPException(status_code=403, detail="没有已批准的授权")
        try:
            result = access_record_consume(
                hospital_name=current_user.hospital_name or current_user.username,
                request_id=req.id,
                accessed_at=datetime.now(timezone.utc).isoformat(),
            )
        except RuntimeError as exc:
            # 链码层拒绝（过期 / 耗尽 / MSP 不匹配 / 已撤销 / 状态非 APPROVED）
            # 迭代 6：记录一次"未授权访问尝试"审计
            try:
                bus.emit_sync(
                    AuditPayload(
                        event_type="UnauthorizedAttempt",
                        actor_id=current_user.id,
                        actor_role=current_user.role,
                        subject_user_id=record.patient_id,
                        record_id=record.id,
                        request_id=req.id,
                        message=(
                            f"{current_user.hospital_name or current_user.username} "
                            f"尝试下载病历 #{record.id}，但被链码层拒绝：{exc}"
                        ),
                        payload={"reason": str(exc)},
                    )
                )
            except Exception:
                logger.exception("emit UnauthorizedAttempt failed")
            raise HTTPException(
                status_code=403, detail=f"链码层拒绝授权：{exc}"
            ) from exc
        chain_obj = (
            result.get("result") if isinstance(result, dict) else None
        ) or {}
        consume_tx = result.get("txId") if isinstance(result, dict) else None
        consume_remaining = chain_obj.get("remainingReads")
        # 把链上扣减镜像到 DB（保证后续列表过滤一致）
        if isinstance(consume_remaining, int):
            req.remaining_reads = consume_remaining
            if consume_remaining <= 0:
                # 链上允许的最后一次消费已经完成；后续状态由 AccessRecord 自身守卫
                pass
            db.commit()

        # 迭代 6：广播"病历被访问"事件 —— 推给患者
        try:
            bus.emit_sync(
                AuditPayload(
                    event_type="AccessRecorded",
                    actor_id=current_user.id,
                    actor_role=current_user.role,
                    subject_user_id=record.patient_id,
                    extra_subject_ids=[current_user.id],
                    record_id=record.id,
                    request_id=req.id,
                    tx_id=consume_tx,
                    message=(
                        f"{current_user.hospital_name or current_user.username} "
                        f"下载了您的病历 #{record.id}（剩余 {consume_remaining} 次）"
                    ),
                    payload={"remaining_reads": consume_remaining},
                )
            )
        except Exception:
            logger.exception("emit AccessRecorded failed")

    plaintext, actual_hash = _verify_and_get_plaintext(record)
    total = len(plaintext)

    media_type = record.file_mime or "application/octet-stream"
    filename = record.file_name or f"record-{record.id}.bin"
    # 仅保留 ASCII 简化（完整 RFC 5987 编码不在本迭代范围）
    safe_filename = filename.encode("ascii", "replace").decode("ascii")
    common_headers = {
        "Content-Disposition": f'attachment; filename="{safe_filename}"',
        "X-Content-Hash": actual_hash,
        "X-Hash-Verified": "1",
        "Accept-Ranges": "bytes",
    }
    if consume_tx is not None:
        common_headers["X-Access-Tx"] = str(consume_tx)
    if consume_remaining is not None:
        common_headers["X-Remaining-Reads"] = str(consume_remaining)

    rng = _parse_range_header(request.headers.get("range"), total)
    if rng is not None:
        start, end = rng
        sliced = plaintext[start : end + 1]
        headers = {
            **common_headers,
            "Content-Range": f"bytes {start}-{end}/{total}",
            "Content-Length": str(len(sliced)),
        }
        return Response(
            content=sliced,
            status_code=status.HTTP_206_PARTIAL_CONTENT,
            media_type=media_type,
            headers=headers,
        )

    def iter_bytes():
        yield from [plaintext[i : i + 64 * 1024] for i in range(0, total, 64 * 1024)]

    headers = {**common_headers, "Content-Length": str(total)}
    return StreamingResponse(
        iter_bytes(),
        media_type=media_type,
        headers=headers,
    )
