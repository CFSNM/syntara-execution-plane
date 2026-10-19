"""Cluster registration orchestration and discovery boundary."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol

from execution_plane.models.cluster import Cluster, ClusterStatus, ClusterType
from execution_plane.models.execution_target import BackendType

if TYPE_CHECKING:
    import uuid
    from collections.abc import Sequence

    from execution_plane.cluster.cluster_store import ClusterStore
    from execution_plane.execution_target.execution_target_service import ExecutionTargetService
    from execution_plane.models.cluster_binding import ClusterBinding
    from execution_plane.models.execution_target import ExecutionTarget
    from execution_plane.models.execution_target_placement import ExecutionTargetPlacement


@dataclass(frozen=True)
class ClusterRegistration:
    """Credential-bearing input passed only to the discovery mechanism."""

    name: str
    endpoint: str
    api_key: str = field(repr=False)
    labels: dict[str, str]


@dataclass(frozen=True)
class DiscoveredExecutionTarget:
    """Domain-level target definition returned by discovery."""

    name: str
    backend_type: BackendType
    endpoint: str
    api_key: str = field(repr=False)
    placement: ExecutionTargetPlacement
    is_default: bool = False


class DiscoveryState(StrEnum):
    """Outcome of a synchronous discovery attempt."""

    DISCOVERED = "discovered"
    FAILED = "failed"


@dataclass(frozen=True)
class DiscoveryResult:
    """Discovery state and domain target definitions."""

    state: DiscoveryState
    targets: tuple[DiscoveredExecutionTarget, ...] = ()
    status_message: str | None = None

    @classmethod
    def failed(cls, status_message: str) -> DiscoveryResult:
        """Build a failed discovery result."""
        return cls(DiscoveryState.FAILED, status_message=status_message)

    @classmethod
    def discovered(cls, targets: list[DiscoveredExecutionTarget]) -> DiscoveryResult:
        """Build a successful result containing discovered target definitions."""
        return cls(DiscoveryState.DISCOVERED, tuple(targets))


class DiscoveryMechanism(Protocol):
    """Synchronous, session-free boundary for provider-specific discovery."""

    def discover(self, registration: ClusterRegistration) -> DiscoveryResult:
        """Discover targets for a registered Cluster."""
        ...


class NoopDiscoveryMechanism:
    """Discovery implementation used until a provider-specific mechanism is configured."""

    def discover(self, _registration: ClusterRegistration) -> DiscoveryResult:
        """Report that no discovery mechanism has been configured."""
        return DiscoveryResult.failed("No discovery mechanism is configured")


class ClusterService:
    """Coordinate discovery and target registration without owning sessions."""

    def __init__(
        self,
        store: ClusterStore,
        execution_target_service: ExecutionTargetService,
        discovery: DiscoveryMechanism,
    ) -> None:
        """Use a Store and target Service for persistence and target creation."""
        self._store = store
        self._execution_target_service = execution_target_service
        self._discovery = discovery

    async def register(
        self,
        name: str,
        endpoint: str,
        api_key: str,
        created_by: uuid.UUID,
        labels: dict[str, str] | None = None,
        *,
        cluster_type: ClusterType = ClusterType.OPENSHIFT,
    ) -> Cluster:
        """Persist first, discover second, and persist the resulting state."""
        registration = ClusterRegistration(name, endpoint, api_key, labels or {})
        cluster = await self._store.create(name, endpoint, api_key, created_by, labels, cluster_type=cluster_type)
        try:
            result = self._discovery.discover(registration)
        except Exception:  # noqa: BLE001
            return await self._store.record_discovery_state(
                cluster.id, ClusterStatus.ERROR, "discovery failed", created_by
            )
        if result.state is DiscoveryState.FAILED:
            return await self._store.record_discovery_state(
                cluster.id, ClusterStatus.ERROR, "discovery failed", created_by
            )

        defaults = [target for target in result.targets if target.is_default]
        if len(defaults) != 1:
            return await self._store.record_discovery_state(
                cluster.id, ClusterStatus.ERROR, "Discovery did not provide exactly one default target", created_by
            )
        failures: list[str] = []
        ordered_targets = [defaults[0], *(target for target in result.targets if not target.is_default)]
        for target in ordered_targets:
            try:
                created_target = await self._execution_target_service.create(
                    cluster_id=cluster.id,
                    name=target.name,
                    backend_type=target.backend_type,
                    endpoint=target.endpoint,
                    placement=target.placement,
                    api_key=target.api_key,
                    is_default=target.is_default,
                    created_by=created_by,
                )
                await self._execution_target_service.activate(created_target.id, created_by)
            except Exception:  # noqa: BLE001
                if target.is_default:
                    return await self._store.record_discovery_state(
                        cluster.id, ClusterStatus.ERROR, "default target registration failed", created_by
                    )
                failures.append(target.name)
        status_message = f"Target registration failures: {', '.join(failures)}" if failures else None
        return await self._store.record_discovery_state(cluster.id, ClusterStatus.ACTIVE, status_message, created_by)

    async def provision(
        self,
        name: str,
        endpoint: str,
        api_key: str,
        placement: ExecutionTargetPlacement,
        created_by: uuid.UUID,
        labels: dict[str, str] | None = None,
        *,
        cluster_type: ClusterType = ClusterType.OPENSHIFT,
        source_client_id: str | None = None,
        source_integration_id: uuid.UUID | None = None,
        source_revision: int = 0,
        project_ids: list[uuid.UUID] | None = None,
    ) -> Cluster:
        """Create a cluster and its sole default execution target from known values.

        Idempotent against a DRAINING cluster of the same name: if one exists
        it is reactivated with the new parameters rather than duplicated.
        Raises if target creation fails, leaving the cluster in ERROR state
        for operator recovery.
        """
        existing = (
            await self._store.get_by_source(source_client_id, source_integration_id)
            if source_client_id is not None and source_integration_id is not None
            else await self._store.get_by_name(name)
        )
        if existing is not None and (existing.status is ClusterStatus.DRAINING or not existing.enabled):
            return await self._reactivate(existing, endpoint, api_key, placement, created_by, labels)

        cluster = await self._store.create(
            name,
            endpoint,
            api_key,
            created_by,
            labels,
            cluster_type=cluster_type,
            source_client_id=source_client_id,
            source_integration_id=source_integration_id,
            source_revision=source_revision,
            project_ids=project_ids,
        )
        try:
            target = await self._execution_target_service.create(
                cluster_id=cluster.id,
                name=name,
                backend_type=BackendType.VANILLA_K8S,
                endpoint=endpoint,
                api_key=api_key,
                placement=placement,
                is_default=True,
                created_by=created_by,
            )
            await self._execution_target_service.activate(target.id, created_by)
        except Exception:
            await self._store.record_discovery_state(
                cluster.id, ClusterStatus.ERROR, "default target creation failed", created_by
            )
            raise
        return await self._store.record_discovery_state(cluster.id, ClusterStatus.ACTIVE, None, created_by)

    async def _reactivate(
        self,
        cluster: Cluster,
        endpoint: str,
        api_key: str,
        placement: ExecutionTargetPlacement,
        updated_by: uuid.UUID,
        labels: dict[str, str] | None = None,
    ) -> Cluster:
        """Reactivate a DRAINING cluster and its default target with new parameters."""
        reactivated = await self._store.reactivate(
            cluster.id,
            updated_by=updated_by,
            endpoint=endpoint,
            api_key=api_key,
            labels=labels,
        )
        default_target = await self.get_default_target(cluster.id)
        if default_target is not None:
            await self._execution_target_service.reactivate(
                default_target.id,
                updated_by=updated_by,
                endpoint=endpoint,
                api_key=api_key,
                placement=placement,
            )
        return reactivated

    async def get_default_target(self, cluster_id: uuid.UUID) -> ExecutionTarget | None:
        """Return the default execution target for a cluster, or None if absent."""
        targets = await self._execution_target_service.list(cluster_id=cluster_id)
        return next((t for t in targets if t.is_default), None)

    async def sync_update(
        self,
        cluster_id: uuid.UUID,
        *,
        updated_by: uuid.UUID,
        name: str | None = None,
        endpoint: str | None = None,
        api_key: str | None = None,
        ca_certificate: str | None = None,
        placement: ExecutionTargetPlacement | None = None,
        labels: dict[str, str] | None = None,
    ) -> None:
        """Update a cluster and its default execution target.

        Only non-None fields are written. Operates via independent store
        transactions; all changes must be idempotent against concurrent reads.
        """
        await self._store.update(
            cluster_id,
            updated_by=updated_by,
            name=name,
            endpoint=endpoint,
            api_key=api_key,
            ca_certificate=ca_certificate,
            labels=labels,
        )
        default_target = await self.get_default_target(cluster_id)
        if default_target is None:
            return
        await self._execution_target_service.update(
            default_target.id,
            updated_by=updated_by,
            name=name,
            endpoint=endpoint,
            api_key=api_key,
            placement=placement,
        )

    async def get(self, cluster_id: uuid.UUID, *, include_secret: bool = False) -> Cluster | None:
        """Return a Cluster through the persistence boundary."""
        return await self._store.get(cluster_id, include_secret=include_secret)

    async def get_by_name(self, name: str) -> Cluster | None:
        """Return a Cluster by name through the persistence boundary."""
        return await self._store.get_by_name(name)

    async def get_by_source(self, client_id: str, integration_id: uuid.UUID) -> Cluster | None:
        """Return a Cluster by its stable source identity."""
        return await self._store.get_by_source(client_id, integration_id)

    async def update_source_binding(
        self,
        cluster_id: uuid.UUID,
        *,
        client_id: str,
        integration_id: uuid.UUID,
        revision: int,
        project_ids: list[uuid.UUID] | None,
        ca_certificate: str | None = None,
    ) -> Cluster:
        """Persist stable source identity, observed revision, and project grants."""
        return await self._store.update_source_binding(
            cluster_id,
            client_id=client_id,
            integration_id=integration_id,
            revision=revision,
            project_ids=project_ids,
            ca_certificate=ca_certificate,
        )

    async def list(self, *, status: ClusterStatus | None = None, enabled: bool | None = None) -> list[Cluster]:
        """List Clusters for administrative or recovery workflows."""
        return await self._store.list(status=status, enabled=enabled)

    async def list_bindings_for_client(self, client_id: str, *, limit: int) -> Sequence[ClusterBinding]:
        """List desired-state bindings owned by one service client."""
        return await self._store.list_bindings_for_client(client_id, limit=limit)

    async def get_binding_for_client(
        self,
        client_id: str,
        source_integration_id: uuid.UUID,
    ) -> ClusterBinding | None:
        """Return one client-owned desired-state binding."""
        return await self._store.get_binding_for_client(client_id, source_integration_id)

    async def request_delete(self, cluster_id: uuid.UUID, updated_by: uuid.UUID) -> Cluster:
        """Disable a Cluster and mark its targets as DRAINING."""
        return await self._store.request_delete(cluster_id, updated_by)

    async def mark_drain_failed(
        self,
        cluster_id: uuid.UUID,
        status_message: str,
        updated_by: uuid.UUID,
    ) -> Cluster:
        """Record an irrecoverable Cluster drain failure."""
        return await self._store.mark_drain_failed(cluster_id, status_message, updated_by)

    async def finalize_delete(self, cluster_id: uuid.UUID) -> None:
        """Delete a drained Cluster when finalization is safe."""
        await self._store.finalize_delete(cluster_id)

    async def record_discovery_state(
        self,
        cluster_id: uuid.UUID,
        status: ClusterStatus,
        status_message: str | None,
        updated_by: uuid.UUID,
    ) -> Cluster:
        """Persist registration or discovery state on an existing Cluster."""
        return await self._store.record_discovery_state(cluster_id, status, status_message, updated_by)
