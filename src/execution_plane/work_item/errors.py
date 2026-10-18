"""Domain errors raised by Work operations."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import uuid


class WorkItemNotFoundError(LookupError):
    """Raised when a lifecycle transition targets an unknown work item."""

    def __init__(self, item_id: uuid.UUID) -> None:
        """Identify the missing work item."""
        super().__init__(f"Work item {item_id} does not exist")


class DuplicateWorkItemError(ValueError):
    """Raised when a client submits a work item ID that already exists."""

    def __init__(self, item_id: uuid.UUID) -> None:
        """Identify the conflicting ID."""
        super().__init__(f"Work item {item_id} already exists")
