"""Core inventory manager implementation."""

from __future__ import annotations

from collections.abc import Iterable

from gh_migration_inventory_manager.models import (
    InventoryItem,
    MigrationRecord,
    MigrationStatus,
)


class InventoryManager:
    """Public interface for managing migration inventories and records."""

    def __init__(self, batch_id: str = "default") -> None:
        """Initialize the inventory manager with a given batch identifier."""
        self._record = MigrationRecord(batch_id=batch_id)

    @property
    def batch_id(self) -> str:
        """Return the current batch ID."""
        return self._record.batch_id

    def add_item(
        self,
        identifier: str,
        source_url: str,
        target_url: str | None = None,
        item_type: str = "repository",
        status: MigrationStatus = MigrationStatus.PENDING,
    ) -> InventoryItem:
        """Register a new item into the inventory manager."""
        item = InventoryItem(
            identifier=identifier,
            source_url=source_url,
            target_url=target_url,
            item_type=item_type,
            status=status,
        )
        self._record.add_item(item)
        return item

    def get_item(self, identifier: str) -> InventoryItem | None:
        """Retrieve an inventory item by its identifier."""
        for item in self._record.items:
            if item.identifier == identifier:
                return item
        return None

    def list_items(self, status: MigrationStatus | None = None) -> list[InventoryItem]:
        """List inventory items, optionally filtered by migration status."""
        if status is None:
            return list(self._record.items)
        return [item for item in self._record.items if item.status == status]

    def update_status(
        self, identifier: str, new_status: MigrationStatus
    ) -> InventoryItem:
        """Update migration status for a given inventory item."""
        item = self.get_item(identifier)
        if item is None:
            raise KeyError(f"Item '{identifier}' not found in inventory")

        old_status = item.status
        item.status = new_status

        # Update aggregated stats
        if old_status == MigrationStatus.COMPLETED:
            self._record.completed_items -= 1
        elif old_status == MigrationStatus.FAILED:
            self._record.failed_items -= 1

        if new_status == MigrationStatus.COMPLETED:
            self._record.completed_items += 1
        elif new_status == MigrationStatus.FAILED:
            self._record.failed_items += 1

        return item

    def bulk_add(self, items: Iterable[InventoryItem]) -> int:
        """Add multiple inventory items in bulk and return the count added."""
        count = 0
        for item in items:
            self._record.add_item(item)
            count += 1
        return count

    def get_summary(self) -> dict[str, int | str]:
        """Return an aggregated overview of the inventory."""
        return {
            "batch_id": self._record.batch_id,
            "total_items": self._record.total_items,
            "completed_items": self._record.completed_items,
            "failed_items": self._record.failed_items,
        }
