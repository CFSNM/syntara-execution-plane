"""Built-in eligibility filters. Policy is a no-op pass-through until Isolation Policy lands."""

from __future__ import annotations

from typing import TYPE_CHECKING

from execution_plane.execution_target_reconciler.matching import effective_labels, matches_selectors
from execution_plane.execution_target_reconciler.types import (
    ACTIVE_LIFECYCLE,
    DEGRADED_LIFECYCLE,
    FilterVerdict,
    IneligibilityReason,
)

if TYPE_CHECKING:
    from execution_plane.execution_target_reconciler.types import (
        ClusterSnapshot,
        ExecutionTargetSnapshot,
        WorkRequirements,
    )


class LifecycleFilter:
    """Eligible when the ExecutionTarget is enabled and active or degraded.

    Degraded is a health signal, not a lifecycle denial. `HealthFilter` owns it.
    """

    name = "lifecycle"

    async def evaluate(
        self,
        cluster: ClusterSnapshot,
        target: ExecutionTargetSnapshot,
        requirements: WorkRequirements,
    ) -> tuple[FilterVerdict, IneligibilityReason | None]:
        """Deny disabled or non-schedulable targets. Cluster disable is handled by the reconciler."""
        del cluster, requirements
        if not target.enabled:
            return FilterVerdict.INELIGIBLE, IneligibilityReason.DISABLED
        if target.lifecycle not in {ACTIVE_LIFECYCLE, DEGRADED_LIFECYCLE}:
            return FilterVerdict.INELIGIBLE, IneligibilityReason.LIFECYCLE
        return FilterVerdict.ELIGIBLE, None


class SelectorFilter:
    """Exact AND match of work selectors against the target's effective labels."""

    name = "selector"

    async def evaluate(
        self,
        cluster: ClusterSnapshot,
        target: ExecutionTargetSnapshot,
        requirements: WorkRequirements,
    ) -> tuple[FilterVerdict, IneligibilityReason | None]:
        """Deny when any requested key is missing or has a different value."""
        labels = effective_labels(cluster, target)
        if matches_selectors(labels, requirements.selectors):
            return FilterVerdict.ELIGIBLE, None
        return FilterVerdict.INELIGIBLE, IneligibilityReason.SELECTOR_MISMATCH


class HealthFilter:
    """Deny degraded targets with HEALTH so the scheduler can re-queue them."""

    name = "health"

    async def evaluate(
        self,
        cluster: ClusterSnapshot,
        target: ExecutionTargetSnapshot,
        requirements: WorkRequirements,
    ) -> tuple[FilterVerdict, IneligibilityReason | None]:
        """Exclude degraded targets; active targets remain eligible."""
        del cluster, requirements
        if target.lifecycle == DEGRADED_LIFECYCLE:
            return FilterVerdict.INELIGIBLE, IneligibilityReason.HEALTH
        return FilterVerdict.ELIGIBLE, None


class PolicyFilter:
    """Reserved for Isolation Policy (AAP-92726). Pass-through in MVP."""

    name = "policy"

    async def evaluate(
        self,
        cluster: ClusterSnapshot,
        target: ExecutionTargetSnapshot,
        requirements: WorkRequirements,
    ) -> tuple[FilterVerdict, IneligibilityReason | None]:
        """Return eligible until isolation policy exists. Ignore `workload_type`."""
        del cluster, target, requirements
        return FilterVerdict.ELIGIBLE, None


def default_filters() -> tuple[LifecycleFilter, SelectorFilter, HealthFilter, PolicyFilter]:
    """Built-in chain: lifecycle, selector, health, then no-op policy."""
    return (LifecycleFilter(), SelectorFilter(), HealthFilter(), PolicyFilter())
