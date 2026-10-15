"""Core inventory manager implementation."""

from __future__ import annotations

from datetime import datetime, timezone

from gh_migration_inventory_manager.google import Spreadsheet
from gh_migration_inventory_manager.models import InventoryRecord, MigrationStatus


class Inventory:
    """Core inventory manager backed by a Google Spreadsheet."""

    def __init__(
        self,
        spreadsheet: Spreadsheet | None = None,
        spredsheet: Spreadsheet | None = None,
    ) -> None:
        target = spreadsheet if spreadsheet is not None else spredsheet
        if target is None:
            raise ValueError("A Spreadsheet instance must be provided.")
        self.spreadsheet = target

    def update_progress(
        self, repository_name: str, status: MigrationStatus
    ) -> InventoryRecord:
        """Update migration progress for a repository in the inventory."""
        repo_name = repository_name.strip()
        if not repo_name:
            raise ValueError("Repository name cannot be empty.")

        # 1. Check if repository exists in the inventory
        matching_rows = self.spreadsheet.find_by("name", repo_name)

        now = datetime.now(timezone.utc)

        # 2. Add a new entry if repository is not there
        if not matching_rows:
            record = InventoryRecord(
                name=repo_name,
                migration_status=status,
            )
            if status == MigrationStatus.ACTIVE_IN_BOTH:
                record.gh_migratation = now
                record.origin_deactivation = None
            elif status == MigrationStatus.MIGRATED:
                record.gh_migratation = now
                record.origin_deactivation = now
            elif status in (MigrationStatus.WAITTING, MigrationStatus.SKIPPED):
                record.gh_migratation = None
                record.origin_deactivation = None

            from gh_migration_inventory_manager.google import Row

            new_row = Row.from_inventory_record(record)
            saved_row = self.spreadsheet.add(new_row)
            return saved_row.to_inventory_record()

        # 3. Update the current entry if record exists
        existing_row = matching_rows[0]
        record = existing_row.to_inventory_record()
        record.migration_status = status

        if status == MigrationStatus.ACTIVE_IN_BOTH:
            if record.gh_migratation is None:
                record.gh_migratation = now
        elif status == MigrationStatus.MIGRATED:
            if record.gh_migratation is None:
                record.gh_migratation = now
            if record.origin_deactivation is None:
                record.origin_deactivation = now
        elif status == MigrationStatus.WAITTING:
            record.gh_migratation = None
            record.origin_deactivation = None

        from gh_migration_inventory_manager.google import Row

        updated_row = Row.from_inventory_record(
            record, row_index=existing_row.row_index
        )
        saved_row = self.spreadsheet.update_row(updated_row)
        return saved_row.to_inventory_record()


# Compatibility alias for skeleton typo
Iventory = Inventory
