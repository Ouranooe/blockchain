#!/usr/bin/env bash
# 迭代 8：一键安全扫描（bandit + npm audit + OWASP ZAP baseline）
# 产出报告到 tools/security/reports/
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
OUT_DIR="${SCRIPT_DIR}/reports"
mkdir -p "${OUT_DIR}"

echo "======================================================"
echo "[scan] bandit: Python 静态安全检查"
echo "======================================================"
if command -v bandit >/dev/null 2>&1; then
  bandit -r "${REPO_ROOT}/backend/app" -f txt -o "${OUT_DIR}/bandit.txt" || true
  echo "[scan] bandit → ${OUT_DIR}/bandit.txt"
else
  echo "[scan] 请先 pip install bandit==1.7.10"
fi

echo "======================================================"
echo "[scan] npm audit: gateway / frontend 依赖漏洞"
echo "======================================================"
for pkg in gateway frontend fabric-network/chaincode/medshare/javascript; do
  dir="${REPO_ROOT}/${pkg}"
  if [ -d "${dir}" ] && [ -f "${dir}/package.json" ]; then
    echo "[scan] npm audit → ${pkg}"
    (cd "${dir}" && npm audit --json > "${OUT_DIR}/npm-audit-$(echo ${pkg} | tr '/' '-').json") || true
  fi
done

echo "======================================================"
echo "[scan] OWASP ZAP baseline（需要正在运行的服务）"
echo "======================================================"
TARGET="${ZAP_TARGET:-https://localhost}"
if command -v docker >/dev/null 2>&1; then
  docker run --rm --network host \
    -v "${OUT_DIR}:/zap/wrk" \
    ghcr.io/zaproxy/zaproxy:stable zap-baseline.py \
    -t "${TARGET}" -r zap-baseline.html -I || true
  echo "[scan] ZAP → ${OUT_DIR}/zap-baseline.html"
else
  echo "[scan] 跳过 ZAP：docker 未安装"
fi

echo
echo "[scan] 报告目录：${OUT_DIR}"
ls -l "${OUT_DIR}"
