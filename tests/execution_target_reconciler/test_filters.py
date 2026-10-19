"""Lifecycle, selector, health, and no-op policy filters."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from execution_plane.execution_target_reconciler.filters import (
    HealthFilter,
    LifecycleFilter,
    PolicyFilter,
    SelectorFilter,
)
from execution_plane.execution_target_reconciler.types import (
    ClusterSnapshot,
    ExecutionTargetSnapshot,
    FilterVerdict,
    IneligibilityReason,
    WorkRequirements,
)

MakeCluster = Callable[..., ClusterSnapshot]
MakeTarget = Callable[..., ExecutionTargetSnapshot]


@pytest.mark.asyncio
async def test_lifecycle_filter_denies_disabled_before_non_active(
    make_cluster: MakeCluster, make_target: MakeTarget
) -> None:
    cluster = make_cluster()
    disabled = make_target(cluster, enabled=False, lifecycle="failed")
    verdict, reason = await LifecycleFilter().evaluate(cluster, disabled, WorkRequirements())
    assert verdict is FilterVerdict.INELIGIBLE
    assert reason is IneligibilityReason.DISABLED


@pytest.mark.asyncio
async def test_lifecycle_filter_denies_non_active_enabled_targets(
    make_cluster: MakeCluster, make_target: MakeTarget
) -> None:
    cluster = make_cluster()
    failed = make_target(cluster, lifecycle="failed")
    verdict, reason = await LifecycleFilter().evaluate(cluster, failed, WorkRequirements())
    assert verdict is FilterVerdict.INELIGIBLE
    assert reason is IneligibilityReason.LIFECYCLE


@pytest.mark.asyncio
async def test_lifecycle_filter_allows_degraded_enabled_targets(
    make_cluster: MakeCluster, make_target: MakeTarget
) -> None:
    cluster = make_cluster()
    degraded = make_target(cluster, lifecycle="degraded")
    verdict, reason = await LifecycleFilter().evaluate(cluster, degraded, WorkRequirements())
    assert verdict is FilterVerdict.ELIGIBLE
    assert reason is None


@pytest.mark.asyncio
async def test_lifecycle_filter_allows_active_enabled_targets(
    make_cluster: MakeCluster, make_target: MakeTarget
) -> None:
    cluster = make_cluster()
    target = make_target(cluster)
    verdict, reason = await LifecycleFilter().evaluate(cluster, target, WorkRequirements())
    assert verdict is FilterVerdict.ELIGIBLE
    assert reason is None


@pytest.mark.asyncio
async def test_selector_filter_matches_effective_labels(make_cluster: MakeCluster, make_target: MakeTarget) -> None:
    cluster = make_cluster(labels={"region": "us-east-1"})
    target = make_target(cluster, labels={"env": "production"})
    filt = SelectorFilter()
    hit = WorkRequirements(selectors={"region": "us-east-1", "env": "production"})
    miss = WorkRequirements(selectors={"region": "us-east-1", "env": "staging"})
    verdict, reason = await filt.evaluate(cluster, target, hit)
    assert verdict is FilterVerdict.ELIGIBLE
    assert reason is None
    verdict, reason = await filt.evaluate(cluster, target, miss)
    assert verdict is FilterVerdict.INELIGIBLE
    assert reason is IneligibilityReason.SELECTOR_MISMATCH


@pytest.mark.asyncio
async def test_health_filter_denies_degraded_with_health(make_cluster: MakeCluster, make_target: MakeTarget) -> None:
    cluster = make_cluster()
    degraded = make_target(cluster, lifecycle="degraded")
    verdict, reason = await HealthFilter().evaluate(cluster, degraded, WorkRequirements())
    assert verdict is FilterVerdict.INELIGIBLE
    assert reason is IneligibilityReason.HEALTH


@pytest.mark.asyncio
async def test_health_filter_allows_active_targets(make_cluster: MakeCluster, make_target: MakeTarget) -> None:
    cluster = make_cluster()
    target = make_target(cluster)
    verdict, reason = await HealthFilter().evaluate(cluster, target, WorkRequirements())
    assert verdict is FilterVerdict.ELIGIBLE
    assert reason is None


@pytest.mark.asyncio
async def test_policy_filter_is_pass_through(make_cluster: MakeCluster, make_target: MakeTarget) -> None:
    cluster = make_cluster()
    target = make_target(cluster, lifecycle="failed", enabled=False)
    requirements = WorkRequirements(selectors={"gpu": "true"}, workload_type="agent")
    verdict, reason = await PolicyFilter().evaluate(cluster, target, requirements)
    assert verdict is FilterVerdict.ELIGIBLE
    assert reason is None
