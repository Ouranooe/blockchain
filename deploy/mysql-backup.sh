#!/bin/sh
# 迭代 8：MySQL 每日冷备份，保留 RETENTION_DAYS（默认 7）天。
# 由 deploy/docker-compose.prod.yml 内 mysql-backup 容器每 24h 触发。
set -eu

MYSQL_HOST="${MYSQL_HOST:-mysql}"
MYSQL_USER="${MYSQL_USER:-medshare}"
MYSQL_PASSWORD="${MYSQL_PASSWORD:-medshare123}"
MYSQL_DB="${MYSQL_DB:-medshare}"
BACKUP_DIR="${BACKUP_DIR:-/backup}"
RETENTION_DAYS="${RETENTION_DAYS:-7}"

STAMP="$(date +%Y%m%d-%H%M%S)"
OUT="${BACKUP_DIR}/medshare-${STAMP}.sql.gz"

mkdir -p "${BACKUP_DIR}"

echo "[mysql-backup] $(date -Iseconds) → ${OUT}"
mysqldump \
  --host="${MYSQL_HOST}" \
  --user="${MYSQL_USER}" \
  --password="${MYSQL_PASSWORD}" \
  --single-transaction \
  --routines --triggers \
  --default-character-set=utf8mb4 \
  "${MYSQL_DB}" | gzip -c > "${OUT}"

echo "[mysql-backup] 清理 ${RETENTION_DAYS} 天前的备份"
find "${BACKUP_DIR}" -maxdepth 1 -type f -name 'medshare-*.sql.gz' \
  -mtime "+${RETENTION_DAYS}" -print -delete || true

echo "[mysql-backup] 完成。"
