"""Adapt Cluster and ExecutionTarget domain data into reconciler snapshots."""

from __future__ import annotations

from typing import TYPE_CHECKING

from execution_plane.execution_target_reconciler.filters import default_filters
from execution_plane.execution_target_reconciler.placement import PlacementResolver, WorkerManagerRegistry
from execution_plane.execution_target_reconciler.protocols import ExecutionTargetSnapshotSource
from execution_plane.execution_target_reconciler.reconciler import ExecutionTargetReconciler
from execution_plane.execution_target_reconciler.types import ClusterSnapshot, ExecutionTargetSnapshot
from execution_plane.models.cluster import ClusterStatus

if TYPE_CHECKING:
    from collections.abc import Sequence

    from execution_plane.cluster.cluster_service import ClusterService
    from execution_plane.execution_target.execution_target_service import ExecutionTargetService
    from execution_plane.execution_target_reconciler.protocols import EligibilityFilter
    from execution_plane.models.cluster import Cluster
    from execution_plane.models.execution_target import ExecutionTarget


def cluster_snapshot_from_model(cluster: Cluster) -> ClusterSnapshot:
    """Map a Cluster row to a reconciler snapshot. Non-ACTIVE clusters are ineligible."""
    return ClusterSnapshot(
        id=cluster.id,
        name=cluster.name,
        labels=dict(cluster.labels),
        cluster_type=cluster.cluster_type,
        enabled=cluster.enabled and cluster.status is ClusterStatus.ACTIVE,
    )


def execution_target_snapshot_from_model(
    target: ExecutionTarget,
    cluster: ClusterSnapshot,
) -> ExecutionTargetSnapshot:
    """Map an ExecutionTarget row onto a snapshot that shares `cluster`."""
    return ExecutionTargetSnapshot(
        id=target.id,
        cluster=cluster,
        name=target.name,
        placement=target.placement,
        backend_type=target.backend_type,
        labels=dict(target.labels),
        lifecycle=target.status.value,
        enabled=target.enabled,
        is_default=target.is_default,
    )


class ExecutionTargetSnapshotSourceAdapter(ExecutionTargetSnapshotSource):
    """`ExecutionTargetSnapshotSource` Protocol backed by Cluster and target Services."""

    def __init__(self, cluster_service: ClusterService, target_service: ExecutionTargetService) -> None:
        """Load Clusters first so every target of a Cluster shares one snapshot instance."""
        self._clusters = cluster_service
        self._targets = target_service

    async def list(self) -> Sequence[ExecutionTargetSnapshot]:
        """Return all targets with interned Cluster backrefs. Orphans with no Cluster are omitted."""
        interned = {cluster.id: cluster_snapshot_from_model(cluster) for cluster in await self._clusters.list()}
        return tuple(
            execution_target_snapshot_from_model(row, interned[row.cluster_id])
            for row in await self._targets.list()
            if row.cluster_id in interned
        )


def service_backed_snapshot_source(
    cluster_service: ClusterService,
    target_service: ExecutionTargetService,
) -> ExecutionTargetSnapshotSourceAdapter:
    """Build a flat target Service that interns ClusterSnapshot instances."""
    return ExecutionTargetSnapshotSourceAdapter(cluster_service, target_service)


def build_placement_resolver(
    cluster_service: ClusterService,
    target_service: ExecutionTargetService,
    worker_managers: WorkerManagerRegistry | None = None,
    filters: Sequence[EligibilityFilter] | None = None,
) -> PlacementResolver:
    """Construct the co-located PlacementResolver used by the worker process."""
    return PlacementResolver(
        ExecutionTargetReconciler(
            service_backed_snapshot_source(cluster_service, target_service),
            filters or default_filters(),
        ),
        worker_managers or WorkerManagerRegistry(),
    )
