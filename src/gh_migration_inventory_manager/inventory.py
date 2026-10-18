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

    def _find_row(self, name: str, project: str | None = None) -> Any | None:
        """Find a row matching repository name and optional project."""
        clean_name = name.strip() if isinstance(name, str) else ""
        if not clean_name:
            raise ValueError("Repository name cannot be empty.")

        matching_rows = self.spreadsheet.find_by("name", clean_name)
        if not matching_rows:
            return None

        if project is not None:
            clean_proj = project.strip().lower()
            for r in matching_rows:
                if str(r.data.get("project", "")).strip().lower() == clean_proj:
                    return r

        return matching_rows[0]

    @staticmethod
    def _apply_status_timestamps(
        record: InventoryRecord, status: MigrationStatus
    ) -> None:
        """Update migration timestamps based on transition status."""
        if status in (MigrationStatus.ACTIVE_IN_BOTH, MigrationStatus.MIGRATED):
            now = datetime.now(timezone.utc)
            if record.gh_migratation is None:
                record.gh_migratation = now
            if (
                status == MigrationStatus.MIGRATED
                and record.origin_deactivation is None
            ):
                record.origin_deactivation = now
        elif status in (MigrationStatus.WAITTING, MigrationStatus.SKIPPED):
            record.gh_migratation = None
            record.origin_deactivation = None

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
        existing_row = self._find_row(repo_name, project)

        if not existing_row:
            record = InventoryRecord(
                name=repo_name,
                project=project or "",
                migration_status=valid_status,
            )
            row_index = None
        else:
            record = RowConverter.to_inventory_record(existing_row)
            if project is not None:
                record.project = project
            record.migration_status = valid_status
            row_index = existing_row.row_index

        self._apply_status_timestamps(record, valid_status)

        row = RowConverter.from_inventory_record(record, row_index=row_index)
        saved_row = (
            self.spreadsheet.update_row(row)
            if row_index is not None
            else self.spreadsheet.add(row)
        )
        return RowConverter.to_inventory_record(saved_row)

    def get_repository(
        self,
        repository_name: str,
        project: str | None = None,
    ) -> InventoryRecord | None:
        """Find an inventory record by repository name (and optional project)."""
        from gh_migration_inventory_manager.google import RowConverter

        row = self._find_row(repository_name, project)
        return RowConverter.to_inventory_record(row) if row is not None else None

    def register_repository(
        self,
        name: str | Any,
        project: str | None = None,
        stack: str | None = None,
        last_commit: datetime | None = None,
        initial_status: MigrationStatus | str = MigrationStatus.WAITTING,
    ) -> InventoryRecord:
        """Register or synchronize a repository in the inventory.

        If the repository is already recorded, its migration status and dates
        are strictly preserved, while metadata (last_commit, stack) are updated.
        If it does not exist, a new record is created with initial_status.

        Supports primitive arguments or an object with name, project, stacks/stack,
        and last_commit attributes (such as a Repository instance).
        """
        from gh_migration_inventory_manager.google import RowConverter

        if not isinstance(name, str) and hasattr(name, "name"):
            repo_obj = name
            actual_name = str(repo_obj.name)
            actual_project = (
                project if project is not None else getattr(repo_obj, "project", None)
            )
            if stack is not None:
                actual_stack = stack
            elif hasattr(repo_obj, "stacks") and isinstance(repo_obj.stacks, list):
                from gh_migration_inventory_manager.scrap import format_stacks

                actual_stack = format_stacks(repo_obj.stacks)
            else:
                actual_stack = str(getattr(repo_obj, "stack", "") or "")
            actual_last_commit = (
                last_commit
                if last_commit is not None
                else getattr(repo_obj, "last_commit", None)
            )
        else:
            actual_name = str(name).strip() if name is not None else ""
            actual_project = project
            actual_stack = stack or ""
            actual_last_commit = last_commit

        if not actual_name:
            raise ValueError("Repository name cannot be empty.")

        valid_initial_status = self._validate_status(initial_status)
        target_row = self._find_row(actual_name, actual_project)

        if target_row is not None:
            record = RowConverter.to_inventory_record(target_row)
            changed = False
            if actual_project is not None and record.project != actual_project:
                record.project = actual_project
                changed = True
            if actual_stack and record.stack != actual_stack:
                record.stack = actual_stack
                changed = True
            if (
                actual_last_commit is not None
                and record.last_commit != actual_last_commit
            ):
                record.last_commit = actual_last_commit
                changed = True

            if changed:
                row = RowConverter.from_inventory_record(
                    record, row_index=target_row.row_index
                )
                saved_row = self.spreadsheet.update_row(row)
                return RowConverter.to_inventory_record(saved_row)
            return record

        record = InventoryRecord(
            name=actual_name,
            project=actual_project or "",
            stack=actual_stack,
            last_commit=actual_last_commit,
            migration_status=valid_initial_status,
        )
        self._apply_status_timestamps(record, valid_initial_status)
        row = RowConverter.from_inventory_record(record)
        saved_row = self.spreadsheet.add(row)
        return RowConverter.to_inventory_record(saved_row)

    def sync_repositories(
        self,
        repositories: Any,
        initial_status: MigrationStatus | str = MigrationStatus.WAITTING,
    ) -> list[InventoryRecord]:
        """Synchronize a collection of repositories into the inventory."""
        return [
            self.register_repository(repo, initial_status=initial_status)
            for repo in repositories
        ]


Iventory = Inventory
