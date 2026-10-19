"""Cluster command label policy without requiring a database."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Self
from unittest.mock import Mock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from execution_plane.commands.clusters import ClusterCommandHandler
from execution_plane.commands.context import EPCommandContext
from execution_plane.models.cluster_binding import ClusterBinding
from execution_plane_client.commands.clusters import ClusterRegister, ClusterUpdate
from execution_plane_client.protocol import MessageEnvelope


class _Session:
    def __init__(self, binding: ClusterBinding | None = None) -> None:
        self.binding = binding
        self.added: ClusterBinding | None = None

    def add(self, binding: ClusterBinding) -> None:
        self.added = binding

    async def execute(self, _statement: object) -> None:
        return None

    async def get(self, _model: object, _key: tuple[str, object], **_kwargs: object) -> ClusterBinding | None:
        return self.binding


class _Transaction:
    def __init__(self, session: _Session) -> None:
        self.session = session

    async def __aenter__(self) -> _Session:
        return self.session

    async def __aexit__(self, *_: object) -> None:
        return None


class _SessionMaker:
    def __init__(self, session: _Session) -> None:
        self.session = session

    def __call__(self, *_: object, **_kwargs: object) -> Self:
        return self

    def begin(self) -> _Transaction:
        return _Transaction(self.session)


def _context(monkeypatch: pytest.MonkeyPatch, session: _Session) -> EPCommandContext:
    monkeypatch.setattr(
        "execution_plane.commands.clusters.async_sessionmaker",
        lambda *_args, **_kwargs: _SessionMaker(session),
    )
    return EPCommandContext(client_id="ao", engine=Mock(spec=AsyncEngine))


@pytest.mark.asyncio
async def test_cluster_register_cleanses_internal_labels(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _Session()
    payload = ClusterRegister(
        resource_key=uuid4(),
        desired_revision=1,
        name="cluster-a",
        endpoint="https://cluster.example",
        namespace="ep",
        credential="secret",
        labels={"team": "platform", "_cluster_id": "spoofed", "_internal": "spoofed"},
    )

    await ClusterCommandHandler().handle(
        Mock(spec=MessageEnvelope),
        payload,
        _context(monkeypatch, session),
    )

    assert session.added is not None
    assert session.added.labels == {"team": "platform"}


@pytest.mark.asyncio
async def test_cluster_update_cleanses_internal_labels(monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime.now(UTC)
    key = uuid4()
    binding = ClusterBinding(
        client_id="ao",
        source_integration_id=key,
        revision=1,
        name="cluster-a",
        endpoint="https://cluster.example",
        namespace="ep",
        credential="secret",
        labels={"team": "old"},
        created_at=now,
        updated_at=now,
    )
    session = _Session(binding)
    payload = ClusterUpdate(
        resource_key=key,
        desired_revision=2,
        labels={"team": "new", "_cluster_id": "spoofed", "_internal": "spoofed"},
    )

    await ClusterCommandHandler().handle(
        Mock(spec=MessageEnvelope),
        payload,
        _context(monkeypatch, session),
    )

    assert binding.labels == {"team": "new"}
