#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SAMPLES_DIR="${ROOT_DIR}/fabric-network/runtime/fabric-samples"

if [ ! -d "${SAMPLES_DIR}/test-network" ]; then
  echo "[fabric-teardown] test-network not found, skip."
  exit 0
fi

pushd "${SAMPLES_DIR}/test-network" >/dev/null
./network.sh down
popd >/dev/null

echo "[fabric-teardown] network stopped."
