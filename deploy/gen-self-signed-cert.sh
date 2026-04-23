#!/usr/bin/env bash
# 迭代 8：自签证书生成脚本（实验 / 内网用）。
# 生产环境建议用 Let's Encrypt：certbot --nginx -d yourdomain.com
set -euo pipefail

OUT_DIR="$(dirname "$0")/certs"
mkdir -p "$OUT_DIR"

CN="${1:-medshare.local}"
DAYS="${2:-3650}"

echo "[cert] 生成 ${CN} 的自签证书，有效期 ${DAYS} 天 → ${OUT_DIR}"

openssl req -x509 -nodes -days "${DAYS}" -newkey rsa:2048 \
  -subj "/C=CN/ST=Internal/L=Internal/O=MedShare/CN=${CN}" \
  -addext "subjectAltName=DNS:${CN},DNS:localhost,IP:127.0.0.1" \
  -keyout "${OUT_DIR}/server.key" \
  -out "${OUT_DIR}/server.crt"

chmod 600 "${OUT_DIR}/server.key"
echo "[cert] 完成：${OUT_DIR}/server.crt / server.key"
