"""Shared builders for Resource Monitor tests."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from execution_plane.config import EPSettings, get_ep_settings
from execution_plane.models.cluster import Cluster, ClusterStatus, ClusterType
from execution_plane.models.execution_target import BackendType, ExecutionTarget, TargetStatus
from execution_plane.models.execution_target_placement import KubernetesPlacement, RHELPlacement


def settings(**overrides: float) -> EPSettings:
    return get_ep_settings().model_copy(update=overrides)


def make_cluster(
    *,
    endpoint: str = "https://cluster.example:6443",
    cluster_type: ClusterType = ClusterType.OPENSHIFT,
    status: ClusterStatus = ClusterStatus.ACTIVE,
    enabled: bool = True,
    api_key: str = "cluster-token",
    ca_certificate: str | None = None,
) -> Cluster:
    now = datetime.now(UTC)
    return Cluster(
        id=uuid.uuid4(),
        name="ocp",
        endpoint=endpoint,
        api_key=api_key,
        ca_certificate=ca_certificate,
        status=status,
        enabled=enabled,
        cluster_type=cluster_type,
        created_by=uuid.uuid4(),
        created_at=now,
        updated_by=uuid.uuid4(),
        updated_at=now,
    )


def make_target(
    cluster: Cluster,
    *,
    name: str = "ep-default",
    status: TargetStatus = TargetStatus.ACTIVE,
    enabled: bool = True,
    namespace: str = "ao-execution",
    rhel: bool = False,
    status_message: str | None = None,
) -> ExecutionTarget:
    now = datetime.now(UTC)
    return ExecutionTarget(
        id=uuid.uuid4(),
        cluster_id=cluster.id,
        name=name,
        backend_type=BackendType.VANILLA_K8S,
        endpoint="https://target.example",
        placement=RHELPlacement() if rhel else KubernetesPlacement(namespace=namespace),
        api_key="target-secret",
        status=status,
        enabled=enabled,
        status_message=status_message,
        created_by=uuid.uuid4(),
        created_at=now,
        updated_by=uuid.uuid4(),
        updated_at=now,
    )
