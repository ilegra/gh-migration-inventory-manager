"""Pytest configuration and shared fixtures."""

import pytest

from gh_migration_inventory_manager.core import InventoryManager
from gh_migration_inventory_manager.models import InventoryItem, MigrationStatus


@pytest.fixture
def manager() -> InventoryManager:
    """Fixture providing a fresh InventoryManager instance."""
    return InventoryManager(batch_id="test-batch-001")


@pytest.fixture
def sample_item() -> InventoryItem:
    """Fixture providing a sample InventoryItem."""
    return InventoryItem(
        identifier="org-a/repo-1",
        source_url="https://github.com/org-a/repo-1",
        target_url="https://github.com/org-b/repo-1",
        status=MigrationStatus.PENDING,
        item_type="repository",
    )
