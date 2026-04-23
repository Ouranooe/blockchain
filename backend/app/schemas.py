from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class LoginRequest(BaseModel):
    username: str
    password: str


class UserInfo(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    role: str
    real_name: str
    hospital_name: Optional[str] = None
    msp_org: Optional[str] = None
    is_active: bool = True


class LoginResponse(BaseModel):
    token: str
    user: UserInfo


class RegisterRequest(BaseModel):
    username: str = Field(min_length=3, max_length=64, pattern=r"^[a-zA-Z0-9_]+$")
    password: str = Field(min_length=6, max_length=64)
    real_name: str = Field(min_length=1, max_length=64)
    role: str = Field(default="patient")  # 自助注册目前仅允许 patient


class ChangePasswordRequest(BaseModel):
    old_password: str = Field(min_length=1)
    new_password: str = Field(min_length=6, max_length=64)


class SimpleMessage(BaseModel):
    detail: str


class MedicalRecordCreate(BaseModel):
    patient_id: int
    title: str = Field(min_length=1, max_length=255)
    diagnosis: str = Field(min_length=1, max_length=255)
    content: str = Field(min_length=1)


class MedicalRecordRevise(BaseModel):
    """迭代 2：病历修订请求（仅原上传医院可调用）。"""

    diagnosis: Optional[str] = Field(default=None, max_length=255)
    content: str = Field(min_length=1)


class MedicalRecordItem(BaseModel):
    id: int
    patient_id: int
    patient_name: str
    uploader_hospital: str
    title: str
    diagnosis: str
    content_hash: str
    tx_id: Optional[str] = None
    version: int = 1
    previous_tx_id: Optional[str] = None
    updated_at: Optional[datetime] = None
    created_at: datetime
    can_view_content: bool = False
    content: Optional[str] = None
    # 迭代 4：文件元数据
    has_file: bool = False
    file_name: Optional[str] = None
    file_mime: Optional[str] = None
    file_size: Optional[int] = None


class FileVerifyResult(BaseModel):
    record_id: int
    chain_hash: str
    decrypted_hash: str
    hash_match: bool
    file_size: int


class RecordVersionItem(BaseModel):
    """迭代 2：单个历史版本（来源于链上）。"""

    version: int
    data_hash: str
    tx_id: str
    previous_tx_id: str = ""
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class RecordHistory(BaseModel):
    """迭代 2：病历完整版本链。"""

    record_id: int
    latest_version: int
    versions: list[RecordVersionItem]


class ChainHistoryEntry(BaseModel):
    """迭代 3：Fabric GetHistoryForKey 的单条历史（倒序，最近在前）。"""

    tx_id: str
    timestamp: Optional[str] = None
    is_delete: bool = False
    # 解析后的业务对象（病历或申请的某一版快照）
    value: Optional[dict] = None


class RecordChainHistory(BaseModel):
    """迭代 3：病历链上全量历史（源自 GetHistoryForKey）。"""

    record_id: int
    cache: str = "miss"  # hit / miss（取自网关）
    entries: list[ChainHistoryEntry]


class AccessRequestChainHistory(BaseModel):
    request_id: int
    cache: str = "miss"
    entries: list[ChainHistoryEntry]


class AccessRequestCreate(BaseModel):
    record_id: int
    reason: str = Field(min_length=1)


class AccessRequestReview(BaseModel):
    decision: str
    # 迭代 5：APPROVED 时必填；REJECTED 忽略
    duration_days: Optional[int] = Field(default=None, ge=1, le=365)
    max_reads: Optional[int] = Field(default=None, ge=1, le=1000)


class AccessRequestItem(BaseModel):
    id: int
    record_id: int
    record_title: str
    applicant_hospital: str
    patient_name: str
    reason: str
    status: str
    create_tx_id: Optional[str] = None
    review_tx_id: Optional[str] = None
    created_at: datetime
    reviewed_at: Optional[datetime] = None
    # 迭代 5：ABAC 字段
    expires_at: Optional[datetime] = None
    remaining_reads: Optional[int] = None
    max_reads: Optional[int] = None
    revoked_at: Optional[datetime] = None
    revoke_tx_id: Optional[str] = None


class AccessConsumeResult(BaseModel):
    request_id: int
    record_id: int
    remaining_reads: int
    reads_used: int
    tx_id: Optional[str] = None


# ---------- 迭代 7：CouchDB 富查询返回结构 ----------

class ChainRecordBrief(BaseModel):
    record_id: str
    patient_id: str
    uploader_hospital: str
    data_hash: str
    version: int
    tx_id: str
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class ChainRecordPage(BaseModel):
    records: list[ChainRecordBrief]
    bookmark: str = ""
    fetched_count: int = 0
    cache: str = "miss"


class ChainPendingRequestBrief(BaseModel):
    request_id: str
    record_id: str
    patient_id: str
    applicant_hospital: str
    applicant_msp: Optional[str] = None
    status: str
    created_at: Optional[str] = None


class ChainPendingRequestPage(BaseModel):
    requests: list[ChainPendingRequestBrief]
    bookmark: str = ""
    fetched_count: int = 0
    cache: str = "miss"


class AuditEvent(BaseModel):
    event_type: str
    business_id: int
    status: str
    tx_id: Optional[str] = None
    operator: str
    created_at: datetime
