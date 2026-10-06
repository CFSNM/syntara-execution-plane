#!/usr/bin/env bash
# Run tests/e2e against AO on the Konflux Kind cluster.
#
# Intended for the Konflux test task after konflux-prepare-execution-plane.sh.
# Uses the AO ingress port-forward left by deploy-ao (https://127.0.0.1:8443)
# and the operator secret myao-initial-admin-password. Does not print the
# password. Replaces konflux-run-hello-world-demo.sh.
set -euo pipefail

export PATH="${HOME}/.local/bin:${HOME}/aap-dev/bin:${PATH}"

KUBECONFIG="${KUBECONFIG:-${HOME}/aap-dev/.tmp/27-next-ao-operator.kubeconfig}"
export KUBECONFIG

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO_ROOT=$(cd "${SCRIPT_DIR}/.." && pwd)
AO_NAMESPACE="${AO_NAMESPACE:-aap27-next}"
E2E_DIR="${REPO_ROOT}/tests/e2e"

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
echo "=== Running tests/e2e (${base}) ==="

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
  E2E_POLL_TIMEOUT="${E2E_POLL_TIMEOUT:-180}" \
  uv run --directory "${REPO_ROOT}" --no-project --with "pytest>=9,<10" --with "httpx>=0.28" -- \
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
