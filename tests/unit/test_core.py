"""Unit tests for InventoryManager and InventoryItem."""

import pytest

from gh_migration_inventory_manager.core import InventoryManager
from gh_migration_inventory_manager.models import InventoryItem, MigrationStatus


def test_inventory_item_dict_serialization() -> None:
    """Test InventoryItem serialization to and from dictionary."""
    item = InventoryItem(
        identifier="repo-test",
        source_url="https://github.com/source/repo-test",
        target_url="https://github.com/target/repo-test",
        status=MigrationStatus.IN_PROGRESS,
    )
    data = item.to_dict()
    assert data["identifier"] == "repo-test"
    assert data["status"] == "in_progress"

    restored = InventoryItem.from_dict(data)
    assert restored.identifier == item.identifier
    assert restored.status == MigrationStatus.IN_PROGRESS


def test_add_and_retrieve_item(manager: InventoryManager) -> None:
    """Test registering an item and retrieving it."""
    item = manager.add_item(
        identifier="my-org/backend",
        source_url="https://github.com/my-org/backend",
    )
    assert item.identifier == "my-org/backend"
    assert item.status == MigrationStatus.PENDING

    retrieved = manager.get_item("my-org/backend")
    assert retrieved is not None
    assert retrieved.identifier == "my-org/backend"


def test_update_status_and_summary(manager: InventoryManager) -> None:
    """Test updating status and verifying aggregated stats."""
    manager.add_item(
        identifier="my-org/repo-1",
        source_url="https://github.com/my-org/repo-1",
    )
    manager.add_item(
        identifier="my-org/repo-2",
        source_url="https://github.com/my-org/repo-2",
    )

    summary = manager.get_summary()
    assert summary["total_items"] == 2
    assert summary["completed_items"] == 0

    manager.update_status("my-org/repo-1", MigrationStatus.COMPLETED)
    manager.update_status("my-org/repo-2", MigrationStatus.FAILED)

    updated_summary = manager.get_summary()
    assert updated_summary["completed_items"] == 1
    assert updated_summary["failed_items"] == 1


def test_update_missing_item_raises(manager: InventoryManager) -> None:
    """Test updating a non-existent item raises KeyError."""
    with pytest.raises(KeyError, match="not found"):
        manager.update_status("non-existent", MigrationStatus.COMPLETED)
