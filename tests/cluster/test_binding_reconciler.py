"""Contracts between versioned client bindings and EP-owned target placement."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from execution_plane.cluster.binding_reconciler import ClusterBindingReconciler
from execution_plane.models.cluster import Cluster, ClusterStatus
from execution_plane.models.cluster_binding import ClusterBinding
from execution_plane.models.execution_target_placement import KubernetesPlacement


def _binding() -> ClusterBinding:
    now = datetime.now(UTC)
    return ClusterBinding(
        client_id="syntara",
        source_integration_id=uuid.uuid4(),
        revision=1,
        name="workers",
        endpoint="https://cluster.example",
        namespace="ep-workers",
        credential="secret",
        created_at=now,
        updated_at=now,
    )


def _cluster() -> Cluster:
    now = datetime.now(UTC)
    return Cluster(
        id=uuid.uuid4(),
        name="workers",
        endpoint="https://cluster.example",
        api_key="secret",
        status=ClusterStatus.ACTIVE,
        created_by=uuid.uuid4(),
        created_at=now,
        updated_by=uuid.uuid4(),
        updated_at=now,
    )


@pytest.mark.asyncio
async def test_new_binding_namespace_becomes_typed_kubernetes_placement() -> None:
    cluster = _cluster()
    cluster_store = AsyncMock()
    cluster_store.update_source_binding.return_value = cluster
    cluster_registrar = AsyncMock()
    reconciler = ClusterBindingReconciler(AsyncMock(), cluster_registrar, cluster_store)
    provision = AsyncMock(return_value=cluster)
    reconciler._registrar.provision = provision

    status, cluster_id = await reconciler._reconcile_upsert(_binding(), None)

    placement = provision.call_args.args[3]
    assert isinstance(placement, KubernetesPlacement)
    assert placement.namespace == "ep-workers"
    assert status == "ready"
    assert cluster_id == cluster.id


@pytest.mark.asyncio
async def test_binding_update_keeps_namespace_in_request_and_sends_typed_placement() -> None:
    binding = _binding()
    binding.labels = {"region": "eu-west"}
    cluster = _cluster()
    cluster_store = AsyncMock()
    cluster_store.update_source_binding.return_value = cluster
    cluster_registrar = AsyncMock()
    reconciler = ClusterBindingReconciler(AsyncMock(), cluster_registrar, cluster_store)
    sync_update = AsyncMock()
    reconciler._registrar.sync_update = sync_update

    status, cluster_id = await reconciler._reconcile_upsert(binding, cluster)

    placement = sync_update.call_args.kwargs["placement"]
    assert isinstance(placement, KubernetesPlacement)
    assert placement.namespace == binding.namespace
    assert sync_update.call_args.kwargs["labels"] == binding.labels
    assert status == "ready"
    assert cluster_id == cluster.id
