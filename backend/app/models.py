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
    # 迭代 2 新增：版本链，DB 只保留当前版本，历史版本从链上查
    version = Column(Integer, nullable=False, default=1)
    previous_tx_id = Column(String(128), nullable=True)
    updated_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, server_default=func.now())
    # 迭代 4 新增：链下加密文件元数据（链上仅存 content_hash）
    file_name = Column(String(255), nullable=True)
    file_mime = Column(String(128), nullable=True)
    file_size = Column(Integer, nullable=True)        # 明文字节数
    file_path = Column(String(512), nullable=True)    # 密文落盘位置（相对 STORAGE_DIR）
    file_nonce_b64 = Column(String(64), nullable=True)
    file_tag_b64 = Column(String(64), nullable=True)


class AuditEventRow(Base):
    """迭代 6：链码事件持久化表。每条对应一次"链上发生的事件"（或后端捕获到的异常尝试）。"""

    __tablename__ = "audit_events"

    id = Column(Integer, primary_key=True, index=True)
    event_type = Column(String(64), nullable=False, index=True)
    # 触发方（可为空，例如 UnauthorizedAttempt 可能来自未认证的请求）
    actor_id = Column(Integer, nullable=True, index=True)
    actor_role = Column(String(32), nullable=True)
    # 事件关注的"主体用户"——通知投递目标
    subject_user_id = Column(Integer, nullable=True, index=True)
    record_id = Column(Integer, nullable=True, index=True)
    request_id = Column(Integer, nullable=True, index=True)
    tx_id = Column(String(128), nullable=True)
    message = Column(String(512), nullable=True)
    payload_json = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, server_default=func.now(), index=True)


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
    # 迭代 5：ABAC 字段（真相在链上，这里缓存镜像便于查询过滤）
    expires_at = Column(DateTime, nullable=True)
    remaining_reads = Column(Integer, nullable=True)
    max_reads = Column(Integer, nullable=True)
    revoked_at = Column(DateTime, nullable=True)
    revoke_tx_id = Column(String(128), nullable=True)
