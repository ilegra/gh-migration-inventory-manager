"""Core inventory manager implementation."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from gh_migration_inventory_manager.google import RowConverter, Spreadsheet
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

    @staticmethod
    def _from_sheet_label(label: str) -> MigrationStatus:
        """Parse spreadsheet label into the corresponding MigrationStatus."""
        clean = (label or "").strip().lower()
        if not clean:
            return MigrationStatus.WAITTING
        if "somente github" in clean or clean in ("migrated", "migrados"):
            return MigrationStatus.MIGRATED
        if (
            "ativos em ambas" in clean
            or "migrando" in clean
            or clean == "active_in_both"
        ):
            return MigrationStatus.ACTIVE_IN_BOTH
        if "ignorado" in clean or "não migrar" in clean or clean == "skipped":
            return MigrationStatus.SKIPPED
        if "aguardando" in clean or clean in ("waitting", "waiting"):
            return MigrationStatus.WAITTING

        try:
            return MigrationStatus(clean)
        except ValueError as err:
            raise ValueError(f"Invalid migration status label: '{label}'.") from err

    @staticmethod
    def _validate_status(status: Any) -> MigrationStatus:
        """Validate that status is a valid MigrationStatus or valid status string.

        Raises:
            ValueError: If status is not provided, is of an invalid type,
                or is not a recognized migration status.
        """
        if isinstance(status, MigrationStatus):
            return status

        valid_values = sorted({s.value for s in MigrationStatus})
        valid_display = ", ".join(f"'{v}'" for v in valid_values)

        if status is None:
            raise ValueError(
                f"Invalid migration status: None. Status cannot be None. "
                f"Expected a valid MigrationStatus or one of: {valid_display}."
            )

        if isinstance(status, str):
            clean = status.strip()
            if not clean:
                raise ValueError(
                    f"Invalid migration status: status cannot be empty. "
                    f"Expected a valid MigrationStatus or one of: {valid_display}."
                )

            # Match against enum values (e.g. "waitting", "migrated", etc.)
            try:
                return MigrationStatus(clean.lower())
            except ValueError:
                pass

            # Match against enum member names (e.g. "WAITING", "MIGRATED", etc.)
            try:
                return MigrationStatus[clean.upper()]
            except KeyError:
                pass

            raise ValueError(
                f"Invalid migration status: '{status}'. "
                f"Expected a valid MigrationStatus or one of: {valid_display}."
            )

        raise ValueError(
            f"Invalid migration status type: '{type(status).__name__}'. "
            f"Expected MigrationStatus or one of: {valid_display}."
        )

    def update_progress(
        self, repository_name: str, status: MigrationStatus | str
    ) -> InventoryRecord:
        """Update migration progress for a repository in the inventory."""
        repo_name = repository_name.strip() if isinstance(repository_name, str) else ""
        if not repo_name:
            raise ValueError("Repository name cannot be empty.")

        valid_status = self._validate_status(status)

        matching_rows = self.spreadsheet.find_by("name", repo_name)
        now = datetime.now(timezone.utc)

        if not matching_rows:
            record = InventoryRecord(name=repo_name, migration_status=valid_status)
            row_index = None
        else:
            existing_row = matching_rows[0]
            record = RowConverter.to_inventory_record(existing_row)
            record.migration_status = valid_status
            row_index = existing_row.row_index

        if valid_status == MigrationStatus.ACTIVE_IN_BOTH:
            if record.gh_migratation is None:
                record.gh_migratation = now
        elif valid_status == MigrationStatus.MIGRATED:
            if record.gh_migratation is None:
                record.gh_migratation = now
            if record.origin_deactivation is None:
                record.origin_deactivation = now
        elif valid_status in (MigrationStatus.WAITTING, MigrationStatus.WAITING):
            record.gh_migratation = None
            record.origin_deactivation = None
        elif valid_status == MigrationStatus.SKIPPED:
            record.gh_migratation = None
            record.origin_deactivation = None

        row = RowConverter.from_inventory_record(record, row_index=row_index)
        saved_row = (
            self.spreadsheet.update_row(row)
            if row_index is not None
            else self.spreadsheet.add(row)
        )
        return RowConverter.to_inventory_record(saved_row)


# Compatibility alias for skeleton typo
Iventory = Inventory
