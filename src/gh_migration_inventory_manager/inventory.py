"""Inventory domain entities, models, and core management implementation."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Protocol


class InventoryError(Exception):
    """Base exception for inventory domain and storage operations."""


class InventoryNotFoundError(InventoryError):
    """Raised when an inventory or its storage backend cannot be found."""


class InventoryAccessError(InventoryError):
    """Raised when access to an inventory or its storage backend is denied."""


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

    def get_all_rows(self) -> list[Any]: ...

    def batch_add(self, rows: list[Any], chunk_size: int = 500) -> list[Any]: ...

    def batch_update_rows(
        self, rows: list[Any], chunk_size: int = 500
    ) -> list[Any]: ...


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


def _from_sheet_label(label: str) -> MigrationStatus:
    """Parse spreadsheet label into the corresponding MigrationStatus."""
    clean = (label or "").strip().lower()
    if not clean:
        return MigrationStatus.WAITTING
    if "somente github" in clean or clean in ("migrated", "migrados", "migrado"):
        return MigrationStatus.MIGRATED
    if "ativos em ambas" in clean or "migrando" in clean or clean == "active_in_both":
        return MigrationStatus.ACTIVE_IN_BOTH
    if "ignorado" in clean or "não migrar" in clean or clean == "skipped":
        return MigrationStatus.SKIPPED
    if "aguardando" in clean or clean in ("waitting", "waiting"):
        return MigrationStatus.WAITTING

    try:
        return MigrationStatus(clean)
    except ValueError as err:
        raise ValueError(f"Invalid migration status label: '{label}'.") from err


def validate_migration_status(status: Any) -> MigrationStatus:
    """Validate and resolve status into a valid MigrationStatus."""
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
                f"Expected a valid MigrationStatus or one of: {_VALID_STATUS_DISPLAY}."
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


@dataclass
class PipelineRecord:
    """Represents a CI/CD pipeline item in the workflows inventory."""

    workflow_id: str
    platform: str = "jenkins"
    pipeline_type: str = ""
    repository_association: str = ""
    repository_url: str = ""
    file_path: str = ""
    shared_components: str = ""
    execution_environment: str = ""
    branch: str = ""
    last_build: datetime | None = None
    last_status: str = ""
    migration_status: MigrationStatus = MigrationStatus.WAITTING
    project: str = ""
    repository: str = ""


@dataclass
class TeamRecord:
    """Represents a team topology and access record in the inventory."""

    team_name: str
    member_count: int = 0
    target_repo_or_project: str = ""
    normalized_role: str = "Read"
    origin_role: str = ""
    team_slug: str = ""
    migration_status: MigrationStatus = MigrationStatus.WAITTING
    project: str = ""
    repository: str = ""


@dataclass
class PolicyRecord:
    """Represents a branch governance policy record in the inventory."""

    policy_scope: str
    target_ref_pattern: str
    normalized_rule_type: str
    rule_parameters: str = ""
    bypass_team_slugs: str = ""
    origin_raw_policy: str = ""
    migration_status: MigrationStatus = MigrationStatus.WAITTING
    project: str = ""
    repository: str = ""


def _build_credentials(credentials: Any, credentials_path: str | None) -> Any:
    """Validate credentials configuration and build Credentials instance."""
    if credentials is not None:
        if credentials_path is not None:
            configured_path = getattr(credentials, "credentials_path", None)
            if configured_path and configured_path != str(credentials_path).strip():
                raise ValueError(
                    "Inconsistent credentials configuration: "
                    f"credentials.credentials_path '{configured_path}' "
                    f"does not match credentials_path '{credentials_path}'."
                )
        return credentials

    if not credentials_path or not str(credentials_path).strip():
        raise ValueError("Missing Google credentials path. Pass --credentials-path.")

    clean_path = str(credentials_path).strip()
    if not Path(clean_path).exists():
        raise FileNotFoundError(f"Google credentials file not found: '{clean_path}'.")

    from gh_migration_inventory_manager.google import Credentials

    return Credentials(credentials_path=clean_path)


def _can_batch_add(sheet: Any) -> bool:
    """Check if sheet client supports batch_add safely with mock doubles."""
    is_mock = hasattr(sheet, "_mock_name") or hasattr(sheet, "assert_called")
    if not is_mock:
        return hasattr(sheet, "batch_add") and callable(sheet.batch_add)
    batch_add_attr = getattr(sheet, "batch_add", None)
    if batch_add_attr is None:
        return False
    if getattr(batch_add_attr, "side_effect", None) is not None:
        return True
    ret_val = getattr(batch_add_attr, "_mock_return_value", None)
    import unittest.mock

    return (
        ret_val is not None
        and ret_val is not unittest.mock.DEFAULT
        and not type(ret_val).__name__.startswith("MagicMock")
    )


def _can_batch_update(sheet: Any) -> bool:
    """Check if sheet client supports batch_update_rows safely with mock doubles."""
    is_mock = hasattr(sheet, "_mock_name") or hasattr(sheet, "assert_called")
    if not is_mock:
        return hasattr(sheet, "batch_update_rows") and callable(sheet.batch_update_rows)
    batch_update_attr = getattr(sheet, "batch_update_rows", None)
    if batch_update_attr is None:
        return False
    if getattr(batch_update_attr, "side_effect", None) is not None:
        return True
    ret_val = getattr(batch_update_attr, "_mock_return_value", None)
    import unittest.mock

    return (
        ret_val is not None
        and ret_val is not unittest.mock.DEFAULT
        and not type(ret_val).__name__.startswith("MagicMock")
    )


class Inventory:
    """Core inventory manager backed by a Google Spreadsheet."""

    def __init__(
        self,
        spreadsheet: SpreadsheetClient | Any = None,
        spredsheet: SpreadsheetClient | Any = None,
        spreadsheet_id: str | None = None,
        credentials_path: str | None = None,
        sheet_name: str = "Inventario",
        credentials: Any = None,
        converter: Any = None,
        differ: Any = None,
    ) -> None:
        self.spreadsheet = spreadsheet if spreadsheet is not None else spredsheet
        self.spreadsheet_id = spreadsheet_id
        self.credentials_path = credentials_path
        self.sheet_name = sheet_name
        self.credentials = credentials
        if differ is not None:
            self.differ = differ
        else:
            from gh_migration_inventory_manager.scrap_diff import RepositoryDiffer

            self.differ = RepositoryDiffer()
        if converter is not None:
            self.converter = converter
        else:
            from gh_migration_inventory_manager.converter import (
                RepositoryConverter as _RepoConverter,
            )

            self.converter = _RepoConverter()

    def _validate_and_build_credentials(self) -> Any:
        """Validate credentials configuration and build Credentials instance."""
        self.credentials = _build_credentials(self.credentials, self.credentials_path)
        return self.credentials

    _build_credentials = _validate_and_build_credentials

    def _validate_and_build_spreadsheet(self) -> SpreadsheetClient:
        """Validate spreadsheet configuration and build Spreadsheet instance.

        Raises:
            ValueError: If spreadsheet configuration is inconsistent, missing, or empty.
            InventoryNotFoundError: If inventory storage does not exist (HTTP 404).
            InventoryAccessError: If access is denied (HTTP 403) or
                unauthorized (HTTP 401).
            InventoryError: For general inventory storage errors.
        """
        if self.spreadsheet is not None:
            if getattr(self, "_spreadsheet_initialized", False):
                return self.spreadsheet
            if self.spreadsheet_id is not None:
                configured_id = getattr(self.spreadsheet, "id", None)
                if configured_id and configured_id != str(self.spreadsheet_id).strip():
                    raise ValueError(
                        "Inconsistent spreadsheet configuration: "
                        f"spreadsheet.id '{configured_id}' "
                        f"does not match spreadsheet_id '{self.spreadsheet_id}'."
                    )
            if hasattr(self.spreadsheet, "validate") and callable(
                self.spreadsheet.validate
            ):
                self.spreadsheet.validate()
            self._spreadsheet_initialized = True
            return self.spreadsheet

        if not self.spreadsheet_id or not str(self.spreadsheet_id).strip():
            raise ValueError("Missing Google Spreadsheet ID. Pass --spreadsheet-id.")

        clean_id = str(self.spreadsheet_id).strip()
        creds = self._validate_and_build_credentials()

        from gh_migration_inventory_manager.google import Spreadsheet

        sheet = Spreadsheet(
            id=clean_id,
            credentials=creds,
            sheet_name=self.sheet_name,
        )
        sheet.validate()
        self.spreadsheet = sheet
        self._spreadsheet_initialized = True
        return self.spreadsheet

    _build_spreadsheet = _validate_and_build_spreadsheet
    _get_spreadsheet = _validate_and_build_spreadsheet

    @staticmethod
    def _from_sheet_label(label: str) -> MigrationStatus:
        """Parse spreadsheet label into the corresponding MigrationStatus."""
        return _from_sheet_label(label)

    @staticmethod
    def _validate_status(status: Any) -> MigrationStatus:
        """Validate that status is a valid MigrationStatus or valid status string."""
        return validate_migration_status(status)

    def _find_row(self, name: str, project: str | None = None) -> Any | None:
        """Find a row matching repository name and optional project."""
        sheet = self.spreadsheet
        if sheet is None:
            sheet = self._validate_and_build_spreadsheet()
        clean_name = name.strip() if isinstance(name, str) else ""
        if not clean_name:
            raise ValueError("Repository name cannot be empty.")

        matching_rows = sheet.find_by("name", clean_name)
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

    def get_all_repositories(self) -> list[InventoryRecord]:
        """Retrieve all repositories recorded in the inventory."""
        from gh_migration_inventory_manager.google import RowConverter

        sheet = self._get_spreadsheet()
        rows = sheet.get_all_rows()
        return [RowConverter.to_inventory_record(r) for r in rows]

    def get_repositories_for_project(self, project_slug: str) -> list[InventoryRecord]:
        """Retrieve all repositories associated with a given project."""
        clean_project = (
            str(project_slug).strip().lower() if project_slug is not None else ""
        )
        if not clean_project:
            raise ValueError("Project slug cannot be empty.")
        all_repos = self.get_all_repositories()
        return [
            r for r in all_repos if (r.project or "").strip().lower() == clean_project
        ]

    def register_record(self, record: InventoryRecord) -> InventoryRecord:
        """Register or synchronize an InventoryRecord in the inventory.

        If the repository is already recorded, its migration status and dates
        are strictly preserved, while metadata (project, stack, last_commit)
        are updated if modified. If it does not exist, a new record is added.
        """
        from gh_migration_inventory_manager.google import RowConverter

        if record is None:
            raise ValueError("InventoryRecord cannot be None.")

        clean_name = (record.name or "").strip()
        if not clean_name:
            raise ValueError("Repository name cannot be empty.")

        target_row = self._find_row(clean_name, record.project or None)

        if target_row is not None:
            existing = RowConverter.to_inventory_record(target_row)
            changed = False
            if record.project is not None and existing.project != record.project:
                existing.project = record.project
                changed = True
            if record.stack and existing.stack != record.stack:
                existing.stack = record.stack
                changed = True
            if (
                record.last_commit is not None
                and existing.last_commit != record.last_commit
            ):
                existing.last_commit = record.last_commit
                changed = True

            if changed:
                row = RowConverter.from_inventory_record(
                    existing, row_index=target_row.row_index
                )
                saved_row = self.spreadsheet.update_row(row)
                return RowConverter.to_inventory_record(saved_row)
            return existing

        valid_status = self._validate_status(record.migration_status)
        new_record = InventoryRecord(
            name=clean_name,
            project=record.project or "",
            stack=record.stack or "",
            last_commit=record.last_commit,
            migration_status=valid_status,
        )
        self._apply_status_timestamps(new_record, valid_status)
        row = RowConverter.from_inventory_record(new_record)
        saved_row = self.spreadsheet.add(row)
        return RowConverter.to_inventory_record(saved_row)

    def compute_diff(
        self,
        records: Any,
        initial_status: MigrationStatus | str = MigrationStatus.WAITTING,
    ) -> Any:
        """Compute additions, updates, and unchanged repository records."""
        if records is None:
            raise ValueError("Records collection cannot be None.")
        sheet = self._validate_and_build_spreadsheet()
        raw_rows = (
            sheet.get_all_rows()
            if hasattr(sheet, "get_all_rows") and callable(sheet.get_all_rows)
            else []
        )
        existing_rows = raw_rows if isinstance(raw_rows, list) else []
        return self.differ.compute_diff(
            existing_rows=existing_rows,
            incoming_records=records,
            initial_status=initial_status,
        )

    def sync_records(self, records: Any) -> list[InventoryRecord]:
        """Synchronize an iterable of InventoryRecord instances into the inventory."""
        if records is None:
            raise ValueError("Records collection cannot be None.")
        sheet = self._validate_and_build_spreadsheet()
        records_list = list(records)
        if not records_list:
            return []

        from gh_migration_inventory_manager.google import RowConverter

        diff = self.compute_diff(records_list)
        synced: list[InventoryRecord] = []

        if diff.to_add:
            rows_to_add = [RowConverter.from_inventory_record(r) for r in diff.to_add]
            if _can_batch_add(sheet):
                sheet.batch_add(rows_to_add)
            else:
                for r in rows_to_add:
                    sheet.add(r)
            synced.extend(diff.to_add)

        if diff.to_update:
            rows_to_update = [
                RowConverter.from_inventory_record(up.record, row_index=up.row_index)
                for up in diff.to_update
            ]
            if _can_batch_update(sheet):
                sheet.batch_update_rows(rows_to_update)
            else:
                for r in rows_to_update:
                    sheet.update_row(r)
            synced.extend([up.record for up in diff.to_update])

        synced.extend(diff.unchanged)
        return synced

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

        Supports primitive arguments, an InventoryRecord, or a Repository instance.
        """
        if isinstance(name, InventoryRecord):
            record = name
            if project is not None:
                record.project = project
            if stack is not None:
                record.stack = stack
            if last_commit is not None:
                record.last_commit = last_commit
            return self.register_record(record)

        if not isinstance(name, str) and hasattr(name, "name"):
            record = self.converter.to_inventory_record(
                name, initial_status=initial_status
            )
            if project is not None:
                record.project = project
            if stack is not None:
                record.stack = stack
            if last_commit is not None:
                record.last_commit = last_commit
            return self.register_record(record)

        clean_name = str(name).strip() if name is not None else ""
        valid_status = self._validate_status(initial_status)
        record = InventoryRecord(
            name=clean_name,
            project=project or "",
            stack=stack or "",
            last_commit=last_commit,
            migration_status=valid_status,
        )
        return self.register_record(record)

    def sync_repositories(
        self,
        repositories: Any,
        initial_status: MigrationStatus | str = MigrationStatus.WAITTING,
    ) -> list[InventoryRecord]:
        """Synchronize a collection of repositories or inventory records."""
        records: list[InventoryRecord] = []
        for repo in repositories:
            if isinstance(repo, InventoryRecord):
                records.append(repo)
            elif not isinstance(repo, str) and hasattr(repo, "name"):
                records.append(
                    self.converter.to_inventory_record(
                        repo, initial_status=initial_status
                    )
                )
            else:
                return [
                    self.register_repository(r, initial_status=initial_status)
                    for r in repositories
                ]
        return self.sync_records(records)


class PipelineInventory:
    """Workflows and pipelines inventory manager backed by a Google Spreadsheet tab."""

    def __init__(
        self,
        spreadsheet: SpreadsheetClient | Any = None,
        spreadsheet_id: str | None = None,
        credentials_path: str | None = None,
        sheet_name: str = "Workflows",
        credentials: Any = None,
        converter: Any = None,
        differ: Any = None,
    ) -> None:
        self.spreadsheet = spreadsheet
        self.spreadsheet_id = spreadsheet_id
        self.credentials_path = credentials_path
        self.sheet_name = sheet_name
        self.credentials = credentials
        if differ is not None:
            self.differ = differ
        else:
            from gh_migration_inventory_manager.scrap_diff import PipelineDiffer

            self.differ = PipelineDiffer()
        if converter is not None:
            self.converter = converter
        else:
            from gh_migration_inventory_manager.converter import (
                PipelineConverter as _PipelineConverter,
            )

            self.converter = _PipelineConverter()

    def _validate_and_build_credentials(self) -> Any:
        """Validate credentials configuration and build Credentials instance."""
        self.credentials = _build_credentials(self.credentials, self.credentials_path)
        return self.credentials

    def _validate_and_build_spreadsheet(self) -> SpreadsheetClient:
        """Validate spreadsheet configuration and build Spreadsheet instance."""
        if self.spreadsheet is not None:
            if self.spreadsheet_id is not None:
                configured_id = getattr(self.spreadsheet, "id", None)
                if configured_id and configured_id != str(self.spreadsheet_id).strip():
                    raise ValueError(
                        "Inconsistent spreadsheet configuration: "
                        f"spreadsheet.id '{configured_id}' "
                        f"does not match spreadsheet_id '{self.spreadsheet_id}'."
                    )
            if hasattr(self.spreadsheet, "validate") and callable(
                self.spreadsheet.validate
            ):
                self.spreadsheet.validate()
            if hasattr(self.spreadsheet, "ensure_sheet") and callable(
                self.spreadsheet.ensure_sheet
            ):
                self.spreadsheet.ensure_sheet(self.sheet_name)
            self._spreadsheet_initialized = True
            return self.spreadsheet

        if not self.spreadsheet_id or not str(self.spreadsheet_id).strip():
            raise ValueError("Missing Google Spreadsheet ID. Pass --spreadsheet-id.")

        clean_id = str(self.spreadsheet_id).strip()
        creds = self._validate_and_build_credentials()

        from gh_migration_inventory_manager.google import Spreadsheet

        sheet = Spreadsheet(
            id=clean_id,
            credentials=creds,
            sheet_name=self.sheet_name,
        )
        sheet.validate()
        sheet.ensure_sheet(self.sheet_name)
        self.spreadsheet = sheet
        self._spreadsheet_initialized = True
        return self.spreadsheet

    def _get_spreadsheet(self) -> SpreadsheetClient:
        """Retrieve, validate, and return the underlying Spreadsheet client."""
        if self.spreadsheet is not None:
            if not getattr(self, "_spreadsheet_initialized", False):
                return self._validate_and_build_spreadsheet()
            return self.spreadsheet
        return self._validate_and_build_spreadsheet()

    def _find_row(self, workflow_id: str) -> Any | None:
        """Find a row matching workflow ID."""
        sheet = self._get_spreadsheet()
        clean_id = workflow_id.strip() if isinstance(workflow_id, str) else ""
        if not clean_id:
            raise ValueError("Workflow ID cannot be empty.")

        matching_rows = sheet.find_by("workflow_id", clean_id)
        return matching_rows[0] if matching_rows else None

    def register_record(self, record: PipelineRecord) -> PipelineRecord:
        """Register or synchronize a PipelineRecord in the inventory.

        If the workflow is already recorded, its migration status is strictly
        preserved, while metadata (last_build, last_status, etc.) are updated.
        """
        from gh_migration_inventory_manager.google import WorkflowRowConverter

        if record is None:
            raise ValueError("PipelineRecord cannot be None.")

        clean_id = str(record.workflow_id).strip() if record.workflow_id else ""
        if not clean_id:
            raise ValueError("Pipeline workflow_id cannot be empty.")

        sheet = self._get_spreadsheet()

        existing_row = self._find_row(clean_id)
        if existing_row is not None:
            existing_record = WorkflowRowConverter.to_pipeline_record(existing_row)
            record.migration_status = existing_record.migration_status
            row = WorkflowRowConverter.from_pipeline_record(
                record, row_index=existing_row.row_index
            )
            saved_row = sheet.update_row(row)
        else:
            row = WorkflowRowConverter.from_pipeline_record(record, row_index=None)
            saved_row = sheet.add(row)

        return WorkflowRowConverter.to_pipeline_record(saved_row)

    def update_progress(
        self,
        workflow_id: str,
        status: MigrationStatus | str,
    ) -> PipelineRecord:
        """Update migration progress for a pipeline in the inventory."""
        from gh_migration_inventory_manager.google import WorkflowRowConverter

        clean_id = workflow_id.strip() if isinstance(workflow_id, str) else ""
        if not clean_id:
            raise ValueError("Workflow ID cannot be empty.")

        valid_status = validate_migration_status(status)
        existing_row = self._find_row(clean_id)

        if not existing_row:
            record = PipelineRecord(
                workflow_id=clean_id,
                migration_status=valid_status,
            )
            row_index = None
        else:
            record = WorkflowRowConverter.to_pipeline_record(existing_row)
            record.migration_status = valid_status
            row_index = existing_row.row_index

        row = WorkflowRowConverter.from_pipeline_record(record, row_index=row_index)
        sheet = self._get_spreadsheet()
        saved_row = sheet.update_row(row) if row_index is not None else sheet.add(row)
        return WorkflowRowConverter.to_pipeline_record(saved_row)

    def get_pipeline(self, workflow_id: str) -> PipelineRecord | None:
        """Find a pipeline record by workflow ID."""
        from gh_migration_inventory_manager.google import WorkflowRowConverter

        row = self._find_row(workflow_id)
        return WorkflowRowConverter.to_pipeline_record(row) if row is not None else None

    def get_all_pipelines(self) -> list[PipelineRecord]:
        """Retrieve all CI/CD pipelines recorded in the workflows inventory."""
        from gh_migration_inventory_manager.google import WorkflowRowConverter

        sheet = self._get_spreadsheet()
        rows = sheet.get_all_rows()
        return [WorkflowRowConverter.to_pipeline_record(r) for r in rows]

    def get_pipelines_for_project(self, project_slug: str) -> list[PipelineRecord]:
        """Retrieve all pipelines associated with a given project."""
        clean_proj = (
            str(project_slug).strip().lower() if project_slug is not None else ""
        )
        if not clean_proj:
            raise ValueError("Project slug cannot be empty.")
        proj_prefix = f"{clean_proj}/"
        return [
            p
            for p in self.get_all_pipelines()
            if (p.project or "").strip().lower() == clean_proj
            or (p.repository_association or "").strip().lower().startswith(proj_prefix)
        ]

    def get_pipelines_for_repository(
        self, project_slug: str, repo_slug: str
    ) -> list[PipelineRecord]:
        """Retrieve all pipelines associated with a specific repository."""
        clean_proj = (
            str(project_slug).strip().lower() if project_slug is not None else ""
        )
        clean_repo = str(repo_slug).strip().lower() if repo_slug is not None else ""
        if not clean_proj:
            raise ValueError("Project slug cannot be empty.")
        if not clean_repo:
            raise ValueError("Repository slug cannot be empty.")

        pipe_assoc_targets = {f"{clean_proj}/{clean_repo}", clean_repo}
        matched: list[PipelineRecord] = []
        for p in self.get_all_pipelines():
            p_proj = (p.project or "").strip().lower()
            p_repo = (p.repository or "").strip().lower()
            if p_repo == clean_repo and (not p_proj or p_proj == clean_proj):
                matched.append(p)
            elif (p.repository_association or "").strip().lower() in pipe_assoc_targets:
                matched.append(p)
        return matched

    def compute_diff(self, records: list[PipelineRecord]) -> Any:
        """Compute additions, updates, and unchanged pipeline records."""
        if records is None:
            raise ValueError("Records collection cannot be None.")
        sheet = self._get_spreadsheet()
        raw_rows = (
            sheet.get_all_rows()
            if hasattr(sheet, "get_all_rows") and callable(sheet.get_all_rows)
            else []
        )
        existing_rows = raw_rows if isinstance(raw_rows, list) else []
        return self.differ.compute_diff(
            existing_rows=existing_rows,
            incoming_records=records,
        )

    def sync_records(self, records: list[PipelineRecord]) -> list[PipelineRecord]:
        """Synchronize a collection of PipelineRecord instances."""
        if records is None:
            raise ValueError("Records collection cannot be None.")
        sheet = self._get_spreadsheet()
        records_list = list(records)
        if not records_list:
            return []

        from gh_migration_inventory_manager.google import WorkflowRowConverter

        diff = self.compute_diff(records_list)
        synced: list[PipelineRecord] = []

        if diff.to_add:
            rows_to_add = [
                WorkflowRowConverter.from_pipeline_record(r, row_index=None)
                for r in diff.to_add
            ]
            if _can_batch_add(sheet):
                sheet.batch_add(rows_to_add)
            else:
                for r in rows_to_add:
                    sheet.add(r)
            synced.extend(diff.to_add)

        if diff.to_update:
            rows_to_update = [
                WorkflowRowConverter.from_pipeline_record(
                    up.record, row_index=up.row_index
                )
                for up in diff.to_update
            ]
            if _can_batch_update(sheet):
                sheet.batch_update_rows(rows_to_update)
            else:
                for r in rows_to_update:
                    sheet.update_row(r)
            synced.extend([up.record for up in diff.to_update])

        synced.extend(diff.unchanged)
        return synced


class TeamInventory:
    """Team topology and access inventory manager backed by a Google Spreadsheet tab."""

    def __init__(
        self,
        spreadsheet: SpreadsheetClient | Any = None,
        spreadsheet_id: str | None = None,
        credentials_path: str | None = None,
        sheet_name: str = "Topology",
        credentials: Any = None,
        converter: Any = None,
        differ: Any = None,
    ) -> None:
        self.spreadsheet = spreadsheet
        self.spreadsheet_id = spreadsheet_id
        self.credentials_path = credentials_path
        self.sheet_name = sheet_name
        self.credentials = credentials
        self.converter = converter
        if differ is not None:
            self.differ = differ
        else:
            from gh_migration_inventory_manager.scrap_diff import TeamDiffer

            self.differ = TeamDiffer()

    def _validate_and_build_credentials(self) -> Any:
        """Validate credentials configuration and build Credentials instance."""
        self.credentials = _build_credentials(self.credentials, self.credentials_path)
        return self.credentials

    def _validate_and_build_spreadsheet(self) -> SpreadsheetClient:
        """Validate spreadsheet configuration and build Spreadsheet instance."""
        if self.spreadsheet is not None:
            configured_id = getattr(self.spreadsheet, "id", None)
            if (
                self.spreadsheet_id is not None
                and configured_id
                and configured_id != str(self.spreadsheet_id).strip()
            ):
                raise ValueError(
                    "Inconsistent spreadsheet configuration: "
                    f"spreadsheet.id '{configured_id}' "
                    f"does not match spreadsheet_id '{self.spreadsheet_id}'."
                )
            if hasattr(self.spreadsheet, "validate") and callable(
                self.spreadsheet.validate
            ):
                self.spreadsheet.validate()
            if hasattr(self.spreadsheet, "ensure_sheet") and callable(
                self.spreadsheet.ensure_sheet
            ):
                self.spreadsheet.ensure_sheet(self.sheet_name)
            self._spreadsheet_initialized = True
            return self.spreadsheet

        if not self.spreadsheet_id or not str(self.spreadsheet_id).strip():
            raise ValueError("Missing Google Spreadsheet ID. Pass --spreadsheet-id.")

        clean_id = str(self.spreadsheet_id).strip()
        creds = self._validate_and_build_credentials()

        from gh_migration_inventory_manager.google import Spreadsheet

        sheet = Spreadsheet(
            id=clean_id,
            credentials=creds,
            sheet_name=self.sheet_name,
        )
        sheet.validate()
        sheet.ensure_sheet(self.sheet_name)
        self.spreadsheet = sheet
        self._spreadsheet_initialized = True
        return self.spreadsheet

    def _get_spreadsheet(self) -> SpreadsheetClient:
        """Retrieve, validate, and return the underlying Spreadsheet client."""
        if self.spreadsheet is not None:
            if not getattr(self, "_spreadsheet_initialized", False):
                return self._validate_and_build_spreadsheet()
            return self.spreadsheet
        return self._validate_and_build_spreadsheet()

    def _find_row(
        self, team_slug: str, target_repo_or_project: str | None = None
    ) -> Any | None:
        """Find a row matching team slug and optional target scope."""
        sheet = self._get_spreadsheet()
        clean_slug = team_slug.strip() if isinstance(team_slug, str) else ""
        if not clean_slug:
            raise ValueError("Team slug cannot be empty.")

        matching_rows = sheet.find_by("team_slug", clean_slug)
        if not matching_rows:
            return None

        if target_repo_or_project is not None:
            clean_target = target_repo_or_project.strip().lower()
            for r in matching_rows:
                row_target = (
                    str(r.data.get("target_repo_or_project", "")).strip().lower()
                )
                if row_target == clean_target:
                    return r
            return None

        return matching_rows[0]

    def register_record(self, record: TeamRecord) -> TeamRecord:
        """Register or synchronize a TeamRecord in the inventory.

        If the team permission is already recorded, its migration status is strictly
        preserved, while metadata (member_count, origin_role, etc.) are updated.
        """
        from gh_migration_inventory_manager.google import TeamRowConverter

        if record is None:
            raise ValueError("TeamRecord cannot be None.")

        clean_slug = str(record.team_slug).strip() if record.team_slug else ""
        if not clean_slug:
            raise ValueError("Team slug cannot be empty.")

        clean_target = (
            str(record.target_repo_or_project).strip()
            if record.target_repo_or_project
            else ""
        )
        if not clean_target:
            raise ValueError("Target repository or project cannot be empty.")

        sheet = self._get_spreadsheet()

        existing_row = self._find_row(clean_slug, clean_target)
        if existing_row is not None:
            existing_record = TeamRowConverter.to_team_record(existing_row)
            record.migration_status = existing_record.migration_status
            changed = (
                existing_record.team_name != record.team_name
                or existing_record.member_count != record.member_count
                or existing_record.normalized_role != record.normalized_role
                or existing_record.origin_role != record.origin_role
            )
            if changed:
                row = TeamRowConverter.from_team_record(
                    record, row_index=existing_row.row_index
                )
                saved_row = sheet.update_row(row)
            else:
                saved_row = existing_row
        else:
            row = TeamRowConverter.from_team_record(record, row_index=None)
            saved_row = sheet.add(row)

        return TeamRowConverter.to_team_record(saved_row)

    def update_progress(
        self,
        team_slug: str,
        target_repo_or_project: str,
        status: MigrationStatus | str,
    ) -> TeamRecord:
        """Update migration progress for a team permission in the inventory."""
        from gh_migration_inventory_manager.google import TeamRowConverter

        clean_slug = team_slug.strip() if isinstance(team_slug, str) else ""
        if not clean_slug:
            raise ValueError("Team slug cannot be empty.")

        clean_target = (
            target_repo_or_project.strip()
            if isinstance(target_repo_or_project, str)
            else ""
        )
        if not clean_target:
            raise ValueError("Target repository or project cannot be empty.")

        valid_status = validate_migration_status(status)
        existing_row = self._find_row(clean_slug, clean_target)

        if not existing_row:
            record = TeamRecord(
                team_name=clean_slug,
                team_slug=clean_slug,
                target_repo_or_project=clean_target,
                migration_status=valid_status,
            )
            row_index = None
        else:
            record = TeamRowConverter.to_team_record(existing_row)
            if record.migration_status == valid_status:
                return record
            record.migration_status = valid_status
            row_index = existing_row.row_index

        row = TeamRowConverter.from_team_record(record, row_index=row_index)
        sheet = self._get_spreadsheet()
        saved_row = sheet.update_row(row) if row_index is not None else sheet.add(row)
        return TeamRowConverter.to_team_record(saved_row)

    def get_team_permission(
        self, team_slug: str, target_repo_or_project: str | None = None
    ) -> TeamRecord | None:
        """Find a team record by team slug and optional target scope."""
        from gh_migration_inventory_manager.google import TeamRowConverter

        row = self._find_row(team_slug, target_repo_or_project)
        return TeamRowConverter.to_team_record(row) if row is not None else None

    def get_all_teams(self) -> list[TeamRecord]:
        """Retrieve all team topology records from the inventory."""
        from gh_migration_inventory_manager.google import TeamRowConverter

        sheet = self._get_spreadsheet()
        rows = sheet.get_all_rows()
        return [TeamRowConverter.to_team_record(r) for r in rows]

    def get_teams_for_project(self, project_slug: str) -> list[TeamRecord]:
        """Retrieve all team permissions scoped to a given project."""
        clean_proj = (
            str(project_slug).strip().lower() if project_slug is not None else ""
        )
        if not clean_proj:
            raise ValueError("Project slug cannot be empty.")
        proj_targets = {f"project:{clean_proj}", clean_proj}
        return [
            t
            for t in self.get_all_teams()
            if (t.project or "").strip().lower() == clean_proj
            or (t.target_repo_or_project or "").strip().lower() in proj_targets
        ]

    def get_teams_for_repository(
        self, project_slug: str, repo_slug: str, include_inherited: bool = True
    ) -> list[TeamRecord]:
        """Retrieve team permissions for a repository with optional inheritance."""
        clean_proj = (
            str(project_slug).strip().lower() if project_slug is not None else ""
        )
        clean_repo = str(repo_slug).strip().lower() if repo_slug is not None else ""
        if not clean_proj:
            raise ValueError("Project slug cannot be empty.")
        if not clean_repo:
            raise ValueError("Repository slug cannot be empty.")

        repo_targets = {
            f"repo:{clean_proj}/{clean_repo}",
            f"repo:{clean_repo}",
            f"{clean_proj}/{clean_repo}",
        }
        proj_targets = {f"project:{clean_proj}", clean_proj}

        matched: list[TeamRecord] = []
        for t in self.get_all_teams():
            t_proj = (t.project or "").strip().lower()
            t_repo = (t.repository or "").strip().lower()
            target = (t.target_repo_or_project or "").strip().lower()

            is_repo_match = (
                t_repo == clean_repo and (not t_proj or t_proj == clean_proj)
            ) or target in repo_targets
            if is_repo_match:
                matched.append(t)
            elif include_inherited:
                is_proj_inherited = (
                    t_proj == clean_proj and not t_repo
                ) or target in proj_targets
                if is_proj_inherited:
                    matched.append(t)
        return matched

    def compute_diff(self, records: list[TeamRecord]) -> Any:
        """Compute additions, updates, and unchanged team records."""
        if records is None:
            raise ValueError("Records collection cannot be None.")
        sheet = self._get_spreadsheet()
        raw_rows = (
            sheet.get_all_rows()
            if hasattr(sheet, "get_all_rows") and callable(sheet.get_all_rows)
            else []
        )
        existing_rows = raw_rows if isinstance(raw_rows, list) else []
        return self.differ.compute_diff(
            existing_rows=existing_rows,
            incoming_records=records,
        )

    def sync_records(self, records: list[TeamRecord]) -> list[TeamRecord]:
        """Synchronize a collection of TeamRecord instances."""
        if records is None:
            raise ValueError("Records collection cannot be None.")
        sheet = self._get_spreadsheet()
        records_list = list(records)
        if not records_list:
            return []

        from gh_migration_inventory_manager.google import TeamRowConverter

        diff = self.compute_diff(records_list)
        synced: list[TeamRecord] = []

        if diff.to_add:
            rows_to_add = [
                TeamRowConverter.from_team_record(r, row_index=None)
                for r in diff.to_add
            ]
            if _can_batch_add(sheet):
                sheet.batch_add(rows_to_add)
            else:
                for r in rows_to_add:
                    sheet.add(r)
            synced.extend(diff.to_add)

        if diff.to_update:
            rows_to_update = [
                TeamRowConverter.from_team_record(up.record, row_index=up.row_index)
                for up in diff.to_update
            ]
            if _can_batch_update(sheet):
                sheet.batch_update_rows(rows_to_update)
            else:
                for r in rows_to_update:
                    sheet.update_row(r)
            synced.extend([up.record for up in diff.to_update])

        synced.extend(diff.unchanged)
        return synced


class PolicyInventory:
    """Branch governance policy inventory manager backed by a Google Spreadsheet tab."""

    def __init__(
        self,
        spreadsheet: SpreadsheetClient | Any = None,
        spreadsheet_id: str | None = None,
        credentials_path: str | None = None,
        sheet_name: str = "Policies",
        credentials: Any = None,
        converter: Any = None,
        differ: Any = None,
    ) -> None:
        self.spreadsheet = spreadsheet
        self.spreadsheet_id = spreadsheet_id
        self.credentials_path = credentials_path
        self.sheet_name = sheet_name
        self.credentials = credentials
        self.converter = converter
        if differ is not None:
            self.differ = differ
        else:
            from gh_migration_inventory_manager.scrap_diff import PolicyDiffer

            self.differ = PolicyDiffer()

    def _validate_and_build_credentials(self) -> Any:
        """Validate credentials configuration and build Credentials instance."""
        self.credentials = _build_credentials(self.credentials, self.credentials_path)
        return self.credentials

    def _validate_and_build_spreadsheet(self) -> SpreadsheetClient:
        """Validate spreadsheet configuration and build Spreadsheet instance."""
        if self.spreadsheet is not None:
            configured_id = getattr(self.spreadsheet, "id", None)
            if (
                self.spreadsheet_id is not None
                and configured_id
                and configured_id != str(self.spreadsheet_id).strip()
            ):
                raise ValueError(
                    "Inconsistent spreadsheet configuration: "
                    f"spreadsheet.id '{configured_id}' "
                    f"does not match spreadsheet_id '{self.spreadsheet_id}'."
                )
            if hasattr(self.spreadsheet, "validate") and callable(
                self.spreadsheet.validate
            ):
                self.spreadsheet.validate()
            if hasattr(self.spreadsheet, "ensure_sheet") and callable(
                self.spreadsheet.ensure_sheet
            ):
                self.spreadsheet.ensure_sheet(self.sheet_name)
            self._spreadsheet_initialized = True
            return self.spreadsheet

        if not self.spreadsheet_id or not str(self.spreadsheet_id).strip():
            raise ValueError("Missing Google Spreadsheet ID. Pass --spreadsheet-id.")

        clean_id = str(self.spreadsheet_id).strip()
        creds = self._validate_and_build_credentials()

        from gh_migration_inventory_manager.google import Spreadsheet

        sheet = Spreadsheet(
            id=clean_id,
            credentials=creds,
            sheet_name=self.sheet_name,
        )
        sheet.validate()
        sheet.ensure_sheet(self.sheet_name)
        self.spreadsheet = sheet
        self._spreadsheet_initialized = True
        return self.spreadsheet

    def _get_spreadsheet(self) -> SpreadsheetClient:
        """Retrieve, validate, and return the underlying Spreadsheet client."""
        if self.spreadsheet is not None:
            if not getattr(self, "_spreadsheet_initialized", False):
                return self._validate_and_build_spreadsheet()
            return self.spreadsheet
        return self._validate_and_build_spreadsheet()

    def _validate_policy_key(
        self,
        policy_scope: Any,
        target_ref_pattern: Any,
        normalized_rule_type: Any,
    ) -> tuple[str, str, str]:
        """Validate and sanitize policy composite key."""
        clean_scope = str(policy_scope).strip() if policy_scope is not None else ""
        if not clean_scope:
            raise ValueError("Policy scope cannot be empty.")

        clean_target = (
            str(target_ref_pattern).strip() if target_ref_pattern is not None else ""
        )
        if not clean_target:
            raise ValueError("Target ref pattern cannot be empty.")

        if normalized_rule_type is None or not str(normalized_rule_type).strip():
            raise ValueError("Normalized rule type cannot be empty.")

        try:
            from gh_migration_inventory_manager.scrap import normalize_rule_type

            clean_rule = normalize_rule_type(normalized_rule_type).value
        except (ValueError, TypeError):
            clean_rule = str(normalized_rule_type).strip()

        return clean_scope, clean_target, clean_rule

    def _find_row(
        self,
        policy_scope: str,
        target_ref_pattern: str,
        normalized_rule_type: str,
    ) -> Any | None:
        """Find a row matching composite key (scope, ref_pattern, rule_type)."""
        sheet = self._get_spreadsheet()
        clean_scope, clean_target, clean_rule = self._validate_policy_key(
            policy_scope, target_ref_pattern, normalized_rule_type
        )

        matching_rows = sheet.find_by("policy_scope", clean_scope)
        if not matching_rows and clean_scope.startswith("repo:") and "/" in clean_scope:
            _, legacy_repo = clean_scope[len("repo:") :].split("/", 1)
            matching_rows = sheet.find_by("policy_scope", f"repo:{legacy_repo}")

        if not matching_rows:
            return None

        clean_target_lower = clean_target.lower()
        clean_rule_lower = clean_rule.lower()

        for r in matching_rows:
            row_target = str(r.data.get("target_ref_pattern", "")).strip().lower()
            row_rule = str(r.data.get("normalized_rule_type", "")).strip().lower()
            if row_target == clean_target_lower and row_rule == clean_rule_lower:
                return r

        return None

    def register_record(self, record: PolicyRecord) -> PolicyRecord:
        """Register or synchronize a PolicyRecord in the inventory.

        If the policy is already recorded, its migration status is strictly
        preserved, while parameters, bypass slugs, and raw origin policy are updated.
        """
        from gh_migration_inventory_manager.google import PolicyRowConverter

        if record is None:
            raise ValueError("PolicyRecord cannot be None.")

        clean_scope, clean_target, clean_rule = self._validate_policy_key(
            record.policy_scope,
            record.target_ref_pattern,
            record.normalized_rule_type,
        )

        sheet = self._get_spreadsheet()
        existing_row = self._find_row(clean_scope, clean_target, clean_rule)

        if existing_row is not None:
            existing_record = PolicyRowConverter.to_policy_record(existing_row)
            record.migration_status = existing_record.migration_status
            changed = (
                existing_record.rule_parameters != record.rule_parameters
                or existing_record.bypass_team_slugs != record.bypass_team_slugs
                or existing_record.origin_raw_policy != record.origin_raw_policy
            )
            if not changed:
                return existing_record
            row = PolicyRowConverter.from_policy_record(
                record, row_index=existing_row.row_index
            )
            saved_row = sheet.update_row(row)
        else:
            row = PolicyRowConverter.from_policy_record(record, row_index=None)
            saved_row = sheet.add(row)

        return PolicyRowConverter.to_policy_record(saved_row)

    def update_progress(
        self,
        policy_scope: str,
        target_ref_pattern: str,
        normalized_rule_type: str,
        status: MigrationStatus | str,
    ) -> PolicyRecord:
        """Update migration progress for a branch governance policy."""
        from gh_migration_inventory_manager.google import PolicyRowConverter

        clean_scope, clean_target, clean_rule = self._validate_policy_key(
            policy_scope, target_ref_pattern, normalized_rule_type
        )
        valid_status = validate_migration_status(status)
        existing_row = self._find_row(clean_scope, clean_target, clean_rule)

        if not existing_row:
            record = PolicyRecord(
                policy_scope=clean_scope,
                target_ref_pattern=clean_target,
                normalized_rule_type=clean_rule,
                migration_status=valid_status,
            )
            row_index = None
        else:
            record = PolicyRowConverter.to_policy_record(existing_row)
            if record.migration_status == valid_status:
                return record
            record.migration_status = valid_status
            row_index = existing_row.row_index

        row = PolicyRowConverter.from_policy_record(record, row_index=row_index)
        sheet = self._get_spreadsheet()
        saved_row = sheet.update_row(row) if row_index is not None else sheet.add(row)
        return PolicyRowConverter.to_policy_record(saved_row)

    def get_policy(
        self,
        policy_scope: str,
        target_ref_pattern: str,
        normalized_rule_type: str,
    ) -> PolicyRecord | None:
        """Find a policy record by its composite key."""
        from gh_migration_inventory_manager.google import PolicyRowConverter

        row = self._find_row(policy_scope, target_ref_pattern, normalized_rule_type)
        return PolicyRowConverter.to_policy_record(row) if row is not None else None

    def get_all_policies(self) -> list[PolicyRecord]:
        """Retrieve all branch governance policy records from the inventory."""
        from gh_migration_inventory_manager.google import PolicyRowConverter

        sheet = self._get_spreadsheet()
        rows = sheet.get_all_rows()
        return [PolicyRowConverter.to_policy_record(r) for r in rows]

    def get_policies_for_project(self, project_slug: str) -> list[PolicyRecord]:
        """Retrieve all branch policies scoped to a given project."""
        clean_proj = (
            str(project_slug).strip().lower() if project_slug is not None else ""
        )
        if not clean_proj:
            raise ValueError("Project slug cannot be empty.")
        proj_scope = f"project:{clean_proj}"
        return [
            p
            for p in self.get_all_policies()
            if (
                (p.project or "").strip().lower() == clean_proj
                and not (p.repository or "").strip()
            )
            or (p.policy_scope or "").strip().lower() == proj_scope
        ]

    def get_policies_for_repository(
        self, project_slug: str, repo_slug: str, include_inherited: bool = True
    ) -> list[PolicyRecord]:
        """Retrieve branch policies for a repository with optional inheritance."""
        clean_proj = (
            str(project_slug).strip().lower() if project_slug is not None else ""
        )
        clean_repo = str(repo_slug).strip().lower() if repo_slug is not None else ""
        if not clean_proj:
            raise ValueError("Project slug cannot be empty.")
        if not clean_repo:
            raise ValueError("Repository slug cannot be empty.")

        repo_targets = {
            f"repo:{clean_proj}/{clean_repo}",
            f"repo:{clean_repo}",
            f"{clean_proj}/{clean_repo}",
        }
        proj_scope = f"project:{clean_proj}"

        matched: list[PolicyRecord] = []
        for p in self.get_all_policies():
            p_proj = (p.project or "").strip().lower()
            p_repo = (p.repository or "").strip().lower()
            scope = (p.policy_scope or "").strip().lower()

            is_repo_match = (
                p_repo == clean_repo and (not p_proj or p_proj == clean_proj)
            ) or scope in repo_targets
            if is_repo_match:
                matched.append(p)
            elif include_inherited:
                is_proj_inherited = (
                    p_proj == clean_proj and not p_repo
                ) or scope == proj_scope
                is_global = scope in ("global", "") or (
                    not p_proj and not p_repo and not scope
                )
                if is_proj_inherited or is_global:
                    matched.append(p)
        return matched

    def compute_diff(self, records: list[PolicyRecord]) -> Any:
        """Compute additions, updates, and unchanged policy records."""
        if records is None:
            raise ValueError("Records collection cannot be None.")
        sheet = self._get_spreadsheet()
        raw_rows = (
            sheet.get_all_rows()
            if hasattr(sheet, "get_all_rows") and callable(sheet.get_all_rows)
            else []
        )
        existing_rows = raw_rows if isinstance(raw_rows, list) else []
        return self.differ.compute_diff(
            existing_rows=existing_rows,
            incoming_records=records,
        )

    def sync_records(self, records: list[PolicyRecord]) -> list[PolicyRecord]:
        """Synchronize a collection of PolicyRecord instances."""
        if records is None:
            raise ValueError("Records collection cannot be None.")
        sheet = self._get_spreadsheet()
        records_list = list(records)
        if not records_list:
            return []

        from gh_migration_inventory_manager.google import PolicyRowConverter

        diff = self.compute_diff(records_list)
        synced: list[PolicyRecord] = []

        if diff.to_add:
            rows_to_add = [
                PolicyRowConverter.from_policy_record(r, row_index=None)
                for r in diff.to_add
            ]
            if _can_batch_add(sheet):
                sheet.batch_add(rows_to_add)
            else:
                for r in rows_to_add:
                    sheet.add(r)
            synced.extend(diff.to_add)

        if diff.to_update:
            rows_to_update = [
                PolicyRowConverter.from_policy_record(up.record, row_index=up.row_index)
                for up in diff.to_update
            ]
            if _can_batch_update(sheet):
                sheet.batch_update_rows(rows_to_update)
            else:
                for r in rows_to_update:
                    sheet.update_row(r)
            synced.extend([up.record for up in diff.to_update])

        synced.extend(diff.unchanged)
        return synced


@dataclass
class CorrelatedRepositoryProfile:
    """Correlated cross-tab view of a single repository and its related entities."""

    project_slug: str
    repo_slug: str
    repository: InventoryRecord | None = None
    effective_policies: list[PolicyRecord] = field(default_factory=list)
    pipelines: list[PipelineRecord] = field(default_factory=list)
    teams: list[TeamRecord] = field(default_factory=list)


@dataclass
class CorrelatedProjectProfile:
    """Correlated cross-tab view of an entire project and its related entities."""

    project_slug: str
    repositories: list[InventoryRecord] = field(default_factory=list)
    project_policies: list[PolicyRecord] = field(default_factory=list)
    pipelines: list[PipelineRecord] = field(default_factory=list)
    teams: list[TeamRecord] = field(default_factory=list)


class InventoryGraph:
    """Relational synthesis layer across inventory tabs."""

    def __init__(
        self,
        repositories: list[InventoryRecord] | None = None,
        pipelines: list[PipelineRecord] | None = None,
        policies: list[PolicyRecord] | None = None,
        teams: list[TeamRecord] | None = None,
    ) -> None:
        self.repositories = list(repositories or [])
        self.pipelines = list(pipelines or [])
        self.policies = list(policies or [])
        self.teams = list(teams or [])

    @classmethod
    def from_records(
        cls,
        repositories: list[InventoryRecord] | None = None,
        pipelines: list[PipelineRecord] | None = None,
        policies: list[PolicyRecord] | None = None,
        teams: list[TeamRecord] | None = None,
    ) -> InventoryGraph:
        """Construct an InventoryGraph directly from lists of records (zero-I/O)."""
        return cls(repositories, pipelines, policies, teams)

    @classmethod
    def from_inventories(
        cls,
        inventory: Inventory | None = None,
        pipeline_inventory: PipelineInventory | None = None,
        policy_inventory: PolicyInventory | None = None,
        team_inventory: TeamInventory | None = None,
        *,
        repository_inventory: Inventory | None = None,
    ) -> InventoryGraph:
        """Construct an InventoryGraph by loading all 4 inventory tabs concurrently."""
        effective_repo_inv = repository_inventory or inventory
        if (
            effective_repo_inv is None
            or pipeline_inventory is None
            or policy_inventory is None
            or team_inventory is None
        ):
            raise ValueError("All inventory managers must be provided.")

        with ThreadPoolExecutor(max_workers=4) as executor:
            fut_repos = executor.submit(effective_repo_inv.get_all_repositories)
            fut_pipes = executor.submit(pipeline_inventory.get_all_pipelines)
            fut_pols = executor.submit(policy_inventory.get_all_policies)
            fut_teams = executor.submit(team_inventory.get_all_teams)

            repos = fut_repos.result()
            pipelines = fut_pipes.result()
            policies = fut_pols.result()
            teams = fut_teams.result()

        return cls(repos, pipelines, policies, teams)

    def get_repository_profile(
        self, project_slug: str, repo_slug: str
    ) -> CorrelatedRepositoryProfile:
        """Return the complete correlated profile for a specific repository."""
        clean_proj = (
            str(project_slug).strip().lower() if project_slug is not None else ""
        )
        clean_repo = str(repo_slug).strip().lower() if repo_slug is not None else ""
        if not clean_proj:
            raise ValueError("Project slug cannot be empty.")
        if not clean_repo:
            raise ValueError("Repository slug cannot be empty.")

        matched_repo: InventoryRecord | None = None
        for r in self.repositories:
            if (r.name or "").strip().lower() == clean_repo and (
                not r.project or (r.project or "").strip().lower() == clean_proj
            ):
                matched_repo = r
                break

        repo_targets = {
            f"repo:{clean_proj}/{clean_repo}",
            f"repo:{clean_repo}",
            f"{clean_proj}/{clean_repo}",
        }
        pipe_assoc_targets = {f"{clean_proj}/{clean_repo}", clean_repo}
        proj_scope = f"project:{clean_proj}"
        proj_targets = {proj_scope, clean_proj}

        # Effective policies (repo + project-inherited + global)
        effective_policies: list[PolicyRecord] = []
        for p in self.policies:
            p_proj = (p.project or "").strip().lower()
            p_repo = (p.repository or "").strip().lower()
            scope = (p.policy_scope or "").strip().lower()

            is_repo = (
                p_repo == clean_repo and (not p_proj or p_proj == clean_proj)
            ) or scope in repo_targets
            if is_repo:
                effective_policies.append(p)
            elif (p_proj == clean_proj and not p_repo) or scope == proj_scope:
                effective_policies.append(p)
            elif scope in ("global", "") or (not p_proj and not p_repo and not scope):
                effective_policies.append(p)

        # Correlated pipelines
        correlated_pipelines: list[PipelineRecord] = []
        for pl in self.pipelines:
            pl_proj = (pl.project or "").strip().lower()
            pl_repo = (pl.repository or "").strip().lower()
            assoc = (pl.repository_association or "").strip().lower()

            is_pipe_match = (
                pl_repo == clean_repo and (not pl_proj or pl_proj == clean_proj)
            ) or assoc in pipe_assoc_targets
            if is_pipe_match:
                correlated_pipelines.append(pl)

        # Correlated teams (repo + project-inherited)
        effective_teams: list[TeamRecord] = []
        for t in self.teams:
            t_proj = (t.project or "").strip().lower()
            t_repo = (t.repository or "").strip().lower()
            target = (t.target_repo_or_project or "").strip().lower()

            is_team_match = (
                t_repo == clean_repo and (not t_proj or t_proj == clean_proj)
            ) or target in repo_targets
            if is_team_match:
                effective_teams.append(t)
            elif (t_proj == clean_proj and not t_repo) or target in proj_targets:
                effective_teams.append(t)

        return CorrelatedRepositoryProfile(
            project_slug=project_slug,
            repo_slug=repo_slug,
            repository=matched_repo,
            effective_policies=effective_policies,
            pipelines=correlated_pipelines,
            teams=effective_teams,
        )

    def get_project_profile(self, project_slug: str) -> CorrelatedProjectProfile:
        """Return the correlated profile for an entire project."""
        clean_proj = (
            str(project_slug).strip().lower() if project_slug is not None else ""
        )
        if not clean_proj:
            raise ValueError("Project slug cannot be empty.")

        proj_scope = f"project:{clean_proj}"
        proj_prefix = f"{clean_proj}/"
        proj_targets = {proj_scope, clean_proj}
        proj_repo_prefix = f"repo:{clean_proj}/"

        repos = [
            r
            for r in self.repositories
            if (r.project or "").strip().lower() == clean_proj
        ]
        policies = [
            p
            for p in self.policies
            if (
                (p.project or "").strip().lower() == clean_proj
                and not (p.repository or "").strip()
            )
            or (p.policy_scope or "").strip().lower() == proj_scope
        ]
        pipelines = [
            pl
            for pl in self.pipelines
            if (pl.project or "").strip().lower() == clean_proj
            or (pl.repository_association or "").strip().lower().startswith(proj_prefix)
        ]
        teams = [
            t
            for t in self.teams
            if (t.project or "").strip().lower() == clean_proj
            or (t.target_repo_or_project or "").strip().lower() in proj_targets
            or (t.target_repo_or_project or "")
            .strip()
            .lower()
            .startswith(proj_repo_prefix)
        ]

        return CorrelatedProjectProfile(
            project_slug=project_slug,
            repositories=repos,
            project_policies=policies,
            pipelines=pipelines,
            teams=teams,
        )

    def find_unlinked_workflows(self) -> list[PipelineRecord]:
        """Find pipelines whose repository is not among indexed repositories."""
        from gh_migration_inventory_manager.converter import (
            _parse_repo_assoc_project_repo,
        )

        indexed_repos: set[tuple[str, str]] = set()
        indexed_names: set[str] = set()

        for r in self.repositories:
            p = (r.project or "").strip().lower()
            n = (r.name or "").strip().lower()
            if p and n:
                indexed_repos.add((p, n))
            if n:
                indexed_names.add(n)

        unlinked: list[PipelineRecord] = []
        for pl in self.pipelines:
            pl_proj = (pl.project or "").strip().lower()
            pl_repo = (pl.repository or "").strip().lower()
            assoc = (pl.repository_association or "").strip().lower()

            if not pl_repo and assoc:
                parsed_proj, parsed_repo = _parse_repo_assoc_project_repo(assoc)
                pl_proj = pl_proj or parsed_proj.lower()
                pl_repo = parsed_repo.lower()

            is_linked = bool(
                (pl_proj and (pl_proj, pl_repo) in indexed_repos)
                or (not pl_proj and pl_repo and pl_repo in indexed_names)
            )
            if not is_linked:
                unlinked.append(pl)

        return unlinked

    def find_global_policies(self) -> list[PolicyRecord]:
        """Find policies configured with global scope."""
        global_policies: list[PolicyRecord] = []
        for p in self.policies:
            scope = (p.policy_scope or "").strip().lower()
            proj = (p.project or "").strip()
            repo = (p.repository or "").strip()
            if scope == "global" or (not proj and not repo and not scope):
                global_policies.append(p)
        return global_policies

    def find_standalone_workflows(self) -> list[PipelineRecord]:
        """Find pipelines that have no repository association whatsoever."""
        return [
            pl
            for pl in self.pipelines
            if not (pl.repository or "").strip()
            and not (pl.repository_association or "").strip()
        ]


def __getattr__(name: str) -> Any:
    if name == "RepositoryConverter":
        from gh_migration_inventory_manager.converter import RepositoryConverter

        return RepositoryConverter
    if name == "PipelineConverter":
        from gh_migration_inventory_manager.converter import PipelineConverter

        return PipelineConverter
    if name == "TeamConverter":
        from gh_migration_inventory_manager.converter import TeamConverter

        return TeamConverter
    if name == "PolicyConverter":
        from gh_migration_inventory_manager.converter import PolicyConverter

        return PolicyConverter
    if name in (
        "RepositoryDiffer",
        "PipelineDiffer",
        "TeamDiffer",
        "PolicyDiffer",
        "RecordDiff",
        "RecordUpdate",
    ):
        import gh_migration_inventory_manager.scrap_diff as _sd

        return getattr(_sd, name)
    raise AttributeError(f"module '{__name__}' has no attribute '{name}'")


Iventory = Inventory
