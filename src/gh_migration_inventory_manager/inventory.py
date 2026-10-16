"""Inventory domain entities, models, and core management implementation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Protocol


class SpreadsheetClient(Protocol):
    """Protocol representing spreadsheet operations required by Inventory."""

    def find_by(
        self,
        column_name: str | None = None,
        content: str = "",
        *,
        colName: str | None = None,
    ) -> list[Any]: ...

    def add(self, row: Any) -> Any: ...

    def update_row(self, row: Any) -> Any: ...


_PT_MONTHS: dict[int, str] = {
    1: "Janeiro",
    2: "Fevereiro",
    3: "Março",
    4: "Abril",
    5: "Maio",
    6: "Junho",
    7: "Julho",
    8: "Agosto",
    9: "Setembro",
    10: "Outubro",
    11: "Novembro",
    12: "Dezembro",
}


def format_migration_month(dt: datetime | None) -> str:
    """Format a datetime into a localized month/year string (e.g. 'Outubro/2026')."""
    if dt is None:
        return ""
    month_name = _PT_MONTHS.get(dt.month, "")
    return f"{month_name}/{dt.year}" if month_name else ""


class MigrationStatus(str, Enum):
    """Status representing the state of an inventory item during migration."""

    WAITTING = "waitting"
    WAITING = "waitting"
    ACTIVE_IN_BOTH = "active_in_both"
    MIGRATED = "migrated"
    SKIPPED = "skipped"

    def to_sheet_label(self) -> str:
        """Map the status enum to the localized spreadsheet display string."""
        match self:
            case MigrationStatus.MIGRATED:
                return "Migrados (Somente GitHub)"
            case MigrationStatus.ACTIVE_IN_BOTH:
                return "Migrando (Ativos em Ambas)"
            case MigrationStatus.SKIPPED:
                return "Ignorado"
            case _:
                return "Aguardando Migração"


_VALID_STATUS_DISPLAY: str = ", ".join(
    f"'{v}'" for v in sorted({s.value for s in MigrationStatus})
)


@dataclass
class InventoryRecord:
    """Represents an item in the inventory: a repository migrated or to be migrated."""

    name: str
    project: str = ""
    stack: str = ""
    last_commit: datetime | None = None
    gh_migratation: datetime | None = None
    origin_deactivation: datetime | None = None
    migration_status: MigrationStatus = MigrationStatus.WAITTING

    @property
    def gh_migration(self) -> datetime | None:
        """Alias for gh_migratation."""
        return self.gh_migratation

    @gh_migration.setter
    def gh_migration(self, value: datetime | None) -> None:
        self.gh_migratation = value

    @property
    def migration_month(self) -> str:
        """Return the formatted migration month (e.g. 'Outubro/2026')."""
        return format_migration_month(self.gh_migratation)


class Inventory:
    """Core inventory manager backed by a Google Spreadsheet."""

    def __init__(
        self,
        spreadsheet: SpreadsheetClient | Any = None,
        spredsheet: SpreadsheetClient | Any = None,
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

        if status is None:
            raise ValueError(
                "Invalid migration status: None. Status cannot be None. "
                f"Expected a valid MigrationStatus or one of: {_VALID_STATUS_DISPLAY}."
            )

        if isinstance(status, str):
            clean = status.strip()
            if not clean:
                raise ValueError(
                    "Invalid migration status: status cannot be empty. "
                    "Expected a valid MigrationStatus or one of: "
                    f"{_VALID_STATUS_DISPLAY}."
                )

            try:
                return MigrationStatus(clean.lower())
            except ValueError:
                pass

            try:
                return MigrationStatus[clean.upper()]
            except KeyError:
                pass

            raise ValueError(
                f"Invalid migration status: '{status}'. "
                f"Expected a valid MigrationStatus or one of: {_VALID_STATUS_DISPLAY}."
            )

        raise ValueError(
            f"Invalid migration status type: '{type(status).__name__}'. "
            f"Expected MigrationStatus or one of: {_VALID_STATUS_DISPLAY}."
        )

    def update_progress(
        self,
        repository_name: str,
        status: MigrationStatus | str,
        project: str | None = None,
    ) -> InventoryRecord:
        """Update migration progress for a repository in the inventory."""
        from gh_migration_inventory_manager.google import RowConverter

        repo_name = repository_name.strip() if isinstance(repository_name, str) else ""
        if not repo_name:
            raise ValueError("Repository name cannot be empty.")

        valid_status = self._validate_status(status)

        matching_rows = self.spreadsheet.find_by("name", repo_name)

        if not matching_rows:
            record = InventoryRecord(
                name=repo_name,
                project=project or "",
                migration_status=valid_status,
            )
            row_index = None
        else:
            existing_row = matching_rows[0]
            record = RowConverter.to_inventory_record(existing_row)
            if project is not None:
                record.project = project
            record.migration_status = valid_status
            row_index = existing_row.row_index

        if valid_status in (MigrationStatus.ACTIVE_IN_BOTH, MigrationStatus.MIGRATED):
            now = datetime.now(timezone.utc)
            if record.gh_migratation is None:
                record.gh_migratation = now
            if (
                valid_status == MigrationStatus.MIGRATED
                and record.origin_deactivation is None
            ):
                record.origin_deactivation = now
        elif valid_status in (MigrationStatus.WAITTING, MigrationStatus.SKIPPED):
            record.gh_migratation = None
            record.origin_deactivation = None

        row = RowConverter.from_inventory_record(record, row_index=row_index)
        saved_row = (
            self.spreadsheet.update_row(row)
            if row_index is not None
            else self.spreadsheet.add(row)
        )
        return RowConverter.to_inventory_record(saved_row)


Iventory = Inventory
