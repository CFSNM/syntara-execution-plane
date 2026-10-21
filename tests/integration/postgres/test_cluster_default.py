"""PostgreSQL integration coverage for global default Clusters."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from execution_plane.cluster.binding_reconciler import ClusterBindingReconciler
from execution_plane.cluster.cluster_registrar import ClusterRegistrar, NoopDiscoveryMechanism
from execution_plane.cluster.cluster_store import ClusterStore, DefaultClusterError
from execution_plane.execution_target.execution_target_store import ExecutionTargetStore
from execution_plane.models.cluster import Cluster, ClusterStatus
from execution_plane.models.cluster_binding import ClusterBinding

pytestmark = pytest.mark.integration


def _binding(*, name: str, is_default: bool) -> ClusterBinding:
    now = datetime.now(UTC)
    return ClusterBinding(
        client_id="syntara",
        source_integration_id=uuid4(),
        revision=1,
        name=name,
        endpoint=f"https://{name}.example",
        namespace="ep",
        credential="secret",
        is_default=is_default,
        created_at=now,
        updated_at=now,
    )


async def _active_cluster(store: ClusterStore, actor_id: UUID, name: str) -> Cluster:
    cluster = await store.create(name, f"https://{name}.example", "secret", actor_id)
    return await store.record_discovery_state(cluster.id, ClusterStatus.ACTIVE, None, actor_id)


async def test_concurrent_cluster_default_promotions_leave_exactly_one_default(migrated_database: str) -> None:
    engine = create_async_engine(migrated_database)
    actor_id = uuid4()
    try:
        async with ClusterStore.from_engine(engine) as store:
            clusters = [await _active_cluster(store, actor_id, f"cluster-{uuid4()}") for _ in range(3)]
            await asyncio.gather(*(store.set_default(cluster.id, actor_id) for cluster in clusters))

            clusters = await store.list()
            defaults = [cluster for cluster in clusters if cluster.is_default]
            assert len(defaults) == 1
            assert defaults[0].id in {cluster.id for cluster in clusters}
    finally:
        await engine.dispose()


async def test_concurrent_cluster_default_bootstrap_leaves_exactly_one_default(migrated_database: str) -> None:
    engine = create_async_engine(migrated_database)
    actor_id = uuid4()
    try:
        async with ClusterStore.from_engine(engine) as store:
            clusters = [await _active_cluster(store, actor_id, f"cluster-{uuid4()}") for _ in range(3)]
            await asyncio.gather(*(store.ensure_default(cluster.id, actor_id) for cluster in clusters))

            clusters = await store.list()
            defaults = [cluster for cluster in clusters if cluster.is_default]
            assert len(defaults) == 1
            assert defaults[0].id in {cluster.id for cluster in clusters}
    finally:
        await engine.dispose()


async def test_default_cluster_delete_is_rejected_until_reassigned(migrated_database: str) -> None:
    engine = create_async_engine(migrated_database)
    actor_id = uuid4()
    try:
        async with ClusterStore.from_engine(engine) as store:
            default = await _active_cluster(store, actor_id, f"cluster-{uuid4()}")
            replacement = await _active_cluster(store, actor_id, f"cluster-{uuid4()}")
            default = await store.set_default(default.id, actor_id)

            with pytest.raises(DefaultClusterError):
                await store.request_delete(default.id, actor_id)

            await store.set_default(replacement.id, actor_id)
            drained = await store.request_delete(default.id, actor_id)
            assert drained.status is ClusterStatus.DRAINING
    finally:
        await engine.dispose()


async def test_binding_reconciliation_reassigns_the_global_default(migrated_database: str) -> None:
    engine = create_async_engine(migrated_database)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with (
            ClusterStore.from_engine(engine) as cluster_store,
            ExecutionTargetStore.from_engine(engine) as target_store,
        ):
            registrar = ClusterRegistrar(cluster_store, target_store, NoopDiscoveryMechanism())
            reconciler = ClusterBindingReconciler(sessions, registrar, cluster_store)
            first = _binding(name=f"first-{uuid4()}", is_default=True)
            second = _binding(name=f"second-{uuid4()}", is_default=False)

            await reconciler._reconcile_upsert(first, None)
            first_cluster = await cluster_store.get_by_source(first.client_id, first.source_integration_id)
            assert first_cluster is not None
            assert first_cluster.is_default is True
            assert first.is_default is True

            await reconciler._reconcile_upsert(second, None)
            second_cluster = await cluster_store.get_by_source(second.client_id, second.source_integration_id)
            assert second_cluster is not None
            assert second_cluster.is_default is False

            second.revision = 2
            second.is_default = True
            await reconciler._reconcile_upsert(second, second_cluster)
            first_cluster = await cluster_store.get_by_source(first.client_id, first.source_integration_id)
            second_cluster = await cluster_store.get_by_source(second.client_id, second.source_integration_id)
            assert first_cluster is not None
            assert second_cluster is not None
            assert first_cluster.is_default is False
            assert second_cluster.is_default is True
    finally:
        await engine.dispose()
