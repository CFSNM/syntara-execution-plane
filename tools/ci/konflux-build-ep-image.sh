#!/usr/bin/env bash
# Build this repository's Containerfile and put the image on the aap-dev Kind
# cluster so konflux-prepare-execution-plane.sh can deploy it.
#
# After deploy-ao the Kind node can pull localhost:5001/<ns>/ep:<AAP_VERSION>.
# This script replaces aap-dev addons/ep: it builds ./Containerfile, pushes to
# that registry, and also loads the image into the Kind node.
set -euo pipefail

export PATH="${HOME}/aap-dev/bin:${PATH}"

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO_ROOT=$(cd "${SCRIPT_DIR}/../.." && pwd)
AO_NAMESPACE="${AO_NAMESPACE:-aap27-next}"
AAP_VERSION="${AAP_VERSION:-2.7-next}"
EP_IMAGE="${EP_IMAGE:-localhost:5001/${AO_NAMESPACE}/ep:${AAP_VERSION}}"
CLUSTER_NAME="${CLUSTER_NAME:-27-next-ao-operator}"
CONTAINERFILE="${CONTAINERFILE:-${REPO_ROOT}/Containerfile}"

if [[ ! -f "${CONTAINERFILE}" ]]; then
  echo "ERROR: Containerfile not found: ${CONTAINERFILE}" >&2
  exit 1
fi

_runtime() {
  if command -v podman >/dev/null 2>&1; then
    printf '%s' podman
    return 0
  fi
  if command -v docker >/dev/null 2>&1; then
    printf '%s' docker
    return 0
  fi
  echo "ERROR: podman or docker is required to build ${EP_IMAGE}" >&2
  exit 1
}

_kind_node_container() {
  printf '%s' "${CLUSTER_NAME}-control-plane"
}

_load_into_kind_node() {
  local node
  node=$(_kind_node_container)
  echo "=== Loading ${EP_IMAGE} into Kind node ${node} ==="
  if command -v kind >/dev/null 2>&1; then
    if kind load docker-image "${EP_IMAGE}" --name "${CLUSTER_NAME}"; then
      return 0
    fi
    echo "kind load failed; importing via containerd" >&2
  fi
  if command -v podman >/dev/null 2>&1 && podman inspect "${node}" >/dev/null 2>&1; then
    "${RUNTIME}" save "${EP_IMAGE}" | podman exec -i "${node}" ctr -n k8s.io images import -
    return 0
  fi
  if command -v docker >/dev/null 2>&1 && docker inspect "${node}" >/dev/null 2>&1; then
    "${RUNTIME}" save "${EP_IMAGE}" | docker exec -i "${node}" ctr -n k8s.io images import -
    return 0
  fi
  echo "ERROR: Kind node container ${node} not found" >&2
  return 1
}

_push_to_local_registry() {
  echo "=== Pushing ${EP_IMAGE} ==="
  if [[ "${RUNTIME}" == podman ]]; then
    podman push --tls-verify=false "${EP_IMAGE}"
    return
  fi
  docker push "${EP_IMAGE}"
}

RUNTIME=$(_runtime)
echo "=== Building ${EP_IMAGE} from ${CONTAINERFILE} (${RUNTIME}) ==="

if command -v chcon >/dev/null 2>&1; then
  chcon -R -t container_file_t "${REPO_ROOT}" 2>/dev/null || true
fi

"${RUNTIME}" build -f "${CONTAINERFILE}" -t "${EP_IMAGE}" "${REPO_ROOT}"

pushed=0
loaded=0
if _push_to_local_registry; then
  pushed=1
else
  echo "WARNING: push of ${EP_IMAGE} failed; Kind load may still succeed" >&2
fi
if _load_into_kind_node; then
  loaded=1
else
  echo "WARNING: Kind load of ${EP_IMAGE} failed; registry pull may still succeed" >&2
fi
if [[ "${pushed}" -eq 0 && "${loaded}" -eq 0 ]]; then
  echo "ERROR: ${EP_IMAGE} was built but not published to the registry or Kind node" >&2
  exit 1
fi

echo "built ${EP_IMAGE}"
