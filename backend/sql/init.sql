SET NAMES utf8mb4;
SET CHARACTER SET utf8mb4;

CREATE TABLE IF NOT EXISTS users (
  id INT PRIMARY KEY AUTO_INCREMENT,
  username VARCHAR(64) NOT NULL UNIQUE,
  password VARCHAR(128) NOT NULL,
  role VARCHAR(32) NOT NULL,
  real_name VARCHAR(64) NOT NULL,
  hospital_name VARCHAR(64) NULL,
  msp_org VARCHAR(32) NULL,
  is_active TINYINT(1) NOT NULL DEFAULT 1,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS medical_records (
  id INT PRIMARY KEY AUTO_INCREMENT,
  patient_id INT NOT NULL,
  uploader_hospital_id INT NOT NULL,
  title VARCHAR(255) NOT NULL,
  diagnosis VARCHAR(255) NOT NULL,
  content TEXT NOT NULL,
  content_hash VARCHAR(64) NOT NULL,
  tx_id VARCHAR(128) NULL,
  version INT NOT NULL DEFAULT 1,
  previous_tx_id VARCHAR(128) NULL,
  updated_at DATETIME NULL,
  file_name VARCHAR(255) NULL,
  file_mime VARCHAR(128) NULL,
  file_size INT NULL,
  file_path VARCHAR(512) NULL,
  file_nonce_b64 VARCHAR(64) NULL,
  file_tag_b64 VARCHAR(64) NULL,
  anchor_batch_id VARCHAR(64) NULL,
  anchor_leaf_index INT NULL,
  frozen TINYINT(1) NOT NULL DEFAULT 0,
  frozen_at DATETIME NULL,
  freeze_tx_id VARCHAR(128) NULL,
  unfreeze_tx_id VARCHAR(128) NULL,
  unfreeze_gov_tx_id VARCHAR(128) NULL,
  category VARCHAR(32) NOT NULL DEFAULT 'GENERAL',
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT fk_record_patient FOREIGN KEY (patient_id) REFERENCES users(id),
  CONSTRAINT fk_record_uploader FOREIGN KEY (uploader_hospital_id) REFERENCES users(id),
  INDEX idx_records_anchor_batch (anchor_batch_id),
  INDEX idx_records_frozen (frozen),
  INDEX idx_records_category (category)
);

CREATE TABLE IF NOT EXISTS access_requests (
  id INT PRIMARY KEY AUTO_INCREMENT,
  record_id INT NOT NULL,
  applicant_hospital_id INT NOT NULL,
  patient_id INT NOT NULL,
  reason TEXT NOT NULL,
  reason_hash VARCHAR(64) NOT NULL,
  status VARCHAR(32) NOT NULL DEFAULT 'PENDING',
  create_tx_id VARCHAR(128) NULL,
  review_tx_id VARCHAR(128) NULL,
  expires_at DATETIME NULL,
  remaining_reads INT NULL,
  max_reads INT NULL,
  revoked_at DATETIME NULL,
  revoke_tx_id VARCHAR(128) NULL,
  purpose VARCHAR(32) NOT NULL DEFAULT 'TREATMENT',
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  reviewed_at DATETIME NULL,
  CONSTRAINT fk_request_record FOREIGN KEY (record_id) REFERENCES medical_records(id),
  CONSTRAINT fk_request_hospital FOREIGN KEY (applicant_hospital_id) REFERENCES users(id),
  CONSTRAINT fk_request_patient FOREIGN KEY (patient_id) REFERENCES users(id),
  INDEX idx_requests_purpose (purpose)
);

CREATE TABLE IF NOT EXISTS audit_events (
  id INT PRIMARY KEY AUTO_INCREMENT,
  event_type VARCHAR(64) NOT NULL,
  actor_id INT NULL,
  actor_role VARCHAR(32) NULL,
  subject_user_id INT NULL,
  record_id INT NULL,
  request_id INT NULL,
  tx_id VARCHAR(128) NULL,
  message VARCHAR(512) NULL,
  payload_json TEXT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_audit_type (event_type),
  INDEX idx_audit_subject (subject_user_id),
  INDEX idx_audit_actor (actor_id),
  INDEX idx_audit_time (created_at)
);

CREATE TABLE IF NOT EXISTS governance_actions (
  id INT PRIMARY KEY AUTO_INCREMENT,
  action_id VARCHAR(64) NOT NULL UNIQUE,
  kind VARCHAR(32) NOT NULL,
  payload_json TEXT NOT NULL,
  proposer_id INT NULL,
  proposer_msp VARCHAR(32) NULL,
  status VARCHAR(32) NOT NULL DEFAULT 'PROPOSED',
  approvers_json TEXT NOT NULL,
  propose_tx_id VARCHAR(128) NULL,
  execute_tx_id VARCHAR(128) NULL,
  reject_tx_id VARCHAR(128) NULL,
  proposed_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  executed_at DATETIME NULL,
  rejected_at DATETIME NULL,
  INDEX idx_gov_kind (kind),
  INDEX idx_gov_proposer (proposer_id),
  INDEX idx_gov_status (status)
);

CREATE TABLE IF NOT EXISTS merkle_anchor_batches (
  id INT PRIMARY KEY AUTO_INCREMENT,
  batch_id VARCHAR(64) NOT NULL UNIQUE,
  merkle_root VARCHAR(64) NOT NULL,
  leaf_count INT NOT NULL,
  record_id_low INT NULL,
  record_id_high INT NULL,
  tx_id VARCHAR(128) NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Seed demo users only. Record and request data must be created through the
-- application so MySQL and Fabric stay in sync.
INSERT INTO users (id, username, password, role, real_name, hospital_name, msp_org, is_active) VALUES
  (1, 'admin',      '123456', 'admin',    'System Admin',     NULL,        NULL,      1),
  (2, 'patient1',   '123456', 'patient',  'Patient One',      NULL,        NULL,      1),
  (3, 'patient2',   '123456', 'patient',  'Patient Two',      NULL,        NULL,      1),
  (4, 'hospital_a', '123456', 'hospital', 'HospitalA Doctor', 'HospitalA', 'Org1MSP', 1),
  (5, 'hospital_b', '123456', 'hospital', 'HospitalB Doctor', 'HospitalB', 'Org2MSP', 1)
ON DUPLICATE KEY UPDATE username = VALUES(username);
