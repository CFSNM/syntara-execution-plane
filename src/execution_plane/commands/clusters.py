"""Cluster commands write desired state for the existing binding reconciler."""

import hashlib
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from execution_plane.cluster.cluster_store import DefaultClusterError
from execution_plane.commands.context import EPCommandContext
from execution_plane.labels import user_labels
from execution_plane.models.cluster import Cluster
from execution_plane.models.cluster_binding import ClusterBinding
from execution_plane_client.commands.base import CommandPayload
from execution_plane_client.commands.clusters import ClusterDelete, ClusterProvision, ClusterRegister, ClusterUpdate
from execution_plane_client.errors import ProtocolConflict, UnsupportedCommand
from execution_plane_client.handlers import HandlingResult
from execution_plane_client.protocol import MessageEnvelope, ResourceReference

_CONFIG_FIELDS = (
    "name",
    "endpoint",
    "namespace",
    "credential",
    "ca_certificate",
    "project_ids",
    "labels",
    "enabled",
    "is_default",
)

_ClusterCommand = ClusterRegister | ClusterUpdate | ClusterDelete


class ResourceNotFoundError(LookupError):
    """A resource is absent or belongs to another client."""


def _validated_payload[T: CommandPayload](payload: CommandPayload, expected: type[T]) -> T:
    """Reject payloads that do not match the handler's installed command."""
    if type(payload) is not expected:
        raise UnsupportedCommand
    return payload


async def _locked_binding(
    session: AsyncSession,
    context: EPCommandContext,
    resource_key: object,
) -> ClusterBinding | None:
    digest = hashlib.sha256(f"binding:{context.client_id}:{resource_key}".encode()).digest()
    await session.execute(select(func.pg_advisory_xact_lock(int.from_bytes(digest[:8], signed=True))))
    return await session.get(ClusterBinding, (context.client_id, resource_key), with_for_update=True)


async def _reject_current_default(session: AsyncSession, binding: ClusterBinding) -> None:
    if binding.cluster_id is None:
        return
    cluster = await session.get(Cluster, binding.cluster_id)
    if cluster is not None and cluster.is_default:
        raise DefaultClusterError


def _persist_binding(
    session: AsyncSession,
    context: EPCommandContext,
    payload: _ClusterCommand,
    desired: ClusterRegister,
    binding: ClusterBinding | None,
) -> None:
    if binding is not None:
        if desired.desired_revision < binding.revision:
            raise ProtocolConflict
        if desired.desired_revision == binding.revision:
            if any(getattr(binding, key) != getattr(desired, key) for key in _CONFIG_FIELDS):
                raise ProtocolConflict
            return

    now = datetime.now(UTC)
    if binding is None:
        session.add(
            ClusterBinding(
                client_id=context.client_id,
                source_integration_id=payload.resource_key,
                created_at=now,
                updated_at=now,
                revision=desired.desired_revision,
                **{key: getattr(desired, key) for key in _CONFIG_FIELDS},
            )
        )
        return

    for key in _CONFIG_FIELDS:
        setattr(binding, key, getattr(desired, key))
    binding.revision = desired.desired_revision
    binding.status = "pending"
    binding.status_message = None
    binding.updated_at = now


def _binding_result(payload: _ClusterCommand) -> HandlingResult:
    return HandlingResult(
        ResourceReference(kind="cluster_binding", resource_id=payload.resource_key, revision=payload.desired_revision)
    )


class ClusterRegisterCommandHandler:
    """Create or replace one complete Cluster binding revision."""

    async def handle(
        self, message: MessageEnvelope, payload: CommandPayload, context: EPCommandContext
    ) -> HandlingResult:
        """Apply a complete desired-state registration atomically."""
        del message
        command = _validated_payload(payload, ClusterRegister)
        desired = command.model_copy(update={"labels": user_labels(command.labels)})
        sessions = async_sessionmaker(context.engine, expire_on_commit=False)
        async with sessions.begin() as session:
            binding = await _locked_binding(session, context, command.resource_key)
            _persist_binding(session, context, command, desired, binding)
        return _binding_result(command)


class ClusterProvisionCommandHandler:
    """Provision one Cluster binding through the shared registration path."""

    async def handle(
        self, message: MessageEnvelope, payload: CommandPayload, context: EPCommandContext
    ) -> HandlingResult:
        """Apply provisioning desired state atomically."""
        del message
        command = _validated_payload(payload, ClusterProvision)
        desired = command.model_copy(update={"labels": user_labels(command.labels)})
        sessions = async_sessionmaker(context.engine, expire_on_commit=False)
        async with sessions.begin() as session:
            binding = await _locked_binding(session, context, command.resource_key)
            _persist_binding(session, context, command, desired, binding)
        return _binding_result(command)


class ClusterUpdateCommandHandler:
    """Patch one existing Cluster binding revision."""

    async def handle(
        self, message: MessageEnvelope, payload: CommandPayload, context: EPCommandContext
    ) -> HandlingResult:
        """Merge an update with persisted state and apply it atomically."""
        del message
        command = _validated_payload(payload, ClusterUpdate)
        sessions = async_sessionmaker(context.engine, expire_on_commit=False)
        async with sessions.begin() as session:
            binding = await _locked_binding(session, context, command.resource_key)
            if binding is None:
                raise ResourceNotFoundError
            if command.is_default is False or command.enabled is False:
                await _reject_current_default(session, binding)
            values = {field: getattr(binding, field) for field in _CONFIG_FIELDS}
            values.update(command.model_dump(exclude_unset=True, exclude={"resource_key", "desired_revision"}))
            values["labels"] = user_labels(values["labels"])
            desired = ClusterRegister(
                resource_key=command.resource_key, desired_revision=command.desired_revision, **values
            )
            _persist_binding(session, context, command, desired, binding)
        return _binding_result(command)


class ClusterDeleteCommandHandler:
    """Disable one existing Cluster binding revision."""

    async def handle(
        self, message: MessageEnvelope, payload: CommandPayload, context: EPCommandContext
    ) -> HandlingResult:
        """Disable desired state without deleting the current default Cluster."""
        del message
        command = _validated_payload(payload, ClusterDelete)
        sessions = async_sessionmaker(context.engine, expire_on_commit=False)
        async with sessions.begin() as session:
            binding = await _locked_binding(session, context, command.resource_key)
            if binding is None:
                raise ResourceNotFoundError
            await _reject_current_default(session, binding)
            values = {field: getattr(binding, field) for field in _CONFIG_FIELDS}
            values["enabled"] = False
            values["labels"] = user_labels(values["labels"])
            desired = ClusterRegister(
                resource_key=command.resource_key, desired_revision=command.desired_revision, **values
            )
            _persist_binding(session, context, command, desired, binding)
        return _binding_result(command)
