"""Google Sheets integration layer for inventory tracking."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from google.oauth2 import service_account
from googleapiclient.discovery import build

from gh_migration_inventory_manager.models import (
    InventoryRecord,
    MigrationStatus,
    format_migration_month,
)

_DATE_FORMAT = "%d/%m/%Y"
_RANGE_PATTERN = re.compile(r"[A-Za-z]+(\d+):[A-Za-z]+(\d+)")
_HEADERS = [
    "Nome do Repositório",
    "Tecnologia",
    "Data do Último Commit",
    "Data de Migração (GitHub)",
    "Data de Desativação (Origem)",
    "Status da Migração",
    "Mês de Migração",
]

_COLUMN_ALIAS_MAP: dict[str, int] = {
    "name": 0,
    "nome": 0,
    "nome do repositório": 0,
    "repositório": 0,
    "repository": 0,
    "stack": 1,
    "tecnologia": 1,
    "tech": 1,
    "last_commit": 2,
    "data do último commit": 2,
    "gh_migration": 3,
    "gh_migratation": 3,
    "data de migração (github)": 3,
    "origin_deactivation": 4,
    "data de desativação (origem)": 4,
    "migration_status": 5,
    "status": 5,
    "status da migração": 5,
    "migration_month": 6,
    "mês de migração": 6,
    "month": 6,
}


def _parse_date(value: str | None) -> datetime | None:
    if not value or not value.strip():
        return None
    val = value.strip()
    for fmt in (_DATE_FORMAT, "%Y-%m-%d", "%Y-%m-%dT%H:%M:%S"):
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

    def to_inventory_record(self) -> InventoryRecord:
        """Convert row data to an InventoryRecord instance."""
        status = self.data.get("migration_status", MigrationStatus.WAITTING)
        if isinstance(status, str):
            from gh_migration_inventory_manager.core import Inventory

            status = Inventory._from_sheet_label(status)

        last_commit = self.data.get("last_commit")
        if isinstance(last_commit, str):
            last_commit = _parse_date(last_commit)

        gh_migratation = self.data.get("gh_migratation") or self.data.get(
            "gh_migration"
        )
        if isinstance(gh_migratation, str):
            gh_migratation = _parse_date(gh_migratation)

        origin_deactivation = self.data.get("origin_deactivation")
        if isinstance(origin_deactivation, str):
            origin_deactivation = _parse_date(origin_deactivation)

        return InventoryRecord(
            name=str(self.data.get("name", "")),
            stack=str(self.data.get("stack", "")),
            last_commit=last_commit,
            gh_migratation=gh_migratation,
            origin_deactivation=origin_deactivation,
            migration_status=status,
        )

    @classmethod
    def from_inventory_record(
        cls, record: InventoryRecord, row_index: int | None = None
    ) -> Row:
        """Construct a Row instance from an InventoryRecord."""
        data: dict[str, Any] = {
            "name": record.name,
            "stack": record.stack,
            "last_commit": record.last_commit,
            "gh_migratation": record.gh_migratation,
            "origin_deactivation": record.origin_deactivation,
            "migration_status": record.migration_status,
            "migration_month": record.migration_month,
        }
        row = cls(row_index=row_index, data=data)
        return row


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

    def _to_sheet_row(self, row: Row) -> list[str]:
        """Convert row data to an ordered list of cell string values.

        Values are formatted for Google Sheets columns.
        """
        name = str(row.data.get("name", ""))
        stack = str(row.data.get("stack", ""))
        last_commit_str = _format_date(row.data.get("last_commit"))

        gh_mig = row.data.get("gh_migratation") or row.data.get("gh_migration")
        gh_mig_str = _format_date(gh_mig)
        origin_deact_str = _format_date(row.data.get("origin_deactivation"))

        status_val = row.data.get("migration_status", MigrationStatus.WAITTING)
        if isinstance(status_val, MigrationStatus):
            status_str = status_val.to_sheet_label()
        else:
            from gh_migration_inventory_manager.core import Inventory

            status_str = Inventory._from_sheet_label(str(status_val)).to_sheet_label()

        month_str = str(row.data.get("migration_month", ""))
        if not month_str and gh_mig and isinstance(gh_mig, datetime):
            month_str = format_migration_month(gh_mig)

        return [
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
        from gh_migration_inventory_manager.core import Inventory

        padded = values + [""] * max(0, len(_HEADERS) - len(values))
        data: dict[str, Any] = {
            "name": padded[0],
            "stack": padded[1],
            "last_commit": _parse_date(padded[2]),
            "gh_migratation": _parse_date(padded[3]),
            "origin_deactivation": _parse_date(padded[4]),
            "migration_status": Inventory._from_sheet_label(padded[5]),
            "migration_month": padded[6],
        }
        return Row(row_index=row_index, data=data, values=values)

    def find_by(self, colName: str, content: str) -> list[Row]:
        """Find rows where the specified column matches content."""
        clean_col = colName.strip().lower()
        col_idx = _COLUMN_ALIAS_MAP.get(clean_col)

        if col_idx is None:
            raise ValueError(f"Unknown column identifier: '{colName}'")

        read_range = f"{self.sheet_name}!A2:G"
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
        append_range = f"{self.sheet_name}!A:G"
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
        target_range = f"{self.sheet_name}!A{row.row_index}:G{row.row_index}"
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
        target_range = f"{self.sheet_name}!A{row_index}:G{row_index}"
        self._service.spreadsheets().values().clear(
            spreadsheetId=self.id, range=target_range, body={}
        ).execute()


# Compatibility alias for skeleton typo
Spreadshseet = Spreadsheet
