"""GitHub Migration Inventory Manager library.

Provides a clean interface to track, catalog, and update repository migration
records backed by Google Sheets.
"""

from gh_migration_inventory_manager.google import (
    Credentials,
    Row,
    RowConverter,
    Spreadsheet,
    Spreadshseet,
)
from gh_migration_inventory_manager.inventory import (
    Inventory,
    InventoryRecord,
    Iventory,
    MigrationStatus,
)
from gh_migration_inventory_manager.scraper import (
    AuthCredentials,
    Authenticator,
    Repository,
    RepositoryScraper,
    ScraperAuthenticationError,
    ScraperConnectionError,
    ScraperError,
    Stack,
    TokenAuthenticator,
)

__version__ = "0.1.0"

__all__ = [
    "__version__",
    "Inventory",
    "Iventory",
    "Spreadsheet",
    "Spreadshseet",
    "Row",
    "RowConverter",
    "Credentials",
    "InventoryRecord",
    "MigrationStatus",
    "Stack",
    "Repository",
    "RepositoryScraper",
    "ScraperError",
    "ScraperAuthenticationError",
    "ScraperConnectionError",
    "AuthCredentials",
    "Authenticator",
    "TokenAuthenticator",
]
