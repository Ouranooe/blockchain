import hashlib
from datetime import datetime, timezone
from typing import Dict, List

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import or_
from sqlalchemy.orm import Session

from .auth import create_access_token, get_current_user, require_role
from .config import settings
from .database import Base, engine, get_db
from .gateway import (
    approve_access_request,
    create_access_request,
    create_record_evidence,
    query_access_request,
    query_access_request_history,
    query_record_history,
    query_record_version,
    reject_access_request,
    revise_record_evidence,
)
from .models import AccessRequest, MedicalRecord, User
from .schemas import (
    AccessRequestChainHistory,
    AccessRequestCreate,
    AccessRequestItem,
    AccessRequestReview,
    AuditEvent,
    ChainHistoryEntry,
    ChangePasswordRequest,
    LoginRequest,
    LoginResponse,
    MedicalRecordCreate,
    MedicalRecordItem,
    MedicalRecordRevise,
    RecordChainHistory,
    RecordHistory,
    RecordVersionItem,
    RegisterRequest,
    SimpleMessage,
    UserInfo,
)
from .security import hash_password, is_hashed, verify_password

app = FastAPI(title=settings.APP_NAME)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def on_startup():
    Base.metadata.create_all(bind=engine)


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
    )


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
        status=req.status,
        create_tx_id=req.create_tx_id,
        review_tx_id=req.review_tx_id,
        created_at=req.created_at,
        reviewed_at=req.reviewed_at,
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


@app.post(f"{settings.API_PREFIX}/auth/login", response_model=LoginResponse)
def login(payload: LoginRequest, db: Session = Depends(get_db)):
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
def register(payload: RegisterRequest, db: Session = Depends(get_db)):
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
    record = MedicalRecord(
        patient_id=payload.patient_id,
        uploader_hospital_id=current_user.id,
        title=payload.title,
        diagnosis=payload.diagnosis,
        content=payload.content,
        content_hash=content_hash,
        version=1,
        previous_tx_id=None,
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
        )
        record.tx_id = chain_result.get("txId")
    except RuntimeError as exc:
        db.rollback()
        raise HTTPException(status_code=502, detail=str(exc))

    db.commit()
    db.refresh(record)
    users = _user_map(db, [record.patient_id, record.uploader_hospital_id])
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
    access_request = AccessRequest(
        record_id=payload.record_id,
        applicant_hospital_id=current_user.id,
        patient_id=record.patient_id,
        reason=payload.reason,
        reason_hash=reason_hash,
        status="PENDING",
    )
    db.add(access_request)
    db.flush()

    now_iso = datetime.now(timezone.utc).isoformat()
    try:
        chain_result = create_access_request(
            hospital_name=current_user.hospital_name or current_user.username,
            request_id=access_request.id,
            record_id=payload.record_id,
            reason_hash=reason_hash,
            created_at=now_iso,
        )
        access_request.create_tx_id = chain_result.get("txId")
    except RuntimeError as exc:
        db.rollback()
        raise HTTPException(status_code=502, detail=str(exc))

    db.commit()
    db.refresh(access_request)
    users = _user_map(db, [access_request.applicant_hospital_id, access_request.patient_id])
    records = {record.id: record}
    return _request_to_item(access_request, users, records)


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
    try:
        _ensure_request_on_chain(req, record, applicant)
        if decision == "APPROVED":
            chain_result = approve_access_request(
                hospital_name=uploader.hospital_name or uploader.username,
                request_id=req.id,
                reviewed_at=now_iso,
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
    db.commit()
    db.refresh(req)

    users = _user_map(db, [req.applicant_hospital_id, req.patient_id])
    records = {record.id: record}
    return _request_to_item(req, users, records)


@app.get(f"{settings.API_PREFIX}/authorized-records", response_model=List[MedicalRecordItem])
def authorized_records(
    current_user: User = Depends(require_role("hospital")),
    db: Session = Depends(get_db),
):
    approved_ids = [
        row.record_id
        for row in db.query(AccessRequest.record_id)
        .filter(
            AccessRequest.applicant_hospital_id == current_user.id,
            AccessRequest.status == "APPROVED",
        )
        .all()
    ]

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
