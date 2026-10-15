"""GitHub Migration Inventory Manager library.

Provides a public interface to track, catalog, and query migration inventory
records across organizations and repositories.
"""

from gh_migration_inventory_manager.core import InventoryManager
from gh_migration_inventory_manager.models import (
    InventoryItem,
    MigrationRecord,
    MigrationStatus,
)

__version__ = "0.1.0"

__all__ = [
    "__version__",
    "InventoryManager",
    "InventoryItem",
    "MigrationRecord",
    "MigrationStatus",
]
