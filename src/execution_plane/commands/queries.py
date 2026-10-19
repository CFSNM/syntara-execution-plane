"""Client-scoped queries of domain resources, without command history."""

import json
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncEngine

from execution_plane.api.schemas import ClusterRead, ExecutionTargetRead, WorkItemRead
from execution_plane.cluster.cluster_service import ClusterService, NoopDiscoveryMechanism
from execution_plane.cluster.cluster_store import ClusterStore
from execution_plane.commands.clusters import ResourceNotFoundError
from execution_plane.execution_target.execution_target_service import ExecutionTargetService
from execution_plane.execution_target.execution_target_store import ExecutionTargetStore
from execution_plane.work_item.work_item_service import WorkItemService
from execution_plane.work_item.work_item_store import WorkStore


class EPResourceQueries:
    """Read only resources owned by the authenticated service client."""

    def __init__(self, engine: AsyncEngine, client_id: str) -> None:
        """Borrow the API's engine and verified client identity."""
        target_service = ExecutionTargetService(ExecutionTargetStore.from_engine(engine))
        self._clusters = ClusterService(
            ClusterStore.from_engine(engine),
            target_service,
            NoopDiscoveryMechanism(),
        )
        self._work = WorkItemService(WorkStore.from_engine(engine))
        self._targets = target_service
        self._client_id = client_id

    async def work_items(self, limit: int) -> list[WorkItemRead]:
        """List work without embedding potentially large results."""
        items = await self._work.list_for_client(client_id=self._client_id, limit=limit)
        return [
            WorkItemRead.model_validate(item).model_copy(
                update={"result": None, "result_omitted": item.result is not None}
            )
            for item in items
        ]

    async def work_item(self, resource_id: UUID) -> WorkItemRead:
        """Recover the complete stored result without needing command history."""
        item = await self._work.get(resource_id, client_id=self._client_id)
        if item is None:
            raise ResourceNotFoundError
        read = WorkItemRead.model_validate(item)
        if item.result is not None:
            read.result_size_bytes = len(json.dumps(item.result, ensure_ascii=False).encode())
        event = await self._work.get_completion_event(resource_id)
        if event is not None:
            read.completion_event_id, read.state_revision = event.id, event.state_revision
        return read

    async def clusters(self, limit: int) -> list[ClusterRead]:
        """List binding resources, including pending setup and deletion."""
        rows = await self._clusters.list_bindings_for_client(self._client_id, limit=limit)
        return [ClusterRead.model_validate(row) for row in rows]

    async def cluster(self, resource_id: UUID) -> ClusterRead:
        """Read binding state using its stable source integration UUID."""
        row = await self._clusters.get_binding_for_client(self._client_id, resource_id)
        if row is None:
            raise ResourceNotFoundError
        return ClusterRead.model_validate(row)

    async def targets(self, limit: int, resource_id: UUID | None = None) -> list[ExecutionTargetRead]:
        """Authorize targets through ownership of their physical cluster."""
        rows = await self._targets.list_for_client(self._client_id, limit=limit, target_id=resource_id)
        return [ExecutionTargetRead.model_validate(row) for row in rows]

    async def target(self, resource_id: UUID) -> ExecutionTargetRead:
        """Use the same not-found response for absent and other-client targets."""
        rows = await self.targets(1, resource_id)
        if not rows:
            raise ResourceNotFoundError
        return rows[0]
