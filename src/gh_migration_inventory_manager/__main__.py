"""CLI entry point executed via python -m gh_migration_inventory_manager."""

import sys

from gh_migration_inventory_manager.cli import main

if __name__ == "__main__":
    sys.exit(main())
