"""Label policy for user input and immutable resource identity labels."""

import uuid
from collections.abc import Mapping

CLUSTER_ID_LABEL = "_cluster_id"
EXECUTION_TARGET_ID_LABEL = "_execution_target_id"


def user_labels(labels: Mapping[str, str] | None) -> dict[str, str]:
    """Return only labels that callers are allowed to set."""
    if labels is None:
        return {}
    return {key: value for key, value in labels.items() if not key.startswith("_")}


def labels_with_cluster_id(labels: Mapping[str, str] | None, cluster_id: uuid.UUID) -> dict[str, str]:
    """Merge caller labels with the immutable Cluster identity label."""
    return user_labels(labels) | {CLUSTER_ID_LABEL: str(cluster_id)}


def labels_with_execution_target_id(labels: Mapping[str, str] | None, target_id: uuid.UUID) -> dict[str, str]:
    """Merge caller labels with the immutable ExecutionTarget identity label."""
    return user_labels(labels) | {EXECUTION_TARGET_ID_LABEL: str(target_id)}


def merge_user_labels(existing: Mapping[str, str], incoming: Mapping[str, str]) -> dict[str, str]:
    """Replace user labels while retaining internal labels."""
    internal_labels = {key: value for key, value in existing.items() if key.startswith("_")}
    return internal_labels | user_labels(incoming)
