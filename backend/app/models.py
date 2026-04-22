from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Text, func

from .database import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(64), unique=True, index=True, nullable=False)
    password = Column(String(128), nullable=False)
    role = Column(String(32), nullable=False, index=True)
    real_name = Column(String(64), nullable=False)
    hospital_name = Column(String(64), nullable=True)
    # 迭代 1 新增：MSP 组织标识（Org1MSP/Org2MSP），用于链上身份映射
    msp_org = Column(String(32), nullable=True)
    # 迭代 1 新增：账号启用标记，支持管理员禁用
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, nullable=False, server_default=func.now())


class MedicalRecord(Base):
    __tablename__ = "medical_records"

    id = Column(Integer, primary_key=True, index=True)
    patient_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    uploader_hospital_id = Column(
        Integer, ForeignKey("users.id"), nullable=False, index=True
    )
    title = Column(String(255), nullable=False)
    diagnosis = Column(String(255), nullable=False)
    content = Column(Text, nullable=False)
    content_hash = Column(String(64), nullable=False, index=True)
    tx_id = Column(String(128), nullable=True)
    created_at = Column(DateTime, nullable=False, server_default=func.now())


class AccessRequest(Base):
    __tablename__ = "access_requests"

    id = Column(Integer, primary_key=True, index=True)
    record_id = Column(Integer, ForeignKey("medical_records.id"), nullable=False, index=True)
    applicant_hospital_id = Column(
        Integer, ForeignKey("users.id"), nullable=False, index=True
    )
    patient_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    reason = Column(Text, nullable=False)
    reason_hash = Column(String(64), nullable=False)
    status = Column(String(32), nullable=False, default="PENDING", index=True)
    create_tx_id = Column(String(128), nullable=True)
    review_tx_id = Column(String(128), nullable=True)
    created_at = Column(DateTime, nullable=False, server_default=func.now())
    reviewed_at = Column(DateTime, nullable=True)
