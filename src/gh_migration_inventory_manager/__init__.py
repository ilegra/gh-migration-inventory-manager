"""GitHub Migration Inventory Manager library.

Provides a clean interface to track, catalog, and update repository migration
records backed by Google Sheets.
"""

from gh_migration_inventory_manager.converter import RepositoryConverter
from gh_migration_inventory_manager.google import (
    Credentials,
    Row,
    RowConverter,
    Spreadsheet,
    SpreadsheetAccessError,
    SpreadsheetError,
    SpreadsheetNotFoundError,
    Spreadshseet,
)
from gh_migration_inventory_manager.inventory import (
    Inventory,
    InventoryRecord,
    Iventory,
    MigrationStatus,
)
from gh_migration_inventory_manager.scrap import (
    AuthCredentials,
    Authenticator,
    BasicAuthenticator,
    Repository,
    RepositoryScraper,
    ScraperAuthenticationError,
    ScraperConnectionError,
    ScraperError,
    ScraperProvider,
    ScraperRegistry,
    Stack,
    TokenAuthenticator,
    default_scraper_registry,
    format_stacks,
)
from gh_migration_inventory_manager.svn_scrap import (
    SvnClient,
    SvnEntry,
    SvnLayoutType,
    SvnRepositoryScraper,
    SvnScraperProvider,
    SvnWebDavClient,
)

__version__ = "0.1.0"

__all__ = [
    "__version__",
    "Inventory",
    "Iventory",
    "Spreadsheet",
    "Spreadshseet",
    "SpreadsheetError",
    "SpreadsheetNotFoundError",
    "SpreadsheetAccessError",
    "Row",
    "RowConverter",
    "Credentials",
    "InventoryRecord",
    "MigrationStatus",
    "RepositoryConverter",
    "Stack",
    "Repository",
    "RepositoryScraper",
    "ScraperError",
    "ScraperAuthenticationError",
    "ScraperConnectionError",
    "AuthCredentials",
    "Authenticator",
    "BasicAuthenticator",
    "TokenAuthenticator",
    "ScraperProvider",
    "ScraperRegistry",
    "default_scraper_registry",
    "format_stacks",
    "SvnClient",
    "SvnEntry",
    "SvnLayoutType",
    "SvnRepositoryScraper",
    "SvnScraperProvider",
    "SvnWebDavClient",
]
