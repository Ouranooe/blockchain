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


class LoginResponse(BaseModel):
    token: str
    user: UserInfo


class MedicalRecordCreate(BaseModel):
    patient_id: int
    title: str = Field(min_length=1, max_length=255)
    diagnosis: str = Field(min_length=1, max_length=255)
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
    created_at: datetime
    can_view_content: bool = False
    content: Optional[str] = None


class AccessRequestCreate(BaseModel):
    record_id: int
    reason: str = Field(min_length=1)


class AccessRequestReview(BaseModel):
    decision: str


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


class AuditEvent(BaseModel):
    event_type: str
    business_id: int
    status: str
    tx_id: Optional[str] = None
    operator: str
    created_at: datetime
