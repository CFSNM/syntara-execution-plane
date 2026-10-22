#!/usr/bin/env bash
# Run tests/e2e against AO and the Execution Plane on the Konflux Kind cluster.
#
# Intended for the Konflux test task after konflux-prepare-execution-plane.sh.
# Uses the AO ingress port-forward left by deploy-ao (https://127.0.0.1:8443)
# and the operator secret myao-initial-admin-password. Port-forwards the EP
# API and exports EP_API_URL plus the AO JWT/CA the hello-world test needs.
# Does not print secrets. Replaces konflux-run-hello-world-demo.sh.
set -euo pipefail

export PATH="${HOME}/.local/bin:${HOME}/aap-dev/bin:${PATH}"

KUBECONFIG="${KUBECONFIG:-${HOME}/aap-dev/.tmp/27-next-ao-operator.kubeconfig}"
export KUBECONFIG

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO_ROOT=$(cd "${SCRIPT_DIR}/.." && pwd)
AO_NAMESPACE="${AO_NAMESPACE:-aap27-next}"
AO_NAME="${AO_NAME:-myao}"
EP_NAMESPACE="${EP_NAMESPACE:-execution-plane}"
EP_API_FORWARD_PORT="${EP_API_FORWARD_PORT:-18443}"
E2E_DIR="${REPO_ROOT}/tests/e2e"
EP_JWT_FILE="${EP_AO_JWT_PRIVATE_KEY_FILE:-/tmp/ep-e2e-jwt-primary.pem}"
EP_CA_FILE="${EP_API_CA_FILE:-/tmp/ep-e2e-ca.pem}"

if [[ ! -f "${KUBECONFIG}" ]]; then
  echo "ERROR: kubeconfig not found: ${KUBECONFIG}" >&2
  exit 1
fi

_ao_admin_password() {
  kubectl get secret myao-initial-admin-password -n "${AO_NAMESPACE}" -o jsonpath='{.data.password}' | base64 -d
}

_ao_api_reachable() {
  local base=$1 code
  code=$(curl -sk -o /dev/null -w '%{http_code}' --max-time 8 "${base}/health" || true)
  if [[ "${code}" == "200" || "${code}" == "204" ]]; then
    return 0
  fi
  code=$(curl -sk -o /dev/null -w '%{http_code}' --max-time 8 "${base}/api/v1/projects" || true)
  [[ -n "${code}" && "${code}" != "000" ]]
}

_ao_api_base() {
  local base="${AO_API_BASE:-https://127.0.0.1:8443}"
  if _ao_api_reachable "${base}"; then
    printf '%s' "${base}"
    return 0
  fi
  echo "ingress ${base} not reachable; port-forwarding myao-backend" >&2
  kubectl port-forward -n "${AO_NAMESPACE}" svc/myao-backend 18000:8000 >/tmp/ao-backend-pf.log 2>&1 &
  sleep 4
  printf '%s' "https://127.0.0.1:18000"
}

_secret_to_file() {
  local secret=$1 namespace=$2 key=$3 dest=$4
  kubectl get secret "${secret}" -n "${namespace}" -o json | python3 -c "
import base64, json, sys
secret = json.load(sys.stdin)
key = sys.argv[1]
dest = sys.argv[2]
data = secret.get('data') or {}
if key not in data:
    raise SystemExit(f'secret is missing key {key}')
open(dest, 'wb').write(base64.b64decode(data[key]))
" "${key}" "${dest}"
  chmod 600 "${dest}"
}

_ep_api_reachable() {
  local base=$1 code
  code=$(curl -sk -o /dev/null -w '%{http_code}' --max-time 8 "${base}/healthz/ready" || true)
  [[ "${code}" == "200" || "${code}" == "204" ]]
}

_ep_api_url() {
  local base="${EP_API_URL:-}"
  if [[ -n "${base}" ]] && _ep_api_reachable "${base}"; then
    printf '%s' "${base}"
    return 0
  fi
  echo "port-forwarding ${EP_NAMESPACE}/svc/execution-plane-api to 127.0.0.1:${EP_API_FORWARD_PORT}" >&2
  kubectl port-forward -n "${EP_NAMESPACE}" svc/execution-plane-api \
    "${EP_API_FORWARD_PORT}:8443" >/tmp/ep-api-pf.log 2>&1 &
  sleep 4
  printf '%s' "https://127.0.0.1:${EP_API_FORWARD_PORT}"
}

_ep_jwt_issuer() {
  local issuer
  issuer=$(kubectl get configmap execution-plane-config -n "${EP_NAMESPACE}" \
    -o jsonpath='{.data.ao-jwt-issuer}' 2>/dev/null || true)
  if [[ -n "${issuer}" ]]; then
    printf '%s' "${issuer}"
    return 0
  fi
  printf '%s' "https://0.0.0.0:8000"
}

_ensure_uv() {
  if command -v uv >/dev/null 2>&1; then
    return 0
  fi
  echo "=== Installing uv ==="
  curl --proto "=https" -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="${HOME}/.local/bin:${PATH}"
  command -v uv >/dev/null 2>&1
}

_diagnose() {
  echo "=== Diagnose execution-plane (e2e tests did not complete) ==="
  kubectl set env deploy/myao-worker -n "${AO_NAMESPACE}" --list 2>/dev/null \
    | grep -E 'APP_SCRIPT_NODES_ENABLED|APP_NODE_CONTAINER_IMAGES' || true
  echo "=== execution-plane pods ==="
  kubectl get pods -n execution-plane -o wide || true
  echo "=== execution-plane events ==="
  kubectl get events -n execution-plane --sort-by=.lastTimestamp 2>/dev/null | tail -40 || true
  echo "=== execution-plane-worker logs ==="
  kubectl logs -n execution-plane deploy/execution-plane-worker --tail=200 || true
  echo "=== myao-worker logs ==="
  kubectl logs -n "${AO_NAMESPACE}" deploy/myao-worker --tail=80 || true
}

password=$(_ao_admin_password)
if [[ -z "${password}" ]]; then
  echo "ERROR: myao-initial-admin-password is empty" >&2
  exit 1
fi
base=$(_ao_api_base)
ep_base=$(_ep_api_url)
if ! _ep_api_reachable "${ep_base}"; then
  echo "ERROR: Execution Plane API not reachable at ${ep_base}" >&2
  cat /tmp/ep-api-pf.log >&2 || true
  exit 1
fi
_secret_to_file "${AO_NAME}-jwt-primary" "${AO_NAMESPACE}" jwt-primary.pem "${EP_JWT_FILE}"
_secret_to_file execution-plane-ao-auth "${EP_NAMESPACE}" ca.pem "${EP_CA_FILE}"
ep_issuer=$(_ep_jwt_issuer)
echo "=== Running tests/e2e (AO ${base}, EP ${ep_base}) ==="

if ! _ensure_uv; then
  echo "ERROR: uv is required to run tests/e2e" >&2
  exit 1
fi

if [[ ! -d "${E2E_DIR}" ]]; then
  echo "ERROR: e2e test directory not found: ${E2E_DIR}" >&2
  exit 1
fi

cd "${REPO_ROOT}"
export UV_LINK_MODE=copy
unset VIRTUAL_ENV
set +e
# --no-project avoids compiling EP native deps on the Kind VM. --confcutdir
# keeps tests/conftest.py (which imports execution_plane) out of collection.
ADMIN_PASSWORD="${password}" BASE="${base}" \
  EP_API_URL="${ep_base}" \
  EP_API_CA_FILE="${EP_CA_FILE}" \
  EP_AO_JWT_PRIVATE_KEY_FILE="${EP_JWT_FILE}" \
  EP_AO_JWT_ISSUER="${ep_issuer}" \
  E2E_POLL_TIMEOUT="${E2E_POLL_TIMEOUT:-180}" \
  uv run --directory "${REPO_ROOT}" --no-project --with "pytest>=9,<10" --with "httpx>=0.28" --with "PyJWT[crypto]>=2.13" -- \
    pytest --confcutdir="${E2E_DIR}" "${E2E_DIR}" \
      -m e2e -v --tb=short --log-cli-level=INFO --color=no
e2e_rc=$?
set -e
unset password
if [[ "${e2e_rc}" -ne 0 ]]; then
  _diagnose
  exit "${e2e_rc}"
fi
echo "e2e tests completed"
