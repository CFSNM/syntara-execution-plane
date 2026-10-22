"""Kubernetes cluster fixture for the dispatch integration tests.

These tests need a live Kubernetes cluster in addition to PostgreSQL. Both resources
are provisioned *outside* the test suite:

* PostgreSQL — service container in the CI job (see tests/integration/conftest.py).
* Kind cluster — stood up by the ``Create kind cluster`` CI step; registered in the
  database by ``make register-kind-cluster`` (tools/ci/register_kind_cluster.py),
  which runs the full production ClusterBindingReconciler path.

Locally, run ``make register-kind-cluster`` after ``make kind-target`` to populate
the test database before running these tests.

Cluster-registration env vars passed to ``make register-kind-cluster``:
* ``EP_IT_K8S_ENDPOINT``  — API server URL (e.g. ``https://127.0.0.1:PORT``)
* ``EP_IT_K8S_TOKEN``     — bearer token for the ``syntara-dispatcher`` ServiceAccount
* ``EP_IT_K8S_NAMESPACE`` — target namespace (default: ``execution-plane``)
* ``EP_IT_K8S_CA_CERT``   — PEM CA certificate for the API server (optional)

The shared PostgreSQL fixtures (``migrated_database`` etc.) come from the parent
``tests/integration/conftest.py``.
"""

from __future__ import annotations

import os
import tempfile
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from kubernetes import client as kubernetes_client
from kubernetes.client import BatchV1Api

from execution_plane.cluster.cluster_store import ClusterStore
from execution_plane.models.cluster import ClusterStatus

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from execution_plane.models.cluster import Cluster

_EP_IT_ENDPOINT = "EP_IT_K8S_ENDPOINT"
_EP_IT_TOKEN = "EP_IT_K8S_TOKEN"  # noqa: S105 — env var name, not a secret value
_EP_IT_CA_CERT = "EP_IT_K8S_CA_CERT"

# Must match the values in tools/ci/register_kind_cluster.py.
_KIND_CLIENT_ID = "integration-test"
_KIND_SOURCE_INTEGRATION_ID = uuid.uuid5(
    uuid.NAMESPACE_URL,
    "https://execution-plane.local/kind/integration-test",
)


def ep_cluster_configured() -> bool:
    """Return True when kind cluster coordinates are present in the environment."""
    return bool(os.environ.get(_EP_IT_ENDPOINT) and os.environ.get(_EP_IT_TOKEN))


@pytest.fixture(scope="session")
async def ep_cluster(migrated_database: str) -> Cluster:
    """Return the kind cluster already registered in the test database.

    The cluster is registered outside the test suite by ``make register-kind-cluster``,
    which exercises the full ClusterBindingReconciler path. Tests skip when the
    cluster coordinates or the registered cluster are absent.
    """
    if not ep_cluster_configured():
        pytest.skip("EP_IT_K8S_ENDPOINT and EP_IT_K8S_TOKEN must be set for cluster dispatch tests")

    async with ClusterStore.from_database_url(migrated_database) as cluster_store:
        cluster = await cluster_store.get_by_source(_KIND_CLIENT_ID, _KIND_SOURCE_INTEGRATION_ID)

    if cluster is None or cluster.status is not ClusterStatus.ACTIVE:
        status = cluster.status if cluster else "not registered"
        pytest.skip(
            f"Kind cluster not found in database (status={status}); run 'make register-kind-cluster' before these tests"
        )

    return cluster


@pytest.fixture(scope="session")
async def kubernetes_batch_api(ep_cluster: Cluster) -> AsyncGenerator[BatchV1Api, None]:
    """Read Kubernetes resources through the configured test cluster credentials."""
    endpoint = os.environ[_EP_IT_ENDPOINT]
    token = os.environ[_EP_IT_TOKEN]
    ca_cert = os.environ.get(_EP_IT_CA_CERT)

    configuration = kubernetes_client.Configuration()
    configuration.host = endpoint
    configuration.api_key["authorization"] = token
    configuration.api_key["BearerToken"] = token
    configuration.api_key_prefix["authorization"] = "Bearer"
    configuration.api_key_prefix["BearerToken"] = "Bearer"
    configuration.verify_ssl = True

    with tempfile.TemporaryDirectory(prefix="ep-it-k8s-ca-") as ca_directory:
        if ca_cert:
            ca_path = Path(ca_directory) / "ca.pem"
            ca_path.write_text(ca_cert, encoding="utf-8")
            configuration.ssl_ca_cert = str(ca_path)

        with kubernetes_client.ApiClient(configuration) as api_client:
            yield BatchV1Api(api_client)
