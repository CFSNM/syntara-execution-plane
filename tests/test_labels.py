"""Unit coverage for the Cluster and ExecutionTarget label policy."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from uuid import UUID, uuid4

import pytest

from execution_plane.labels import (
    CLUSTER_ID_LABEL,
    EXECUTION_TARGET_ID_LABEL,
    labels_with_cluster_id,
    labels_with_execution_target_id,
    merge_user_labels,
    user_labels,
)

LabelFactory = Callable[[Mapping[str, str] | None, UUID], dict[str, str]]


def test_user_labels_remove_internal_keys_and_accept_none() -> None:
    labels = {"region": "eu-west", "_cluster_id": "spoofed", "_internal": "spoofed"}

    assert user_labels(labels) == {"region": "eu-west"}
    assert user_labels(None) == {}


@pytest.mark.parametrize(
    ("identity_function", "identity_label"),
    [
        (labels_with_cluster_id, CLUSTER_ID_LABEL),
        (labels_with_execution_target_id, EXECUTION_TARGET_ID_LABEL),
    ],
)
def test_identity_labels_use_the_resource_primary_key(identity_function: LabelFactory, identity_label: str) -> None:
    resource_id = uuid4()

    result = identity_function({"region": "eu-west", identity_label: "spoofed", "_internal": "spoofed"}, resource_id)

    assert result == {"region": "eu-west", identity_label: str(resource_id)}


def test_merge_user_labels_replace_public_labels_and_preserve_internal_labels() -> None:
    existing = {"_cluster_id": "cluster-id", "_internal": "internal", "region": "old", "team": "old"}
    incoming = {"region": "new", "_cluster_id": "spoofed", "_internal": "spoofed"}

    result = merge_user_labels(existing, incoming)

    assert result == {"_cluster_id": "cluster-id", "_internal": "internal", "region": "new"}
