"""Integration test verifying library usage as an external dependency."""

from gh_migration_inventory_manager import (
    InventoryItem,
    InventoryManager,
    MigrationStatus,
    __version__,
)


def test_public_api_exports() -> None:
    """Verify that all public symbols are accessible from top-level package."""
    assert __version__ == "0.1.0"
    assert InventoryManager is not None
    assert InventoryItem is not None
    assert MigrationStatus is not None


def test_end_to_end_inventory_workflow() -> None:
    """Test realistic consumer flow: batch creation, bulk population, filtering."""
    mgr = InventoryManager(batch_id="migration-batch-2026")

    items = [
        InventoryItem(
            identifier=f"org/repo-{i}",
            source_url=f"https://github.com/org/repo-{i}",
            status=MigrationStatus.PENDING,
        )
        for i in range(10)
    ]
    added = mgr.bulk_add(items)
    assert added == 10

    # Advance status for some items
    for i in range(4):
        mgr.update_status(f"org/repo-{i}", MigrationStatus.COMPLETED)

    completed = mgr.list_items(status=MigrationStatus.COMPLETED)
    assert len(completed) == 4

    pending = mgr.list_items(status=MigrationStatus.PENDING)
    assert len(pending) == 6

    summary = mgr.get_summary()
    assert summary["batch_id"] == "migration-batch-2026"
    assert summary["total_items"] == 10
    assert summary["completed_items"] == 4
