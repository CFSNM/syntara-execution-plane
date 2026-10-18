"""Domain-facing operations for WorkItem lifecycle and placement."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from execution_plane.execution_target_reconciler.adapters import (
    cluster_snapshot_from_model,
    execution_target_snapshot_from_model,
)
from execution_plane.execution_target_reconciler.types import WorkRequirements
from execution_plane.models.work_item import WorkItemStatus

if TYPE_CHECKING:
    import uuid

    from execution_plane.execution_target_reconciler.placement import PlacementResolver
    from execution_plane.models.cluster import Cluster
    from execution_plane.models.completion_event import CompletionEvent
    from execution_plane.models.execution_target import ExecutionTarget
    from execution_plane.models.work_item import WorkItem
    from execution_plane.work_item.work_item_store import WorkStore

_TERMINAL_RESULT_STATUSES = frozenset(
    {
        WorkItemStatus.COMPLETED,
        WorkItemStatus.FAILED,
        WorkItemStatus.CANCELLED,
    }
)


class WorkItemService:
    """Expose Work use cases without leaking persistence or session details."""

    def __init__(self, store: WorkStore, placement_resolver: PlacementResolver | None = None) -> None:
        """Use the Store for persistence and the resolver for placement policy."""
        self._store = store
        self._placement_resolver = placement_resolver

    async def dispatch(self, client_id: str, item_id: uuid.UUID, payload: dict[str, Any]) -> WorkItem:
        """Submit caller-identified work for asynchronous execution."""
        return await self._store.dispatch(client_id=client_id, item_id=item_id, payload=payload)

    async def get(self, item_id: uuid.UUID, *, client_id: str) -> WorkItem | None:
        """Return one work item scoped to the authenticated client."""
        return await self._store.get(item_id, client_id=client_id)

    async def get_completion_event(self, work_item_id: uuid.UUID) -> CompletionEvent | None:
        """Return the durable completion event for terminal work."""
        return await self._store.get_completion_event(work_item_id)

    async def list_for_client(self, *, client_id: str, limit: int) -> list[WorkItem]:
        """Return bounded work owned by the authenticated client."""
        return await self._store.list_for_client(client_id=client_id, limit=limit)

    async def reserve_one(self) -> WorkItem | None:
        """Reserve one queued or recoverable work item."""
        return await self._store.reserve_one()

    async def place_reserved_item(self, item: WorkItem) -> WorkItem | None:
        """Resolve placement and atomically assign a still-eligible target."""
        if self._placement_resolver is None:
            raise RuntimeError("WorkItemService has no PlacementResolver")  # noqa: TRY003, EM101
        claim_owner_id = item.claim_owner_id
        if claim_owner_id is None:
            raise ValueError("Reserved work item has no claim owner")  # noqa: TRY003, EM101

        placement_resolver = self._placement_resolver

        requirements = self._requirements_for(item)
        resolved = await self._placement_resolver.resolve(requirements)
        if not resolved.available_targets:
            await self._store.set_result(
                item.id,
                {"error": "No execution target matched placement requirements", "error_type": "TargetUnavailable"},
                WorkItemStatus.FAILED,
                claim_owner_id=claim_owner_id,
                claim_generation=item.claim_generation,
            )
            return None

        async def target_is_eligible(cluster: Cluster, target: ExecutionTarget) -> bool:
            cluster_snapshot = cluster_snapshot_from_model(cluster)
            return await placement_resolver.target_is_eligible(
                cluster_snapshot,
                execution_target_snapshot_from_model(target, cluster_snapshot),
                requirements,
            )

        for target in resolved.available_targets:
            assigned = await self._store.assign_target(
                item.id,
                target.id,
                claim_owner_id=claim_owner_id,
                claim_generation=item.claim_generation,
                eligibility=target_is_eligible,
            )
            if assigned is not None:
                return assigned

        await self._store.release_claim(
            item.id,
            claim_owner_id=claim_owner_id,
            claim_generation=item.claim_generation,
        )
        return None

    async def mark_dispatched(
        self,
        item_id: uuid.UUID,
        *,
        claim_owner_id: uuid.UUID | None = None,
        claim_generation: int | None = None,
    ) -> bool:
        """Persist the external-dispatch boundary for a current claim."""
        return await self._store.mark_dispatched(
            item_id,
            claim_owner_id=claim_owner_id,
            claim_generation=claim_generation,
        )

    async def refresh_claim(
        self,
        item_id: uuid.UUID,
        *,
        claim_owner_id: uuid.UUID | None = None,
        claim_generation: int | None = None,
    ) -> bool | None:
        """Renew a current controller lease and report cancellation intent."""
        return await self._store.refresh_claim(
            item_id,
            claim_owner_id=claim_owner_id,
            claim_generation=claim_generation,
        )

    async def update_backend_resource(
        self,
        item_id: uuid.UUID,
        *,
        resource_name: str,
        resource_uid: str | None,
        cleanup_status: str,
        cleanup_error: str | None = None,
        claim_owner_id: uuid.UUID | None,
        claim_generation: int,
    ) -> None:
        """Persist cold-start allocation identity and cleanup state."""
        await self._store.update_backend_resource(
            item_id,
            resource_name=resource_name,
            resource_uid=resource_uid,
            cleanup_status=cleanup_status,
            cleanup_error=cleanup_error[:1000] if cleanup_error else None,
            claim_owner_id=claim_owner_id,
            claim_generation=claim_generation,
        )

    async def set_result(
        self,
        item_id: uuid.UUID,
        result: dict[str, Any],
        status: WorkItemStatus,
        *,
        claim_owner_id: uuid.UUID | None = None,
        claim_generation: int | None = None,
    ) -> WorkItem:
        """Persist a terminal result and its completion event."""
        if status not in _TERMINAL_RESULT_STATUSES:
            message = f"Work result requires a terminal status, received {status.value}"
            raise ValueError(message)
        return await self._store.set_result(
            item_id,
            result,
            status,
            claim_owner_id=claim_owner_id,
            claim_generation=claim_generation,
        )

    async def request_cancel(self, item_id: uuid.UUID, *, client_id: str) -> WorkItem:
        """Cancel queued work or request termination of dispatched work."""
        return await self._store.request_cancel(item_id, client_id=client_id)

    async def mark_reconciliation_required(
        self,
        item_id: uuid.UUID,
        reason: str,
        *,
        claim_owner_id: uuid.UUID | None = None,
        claim_generation: int | None = None,
    ) -> WorkItem:
        """Persist an uncertain outcome without implying failure or rerun."""
        return await self._store.mark_reconciliation_required(
            item_id,
            reason[:1000],
            claim_owner_id=claim_owner_id,
            claim_generation=claim_generation,
        )

    async def requeue_pre_execute_failure(
        self,
        item_id: uuid.UUID,
        *,
        claim_owner_id: uuid.UUID,
        claim_generation: int,
    ) -> WorkItem:
        """Return a proven pre-Execute failure to the pending queue."""
        return await self._store.requeue_pre_execute_failure(
            item_id,
            claim_owner_id=claim_owner_id,
            claim_generation=claim_generation,
        )

    async def get_pending_completion_events(self, *, limit: int = 100) -> list[CompletionEvent]:
        """Load a bounded batch of due completion events."""
        return await self._store.get_pending_completion_events(limit=limit)

    async def record_completion_event_attempt(
        self,
        event_id: uuid.UUID,
        *,
        delivered: bool,
        error: str | None = None,
        retry_after_seconds: int = 10,
    ) -> None:
        """Record delivery or schedule a bounded retry."""
        if retry_after_seconds < 0:
            raise ValueError("Completion retry delay must be non-negative")  # noqa: TRY003, EM101
        await self._store.record_completion_event_attempt(
            event_id,
            delivered=delivered,
            error=error,
            retry_after_seconds=retry_after_seconds,
        )

    async def is_target_drained(self, target_id: uuid.UUID) -> bool:
        """Return whether no owned or uncertain work remains on a target."""
        return await self._store.is_target_drained(target_id)

    @staticmethod
    def _requirements_for(item: WorkItem) -> WorkRequirements:
        """Extract resolver requirements from the versioned work payload."""
        selectors: dict[str, str] = item.payload.get("selectors", {})
        if not isinstance(selectors, dict):
            selectors = {}
        workload_type = item.payload.get("workload_type")
        return WorkRequirements(
            selectors=selectors,
            workload_type=workload_type if isinstance(workload_type, str) else None,
        )
