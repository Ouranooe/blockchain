#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
RUNTIME_DIR="${ROOT_DIR}/fabric-network/runtime"
SAMPLES_DIR="${RUNTIME_DIR}/fabric-samples"
CHANNEL_NAME="${CHANNEL_NAME:-medicalchannel}"
CHAINCODE_NAME="${CHAINCODE_NAME:-medshare}"
CHAINCODE_PATH="${ROOT_DIR}/fabric-network/chaincode/medshare/javascript"
CHAINCODE_PATH_RUN="${CHAINCODE_PATH}"
FABRIC_VERSION="${FABRIC_VERSION:-2.4.9}"
CA_VERSION="${CA_VERSION:-1.5.7}"

mkdir -p "${RUNTIME_DIR}"

if [[ "${CHAINCODE_PATH_RUN}" == *" "* ]]; then
  CHAINCODE_LINK="/tmp/medshare-chaincode"
  ln -sfn "${CHAINCODE_PATH_RUN}" "${CHAINCODE_LINK}"
  CHAINCODE_PATH_RUN="${CHAINCODE_LINK}"
fi

if [ ! -d "${SAMPLES_DIR}" ]; then
  echo "[fabric-bootstrap] cloning fabric-samples..."
  git clone --depth 1 --branch "v${FABRIC_VERSION}" https://github.com/hyperledger/fabric-samples.git "${SAMPLES_DIR}"
fi

pushd "${SAMPLES_DIR}" >/dev/null
echo "[fabric-bootstrap] downloading fabric binaries..."
if [ ! -x "${SAMPLES_DIR}/bin/peer" ]; then
  curl -L "https://github.com/hyperledger/fabric/releases/download/v${FABRIC_VERSION}/hyperledger-fabric-linux-amd64-${FABRIC_VERSION}.tar.gz" -o /tmp/hyperledger-fabric.tgz
  tar -xzf /tmp/hyperledger-fabric.tgz -C "${SAMPLES_DIR}"
fi

echo "[fabric-bootstrap] downloading fabric-ca binaries..."
if [ ! -x "${SAMPLES_DIR}/bin/fabric-ca-client" ]; then
  curl -L "https://github.com/hyperledger/fabric-ca/releases/download/v${CA_VERSION}/hyperledger-fabric-ca-linux-amd64-${CA_VERSION}.tar.gz" -o /tmp/hyperledger-fabric-ca.tgz
  tar -xzf /tmp/hyperledger-fabric-ca.tgz -C "${SAMPLES_DIR}"
fi

echo "[fabric-bootstrap] pulling docker images..."
pull_with_retry() {
  local image="$1"
  local retries="${2:-5}"
  local attempt=1
  while [ "${attempt}" -le "${retries}" ]; do
    if docker pull "${image}"; then
      return 0
    fi
    echo "[fabric-bootstrap] pull failed for ${image}, retry ${attempt}/${retries}..."
    sleep 2
    attempt=$((attempt + 1))
  done
  return 1
}

pull_with_retry "hyperledger/fabric-peer:${FABRIC_VERSION}"
pull_with_retry "hyperledger/fabric-orderer:${FABRIC_VERSION}"
pull_with_retry "hyperledger/fabric-tools:${FABRIC_VERSION}"
pull_with_retry "hyperledger/fabric-ccenv:${FABRIC_VERSION}"
pull_with_retry "hyperledger/fabric-baseos:${FABRIC_VERSION}" || echo "[fabric-bootstrap] warning: fabric-baseos pull failed, continuing"
pull_with_retry "hyperledger/fabric-ca:${CA_VERSION}"

echo "[fabric-bootstrap] pinning test-network image tags..."
NETWORK_SH="${SAMPLES_DIR}/test-network/network.sh"
sed -i "s|hyperledger/fabric-tools:latest|hyperledger/fabric-tools:${FABRIC_VERSION}|g" "${NETWORK_SH}"
sed -i "s|hyperledger/fabric-ca:latest|hyperledger/fabric-ca:${CA_VERSION}|g" "${NETWORK_SH}"
sed -i 's|pushd ${ROOTDIR} > /dev/null|pushd "${ROOTDIR}" > /dev/null|g' "${NETWORK_SH}"
find "${SAMPLES_DIR}/test-network/compose" "${SAMPLES_DIR}/test-network/addOrg3/compose" -type f -name "*.yaml" -print0 \
  | xargs -0 sed -i \
    -e "s|hyperledger/fabric-orderer:latest|hyperledger/fabric-orderer:${FABRIC_VERSION}|g" \
    -e "s|hyperledger/fabric-peer:latest|hyperledger/fabric-peer:${FABRIC_VERSION}|g" \
    -e "s|hyperledger/fabric-tools:latest|hyperledger/fabric-tools:${FABRIC_VERSION}|g" \
    -e "s|hyperledger/fabric-ca:latest|hyperledger/fabric-ca:${CA_VERSION}|g"
sed -i \
  -e 's|CORE_VM_ENDPOINT=unix:///host/var/run/docker.sock|CORE_VM_ENDPOINT=unix:///var/run/docker.sock|g' \
  -e 's|${DOCKER_SOCK}:/host/var/run/docker.sock|${DOCKER_SOCK}:/var/run/docker.sock|g' \
  "${SAMPLES_DIR}/test-network/compose/docker/docker-compose-test-net.yaml" \
  "${SAMPLES_DIR}/test-network/addOrg3/compose/docker/docker-compose-org3.yaml"
popd >/dev/null

pushd "${SAMPLES_DIR}/test-network" >/dev/null
echo "[fabric-bootstrap] stopping old network (if exists)..."
./network.sh down || true

echo "[fabric-bootstrap] starting network and channel..."
./network.sh up createChannel -ca -c "${CHANNEL_NAME}"

echo "[fabric-bootstrap] deploying chaincode..."
./network.sh deployCC -c "${CHANNEL_NAME}" -ccn "${CHAINCODE_NAME}" -ccp "${CHAINCODE_PATH_RUN}" -ccl javascript

echo "[fabric-bootstrap] patching connection profiles for docker gateway..."
sed -i 's/localhost/host.docker.internal/g' \
  "${SAMPLES_DIR}/test-network/organizations/peerOrganizations/org1.example.com/connection-org1.json" \
  "${SAMPLES_DIR}/test-network/organizations/peerOrganizations/org2.example.com/connection-org2.json"

echo "[fabric-bootstrap] done."
popd >/dev/null
