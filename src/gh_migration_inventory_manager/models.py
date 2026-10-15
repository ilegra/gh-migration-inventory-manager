"""Data models and entities for GitHub Migration Inventory Manager."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class MigrationStatus(str, Enum):
    """Status representing the state of an inventory item during migration."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class InventoryItem:
    """Represents an individual resource in the migration inventory."""

    identifier: str
    source_url: str
    target_url: str | None = None
    status: MigrationStatus = MigrationStatus.PENDING
    item_type: str = "repository"
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> dict[str, Any]:
        """Convert inventory item to a JSON-serializable dictionary."""
        data = asdict(self)
        data["status"] = self.status.value
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> InventoryItem:
        """Create an InventoryItem from a dictionary."""
        item_data = dict(data)
        if "status" in item_data and isinstance(item_data["status"], str):
            item_data["status"] = MigrationStatus(item_data["status"])
        return cls(**item_data)


@dataclass
class MigrationRecord:
    """Represents a migration summary record for an inventory batch."""

    batch_id: str
    total_items: int = 0
    completed_items: int = 0
    failed_items: int = 0
    items: list[InventoryItem] = field(default_factory=list)

    def add_item(self, item: InventoryItem) -> None:
        """Add an item to the record and update statistics."""
        self.items.append(item)
        self.total_items += 1
        if item.status == MigrationStatus.COMPLETED:
            self.completed_items += 1
        elif item.status == MigrationStatus.FAILED:
            self.failed_items += 1
