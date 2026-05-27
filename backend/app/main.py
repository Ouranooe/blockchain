import asyncio
import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from fastapi import (
    Depends,
    FastAPI,
    HTTPException,
    Query,
    Request,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from fastapi.middleware.cors import CORSMiddleware
from jose import JWTError, jwt
from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from .auth import ALGORITHM, create_access_token, get_current_user, require_role
from .config import settings
from .database import Base, engine, get_db
from .events import AuditEvent as AuditPayload, bus
from .files import router as files_router
from .metrics import CHAIN_CALLS, WS_CONNECTIONS, install_metrics
from .gateway import (
    anchor_record_batch,
    approve_access_request,
    approve_governance_action,
    check_gateway_ready,
    create_access_request,
    create_record_evidence,
    credit_balance,
    credit_history,
    credit_transfer,
    execute_governance_action,
    freeze_record,
    get_anchor_batch,
    get_governance_action,
    get_schema_version,
    list_anchor_batches,
    list_governance_actions,
    migrate_records_v2,
    propose_governance_action,
    query_records_by_category,
    unfreeze_record,
    query_access_request,
    query_access_request_history,
    query_pending_requests_for_patient,
    query_record_history,
    query_record_version,
    query_records_by_date,
    query_records_by_hospital,
    reject_access_request,
    reject_governance_action,
    revise_record_evidence,
    revoke_access_request,
    verify_record_inclusion,
)
from . import merkle as merkle_util
from .models import (
    AccessRequest,
    AuditEventRow,
    GovernanceAction,
    MedicalRecord,
    MerkleAnchorBatch,
    User,
)
from .schemas import (
    AccessRequestChainHistory,
    AccessRequestCreate,
    AccessRequestItem,
    AccessRequestReview,
    AnchorBatchInfo,
    AnchorRunResult,
    AuditEvent,
    ChainHistoryEntry,
    ChainPendingRequestBrief,
    ChainPendingRequestPage,
    ChainRecordBrief,
    ChainRecordPage,
    ChangePasswordRequest,
    CreditBalance as CreditBalanceSchema,
    CreditHistoryPage,
    CreditLedgerItem,
    CreditTransferRequest,
    GovernanceActionInfo,
    GovernanceApprover,
    GovernanceProposeRequest,
    InclusionVerifyRequest,
    InclusionVerifyResponse,
    LoginRequest,
    LoginResponse,
    MedicalRecordCreate,
    MedicalRecordItem,
    MedicalRecordRevise,
    MerkleProofStep,
    MigrationRequest,
    MigrationResponse,
    RecordChainHistory,
    RecordHistory,
    RecordInclusionProof,
    RecordVersionItem,
    RegisterRequest,
    SimpleMessage,
    SystemInfo,
    UserInfo,
)
from .security import hash_password, is_hashed, verify_password

app = FastAPI(title=settings.APP_NAME)

# 迭代 8：限流（敏感接口防暴力破解 / 注册刷库）
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from fastapi.responses import JSONResponse

limiter = Limiter(
    key_func=get_remote_address,
    default_limits=[],
    enabled=settings.RATE_LIMIT_ENABLED,
)
app.state.limiter = limiter


@app.exception_handler(RateLimitExceeded)
async def _rate_limit_handler(request, exc):
    return JSONResponse(
        status_code=429,
        content={"detail": f"请求过于频繁，请稍后再试：{exc.detail}"},
    )


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 迭代 8：Prometheus /metrics（放在 CORS 之后、路由注册之前）
install_metrics(app)


@app.on_event("startup")
async def on_startup():
    Base.metadata.create_all(bind=engine)
    # 迭代 6：事件总线异步任务启动（批量写审计 + WebSocket 广播）
    await bus.start()


@app.on_event("shutdown")
async def on_shutdown():
    await bus.stop()


app.include_router(files_router)


# ---------------- 迭代 6：事件发射 helpers ----------------

def _safe_emit(event: AuditPayload) -> None:
    """在业务流中调用；异常不影响主流程。"""
    try:
        bus.emit_sync(event)
    except Exception:  # pragma: no cover - 事件总线异常不应影响业务
        import logging as _logging

        _logging.getLogger(__name__).exception("emit event failed")


def _user_map(db: Session, ids: List[int]) -> Dict[int, User]:
    if not ids:
        return {}
    users = db.query(User).filter(User.id.in_(set(ids))).all()
    return {user.id: user for user in users}


def _record_to_item(record: MedicalRecord, users: Dict[int, User], can_view: bool) -> MedicalRecordItem:
    patient = users.get(record.patient_id)
    hospital = users.get(record.uploader_hospital_id)
    return MedicalRecordItem(
        id=record.id,
        patient_id=record.patient_id,
        patient_name=patient.real_name if patient else f"patient_{record.patient_id}",
        uploader_hospital=hospital.hospital_name if hospital else f"hospital_{record.uploader_hospital_id}",
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
        frozen=bool(record.frozen),
        frozen_at=record.frozen_at,
        freeze_tx_id=record.freeze_tx_id,
        unfreeze_tx_id=record.unfreeze_tx_id,
        category=record.category or "GENERAL",
    )


def _derive_status(req: AccessRequest) -> str:
    """迭代 5：APPROVED 状态下若过期或次数耗尽，展示为 EXPIRED。
    这只是"视图"，链上真相只有通过 AccessRecord 时的守卫才能拒绝访问。"""
    if req.status != "APPROVED":
        return req.status
    now = datetime.now(timezone.utc)
    exp = req.expires_at
    if exp is not None:
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        if exp <= now:
            return "EXPIRED"
    if req.remaining_reads is not None and req.remaining_reads <= 0:
        return "EXHAUSTED"
    return req.status


def _request_to_item(req: AccessRequest, users: Dict[int, User], records: Dict[int, MedicalRecord]) -> AccessRequestItem:
    record = records.get(req.record_id)
    applicant = users.get(req.applicant_hospital_id)
    patient = users.get(req.patient_id)
    return AccessRequestItem(
        id=req.id,
        record_id=req.record_id,
        record_title=record.title if record else f"record_{req.record_id}",
        applicant_hospital=applicant.hospital_name if applicant else f"hospital_{req.applicant_hospital_id}",
        patient_name=patient.real_name if patient else f"patient_{req.patient_id}",
        reason=req.reason,
        status=_derive_status(req),
        create_tx_id=req.create_tx_id,
        review_tx_id=req.review_tx_id,
        created_at=req.created_at,
        reviewed_at=req.reviewed_at,
        expires_at=req.expires_at,
        remaining_reads=req.remaining_reads,
        max_reads=req.max_reads,
        revoked_at=req.revoked_at,
        revoke_tx_id=req.revoke_tx_id,
        purpose=req.purpose or "TREATMENT",
    )


def _as_utc_iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc).isoformat()
    return dt.astimezone(timezone.utc).isoformat()


def _ensure_request_on_chain(req: AccessRequest, record: MedicalRecord, applicant: User):
    if req.create_tx_id:
        return

    reason_hash = req.reason_hash or hashlib.sha256(req.reason.encode("utf-8")).hexdigest()
    if not req.reason_hash:
        req.reason_hash = reason_hash

    try:
        chain_result = create_access_request(
            hospital_name=applicant.hospital_name or applicant.username,
            request_id=req.id,
            record_id=req.record_id,
            patient_id=req.patient_id,   # 迭代 5：链码写入 patientId
            reason_hash=reason_hash,
            created_at=_as_utc_iso(req.created_at),
        )
        req.create_tx_id = chain_result.get("txId")
    except RuntimeError as exc:
        message = str(exc)
        if "already exists" not in message:
            raise
        chain_state = query_access_request(req.id)
        if isinstance(chain_state, dict):
            result = chain_state.get("result") or {}
            req.create_tx_id = result.get("txId") or chain_state.get("txId") or req.create_tx_id


@app.get("/health")
def health_check():
    return {"status": "ok"}


@app.get("/health/live", include_in_schema=False)
def liveness():
    """迭代 8：K8s liveness 探针 —— 进程在跑即 200。"""
    return {"status": "alive"}


@app.get("/health/ready", include_in_schema=False)
def readiness(db: Session = Depends(get_db)):
    checks = {}
    try:
        from sqlalchemy import text

        db.execute(text("SELECT 1"))
        checks["database"] = "ready"
    except Exception as exc:  # pragma: no cover
        checks["database"] = f"unready: {exc}"

    try:
        checks["gateway"] = check_gateway_ready()
    except RuntimeError as exc:
        checks["gateway"] = f"unready: {exc}"

    if checks["database"] == "ready" and isinstance(checks["gateway"], dict):
        return {"status": "ready", "checks": checks}
    raise HTTPException(status_code=503, detail={"status": "unready", "checks": checks})


@app.post(f"{settings.API_PREFIX}/auth/login", response_model=LoginResponse)
@limiter.limit(settings.RATE_LIMIT_LOGIN)
def login(request: Request, payload: LoginRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.username == payload.username).first()
    if not user or not verify_password(payload.password, user.password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="用户名或密码错误"
        )
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="账号已禁用")

    # 迭代 1：种子用户的历史明文密码首次成功登录后自动迁移为 bcrypt 哈希
    if not is_hashed(user.password):
        user.password = hash_password(payload.password)
        db.commit()

    token = create_access_token(user)
    return {"token": token, "user": user}


@app.post(f"{settings.API_PREFIX}/auth/register", response_model=UserInfo)
@limiter.limit(settings.RATE_LIMIT_REGISTER)
def register(request: Request, payload: RegisterRequest, db: Session = Depends(get_db)):
    # 自助注册目前仅开放患者角色；医院/管理员由管理员线下创建
    if payload.role != "patient":
        raise HTTPException(status_code=400, detail="自助注册仅支持 patient 角色")
    existing = db.query(User).filter(User.username == payload.username).first()
    if existing:
        raise HTTPException(status_code=409, detail="用户名已被占用")

    user = User(
        username=payload.username,
        password=hash_password(payload.password),
        role=payload.role,
        real_name=payload.real_name,
        hospital_name=None,
        msp_org=None,
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@app.post(f"{settings.API_PREFIX}/auth/change-password", response_model=SimpleMessage)
def change_password(
    payload: ChangePasswordRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not verify_password(payload.old_password, current_user.password):
        raise HTTPException(status_code=400, detail="原密码不正确")
    if payload.old_password == payload.new_password:
        raise HTTPException(status_code=400, detail="新密码不能与原密码相同")

    current_user.password = hash_password(payload.new_password)
    db.commit()
    return {"detail": "密码已更新"}


@app.get(f"{settings.API_PREFIX}/auth/me", response_model=UserInfo)
def whoami(current_user: User = Depends(get_current_user)):
    return current_user


@app.get(f"{settings.API_PREFIX}/users/patients", response_model=List[UserInfo])
def list_patients(
    _current_user: User = Depends(require_role("hospital")),
    db: Session = Depends(get_db),
):
    patients = (
        db.query(User)
        .filter(User.role == "patient")
        .order_by(User.id.asc())
        .all()
    )
    return patients


@app.get(f"{settings.API_PREFIX}/records", response_model=List[MedicalRecordItem])
def list_records(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if current_user.role == "patient":
        records = (
            db.query(MedicalRecord)
            .filter(MedicalRecord.patient_id == current_user.id)
            .order_by(MedicalRecord.created_at.desc())
            .all()
        )
        ids = [x.patient_id for x in records] + [x.uploader_hospital_id for x in records]
        users = _user_map(db, ids)
        return [_record_to_item(record, users, True) for record in records]

    if current_user.role == "hospital":
        records = db.query(MedicalRecord).order_by(MedicalRecord.created_at.desc()).all()
        approved_ids = {
            row.record_id
            for row in db.query(AccessRequest.record_id)
            .filter(
                AccessRequest.applicant_hospital_id == current_user.id,
                AccessRequest.status == "APPROVED",
            )
            .all()
        }
        ids = [x.patient_id for x in records] + [x.uploader_hospital_id for x in records]
        users = _user_map(db, ids)
        return [
            _record_to_item(
                record,
                users,
                record.uploader_hospital_id == current_user.id or record.id in approved_ids,
            )
            for record in records
        ]

    # 管理员仅用于审计，不返回正文内容
    records = db.query(MedicalRecord).order_by(MedicalRecord.created_at.desc()).all()
    ids = [x.patient_id for x in records] + [x.uploader_hospital_id for x in records]
    users = _user_map(db, ids)
    return [_record_to_item(record, users, False) for record in records]


@app.get(f"{settings.API_PREFIX}/patient/records", response_model=List[MedicalRecordItem])
def patient_records(
    current_user: User = Depends(require_role("patient")),
    db: Session = Depends(get_db),
):
    records = (
        db.query(MedicalRecord)
        .filter(MedicalRecord.patient_id == current_user.id)
        .order_by(MedicalRecord.created_at.desc())
        .all()
    )
    ids = [x.patient_id for x in records] + [x.uploader_hospital_id for x in records]
    users = _user_map(db, ids)
    return [_record_to_item(record, users, True) for record in records]


@app.post(f"{settings.API_PREFIX}/records", response_model=MedicalRecordItem)
def create_record(
    payload: MedicalRecordCreate,
    current_user: User = Depends(require_role("hospital")),
    db: Session = Depends(get_db),
):
    patient = (
        db.query(User)
        .filter(User.id == payload.patient_id, User.role == "patient")
        .first()
    )
    if not patient:
        raise HTTPException(status_code=400, detail="patient_id 无效")

    content_hash = hashlib.sha256(payload.content.encode("utf-8")).hexdigest()
    category = payload.category or "GENERAL"
    record = MedicalRecord(
        patient_id=payload.patient_id,
        uploader_hospital_id=current_user.id,
        title=payload.title,
        diagnosis=payload.diagnosis,
        content=payload.content,
        content_hash=content_hash,
        version=1,
        previous_tx_id=None,
        category=category,
    )
    db.add(record)
    db.flush()

    now_iso = datetime.now(timezone.utc).isoformat()
    try:
        chain_result = create_record_evidence(
            hospital_name=current_user.hospital_name or current_user.username,
            record_id=record.id,
            patient_id=record.patient_id,
            data_hash=content_hash,
            created_at=now_iso,
            category=category,
        )
        record.tx_id = chain_result.get("txId")
    except RuntimeError as exc:
        db.rollback()
        raise HTTPException(status_code=502, detail=str(exc))

    db.commit()
    db.refresh(record)
    users = _user_map(db, [record.patient_id, record.uploader_hospital_id])

    _safe_emit(
        AuditPayload(
            event_type="RecordCreated",
            actor_id=current_user.id,
            actor_role=current_user.role,
            subject_user_id=record.patient_id,
            record_id=record.id,
            tx_id=record.tx_id,
            message=f"{current_user.hospital_name or current_user.username} 上传了一条新病历",
            payload={"title": record.title, "version": 1},
        )
    )
    return _record_to_item(record, users, True)


@app.post(f"{settings.API_PREFIX}/access-requests", response_model=AccessRequestItem)
def submit_access_request(
    payload: AccessRequestCreate,
    current_user: User = Depends(require_role("hospital")),
    db: Session = Depends(get_db),
):
    record = db.query(MedicalRecord).filter(MedicalRecord.id == payload.record_id).first()
    if not record:
        raise HTTPException(status_code=404, detail="病历记录不存在")
    if record.uploader_hospital_id == current_user.id:
        raise HTTPException(status_code=400, detail="不能对自己上传的记录重复申请")

    existing_pending = (
        db.query(AccessRequest)
        .filter(
            AccessRequest.record_id == payload.record_id,
            AccessRequest.applicant_hospital_id == current_user.id,
            AccessRequest.status == "PENDING",
        )
        .first()
    )
    if existing_pending:
        raise HTTPException(status_code=400, detail="该记录已有待审批申请")

    reason_hash = hashlib.sha256(payload.reason.encode("utf-8")).hexdigest()
    purpose = payload.purpose or "TREATMENT"
    access_request = AccessRequest(
        record_id=payload.record_id,
        applicant_hospital_id=current_user.id,
        patient_id=record.patient_id,
        reason=payload.reason,
        reason_hash=reason_hash,
        status="PENDING",
        purpose=purpose,
    )
    db.add(access_request)
    db.flush()

    now_iso = datetime.now(timezone.utc).isoformat()
    try:
        chain_result = create_access_request(
            hospital_name=current_user.hospital_name or current_user.username,
            request_id=access_request.id,
            record_id=payload.record_id,
            patient_id=record.patient_id,
            reason_hash=reason_hash,
            created_at=now_iso,
            purpose=purpose,
        )
        access_request.create_tx_id = chain_result.get("txId")
    except RuntimeError as exc:
        db.rollback()
        raise HTTPException(status_code=502, detail=str(exc))

    db.commit()
    db.refresh(access_request)
    users = _user_map(db, [access_request.applicant_hospital_id, access_request.patient_id])
    records = {record.id: record}

    _safe_emit(
        AuditPayload(
            event_type="AccessRequestCreated",
            actor_id=current_user.id,
            actor_role=current_user.role,
            subject_user_id=record.patient_id,
            record_id=record.id,
            request_id=access_request.id,
            tx_id=access_request.create_tx_id,
            message=f"{current_user.hospital_name or current_user.username} 申请访问您的病历 #{record.id}",
        )
    )
    return _request_to_item(access_request, users, records)


@app.get(f"{settings.API_PREFIX}/access-requests/mine", response_model=List[AccessRequestItem])
def list_my_access_requests(
    current_user: User = Depends(require_role("patient")),
    db: Session = Depends(get_db),
):
    """迭代 5：患者侧所有与自己相关的申请（含已批准/已撤销，便于撤销管理）。"""
    requests = (
        db.query(AccessRequest)
        .filter(AccessRequest.patient_id == current_user.id)
        .order_by(AccessRequest.created_at.desc())
        .all()
    )
    user_ids = [x.applicant_hospital_id for x in requests] + [x.patient_id for x in requests]
    record_ids = [x.record_id for x in requests]
    users = _user_map(db, user_ids)
    records = {
        x.id: x
        for x in db.query(MedicalRecord).filter(MedicalRecord.id.in_(record_ids)).all()
    }
    return [_request_to_item(req, users, records) for req in requests]


@app.get(f"{settings.API_PREFIX}/access-requests/pending", response_model=List[AccessRequestItem])
def list_pending_requests(
    current_user: User = Depends(require_role("patient")),
    db: Session = Depends(get_db),
):
    requests = (
        db.query(AccessRequest)
        .filter(
            AccessRequest.patient_id == current_user.id,
            AccessRequest.status == "PENDING",
        )
        .order_by(AccessRequest.created_at.desc())
        .all()
    )
    user_ids = [x.applicant_hospital_id for x in requests] + [x.patient_id for x in requests]
    record_ids = [x.record_id for x in requests]
    users = _user_map(db, user_ids)
    records = {
        x.id: x
        for x in db.query(MedicalRecord).filter(MedicalRecord.id.in_(record_ids)).all()
    }
    return [_request_to_item(req, users, records) for req in requests]


@app.post(f"{settings.API_PREFIX}/access-requests/{{request_id}}/review", response_model=AccessRequestItem)
def review_access_request(
    request_id: int,
    payload: AccessRequestReview,
    current_user: User = Depends(require_role("patient")),
    db: Session = Depends(get_db),
):
    req = (
        db.query(AccessRequest)
        .filter(AccessRequest.id == request_id, AccessRequest.patient_id == current_user.id)
        .first()
    )
    if not req:
        raise HTTPException(status_code=404, detail="访问申请不存在")
    if req.status != "PENDING":
        raise HTTPException(status_code=400, detail="该申请已处理")

    decision = payload.decision.upper().strip()
    if decision not in {"APPROVED", "REJECTED"}:
        raise HTTPException(status_code=400, detail="decision 仅支持 APPROVED 或 REJECTED")

    record = db.query(MedicalRecord).filter(MedicalRecord.id == req.record_id).first()
    if not record:
        raise HTTPException(status_code=404, detail="关联病历不存在")
    uploader = db.query(User).filter(User.id == record.uploader_hospital_id).first()
    if not uploader:
        raise HTTPException(status_code=404, detail="上传医院不存在")
    applicant = db.query(User).filter(User.id == req.applicant_hospital_id).first()
    if not applicant:
        raise HTTPException(status_code=404, detail="申请医院不存在")

    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()

    if decision == "APPROVED":
        if not payload.duration_days or not payload.max_reads:
            raise HTTPException(
                status_code=400,
                detail="APPROVED 时必须指定 duration_days 与 max_reads",
            )

    try:
        _ensure_request_on_chain(req, record, applicant)
        if decision == "APPROVED":
            chain_result = approve_access_request(
                hospital_name=uploader.hospital_name or uploader.username,
                request_id=req.id,
                reviewed_at=now_iso,
                duration_days=payload.duration_days,
                max_reads=payload.max_reads,
            )
        else:
            chain_result = reject_access_request(
                hospital_name=uploader.hospital_name or uploader.username,
                request_id=req.id,
                reviewed_at=now_iso,
            )
        req.review_tx_id = chain_result.get("txId")
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    req.status = decision
    req.reviewed_at = now
    if decision == "APPROVED":
        req.max_reads = payload.max_reads
        req.remaining_reads = payload.max_reads
        req.expires_at = now + timedelta(days=int(payload.duration_days))
    db.commit()
    db.refresh(req)

    _safe_emit(
        AuditPayload(
            event_type="AccessApproved" if decision == "APPROVED" else "AccessRejected",
            actor_id=current_user.id,
            actor_role=current_user.role,
            subject_user_id=req.applicant_hospital_id,
            record_id=req.record_id,
            request_id=req.id,
            tx_id=req.review_tx_id,
            message=(
                f"您对病历 #{req.record_id} 的申请已被{'批准' if decision == 'APPROVED' else '拒绝'}"
                + (
                    f"（有效 {payload.duration_days} 天，{payload.max_reads} 次）"
                    if decision == "APPROVED"
                    else ""
                )
            ),
            payload={
                "duration_days": payload.duration_days,
                "max_reads": payload.max_reads,
            } if decision == "APPROVED" else {},
        )
    )

    users = _user_map(db, [req.applicant_hospital_id, req.patient_id])
    records = {record.id: record}
    return _request_to_item(req, users, records)


@app.get(f"{settings.API_PREFIX}/authorized-records", response_model=List[MedicalRecordItem])
def authorized_records(
    current_user: User = Depends(require_role("hospital")),
    db: Session = Depends(get_db),
):
    # 迭代 5：过滤过期/耗尽/已撤销的授权（链上真相由 AccessRecord 守卫保证）
    now = datetime.now(timezone.utc)
    candidates = (
        db.query(AccessRequest)
        .filter(
            AccessRequest.applicant_hospital_id == current_user.id,
            AccessRequest.status == "APPROVED",
        )
        .all()
    )
    approved_ids: List[int] = []
    for req in candidates:
        if _derive_status(req) != "APPROVED":
            continue
        approved_ids.append(req.record_id)

    records = (
        db.query(MedicalRecord)
        .filter(
            or_(
                MedicalRecord.uploader_hospital_id == current_user.id,
                MedicalRecord.id.in_(approved_ids or [-1]),
            )
        )
        .order_by(MedicalRecord.created_at.desc())
        .all()
    )

    ids = [x.patient_id for x in records] + [x.uploader_hospital_id for x in records]
    users = _user_map(db, ids)
    return [_record_to_item(record, users, True) for record in records]


@app.get(f"{settings.API_PREFIX}/audit", response_model=List[AuditEvent])
def audit_events(
    _current_user: User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
):
    events: List[AuditEvent] = []
    users = {u.id: u for u in db.query(User).all()}

    records = db.query(MedicalRecord).order_by(MedicalRecord.created_at.desc()).all()
    for record in records:
        if record.tx_id:
            operator = users.get(record.uploader_hospital_id)
            events.append(
                AuditEvent(
                    event_type="RECORD_UPLOAD",
                    business_id=record.id,
                    status="ON_CHAIN",
                    tx_id=record.tx_id,
                    operator=operator.username if operator else "unknown",
                    created_at=record.created_at,
                )
            )

    requests = db.query(AccessRequest).order_by(AccessRequest.created_at.desc()).all()
    for req in requests:
        operator = users.get(req.applicant_hospital_id)
        if req.create_tx_id:
            events.append(
                AuditEvent(
                    event_type="ACCESS_REQUEST_CREATED",
                    business_id=req.id,
                    status="ON_CHAIN",
                    tx_id=req.create_tx_id,
                    operator=operator.username if operator else "unknown",
                    created_at=req.created_at,
                )
            )
        if req.review_tx_id and req.reviewed_at:
            reviewer = users.get(req.patient_id)
            events.append(
                AuditEvent(
                    event_type="ACCESS_REQUEST_REVIEWED",
                    business_id=req.id,
                    status=req.status,
                    tx_id=req.review_tx_id,
                    operator=reviewer.username if reviewer else "unknown",
                    created_at=req.reviewed_at,
                )
            )

    events.sort(key=lambda x: x.created_at, reverse=True)
    return events


@app.get(f"{settings.API_PREFIX}/access-requests/{{request_id}}/chain")
def access_request_chain_status(
    request_id: int,
    current_user: User = Depends(get_current_user),
):
    if current_user.role not in {"admin", "hospital", "patient"}:
        raise HTTPException(status_code=403, detail="无权限访问该接口")
    try:
        return query_access_request(request_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc))


# ---------------- 迭代 2：病历版本链 ----------------


@app.post(
    f"{settings.API_PREFIX}/records/{{record_id}}/revise",
    response_model=MedicalRecordItem,
)
def revise_record(
    record_id: int,
    payload: MedicalRecordRevise,
    current_user: User = Depends(require_role("hospital")),
    db: Session = Depends(get_db),
):
    record = db.query(MedicalRecord).filter(MedicalRecord.id == record_id).first()
    if not record:
        raise HTTPException(status_code=404, detail="病历记录不存在")
    if record.uploader_hospital_id != current_user.id:
        raise HTTPException(status_code=403, detail="仅原上传医院可修订该病历")

    new_hash = hashlib.sha256(payload.content.encode("utf-8")).hexdigest()
    if new_hash == record.content_hash and (
        payload.diagnosis is None or payload.diagnosis == record.diagnosis
    ):
        raise HTTPException(status_code=400, detail="内容未发生变更")

    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    try:
        chain_result = revise_record_evidence(
            hospital_name=current_user.hospital_name or current_user.username,
            record_id=record.id,
            new_data_hash=new_hash,
            updated_at=now_iso,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    previous_tx = record.tx_id
    record.content = payload.content
    record.content_hash = new_hash
    if payload.diagnosis is not None:
        record.diagnosis = payload.diagnosis
    record.version = (record.version or 1) + 1
    record.previous_tx_id = previous_tx
    record.tx_id = chain_result.get("txId")
    record.updated_at = now

    db.commit()
    db.refresh(record)
    users = _user_map(db, [record.patient_id, record.uploader_hospital_id])

    _safe_emit(
        AuditPayload(
            event_type="RecordUpdated",
            actor_id=current_user.id,
            actor_role=current_user.role,
            subject_user_id=record.patient_id,
            record_id=record.id,
            tx_id=record.tx_id,
            message=f"{current_user.hospital_name or current_user.username} 修订了您的病历 #{record.id}（v{record.version}）",
            payload={"version": record.version, "previous_tx_id": record.previous_tx_id},
        )
    )
    return _record_to_item(record, users, True)


def _authorize_record_view(
    current_user: User, record: MedicalRecord, db: Session
) -> bool:
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


@app.get(
    f"{settings.API_PREFIX}/records/{{record_id}}/history",
    response_model=RecordHistory,
)
def record_history(
    record_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """迭代 3：基于 Fabric GetHistoryForKey 构建病历版本链（保留原 schema）。"""
    record = db.query(MedicalRecord).filter(MedicalRecord.id == record_id).first()
    if not record:
        raise HTTPException(status_code=404, detail="病历记录不存在")

    if not _authorize_record_view(current_user, record, db):
        raise HTTPException(status_code=403, detail="无权限查看该病历历史")

    try:
        chain_payload = query_record_history(record_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    raw_entries = chain_payload.get("result") if isinstance(chain_payload, dict) else None
    if not isinstance(raw_entries, list):
        raw_entries = []

    # 链码返回倒序（最近在前）；版本链按正序更直观
    ordered = sorted(
        raw_entries,
        key=lambda e: (e.get("value") or {}).get("version") or 0,
    )

    versions: List[RecordVersionItem] = []
    for entry in ordered:
        v = entry.get("value") or {}
        if not v:
            continue
        versions.append(
            RecordVersionItem(
                version=int(v.get("version", 0) or 0),
                data_hash=v.get("dataHash", "") or "",
                tx_id=v.get("txId") or entry.get("txId", ""),
                previous_tx_id=v.get("previousTxId", "") or "",
                created_at=v.get("createdAt"),
                updated_at=v.get("updatedAt") or entry.get("timestamp"),
            )
        )
    latest_version = versions[-1].version if versions else (record.version or 1)
    return RecordHistory(
        record_id=record_id, latest_version=latest_version, versions=versions
    )


@app.get(
    f"{settings.API_PREFIX}/records/{{record_id}}/chain-history",
    response_model=RecordChainHistory,
)
def record_chain_history(
    record_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """迭代 3：原始链上时间线（倒序），附带网关缓存命中标记。"""
    record = db.query(MedicalRecord).filter(MedicalRecord.id == record_id).first()
    if not record:
        raise HTTPException(status_code=404, detail="病历记录不存在")
    if not _authorize_record_view(current_user, record, db):
        raise HTTPException(status_code=403, detail="无权限查看该病历历史")

    try:
        chain_payload = query_record_history(record_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    raw_entries = chain_payload.get("result") if isinstance(chain_payload, dict) else None
    if not isinstance(raw_entries, list):
        raw_entries = []

    entries: List[ChainHistoryEntry] = [
        ChainHistoryEntry(
            tx_id=e.get("txId", ""),
            timestamp=e.get("timestamp"),
            is_delete=bool(e.get("isDelete", False)),
            value=e.get("value") if isinstance(e.get("value"), dict) else None,
        )
        for e in raw_entries
    ]
    cache_flag = str(chain_payload.get("cache", "miss")) if isinstance(chain_payload, dict) else "miss"
    return RecordChainHistory(record_id=record_id, cache=cache_flag, entries=entries)


@app.get(
    f"{settings.API_PREFIX}/access-requests/{{request_id}}/history",
    response_model=AccessRequestChainHistory,
)
def access_request_history(
    request_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """迭代 3：审批流完整状态轨迹（GetAccessRequestHistory）。"""
    req = db.query(AccessRequest).filter(AccessRequest.id == request_id).first()
    if not req:
        raise HTTPException(status_code=404, detail="访问申请不存在")

    authorized = False
    if current_user.role == "admin":
        authorized = True
    elif current_user.role == "patient" and req.patient_id == current_user.id:
        authorized = True
    elif current_user.role == "hospital" and (
        req.applicant_hospital_id == current_user.id
    ):
        authorized = True
    if not authorized:
        raise HTTPException(status_code=403, detail="无权限查看该申请历史")

    try:
        chain_payload = query_access_request_history(request_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    raw_entries = chain_payload.get("result") if isinstance(chain_payload, dict) else None
    if not isinstance(raw_entries, list):
        raw_entries = []

    entries: List[ChainHistoryEntry] = [
        ChainHistoryEntry(
            tx_id=e.get("txId", ""),
            timestamp=e.get("timestamp"),
            is_delete=bool(e.get("isDelete", False)),
            value=e.get("value") if isinstance(e.get("value"), dict) else None,
        )
        for e in raw_entries
    ]
    cache_flag = str(chain_payload.get("cache", "miss")) if isinstance(chain_payload, dict) else "miss"
    return AccessRequestChainHistory(
        request_id=request_id, cache=cache_flag, entries=entries
    )


# ---------------- 迭代 5：链上授权撤销（仅归属患者） ----------------

@app.post(
    f"{settings.API_PREFIX}/access-requests/{{request_id}}/revoke",
    response_model=AccessRequestItem,
)
def revoke_access_request_api(
    request_id: int,
    current_user: User = Depends(require_role("patient")),
    db: Session = Depends(get_db),
):
    req = (
        db.query(AccessRequest)
        .filter(
            AccessRequest.id == request_id,
            AccessRequest.patient_id == current_user.id,
        )
        .first()
    )
    if not req:
        raise HTTPException(status_code=404, detail="访问申请不存在或非本人")
    if req.status != "APPROVED":
        raise HTTPException(
            status_code=400,
            detail=f"仅 APPROVED 可撤销，当前状态 {req.status}",
        )

    applicant = db.query(User).filter(User.id == req.applicant_hospital_id).first()
    if not applicant:
        raise HTTPException(status_code=404, detail="申请医院不存在")

    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    try:
        chain_result = revoke_access_request(
            org_hint=applicant.hospital_name or applicant.username,
            request_id=req.id,
            patient_id=current_user.id,
            revoked_at=now_iso,
        )
        req.revoke_tx_id = chain_result.get("txId")
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    req.status = "REVOKED"
    req.revoked_at = now
    req.remaining_reads = 0  # 安全兜底
    db.commit()
    db.refresh(req)

    users = _user_map(db, [req.applicant_hospital_id, req.patient_id])
    record = db.query(MedicalRecord).filter(MedicalRecord.id == req.record_id).first()
    records = {record.id: record} if record else {}

    _safe_emit(
        AuditPayload(
            event_type="AccessRevoked",
            actor_id=current_user.id,
            actor_role=current_user.role,
            subject_user_id=req.applicant_hospital_id,  # 通知申请医院
            record_id=req.record_id,
            request_id=req.id,
            tx_id=req.revoke_tx_id,
            message=f"患者撤销了对病历 #{req.record_id} 的授权",
        )
    )
    return _request_to_item(req, users, records)


# ---------------- 迭代 6：WebSocket 通知 + 审计事件查询 ----------------


def _authenticate_ws(token: str, db: Session) -> Optional[User]:
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[ALGORITHM])
        user_id = int(payload.get("sub"))
    except (JWTError, ValueError, TypeError):
        return None
    user = db.query(User).filter(User.id == user_id).first()
    if not user or not user.is_active:
        return None
    return user


@app.websocket("/ws/notifications")
async def ws_notifications(
    websocket: WebSocket,
    token: str = Query(...),
    db: Session = Depends(get_db),
):
    try:
        user = _authenticate_ws(token, db)
    finally:
        db.close()
    if not user:
        await websocket.close(code=4401)
        return

    await websocket.accept()
    queue = await bus.subscribe(user.id, is_admin=(user.role == "admin"))
    await websocket.send_json(
        {
            "event_type": "_connected",
            "message": f"已订阅：{user.username}",
            "timestamp_ms": int(datetime.now(timezone.utc).timestamp() * 1000),
        }
    )

    async def reader():
        # 客户端可能发心跳；我们只读取以检测断开
        try:
            while True:
                await websocket.receive_text()
        except WebSocketDisconnect:
            raise
        except Exception:
            raise

    async def writer():
        while True:
            msg = await queue.get()
            await websocket.send_json(msg)

    try:
        reader_task = asyncio.create_task(reader())
        writer_task = asyncio.create_task(writer())
        done, pending = await asyncio.wait(
            {reader_task, writer_task}, return_when=asyncio.FIRST_COMPLETED
        )
        for task in pending:
            task.cancel()
    except WebSocketDisconnect:
        pass
    finally:
        await bus.unsubscribe(user.id, queue)


# ---------------- 迭代 7：CouchDB 富查询 ----------------

def _record_brief(raw: dict) -> ChainRecordBrief:
    return ChainRecordBrief(
        record_id=str(raw.get("recordId", "")),
        patient_id=str(raw.get("patientId", "")),
        uploader_hospital=raw.get("uploaderHospital", ""),
        data_hash=raw.get("dataHash", ""),
        version=int(raw.get("version", 1) or 1),
        tx_id=raw.get("txId", ""),
        created_at=raw.get("createdAt"),
        updated_at=raw.get("updatedAt"),
    )


def _pending_brief(raw: dict) -> ChainPendingRequestBrief:
    return ChainPendingRequestBrief(
        request_id=str(raw.get("requestId", "")),
        record_id=str(raw.get("recordId", "")),
        patient_id=str(raw.get("patientId", "")),
        applicant_hospital=raw.get("applicantHospital", ""),
        applicant_msp=raw.get("applicantMsp"),
        status=raw.get("status", ""),
        created_at=raw.get("createdAt"),
    )


def _chain_page_records(payload: dict) -> ChainRecordPage:
    result = payload.get("result", {}) if isinstance(payload, dict) else {}
    records = [
        _record_brief(r) for r in (result.get("records") or []) if isinstance(r, dict)
    ]
    return ChainRecordPage(
        records=records,
        bookmark=result.get("bookmark", "") or "",
        fetched_count=int(result.get("fetchedCount", len(records)) or len(records)),
        cache=str(payload.get("cache", "miss") if isinstance(payload, dict) else "miss"),
    )


def _chain_page_requests(payload: dict) -> ChainPendingRequestPage:
    result = payload.get("result", {}) if isinstance(payload, dict) else {}
    reqs = [
        _pending_brief(r) for r in (result.get("records") or []) if isinstance(r, dict)
    ]
    return ChainPendingRequestPage(
        requests=reqs,
        bookmark=result.get("bookmark", "") or "",
        fetched_count=int(result.get("fetchedCount", len(reqs)) or len(reqs)),
        cache=str(payload.get("cache", "miss") if isinstance(payload, dict) else "miss"),
    )


@app.get(
    f"{settings.API_PREFIX}/records/chain/by-hospital",
    response_model=ChainRecordPage,
)
def chain_records_by_hospital(
    hospital: Optional[str] = None,
    page_size: int = Query(20, ge=1, le=200),
    bookmark: str = "",
    current_user: User = Depends(get_current_user),
):
    """迭代 7：通过链上 CouchDB 富查询列出某医院上传的最新版病历。
    - hospital：医院人员默认查自己；admin 必须传入 hospital 参数
    - patient：仅 admin 可访问（患者不需要按医院查）
    """
    target = hospital
    if current_user.role == "hospital" and not target:
        target = current_user.hospital_name
    if current_user.role not in {"admin", "hospital"}:
        raise HTTPException(status_code=403, detail="仅 admin 与 hospital 可调用")
    if not target:
        raise HTTPException(status_code=400, detail="缺少 hospital 参数")

    try:
        payload = query_records_by_hospital(
            uploader_hospital=target, page_size=page_size, bookmark=bookmark
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    return _chain_page_records(payload)


@app.get(
    f"{settings.API_PREFIX}/records/chain/by-date",
    response_model=ChainRecordPage,
)
def chain_records_by_date(
    date_from: str = Query(..., alias="from"),
    date_to: str = Query(..., alias="to"),
    page_size: int = Query(20, ge=1, le=200),
    bookmark: str = "",
    current_user: User = Depends(require_role("admin")),
):
    """迭代 7：按 createdAt 闭区间查询链上所有最新版病历（admin 专属：审计/统计）。"""
    try:
        payload = query_records_by_date(
            date_from=date_from, date_to=date_to, page_size=page_size, bookmark=bookmark
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    return _chain_page_records(payload)


@app.get(
    f"{settings.API_PREFIX}/access-requests/chain/pending",
    response_model=ChainPendingRequestPage,
)
def chain_pending_requests_for_me(
    page_size: int = Query(20, ge=1, le=200),
    bookmark: str = "",
    current_user: User = Depends(require_role("patient")),
):
    """迭代 7：患者端从链上直接查询自己名下所有 PENDING 申请（摆脱 MySQL 镜像）。"""
    try:
        payload = query_pending_requests_for_patient(
            patient_id=current_user.id, page_size=page_size, bookmark=bookmark
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    return _chain_page_requests(payload)


@app.get(f"{settings.API_PREFIX}/audit/events", response_model=List[Dict])
def list_audit_events(
    event_type: Optional[str] = None,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """迭代 6：持久化审计事件流。
    - admin：可查全部
    - hospital / patient：仅可查与自己相关（actor_id == me 或 subject_user_id == me）
    """
    q = db.query(AuditEventRow)
    if current_user.role != "admin":
        q = q.filter(
            or_(
                AuditEventRow.actor_id == current_user.id,
                AuditEventRow.subject_user_id == current_user.id,
            )
        )
    if event_type:
        q = q.filter(AuditEventRow.event_type == event_type)
    rows = q.order_by(AuditEventRow.created_at.desc()).offset(offset).limit(limit).all()
    out: List[Dict] = []
    for row in rows:
        try:
            payload = json.loads(row.payload_json) if row.payload_json else {}
        except Exception:
            payload = {}
        out.append(
            {
                "id": row.id,
                "event_type": row.event_type,
                "actor_id": row.actor_id,
                "actor_role": row.actor_role,
                "subject_user_id": row.subject_user_id,
                "record_id": row.record_id,
                "request_id": row.request_id,
                "tx_id": row.tx_id,
                "message": row.message,
                "payload": payload,
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
        )
    return out


# ---------------- 迭代 9：Merkle 批量锚定 + 链上包含证明 ----------------


def _record_leaf_hash(record: MedicalRecord) -> str:
    return merkle_util.leaf_hash(record.id, record.content_hash)


def _batch_to_info(batch: MerkleAnchorBatch) -> AnchorBatchInfo:
    return AnchorBatchInfo(
        batch_id=batch.batch_id,
        merkle_root=batch.merkle_root,
        leaf_count=batch.leaf_count,
        record_id_low=batch.record_id_low,
        record_id_high=batch.record_id_high,
        tx_id=batch.tx_id,
        created_at=batch.created_at,
    )


@app.post(f"{settings.API_PREFIX}/anchor/run", response_model=AnchorRunResult)
def run_anchor(
    _current_user: User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
):
    """迭代 9：把所有"未锚定"的最新版病历聚合成一个 Merkle 批次上链。
    幂等：无新增病历时 anchored=0，不上链。"""
    pending = (
        db.query(MedicalRecord)
        .filter(MedicalRecord.anchor_batch_id.is_(None))
        .order_by(MedicalRecord.id.asc())
        .all()
    )
    if not pending:
        return AnchorRunResult(anchored=0, batch=None, detail="无未锚定病历")

    leaves = [_record_leaf_hash(r) for r in pending]
    root = merkle_util.compute_merkle_root(leaves)
    now = datetime.now(timezone.utc)
    batch_id = f"B-{now.strftime('%Y%m%d%H%M%S')}-{pending[-1].id}"

    try:
        chain_result = anchor_record_batch(
            batch_id=batch_id,
            merkle_root=root,
            leaf_count=len(leaves),
            created_at=now.isoformat(),
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    batch = MerkleAnchorBatch(
        batch_id=batch_id,
        merkle_root=root,
        leaf_count=len(leaves),
        record_id_low=pending[0].id,
        record_id_high=pending[-1].id,
        tx_id=chain_result.get("txId"),
    )
    db.add(batch)
    db.flush()

    for idx, record in enumerate(pending):
        record.anchor_batch_id = batch_id
        record.anchor_leaf_index = idx
    db.commit()
    db.refresh(batch)

    _safe_emit(
        AuditPayload(
            event_type="BatchAnchored",
            actor_id=_current_user.id,
            actor_role=_current_user.role,
            tx_id=batch.tx_id,
            message=f"批量锚定 {len(leaves)} 条病历到链上（batch={batch_id}）",
            payload={"batch_id": batch_id, "leaf_count": len(leaves)},
        )
    )
    return AnchorRunResult(
        anchored=len(leaves),
        batch=_batch_to_info(batch),
        detail="ok",
    )


@app.get(
    f"{settings.API_PREFIX}/records/{{record_id}}/proof",
    response_model=RecordInclusionProof,
)
def record_inclusion_proof(
    record_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """迭代 9：返回某条病历在其批次中的 Merkle 包含证明 + 链上 TxID。
    任何"有权查看该病历"的角色都能拿到证明（admin / 归属患者 / 上传医院 / 被授权医院）。"""
    record = db.query(MedicalRecord).filter(MedicalRecord.id == record_id).first()
    if not record:
        raise HTTPException(status_code=404, detail="病历记录不存在")
    if not _authorize_record_view(current_user, record, db):
        raise HTTPException(status_code=403, detail="无权限查看该病历")
    if not record.anchor_batch_id:
        raise HTTPException(status_code=409, detail="该病历尚未被批量锚定")

    batch = (
        db.query(MerkleAnchorBatch)
        .filter(MerkleAnchorBatch.batch_id == record.anchor_batch_id)
        .first()
    )
    if not batch:
        raise HTTPException(status_code=500, detail="锚定批次元数据缺失")

    # 重建批次叶子集合（按 anchor_leaf_index 顺序）
    leaves_in_batch = (
        db.query(MedicalRecord)
        .filter(MedicalRecord.anchor_batch_id == record.anchor_batch_id)
        .order_by(MedicalRecord.anchor_leaf_index.asc())
        .all()
    )
    leaf_hashes = [_record_leaf_hash(r) for r in leaves_in_batch]
    if record.anchor_leaf_index is None:
        raise HTTPException(status_code=500, detail="anchor_leaf_index 缺失（数据损坏）")
    idx = record.anchor_leaf_index
    proof_steps = merkle_util.compute_proof(leaf_hashes, idx)

    leaf_hex = leaf_hashes[idx]
    return RecordInclusionProof(
        record_id=record.id,
        leaf_hash=leaf_hex,
        batch=_batch_to_info(batch),
        proof=[MerkleProofStep(**step) for step in proof_steps],
    )


@app.post(
    f"{settings.API_PREFIX}/anchor/verify",
    response_model=InclusionVerifyResponse,
)
def verify_inclusion(
    payload: InclusionVerifyRequest,
    _current_user: User = Depends(get_current_user),
):
    """迭代 9：链上验证一份 Merkle 包含证明（任何登录用户都可用）。"""
    proof_list = [step.model_dump() for step in payload.proof]
    try:
        chain_result = verify_record_inclusion(
            batch_id=payload.batch_id,
            leaf_hash=payload.leaf_hash,
            proof=proof_list,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    result = chain_result.get("result", {}) if isinstance(chain_result, dict) else {}
    return InclusionVerifyResponse(
        ok=bool(result.get("ok")),
        recomputed_root=result.get("recomputedRoot", ""),
        anchored_root=result.get("anchoredRoot", ""),
        batch_id=result.get("batchId", payload.batch_id),
        leaf_count=int(result.get("leafCount", 0) or 0),
        tx_id=result.get("txId"),
    )


@app.get(f"{settings.API_PREFIX}/anchor/batches", response_model=List[AnchorBatchInfo])
def list_anchor_batches_api(
    _current_user: User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
):
    """迭代 9：管理员列出所有锚定批次（DB 镜像，避免每次都查链）。"""
    batches = (
        db.query(MerkleAnchorBatch)
        .order_by(MerkleAnchorBatch.created_at.desc())
        .all()
    )
    return [_batch_to_info(b) for b in batches]


# ---------------- 迭代 10：链上多签治理（双 MSP endorse） ----------------


def _gov_action_to_info(row: GovernanceAction) -> GovernanceActionInfo:
    try:
        payload = json.loads(row.payload_json) if row.payload_json else {}
    except Exception:
        payload = {}
    try:
        approvers_raw = json.loads(row.approvers_json) if row.approvers_json else []
    except Exception:
        approvers_raw = []
    approvers = [
        GovernanceApprover(
            msp=a.get("msp", ""),
            approved_at=a.get("approvedAt"),
            tx_id=a.get("txId"),
        )
        for a in approvers_raw
        if isinstance(a, dict)
    ]
    return GovernanceActionInfo(
        action_id=row.action_id,
        kind=row.kind,
        payload=payload,
        proposer_id=row.proposer_id,
        proposer_msp=row.proposer_msp,
        status=row.status,
        approvers=approvers,
        propose_tx_id=row.propose_tx_id,
        execute_tx_id=row.execute_tx_id,
        reject_tx_id=row.reject_tx_id,
        proposed_at=row.proposed_at,
        executed_at=row.executed_at,
        rejected_at=row.rejected_at,
    )


def _sync_governance_from_chain_result(
    db: Session, row: GovernanceAction, chain_result: dict
) -> None:
    """从链码返回的最新动作 JSON 同步到 DB 镜像。"""
    result = chain_result.get("result") if isinstance(chain_result, dict) else None
    if not isinstance(result, dict):
        return
    row.status = result.get("status", row.status)
    approvers = result.get("approvers") or []
    row.approvers_json = json.dumps(approvers, ensure_ascii=False)
    if result.get("executeTxId"):
        row.execute_tx_id = result.get("executeTxId")
    if result.get("rejectTxId"):
        row.reject_tx_id = result.get("rejectTxId")
    if result.get("executedAt"):
        try:
            row.executed_at = datetime.fromisoformat(
                result["executedAt"].replace("Z", "+00:00")
            )
        except Exception:
            pass
    if result.get("rejectedAt"):
        try:
            row.rejected_at = datetime.fromisoformat(
                result["rejectedAt"].replace("Z", "+00:00")
            )
        except Exception:
            pass
    db.commit()
    db.refresh(row)


def _proposer_org_to_gateway(user: User) -> str:
    """根据用户的 msp_org 映射到 gateway 的 org 参数（org1 / org2）。
    没有 msp_org 时（如 admin）默认走 org1。"""
    msp = (user.msp_org or "").lower()
    return "org2" if "org2" in msp else "org1"


@app.post(
    f"{settings.API_PREFIX}/governance/actions",
    response_model=GovernanceActionInfo,
)
def propose_governance(
    payload: GovernanceProposeRequest,
    current_user: User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
):
    """迭代 10：admin 发起治理提案（链上 PROPOSED）。"""
    existing = (
        db.query(GovernanceAction)
        .filter(GovernanceAction.action_id == payload.action_id)
        .first()
    )
    if existing:
        raise HTTPException(status_code=409, detail="action_id 已存在")

    org = _proposer_org_to_gateway(current_user)
    now = datetime.now(timezone.utc)
    try:
        chain_result = propose_governance_action(
            action_id=payload.action_id,
            kind=payload.kind,
            payload=payload.payload,
            proposed_at=now.isoformat(),
            org=org,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    row = GovernanceAction(
        action_id=payload.action_id,
        kind=payload.kind,
        payload_json=json.dumps(payload.payload, ensure_ascii=False),
        proposer_id=current_user.id,
        proposer_msp=current_user.msp_org,
        status="PROPOSED",
        approvers_json="[]",
        propose_tx_id=chain_result.get("txId"),
        proposed_at=now.replace(tzinfo=None),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    _sync_governance_from_chain_result(db, row, chain_result)

    _safe_emit(
        AuditPayload(
            event_type="GovernanceProposed",
            actor_id=current_user.id,
            actor_role=current_user.role,
            tx_id=row.propose_tx_id,
            message=f"治理提案：{payload.kind} #{payload.action_id}",
            payload={"action_id": payload.action_id, "kind": payload.kind},
        )
    )
    return _gov_action_to_info(row)


def _load_governance(db: Session, action_id: str) -> GovernanceAction:
    row = (
        db.query(GovernanceAction)
        .filter(GovernanceAction.action_id == action_id)
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="治理动作不存在")
    return row


@app.post(
    f"{settings.API_PREFIX}/governance/actions/{{action_id}}/approve",
    response_model=GovernanceActionInfo,
)
def approve_governance(
    action_id: str,
    current_user: User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
):
    """迭代 10：admin 批准治理动作。链码守卫双 MSP 不重复 / 状态合法。"""
    row = _load_governance(db, action_id)
    org = _proposer_org_to_gateway(current_user)
    try:
        chain_result = approve_governance_action(
            action_id=action_id,
            approved_at=datetime.now(timezone.utc).isoformat(),
            org=org,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    _sync_governance_from_chain_result(db, row, chain_result)

    _safe_emit(
        AuditPayload(
            event_type="GovernanceApproved",
            actor_id=current_user.id,
            actor_role=current_user.role,
            tx_id=chain_result.get("txId"),
            message=f"治理批准：#{action_id}（当前状态 {row.status}）",
            payload={"action_id": action_id, "status": row.status},
        )
    )
    return _gov_action_to_info(row)


@app.post(
    f"{settings.API_PREFIX}/governance/actions/{{action_id}}/reject",
    response_model=GovernanceActionInfo,
)
def reject_governance(
    action_id: str,
    current_user: User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
):
    row = _load_governance(db, action_id)
    org = _proposer_org_to_gateway(current_user)
    try:
        chain_result = reject_governance_action(
            action_id=action_id,
            rejected_at=datetime.now(timezone.utc).isoformat(),
            org=org,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    _sync_governance_from_chain_result(db, row, chain_result)
    _safe_emit(
        AuditPayload(
            event_type="GovernanceRejected",
            actor_id=current_user.id,
            actor_role=current_user.role,
            tx_id=chain_result.get("txId"),
            message=f"治理拒绝：#{action_id}",
        )
    )
    return _gov_action_to_info(row)


@app.post(
    f"{settings.API_PREFIX}/governance/actions/{{action_id}}/execute",
    response_model=GovernanceActionInfo,
)
def execute_governance(
    action_id: str,
    current_user: User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
):
    row = _load_governance(db, action_id)
    org = _proposer_org_to_gateway(current_user)
    try:
        chain_result = execute_governance_action(
            action_id=action_id,
            executed_at=datetime.now(timezone.utc).isoformat(),
            org=org,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    _sync_governance_from_chain_result(db, row, chain_result)
    _safe_emit(
        AuditPayload(
            event_type="GovernanceExecuted",
            actor_id=current_user.id,
            actor_role=current_user.role,
            tx_id=row.execute_tx_id,
            message=f"治理执行：#{action_id}（kind={row.kind}）",
            payload={"action_id": action_id, "kind": row.kind},
        )
    )
    return _gov_action_to_info(row)


@app.get(
    f"{settings.API_PREFIX}/governance/actions",
    response_model=List[GovernanceActionInfo],
)
def list_governance(
    status: Optional[str] = None,
    _current_user: User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
):
    q = db.query(GovernanceAction)
    if status:
        q = q.filter(GovernanceAction.status == status)
    rows = q.order_by(GovernanceAction.proposed_at.desc()).all()
    return [_gov_action_to_info(r) for r in rows]


@app.get(
    f"{settings.API_PREFIX}/governance/actions/{{action_id}}",
    response_model=GovernanceActionInfo,
)
def get_governance(
    action_id: str,
    _current_user: User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
):
    row = _load_governance(db, action_id)
    return _gov_action_to_info(row)


# ---------------- 迭代 11：链上紧急冻结 + 治理解冻闭环 ----------------


@app.post(
    f"{settings.API_PREFIX}/records/{{record_id}}/freeze",
    response_model=MedicalRecordItem,
)
def freeze_record_api(
    record_id: int,
    current_user: User = Depends(require_role("patient")),
    db: Session = Depends(get_db),
):
    """迭代 11：患者紧急冻结自己病历。链码层强校验 patientId。"""
    record = db.query(MedicalRecord).filter(MedicalRecord.id == record_id).first()
    if not record:
        raise HTTPException(status_code=404, detail="病历记录不存在")
    if record.patient_id != current_user.id:
        raise HTTPException(status_code=403, detail="仅归属患者可冻结")
    if record.frozen:
        raise HTTPException(status_code=409, detail="病历已处于冻结状态")

    now = datetime.now(timezone.utc)
    reason_hash = hashlib.sha256(
        f"freeze-by-{current_user.id}-{now.isoformat()}".encode("utf-8")
    ).hexdigest()
    try:
        chain_result = freeze_record(
            record_id=record.id,
            patient_id=current_user.id,
            reason_hash=reason_hash,
            frozen_at=now.isoformat(),
            org="org1",
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    record.frozen = True
    record.frozen_at = now.replace(tzinfo=None)
    record.freeze_tx_id = chain_result.get("txId")
    db.commit()
    db.refresh(record)

    _safe_emit(
        AuditPayload(
            event_type="RecordFrozen",
            actor_id=current_user.id,
            actor_role=current_user.role,
            subject_user_id=record.uploader_hospital_id,
            record_id=record.id,
            tx_id=record.freeze_tx_id,
            message=f"患者紧急冻结了病历 #{record.id}",
        )
    )
    users = _user_map(db, [record.patient_id, record.uploader_hospital_id])
    return _record_to_item(record, users, True)


@app.post(
    f"{settings.API_PREFIX}/records/{{record_id}}/unfreeze",
    response_model=MedicalRecordItem,
)
def unfreeze_record_api(
    record_id: int,
    governance_action_id: str = Query(..., alias="governance_action_id"),
    current_user: User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
):
    """迭代 11：admin 用一个 EXECUTED 状态的治理动作解冻病历。
    链码层校验该动作 kind=UNFREEZE_RECORD 且 payload.recordId 一致。"""
    record = db.query(MedicalRecord).filter(MedicalRecord.id == record_id).first()
    if not record:
        raise HTTPException(status_code=404, detail="病历记录不存在")
    if not record.frozen:
        raise HTTPException(status_code=409, detail="病历未处于冻结状态")

    gov = (
        db.query(GovernanceAction)
        .filter(GovernanceAction.action_id == governance_action_id)
        .first()
    )
    if not gov:
        raise HTTPException(status_code=400, detail="治理动作不存在")
    if gov.status != "EXECUTED":
        raise HTTPException(
            status_code=400,
            detail=f"治理动作状态 {gov.status}，必须 EXECUTED 才能解冻",
        )
    if gov.kind != "UNFREEZE_RECORD":
        raise HTTPException(
            status_code=400,
            detail=f"治理动作 kind={gov.kind}，期望 UNFREEZE_RECORD",
        )

    now = datetime.now(timezone.utc)
    try:
        chain_result = unfreeze_record(
            record_id=record.id,
            governance_action_id=governance_action_id,
            unfrozen_at=now.isoformat(),
            org="org1",
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    record.frozen = False
    record.unfreeze_tx_id = chain_result.get("txId")
    record.unfreeze_gov_tx_id = gov.execute_tx_id
    db.commit()
    db.refresh(record)

    _safe_emit(
        AuditPayload(
            event_type="RecordUnfrozen",
            actor_id=current_user.id,
            actor_role=current_user.role,
            subject_user_id=record.patient_id,
            record_id=record.id,
            tx_id=record.unfreeze_tx_id,
            message=f"治理解冻：病历 #{record.id} 已解冻（gov={governance_action_id}）",
            payload={"governance_action_id": governance_action_id},
        )
    )
    users = _user_map(db, [record.patient_id, record.uploader_hospital_id])
    return _record_to_item(record, users, True)


# ---------------- 迭代 12：链码 v2 升级 + 状态迁移 ----------------


@app.get(f"{settings.API_PREFIX}/system/info", response_model=SystemInfo)
def system_info(
    _current_user: User = Depends(get_current_user),
):
    """迭代 12：返回链码 schema 版本号 + 可用字段枚举。"""
    try:
        chain_result = get_schema_version()
        result = chain_result.get("result")
        schema_version = str(result) if isinstance(result, str) else "v2"
    except RuntimeError:
        schema_version = "v2"
    return SystemInfo(
        schema_version=schema_version,
        contract_kinds={
            "record_categories": ["GENERAL", "INPATIENT", "OUTPATIENT", "EMERGENCY"],
            "request_purposes": ["TREATMENT", "RESEARCH", "AUDIT"],
        },
    )


@app.post(
    f"{settings.API_PREFIX}/admin/migrate/records-v2",
    response_model=MigrationResponse,
)
def migrate_records_v2_api(
    payload: MigrationRequest,
    _current_user: User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
):
    """迭代 12：把指定 records 的 category 一次性迁移到 v2。
    后端层先在 DB 写入（保持镜像同步），然后批量提交给链码。"""
    items = []
    db_targets = []
    for item in payload.items:
        record = (
            db.query(MedicalRecord)
            .filter(MedicalRecord.id == item.record_id)
            .first()
        )
        if not record:
            raise HTTPException(
                status_code=404, detail=f"记录 {item.record_id} 不存在"
            )
        db_targets.append((record, item.category))
        items.append({"recordId": str(item.record_id), "category": item.category})

    try:
        chain_result = migrate_records_v2(items=items, org="org1")
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    # 链上成功后同步 DB
    for record, category in db_targets:
        record.category = category
    db.commit()

    result = chain_result.get("result") if isinstance(chain_result, dict) else None
    if not isinstance(result, dict):
        result = {
            "migrated": [str(it["recordId"]) for it in items],
            "count": len(items),
        }
    return MigrationResponse(
        migrated=[str(x) for x in (result.get("migrated") or [])],
        count=int(result.get("count", 0) or 0),
    )


@app.get(
    f"{settings.API_PREFIX}/records/chain/by-category",
    response_model=ChainRecordPage,
)
def chain_records_by_category(
    category: str = Query(
        ..., pattern=r"^(GENERAL|INPATIENT|OUTPATIENT|EMERGENCY)$"
    ),
    page_size: int = Query(20, ge=1, le=200),
    bookmark: str = "",
    _current_user: User = Depends(get_current_user),
):
    """迭代 12：按 category 链上富查询最新版病历。"""
    try:
        payload = query_records_by_category(
            category=category, page_size=page_size, bookmark=bookmark
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    return _chain_page_records(payload)


# ---------------- 迭代 13：数据共享积分（FT） ----------------


def _user_credit_id(user: User) -> str:
    """链上 credit 账户 ID 约定：
    - 医院：使用 hospital_name（e.g. "HospitalA"），与链码 uploaderHospital 对齐
    - 患者 / admin：使用 str(user.id)，与链码 patientId 对齐
    """
    if user.role == "hospital" and user.hospital_name:
        return user.hospital_name
    return str(user.id)


def _credit_balance_from_chain(chain_result: dict, fallback_id: str) -> CreditBalanceSchema:
    result = chain_result.get("result") if isinstance(chain_result, dict) else None
    if isinstance(result, dict):
        return CreditBalanceSchema(
            user_id=str(result.get("userId", fallback_id)),
            balance=int(result.get("balance", 0) or 0),
        )
    return CreditBalanceSchema(user_id=fallback_id, balance=0)


@app.get(f"{settings.API_PREFIX}/credits/balance", response_model=CreditBalanceSchema)
def my_credit_balance(
    current_user: User = Depends(get_current_user),
):
    uid = _user_credit_id(current_user)
    try:
        chain_result = credit_balance(uid)
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    return _credit_balance_from_chain(chain_result, uid)


@app.post(f"{settings.API_PREFIX}/credits/transfer", response_model=CreditBalanceSchema)
def transfer_credits(
    payload: CreditTransferRequest,
    current_user: User = Depends(get_current_user),
):
    """迭代 13：任意角色互相转账。链码层校验余额 / 自转。"""
    from_id = _user_credit_id(current_user)
    if str(payload.to_user_id) == str(from_id):
        raise HTTPException(status_code=400, detail="不允许自转")
    now_iso = datetime.now(timezone.utc).isoformat()
    try:
        credit_transfer(
            from_user_id=from_id,
            to_user_id=str(payload.to_user_id),
            amount=int(payload.amount),
            reason_code=payload.reason_code or "TRANSFER",
            tx_at=now_iso,
        )
    except RuntimeError as exc:
        msg = str(exc)
        if "余额不足" in msg or "不允许自转" in msg:
            raise HTTPException(status_code=400, detail=msg)
        raise HTTPException(status_code=502, detail=msg)

    chain_result = credit_balance(from_id)
    return _credit_balance_from_chain(chain_result, from_id)


@app.get(f"{settings.API_PREFIX}/credits/history", response_model=CreditHistoryPage)
def my_credit_history(
    page_size: int = Query(20, ge=1, le=200),
    bookmark: str = "",
    current_user: User = Depends(get_current_user),
):
    uid = _user_credit_id(current_user)
    try:
        chain_result = credit_history(
            user_id=uid, page_size=page_size, bookmark=bookmark
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    result = chain_result.get("result", {}) if isinstance(chain_result, dict) else {}
    items_raw = result.get("records") or []
    items = []
    for r in items_raw:
        if not isinstance(r, dict):
            continue
        items.append(
            CreditLedgerItem(
                ledger_id=str(r.get("ledgerId", "")),
                from_user_id=str(r.get("fromUserId", "")),
                to_user_id=str(r.get("toUserId", "")),
                amount=int(r.get("amount", 0) or 0),
                reason_code=str(r.get("reasonCode", "")),
                tx_id=r.get("txId"),
                created_at=r.get("createdAt"),
            )
        )
    return CreditHistoryPage(
        items=items,
        bookmark=str(result.get("bookmark", "") or ""),
        fetched_count=int(result.get("fetchedCount", len(items)) or len(items)),
    )
