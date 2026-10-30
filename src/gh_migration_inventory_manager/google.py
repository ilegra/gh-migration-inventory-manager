"""Google Sheets integration layer for inventory tracking."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from gh_migration_inventory_manager.inventory import (
    InventoryAccessError,
    InventoryError,
    InventoryNotFoundError,
    InventoryRecord,
    MigrationStatus,
    PipelineRecord,
    PolicyRecord,
    TeamRecord,
    _from_sheet_label,
    format_migration_month,
)
from gh_migration_inventory_manager.scrap import normalize_role


class SpreadsheetError(InventoryError):
    """Base exception for spreadsheet backend operations."""


class SpreadsheetNotFoundError(SpreadsheetError, InventoryNotFoundError):
    """Raised when a specified spreadsheet does not exist or cannot be found."""


class SpreadsheetAccessError(SpreadsheetError, InventoryAccessError):
    """Raised when credentials do not have permission to access the spreadsheet."""


_DATE_FORMAT = "%d/%m/%Y"
_SUPPORTED_DATE_FORMATS: tuple[str, ...] = (
    _DATE_FORMAT,
    "%Y-%m-%d",
    "%Y-%m-%dT%H:%M:%S",
)
_RANGE_PATTERN = re.compile(r"[A-Za-z]+(\d+):[A-Za-z]+(\d+)")
_HEADERS = [
    "Projeto",
    "Nome do Repositório",
    "Tecnologia",
    "Data do Último Commit",
    "Data de Migração (GitHub)",
    "Data de Desativação (Origem)",
    "Status da Migração",
    "Mês de Migração",
]
HEADERS = _HEADERS

_COLUMN_ALIAS_MAP: dict[str, int] = {
    "project": 0,
    "projeto": 0,
    "name": 1,
    "nome": 1,
    "nome do repositório": 1,
    "repositório": 1,
    "repository": 1,
    "stack": 2,
    "tecnologia": 2,
    "tech": 2,
    "last_commit": 3,
    "data do último commit": 3,
    "gh_migration": 4,
    "gh_migratation": 4,
    "data de migração (github)": 4,
    "origin_deactivation": 5,
    "data de desativação (origem)": 5,
    "migration_status": 6,
    "status": 6,
    "status da migração": 6,
    "migration_month": 7,
    "mês de migração": 7,
    "month": 7,
}


def _parse_date(value: datetime | str | None) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str) or not value.strip():
        return None
    val = value.strip()
    for fmt in _SUPPORTED_DATE_FORMATS:
        try:
            return datetime.strptime(val, fmt)
        except ValueError:
            continue
    return None


def _format_date(dt: datetime | str | None) -> str:
    if isinstance(dt, datetime):
        return dt.strftime(_DATE_FORMAT)
    return str(dt or "")


@dataclass
class Row:
    """Represents a row in a Google Spreadsheet."""

    row_index: int | None = None
    data: dict[str, Any] = field(default_factory=dict)
    values: list[str] = field(default_factory=list)


_WORKFLOW_HEADERS = [
    "ID do Workflow",
    "Plataforma",
    "Tipo de Pipeline",
    "Repositório Associado",
    "URL do Repositório",
    "Caminho do Workflow",
    "Componentes Compartilhados",
    "Ambiente de Execução",
    "Branch Padrão",
    "Último Build",
    "Status do Último Build",
    "Status da Migração",
]
WORKFLOW_HEADERS = _WORKFLOW_HEADERS


def _format_status_label(status_val: Any) -> str:
    """Format migration status into localized spreadsheet label."""
    if isinstance(status_val, MigrationStatus):
        return status_val.to_sheet_label()
    return _from_sheet_label(str(status_val)).to_sheet_label()


_WORKFLOW_COLUMN_ALIAS_MAP: dict[str, int] = {
    "workflow_id": 0,
    "id do workflow": 0,
    "id": 0,
    "name": 0,
    "platform": 1,
    "plataforma": 1,
    "pipeline_type": 2,
    "tipo de pipeline": 2,
    "type": 2,
    "repository_association": 3,
    "repositório associado": 3,
    "repositorio associado": 3,
    "repository_url": 4,
    "url do repositório": 4,
    "url do repositorio": 4,
    "file_path": 5,
    "caminho do workflow": 5,
    "path": 5,
    "shared_components": 6,
    "componentes compartilhados": 6,
    "componentes_compartilhados": 6,
    "bibliotecas compartilhadas": 6,
    "bibliotecas_compartilhadas": 6,
    "shared_libraries": 6,
    "templates": 6,
    "includes": 6,
    "reusable_workflows": 6,
    "execution_environment": 7,
    "ambiente de execução": 7,
    "ambiente de execucao": 7,
    "ambiente_de_execucao": 7,
    "labels de agentes": 7,
    "agent_labels": 7,
    "runs_on": 7,
    "runner_labels": 7,
    "tags": 7,
    "pool": 7,
    "branch": 8,
    "branch padrão": 8,
    "branch padrao": 8,
    "default_branch": 8,
    "last_build": 9,
    "último build": 9,
    "ultimo build": 9,
    "last_status": 10,
    "status do último build": 10,
    "status do ultimo build": 10,
    "migration_status": 11,
    "status da migração": 11,
    "status da migracao": 11,
    "status": 11,
}

_TOPOLOGY_HEADERS = [
    "Nome do Time",
    "Slug do Time",
    "Contagem de Membros",
    "Repositório / Projeto",
    "Papel Normalizado (GitHub)",
    "Papel de Origem",
    "Status da Migração",
]
TOPOLOGY_HEADERS = _TOPOLOGY_HEADERS
_WORKFLOW_SHEET_NAMES: tuple[str, ...] = ("workflows", "pipelines")
_TOPOLOGY_SHEET_NAMES: tuple[str, ...] = ("topology", "teams", "topologia")
_POLICIES_HEADERS = [
    "Escopo da Política",
    "Padrão de Ref / Branch",
    "Tipo de Regra (GitHub)",
    "Parâmetros da Regra",
    "Slugs de Times de Bypass",
    "Política de Origem (Raw)",
    "Status da Migração",
]
POLICIES_HEADERS = _POLICIES_HEADERS
_POLICIES_SHEET_NAMES: tuple[str, ...] = (
    "policies",
    "politicas",
    "políticas",
    "governance",
    "branch_policies",
)

_POLICIES_COLUMN_ALIAS_MAP: dict[str, int] = {
    "policy_scope": 0,
    "escopo da política": 0,
    "escopo da politica": 0,
    "escopo": 0,
    "scope": 0,
    "target_ref_pattern": 1,
    "padrão de ref / branch": 1,
    "padrao de ref / branch": 1,
    "ref_pattern": 1,
    "branch": 1,
    "ref": 1,
    "target": 1,
    "normalized_rule_type": 2,
    "tipo de regra (github)": 2,
    "tipo de regra": 2,
    "rule_type": 2,
    "tipo": 2,
    "rule": 2,
    "rule_parameters": 3,
    "parâmetros da regra": 3,
    "parametros da regra": 3,
    "parâmetros": 3,
    "parametros": 3,
    "parameters": 3,
    "params": 3,
    "bypass_team_slugs": 4,
    "slugs de times de bypass": 4,
    "times de bypass": 4,
    "bypass_teams": 4,
    "bypass": 4,
    "bypasses": 4,
    "origin_raw_policy": 5,
    "política de origem (raw)": 5,
    "politica de origem (raw)": 5,
    "origem": 5,
    "raw": 5,
    "origin": 5,
    "raw_policy": 5,
    "migration_status": 6,
    "status da migração": 6,
    "status da migracao": 6,
    "status": 6,
}

_TOPOLOGY_COLUMN_ALIAS_MAP: dict[str, int] = {
    "team_name": 0,
    "nome do time": 0,
    "time": 0,
    "team": 0,
    "name": 0,
    "nome": 0,
    "team_slug": 1,
    "slug do time": 1,
    "slug": 1,
    "member_count": 2,
    "contagem de membros": 2,
    "membros": 2,
    "members": 2,
    "count": 2,
    "target_repo_or_project": 3,
    "repositório / projeto": 3,
    "repositorio / projeto": 3,
    "repositório": 3,
    "repositorio": 3,
    "target": 3,
    "scope": 3,
    "project": 3,
    "normalized_role": 4,
    "papel normalizado (github)": 4,
    "papel normalizado": 4,
    "role": 4,
    "papel": 4,
    "origin_role": 5,
    "papel de origem": 5,
    "origin": 5,
    "migration_status": 6,
    "status da migração": 6,
    "status da migracao": 6,
    "status": 6,
}


class TeamRowConverter:
    """Conversion utility between Row and TeamRecord instances."""

    @staticmethod
    def to_team_record(row: Row) -> TeamRecord:
        """Convert a Row instance to a TeamRecord instance."""
        if row is None:
            raise ValueError("Row cannot be None.")
        status = row.data.get("migration_status", MigrationStatus.WAITTING)
        if isinstance(status, str):
            status = _from_sheet_label(status)

        raw_count = row.data.get("member_count", 0)
        try:
            member_count = int(raw_count) if str(raw_count).strip() else 0
        except (ValueError, TypeError):
            member_count = 0

        norm_role = row.data.get("normalized_role")
        if norm_role is None or norm_role == "":
            norm_role = "Read"
        elif hasattr(norm_role, "value"):
            norm_role = norm_role.value
        else:
            try:
                norm_role = normalize_role(str(norm_role)).value
            except ValueError:
                norm_role = str(norm_role)

        return TeamRecord(
            team_name=str(row.data.get("team_name", "")),
            team_slug=str(row.data.get("team_slug", "")),
            member_count=member_count,
            target_repo_or_project=str(row.data.get("target_repo_or_project", "")),
            normalized_role=str(norm_role),
            origin_role=str(row.data.get("origin_role", "")),
            migration_status=status,
        )

    @staticmethod
    def from_team_record(record: TeamRecord, row_index: int | None = None) -> Row:
        """Construct a Row instance from a TeamRecord."""
        if record is None:
            raise ValueError("TeamRecord cannot be None.")
        norm_role = getattr(
            record.normalized_role, "value", str(record.normalized_role)
        )
        data: dict[str, Any] = {
            "team_name": record.team_name,
            "team_slug": record.team_slug,
            "member_count": record.member_count,
            "target_repo_or_project": record.target_repo_or_project,
            "normalized_role": norm_role,
            "origin_role": record.origin_role,
            "migration_status": record.migration_status,
        }
        return Row(row_index=row_index, data=data)


class PolicyRowConverter:
    """Conversion utility between Row and PolicyRecord instances."""

    @staticmethod
    def to_policy_record(row: Row) -> PolicyRecord:
        """Convert a Row instance to a PolicyRecord instance."""
        if row is None:
            raise ValueError("Row cannot be None.")
        status = row.data.get("migration_status", MigrationStatus.WAITTING)
        if isinstance(status, str):
            status = _from_sheet_label(status)

        return PolicyRecord(
            policy_scope=str(row.data.get("policy_scope", "")),
            target_ref_pattern=str(row.data.get("target_ref_pattern", "")),
            normalized_rule_type=str(row.data.get("normalized_rule_type", "")),
            rule_parameters=str(row.data.get("rule_parameters", "")),
            bypass_team_slugs=str(row.data.get("bypass_team_slugs", "")),
            origin_raw_policy=str(row.data.get("origin_raw_policy", "")),
            migration_status=status,
        )

    @staticmethod
    def from_policy_record(record: PolicyRecord, row_index: int | None = None) -> Row:
        """Construct a Row instance from a PolicyRecord."""
        if record is None:
            raise ValueError("PolicyRecord cannot be None.")
        data: dict[str, Any] = {
            "policy_scope": record.policy_scope,
            "target_ref_pattern": record.target_ref_pattern,
            "normalized_rule_type": record.normalized_rule_type,
            "rule_parameters": record.rule_parameters,
            "bypass_team_slugs": record.bypass_team_slugs,
            "origin_raw_policy": record.origin_raw_policy,
            "migration_status": record.migration_status,
        }
        return Row(row_index=row_index, data=data)


class WorkflowRowConverter:
    """Conversion utility between Row and PipelineRecord instances."""

    @staticmethod
    def to_pipeline_record(row: Row) -> PipelineRecord:
        """Convert a Row instance to a PipelineRecord instance."""
        if row is None:
            raise ValueError("Row cannot be None.")
        status = row.data.get("migration_status", MigrationStatus.WAITTING)
        if isinstance(status, str):
            status = _from_sheet_label(status)

        shared_components = str(
            row.data.get("shared_components")
            or row.data.get("componentes compartilhados")
            or row.data.get("bibliotecas compartilhadas")
            or ""
        )
        execution_env = str(
            row.data.get("execution_environment")
            or row.data.get("ambiente de execução")
            or row.data.get("labels de agentes")
            or ""
        )

        return PipelineRecord(
            workflow_id=str(row.data.get("workflow_id", "")),
            platform=str(row.data.get("platform", "jenkins")),
            pipeline_type=str(row.data.get("pipeline_type", "")),
            repository_association=str(row.data.get("repository_association", "")),
            repository_url=str(row.data.get("repository_url", "")),
            file_path=str(row.data.get("file_path", "")),
            shared_components=shared_components,
            execution_environment=execution_env,
            branch=str(row.data.get("branch", "")),
            last_build=_parse_date(row.data.get("last_build")),
            last_status=str(row.data.get("last_status", "")),
            migration_status=status,
        )

    @staticmethod
    def from_pipeline_record(
        record: PipelineRecord, row_index: int | None = None
    ) -> Row:
        """Construct a Row instance from a PipelineRecord."""
        if record is None:
            raise ValueError("PipelineRecord cannot be None.")
        data: dict[str, Any] = {
            "workflow_id": record.workflow_id,
            "platform": record.platform,
            "pipeline_type": record.pipeline_type,
            "repository_association": record.repository_association,
            "repository_url": record.repository_url,
            "file_path": record.file_path,
            "shared_components": record.shared_components,
            "execution_environment": record.execution_environment,
            "branch": record.branch,
            "last_build": record.last_build,
            "last_status": record.last_status,
            "migration_status": record.migration_status,
        }
        return Row(row_index=row_index, data=data)


class RowConverter:
    """Conversion utility between Row and InventoryRecord instances."""

    @staticmethod
    def to_inventory_record(row: Row) -> InventoryRecord:
        """Convert a Row instance to an InventoryRecord instance."""
        status = row.data.get("migration_status", MigrationStatus.WAITTING)
        if isinstance(status, str):
            status = _from_sheet_label(status)

        return InventoryRecord(
            name=str(row.data.get("name", "")),
            project=str(row.data.get("project", "")),
            stack=str(row.data.get("stack", "")),
            last_commit=_parse_date(row.data.get("last_commit")),
            gh_migratation=_parse_date(
                row.data.get("gh_migratation") or row.data.get("gh_migration")
            ),
            origin_deactivation=_parse_date(row.data.get("origin_deactivation")),
            migration_status=status,
        )

    @staticmethod
    def from_inventory_record(
        record: InventoryRecord, row_index: int | None = None
    ) -> Row:
        """Construct a Row instance from an InventoryRecord."""
        data: dict[str, Any] = {
            "project": record.project,
            "name": record.name,
            "stack": record.stack,
            "last_commit": record.last_commit,
            "gh_migratation": record.gh_migratation,
            "origin_deactivation": record.origin_deactivation,
            "migration_status": record.migration_status,
            "migration_month": record.migration_month,
        }
        return Row(row_index=row_index, data=data)


@dataclass
class Credentials:
    """Credentials configuration for Google Sheets API access."""

    credentials_path: str
    scopes: list[str] = field(
        default_factory=lambda: ["https://www.googleapis.com/auth/spreadsheets"]
    )


class Spreadsheet:
    """Client for reading and updating rows in a Google Spreadsheet."""

    def __init__(
        self,
        id: str,
        credentials: Credentials | None = None,
        sheet_name: str = "Inventario",
        service: Any = None,
    ) -> None:
        self.id = id
        self.sheet_name = sheet_name
        self.credentials = credentials

        if service is not None:
            self._service = service
        elif credentials is not None:
            google_creds = self._get_google_credentials(credentials)
            self._service = build(
                "sheets", "v4", credentials=google_creds, cache_discovery=False
            )
        else:
            raise ValueError(
                "Either credentials or a service instance must be provided."
            )

    def _get_google_credentials(self, credentials: Credentials) -> Any:
        """Load service account credentials from credentials configuration."""
        loader: Any = service_account.Credentials.from_service_account_file
        return loader(credentials.credentials_path, scopes=credentials.scopes)

    def validate(self) -> None:
        """Validate that the spreadsheet reference exists and is accessible.

        Raises:
            ValueError: If spreadsheet ID is empty or invalid.
            SpreadsheetNotFoundError: If spreadsheet does not exist (HTTP 404).
            SpreadsheetAccessError: If access is denied (HTTP 403) or
                unauthorized (HTTP 401).
            SpreadsheetError: If any other error occurs contacting Google Sheets.
        """
        clean_id = (self.id or "").strip()
        if not clean_id:
            raise ValueError("Spreadsheet ID cannot be empty.")

        try:
            self._service.spreadsheets().get(
                spreadsheetId=clean_id, fields="spreadsheetId,properties.title"
            ).execute()
        except HttpError as err:
            status = getattr(getattr(err, "resp", None), "status", None)
            if status == 404:
                raise SpreadsheetNotFoundError(
                    f"Spreadsheet not found: '{clean_id}'."
                ) from err
            if status in (401, 403):
                raise SpreadsheetAccessError(
                    f"Access denied to spreadsheet: '{clean_id}'."
                ) from err
            raise SpreadsheetError(
                f"Error accessing spreadsheet '{clean_id}': {err}"
            ) from err
        except Exception as err:
            raise SpreadsheetError(
                f"Error accessing spreadsheet '{clean_id}': {err}"
            ) from err

    def _end_column_for(self, target: str | None = None) -> str:
        """Return the last column letter for the specified or active sheet."""
        clean = (target or self.sheet_name or "").strip().lower()
        if clean in _WORKFLOW_SHEET_NAMES:
            return "L"
        if clean in _TOPOLOGY_SHEET_NAMES or clean in _POLICIES_SHEET_NAMES:
            return "G"
        return "H"

    def _is_workflow_sheet(self) -> bool:
        return (self.sheet_name or "").strip().lower() in _WORKFLOW_SHEET_NAMES

    def _is_topology_sheet(self) -> bool:
        return (self.sheet_name or "").strip().lower() in _TOPOLOGY_SHEET_NAMES

    def _is_policy_sheet(self) -> bool:
        return (self.sheet_name or "").strip().lower() in _POLICIES_SHEET_NAMES

    def ensure_sheet(
        self,
        sheet_name: str | None = None,
        headers: list[str] | None = None,
    ) -> None:
        """Ensure the target sheet tab exists, creating it with headers if missing."""
        target = sheet_name or self.sheet_name
        hdrs = headers
        if hdrs is None:
            clean_target = (target or "").strip().lower()
            if clean_target in _WORKFLOW_SHEET_NAMES:
                hdrs = _WORKFLOW_HEADERS
            elif clean_target in _TOPOLOGY_SHEET_NAMES:
                hdrs = _TOPOLOGY_HEADERS
            elif clean_target in _POLICIES_SHEET_NAMES:
                hdrs = _POLICIES_HEADERS
            else:
                hdrs = _HEADERS

        clean_id = (self.id or "").strip()
        if not clean_id:
            return

        try:
            meta = (
                self._service.spreadsheets()
                .get(spreadsheetId=clean_id, fields="sheets.properties.title")
                .execute()
            )
            existing_sheets = [
                s.get("properties", {}).get("title")
                for s in meta.get("sheets", [])
                if s.get("properties", {}).get("title")
            ]
            if target not in existing_sheets:
                req = {"addSheet": {"properties": {"title": target}}}
                self._service.spreadsheets().batchUpdate(
                    spreadsheetId=clean_id,
                    body={"requests": [req]},
                ).execute()

                if hdrs:
                    end_col = chr(ord("A") + len(hdrs) - 1)
                    header_range = f"{target}!A1:{end_col}1"
                    self._service.spreadsheets().values().update(
                        spreadsheetId=clean_id,
                        range=header_range,
                        valueInputOption="USER_ENTERED",
                        body={"values": [hdrs]},
                    ).execute()
        except Exception as err:
            raise SpreadsheetError(f"Error ensuring sheet '{target}': {err}") from err

    def clear_tab(self, sheet_name: str | None = None) -> None:
        """Clear all data rows in target tab (keeping row 1 headers intact)."""
        target = sheet_name or self.sheet_name
        clean_id = (self.id or "").strip()
        if not clean_id:
            return
        self.ensure_sheet(target)
        end_col = self._end_column_for(target)
        clear_range = f"{target}!A2:{end_col}"
        try:
            self._service.spreadsheets().values().clear(
                spreadsheetId=clean_id,
                range=clear_range,
            ).execute()
        except Exception as err:
            raise SpreadsheetError(f"Error clearing sheet '{target}': {err}") from err

    def _to_sheet_row(self, row: Row) -> list[str]:
        """Convert row data to an ordered list of cell string values.

        Values are formatted for Google Sheets columns.
        """
        if self._is_workflow_sheet():
            workflow_id = str(row.data.get("workflow_id", ""))
            platform = str(row.data.get("platform", "jenkins"))
            pipeline_type = str(row.data.get("pipeline_type", ""))
            repo_assoc = str(row.data.get("repository_association", ""))
            repo_url = str(row.data.get("repository_url", ""))
            file_path = str(row.data.get("file_path", ""))
            shared_components = str(
                row.data.get("shared_components")
                or row.data.get("componentes compartilhados")
                or row.data.get("bibliotecas compartilhadas")
                or ""
            )
            execution_env = str(
                row.data.get("execution_environment")
                or row.data.get("ambiente de execução")
                or row.data.get("labels de agentes")
                or ""
            )
            branch = str(row.data.get("branch", ""))
            last_build_str = _format_date(row.data.get("last_build"))
            last_status = str(row.data.get("last_status", ""))
            status_str = _format_status_label(
                row.data.get("migration_status", MigrationStatus.WAITTING)
            )

            return [
                workflow_id,
                platform,
                pipeline_type,
                repo_assoc,
                repo_url,
                file_path,
                shared_components,
                execution_env,
                branch,
                last_build_str,
                last_status,
                status_str,
            ]

        if self._is_topology_sheet():
            team_name = str(row.data.get("team_name", ""))
            team_slug = str(row.data.get("team_slug", ""))
            member_count = str(row.data.get("member_count", 0))
            target = str(row.data.get("target_repo_or_project", ""))
            raw_role: Any = row.data.get("normalized_role")
            if raw_role is not None and hasattr(raw_role, "value"):
                norm_role = str(raw_role.value)
            elif raw_role:
                try:
                    norm_role = normalize_role(str(raw_role)).value
                except ValueError:
                    norm_role = str(raw_role)
            else:
                norm_role = "Read"
            origin_role = str(row.data.get("origin_role", ""))
            status_str = _format_status_label(
                row.data.get("migration_status", MigrationStatus.WAITTING)
            )
            return [
                team_name,
                team_slug,
                member_count,
                target,
                norm_role,
                origin_role,
                status_str,
            ]

        if self._is_policy_sheet():
            policy_scope = str(row.data.get("policy_scope", ""))
            target_ref = str(row.data.get("target_ref_pattern", ""))
            rule_type = str(row.data.get("normalized_rule_type", ""))
            params = str(row.data.get("rule_parameters", ""))
            bypasses = str(row.data.get("bypass_team_slugs", ""))
            origin_raw = str(row.data.get("origin_raw_policy", ""))
            status_str = _format_status_label(
                row.data.get("migration_status", MigrationStatus.WAITTING)
            )
            return [
                policy_scope,
                target_ref,
                rule_type,
                params,
                bypasses,
                origin_raw,
                status_str,
            ]

        project = str(row.data.get("project", ""))
        name = str(row.data.get("name", ""))
        stack = str(row.data.get("stack", ""))
        last_commit_str = _format_date(row.data.get("last_commit"))

        gh_mig = row.data.get("gh_migratation") or row.data.get("gh_migration")
        gh_mig_str = _format_date(gh_mig)
        origin_deact_str = _format_date(row.data.get("origin_deactivation"))
        status_str = _format_status_label(
            row.data.get("migration_status", MigrationStatus.WAITTING)
        )

        month_str = str(row.data.get("migration_month", ""))
        if not month_str and gh_mig and isinstance(gh_mig, datetime):
            month_str = format_migration_month(gh_mig)

        return [
            project,
            name,
            stack,
            last_commit_str,
            gh_mig_str,
            origin_deact_str,
            status_str,
            month_str,
        ]

    def _from_sheet_row(self, values: list[str], row_index: int | None = None) -> Row:
        """Parse raw sheet cell values into a Row."""
        if self._is_workflow_sheet():
            padded = values + [""] * max(0, len(_WORKFLOW_HEADERS) - len(values))
            data: dict[str, Any] = {
                "workflow_id": padded[0],
                "platform": padded[1],
                "pipeline_type": padded[2],
                "repository_association": padded[3],
                "repository_url": padded[4],
                "file_path": padded[5],
                "shared_components": padded[6],
                "execution_environment": padded[7],
                "branch": padded[8],
                "last_build": _parse_date(padded[9]),
                "last_status": padded[10],
                "migration_status": _from_sheet_label(padded[11]),
            }
            return Row(row_index=row_index, data=data, values=values)

        if self._is_topology_sheet():
            padded = values + [""] * max(0, len(_TOPOLOGY_HEADERS) - len(values))
            try:
                cnt = int(padded[2]) if padded[2].strip() else 0
            except (ValueError, TypeError):
                cnt = 0
            data = {
                "team_name": padded[0],
                "team_slug": padded[1],
                "member_count": cnt,
                "target_repo_or_project": padded[3],
                "normalized_role": padded[4],
                "origin_role": padded[5],
                "migration_status": _from_sheet_label(padded[6]),
            }
            return Row(row_index=row_index, data=data, values=values)

        if self._is_policy_sheet():
            padded = values + [""] * max(0, len(_POLICIES_HEADERS) - len(values))
            data = {
                "policy_scope": padded[0],
                "target_ref_pattern": padded[1],
                "normalized_rule_type": padded[2],
                "rule_parameters": padded[3],
                "bypass_team_slugs": padded[4],
                "origin_raw_policy": padded[5],
                "migration_status": _from_sheet_label(padded[6]),
            }
            return Row(row_index=row_index, data=data, values=values)

        padded = values + [""] * max(0, len(_HEADERS) - len(values))
        data = {
            "project": padded[0],
            "name": padded[1],
            "stack": padded[2],
            "last_commit": _parse_date(padded[3]),
            "gh_migratation": _parse_date(padded[4]),
            "origin_deactivation": _parse_date(padded[5]),
            "migration_status": _from_sheet_label(padded[6]),
            "migration_month": padded[7],
        }
        return Row(row_index=row_index, data=data, values=values)

    @property
    def _end_column(self) -> str:
        """Return the last column letter based on the current sheet structure."""
        return self._end_column_for()

    def find_by(
        self,
        column_name: str | None = None,
        content: str = "",
        *,
        colName: str | None = None,
    ) -> list[Row]:
        """Find rows where the specified column matches content."""
        target_col = column_name if column_name is not None else colName
        if target_col is None:
            raise ValueError("Column name cannot be None.")
        clean_col = target_col.strip().lower()
        if self._is_workflow_sheet():
            alias_map = _WORKFLOW_COLUMN_ALIAS_MAP
        elif self._is_topology_sheet():
            alias_map = _TOPOLOGY_COLUMN_ALIAS_MAP
        elif self._is_policy_sheet():
            alias_map = _POLICIES_COLUMN_ALIAS_MAP
        else:
            alias_map = _COLUMN_ALIAS_MAP
        col_idx = alias_map.get(clean_col)

        if col_idx is None:
            raise ValueError(f"Unknown column identifier: '{target_col}'")

        read_range = f"{self.sheet_name}!A2:{self._end_column}"
        resp = (
            self._service.spreadsheets()
            .values()
            .get(spreadsheetId=self.id, range=read_range)
            .execute()
        )
        raw_rows: list[list[str]] = resp.get("values", [])

        target = content.strip().lower()
        matched: list[Row] = []

        for idx, row_vals in enumerate(raw_rows):
            actual_row_number = idx + 2  # Row 1 is header
            if col_idx < len(row_vals):
                val = row_vals[col_idx].strip().lower()
                if val == target:
                    matched.append(
                        self._from_sheet_row(row_vals, row_index=actual_row_number)
                    )

        return matched

    def add(self, row: Row) -> Row:
        """Append a new row to the spreadsheet and update its row_index."""
        values = self._to_sheet_row(row)
        append_range = f"{self.sheet_name}!A:{self._end_column}"
        body = {"values": [values]}

        res = (
            self._service.spreadsheets()
            .values()
            .append(
                spreadsheetId=self.id,
                range=append_range,
                valueInputOption="USER_ENTERED",
                insertDataOption="INSERT_ROWS",
                body=body,
            )
            .execute()
        )

        updated_range = res.get("updates", {}).get("updatedRange", "")
        match = _RANGE_PATTERN.search(updated_range)
        if match:
            row.row_index = int(match.group(1))

        row.values = values
        return row

    def update_row(self, row: Row) -> Row:
        """Update an existing row in the spreadsheet by its row_index."""
        if row.row_index is None:
            raise ValueError("Cannot update row without a valid row_index.")

        values = self._to_sheet_row(row)
        target_range = (
            f"{self.sheet_name}!A{row.row_index}:{self._end_column}{row.row_index}"
        )
        body = {"values": [values]}

        self._service.spreadsheets().values().update(
            spreadsheetId=self.id,
            range=target_range,
            valueInputOption="USER_ENTERED",
            body=body,
        ).execute()

        row.values = values
        return row

    def clear_row(self, row_index: int) -> None:
        """Clear all cell values in the specified row."""
        target_range = f"{self.sheet_name}!A{row_index}:{self._end_column}{row_index}"
        self._service.spreadsheets().values().clear(
            spreadsheetId=self.id, range=target_range, body={}
        ).execute()


Spreadshseet = Spreadsheet
