"""Domain-facing execution-target lifecycle operations."""

from __future__ import annotations

from typing import TYPE_CHECKING

from execution_plane.execution_target.execution_target_store import (
    DefaultExecutionTargetError,
    ExecutionTargetNotFoundError,
)

if TYPE_CHECKING:
    import uuid
    from collections.abc import Sequence

    from execution_plane.execution_target.execution_target_store import ExecutionTargetStore
    from execution_plane.models.execution_target import BackendType, ExecutionTarget, TargetStatus
    from execution_plane.models.execution_target_placement import ExecutionTargetPlacement


class ExecutionTargetService:
    """Enforce execution-target domain rules without owning a database session."""

    def __init__(self, store: ExecutionTargetStore) -> None:
        """Use the store for persistence."""
        self._store = store

    async def create(
        self,
        cluster_id: uuid.UUID,
        name: str,
        backend_type: BackendType,
        endpoint: str,
        api_key: str,
        is_default: bool,  # noqa: FBT001
        created_by: uuid.UUID,
        placement: ExecutionTargetPlacement,
        labels: dict[str, str] | None = None,
        *,
        target_id: uuid.UUID | None = None,
    ) -> ExecutionTarget:
        """Create an execution target through the persistence boundary."""
        return await self._store.create(
            cluster_id,
            name,
            backend_type,
            endpoint,
            api_key,
            is_default,
            created_by,
            labels=labels,
            placement=placement,
            target_id=target_id,
        )

    async def get(self, target_id: uuid.UUID, *, include_secret: bool = False) -> ExecutionTarget | None:
        """Return a target by ID, if it exists."""
        return await self._store.get(target_id, include_secret=include_secret)

    async def list(
        self,
        cluster_id: uuid.UUID | None = None,
        eligible_only: bool = False,  # noqa: FBT001, FBT002
        status: TargetStatus | None = None,
        limit: int | None = None,
    ) -> Sequence[ExecutionTarget]:
        """List targets, optionally restricted to those eligible for new work."""
        return await self._store.list(
            cluster_id=cluster_id,
            eligible_only=eligible_only,
            status=status,
            limit=limit,
        )

    async def list_for_client(
        self,
        client_id: str,
        *,
        limit: int,
        target_id: uuid.UUID | None = None,
    ) -> Sequence[ExecutionTarget]:
        """List targets owned by clusters belonging to one service client."""
        return await self._store.list_for_client(client_id, limit=limit, target_id=target_id)

    async def request_delete(self, target_id: uuid.UUID, updated_by: uuid.UUID) -> ExecutionTarget:
        """Disable a non-default target and mark it as DRAINING."""
        target = await self._require_non_default(target_id)
        return await self._store.request_delete(target.id, updated_by)

    async def activate(self, target_id: uuid.UUID, updated_by: uuid.UUID) -> ExecutionTarget:
        """Mark a registered target active through the persistence boundary."""
        return await self._store.activate(target_id, updated_by)

    async def update(
        self,
        target_id: uuid.UUID,
        *,
        updated_by: uuid.UUID,
        name: str | None = None,
        endpoint: str | None = None,
        placement: ExecutionTargetPlacement | None = None,
        labels: dict[str, str] | None = None,
        status_message: str | None = None,
        api_key: str | None = None,
    ) -> ExecutionTarget:
        """Update target fields through the persistence boundary."""
        return await self._store.update(
            target_id,
            updated_by=updated_by,
            name=name,
            endpoint=endpoint,
            placement=placement,
            labels=labels,
            status_message=status_message,
            api_key=api_key,
        )

    async def reactivate(
        self,
        target_id: uuid.UUID,
        *,
        updated_by: uuid.UUID,
        endpoint: str | None = None,
        api_key: str | None = None,
        placement: ExecutionTargetPlacement,
    ) -> ExecutionTarget:
        """Re-enable a DRAINING target and transition it back to ACTIVE."""
        return await self._store.reactivate(
            target_id,
            updated_by=updated_by,
            endpoint=endpoint,
            api_key=api_key,
            placement=placement,
        )

    async def record_health(
        self,
        target_id: uuid.UUID,
        *,
        healthy: bool,
        message: str | None,
        updated_by: uuid.UUID,
    ) -> ExecutionTarget:
        """Persist ACTIVE ↔ DEGRADED from a health probe."""
        return await self._store.record_health(
            target_id,
            healthy=healthy,
            message=message,
            updated_by=updated_by,
        )

    async def mark_failed(self, target_id: uuid.UUID, updated_by: uuid.UUID) -> ExecutionTarget:
        """Record an irrecoverable target drain failure."""
        return await self._store.mark_failed(target_id, updated_by)

    async def finalize_delete(self, target_id: uuid.UUID) -> None:
        """Delete a drained target when finalization is safe."""
        await self._store.finalize_delete(target_id)

    async def _require_non_default(self, target_id: uuid.UUID) -> ExecutionTarget:
        """Return a target only if it is not the protected default target."""
        target = await self._store.get(target_id)
        if target is None:
            raise ExecutionTargetNotFoundError(target_id)
        if target.is_default:
            raise DefaultExecutionTargetError
        return target
