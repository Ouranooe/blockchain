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

-- 迭代 1 新增 users.msp_org / users.is_active 两列。本文件仅在首次建库执行。
-- 若升级旧库（已有数据），请手动执行：
--   ALTER TABLE users ADD COLUMN msp_org VARCHAR(32) NULL;
--   ALTER TABLE users ADD COLUMN is_active TINYINT(1) NOT NULL DEFAULT 1;
--   UPDATE users SET msp_org='Org1MSP' WHERE username='hospital_a';
--   UPDATE users SET msp_org='Org2MSP' WHERE username='hospital_b';

CREATE TABLE IF NOT EXISTS medical_records (
  id INT PRIMARY KEY AUTO_INCREMENT,
  patient_id INT NOT NULL,
  uploader_hospital_id INT NOT NULL,
  title VARCHAR(255) NOT NULL,
  diagnosis VARCHAR(255) NOT NULL,
  content TEXT NOT NULL,
  content_hash VARCHAR(64) NOT NULL,
  tx_id VARCHAR(128) NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT fk_record_patient FOREIGN KEY (patient_id) REFERENCES users(id),
  CONSTRAINT fk_record_uploader FOREIGN KEY (uploader_hospital_id) REFERENCES users(id)
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
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  reviewed_at DATETIME NULL,
  CONSTRAINT fk_request_record FOREIGN KEY (record_id) REFERENCES medical_records(id),
  CONSTRAINT fk_request_hospital FOREIGN KEY (applicant_hospital_id) REFERENCES users(id),
  CONSTRAINT fk_request_patient FOREIGN KEY (patient_id) REFERENCES users(id)
);

-- 注：初始种子密码仍为明文 '123456'，首次成功登录时后端会自动替换为 bcrypt 哈希
INSERT INTO users (id, username, password, role, real_name, hospital_name, msp_org, is_active) VALUES
  (1, 'admin',      '123456', 'admin',    '系统管理员',   NULL,        NULL,      1),
  (2, 'patient1',   '123456', 'patient',  '张三',         NULL,        NULL,      1),
  (3, 'patient2',   '123456', 'patient',  '李四',         NULL,        NULL,      1),
  (4, 'hospital_a', '123456', 'hospital', 'HospitalA医生','HospitalA', 'Org1MSP', 1),
  (5, 'hospital_b', '123456', 'hospital', 'HospitalB医生','HospitalB', 'Org2MSP', 1)
ON DUPLICATE KEY UPDATE username = VALUES(username);

INSERT INTO medical_records (
  id, patient_id, uploader_hospital_id, title, diagnosis, content, content_hash, tx_id
) VALUES (
  1,
  2,
  4,
  '2026春季门诊记录',
  '轻度贫血',
  '患者近期体检发现轻度贫血，建议复查血常规并补充铁剂。',
  '7d57f98c91b4f972a4acf2c0bc4c7f41602c118acdb83fc0a53ed2106b1ddc5b',
  NULL
)
ON DUPLICATE KEY UPDATE title = VALUES(title);

INSERT INTO access_requests (
  id, record_id, applicant_hospital_id, patient_id, reason, reason_hash, status, create_tx_id
) VALUES (
  1,
  1,
  5,
  2,
  '用于跨院会诊，评估后续治疗方案',
  'f9cd2c8f01e5785c927a7adbd74f10217cb5f92adac2e7f3099b9cfe0ca84dfe',
  'PENDING',
  NULL
)
ON DUPLICATE KEY UPDATE reason = VALUES(reason);
