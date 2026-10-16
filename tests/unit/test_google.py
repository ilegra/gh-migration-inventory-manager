"""Unit tests for Google Sheets integration module."""

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from gh_migration_inventory_manager.google import (
    Credentials,
    Row,
    Spreadsheet,
    Spreadshseet,
)
from gh_migration_inventory_manager.models import InventoryRecord, MigrationStatus


def test_should_add_new_row_when_input_is_valid(
    mock_spreadsheet: Spreadsheet, mock_sheets_service: MagicMock
) -> None:
    """Test adding a new row asserts every returned attribute."""
    mock_append = (
        mock_sheets_service.spreadsheets.return_value.values.return_value.append
    )
    mock_append.return_value.execute.return_value = {
        "updates": {"updatedRange": "Inventario!A10:G10"}
    }

    record = InventoryRecord(
        name="backend-service",
        stack="Go",
        last_commit=datetime(2026, 10, 15, tzinfo=timezone.utc),
        gh_migratation=datetime(2026, 10, 1, tzinfo=timezone.utc),
        origin_deactivation=None,
        migration_status=MigrationStatus.ACTIVE_IN_BOTH,
    )
    row = Row.from_inventory_record(record)

    added_row = mock_spreadsheet.add(row)

    # Assert every returned attribute
    assert added_row.row_index == 10
    assert added_row.data["name"] == "backend-service"
    assert added_row.data["stack"] == "Go"
    assert added_row.data["migration_status"] == MigrationStatus.ACTIVE_IN_BOTH
    assert added_row.data["migration_month"] == "Outubro/2026"
    assert added_row.values[0] == "backend-service"
    assert added_row.values[1] == "Go"
    assert added_row.values[5] == "Migrando (Ativos em Ambas)"
    assert added_row.values[6] == "Outubro/2026"

    mock_append.assert_called_once()


def test_should_update_current_row_when_input_is_valid(
    mock_spreadsheet: Spreadsheet, mock_sheets_service: MagicMock
) -> None:
    """Test updating an existing row targets correct index and updates attributes."""
    mock_update = (
        mock_sheets_service.spreadsheets.return_value.values.return_value.update
    )
    mock_update.return_value.execute.return_value = {"updatedRange": "Inventario!A5:G5"}

    record = InventoryRecord(
        name="frontend-app",
        stack="TypeScript",
        last_commit=datetime(2026, 10, 12, tzinfo=timezone.utc),
        gh_migratation=datetime(2026, 10, 5, tzinfo=timezone.utc),
        origin_deactivation=datetime(2026, 10, 12, tzinfo=timezone.utc),
        migration_status=MigrationStatus.MIGRATED,
    )
    row = Row.from_inventory_record(record, row_index=5)

    updated = mock_spreadsheet.update_row(row)

    # Assert every returned attribute
    assert updated.row_index == 5
    assert updated.data["name"] == "frontend-app"
    assert updated.data["stack"] == "TypeScript"
    assert updated.data["migration_status"] == MigrationStatus.MIGRATED
    assert updated.values[0] == "frontend-app"
    assert updated.values[5] == "Migrados (Somente GitHub)"

    mock_update.assert_called_once()


def test_should_return_empty_object_when_row_is_not_found(
    mock_spreadsheet: Spreadsheet, mock_sheets_service: MagicMock
) -> None:
    """Test querying a non-existent repository returns empty list."""
    mock_get = mock_sheets_service.spreadsheets.return_value.values.return_value.get
    mock_get.return_value.execute.return_value = {
        "values": [
            [
                "existing-repo",
                "Java",
                "10/10/2026",
                "01/10/2026",
                "",
                "Migrando (Ativos em Ambas)",
                "Outubro/2026",
            ]
        ]
    }

    results = mock_spreadsheet.find_by("name", "unknown-repo")
    assert results == []


def test_should_find_row_when_exists(
    mock_spreadsheet: Spreadsheet, mock_sheets_service: MagicMock
) -> None:
    """Test finding a row by column name returns matching populated Row."""
    mock_get = mock_sheets_service.spreadsheets.return_value.values.return_value.get
    mock_get.return_value.execute.return_value = {
        "values": [
            [
                "api-gateway",
                "Java",
                "10/10/2026",
                "01/10/2026",
                "10/10/2026",
                "Migrados (Somente GitHub)",
                "Outubro/2026",
            ],
            [
                "frontend-web",
                "React",
                "12/10/2026",
                "05/10/2026",
                "",
                "Migrando (Ativos em Ambas)",
                "Outubro/2026",
            ],
        ]
    }

    results = mock_spreadsheet.find_by("Nome do Repositório", "api-gateway")

    assert len(results) == 1
    row = results[0]
    assert row.row_index == 2  # Row 1 is header
    assert row.data["name"] == "api-gateway"
    assert row.data["stack"] == "Java"
    assert row.data["migration_status"] == MigrationStatus.MIGRATED
    assert row.data["migration_month"] == "Outubro/2026"


def test_should_raise_when_updating_row_without_index(
    mock_spreadsheet: Spreadsheet,
) -> None:
    """Test updating a row with missing row_index raises ValueError."""
    row = Row(data={"name": "test-repo"})
    with pytest.raises(ValueError, match="Cannot update row without a valid row_index"):
        mock_spreadsheet.update_row(row)


def test_should_raise_when_column_name_is_invalid(
    mock_spreadsheet: Spreadsheet,
) -> None:
    """Test querying an invalid column raises ValueError."""
    with pytest.raises(ValueError, match="Unknown column identifier"):
        mock_spreadsheet.find_by("non_existent_column_header", "some_value")


def test_should_clear_row(
    mock_spreadsheet: Spreadsheet, mock_sheets_service: MagicMock
) -> None:
    """Test clearing a row executes spreadsheet values clear."""
    mock_clear = mock_sheets_service.spreadsheets.return_value.values.return_value.clear
    mock_clear.return_value.execute.return_value = {}

    mock_spreadsheet.clear_row(10)
    mock_clear.assert_called_once()


def test_row_to_and_from_inventory_record(sample_record: InventoryRecord) -> None:
    """Test Row conversions to and from InventoryRecord."""
    row = Row.from_inventory_record(sample_record, row_index=3)
    assert row.row_index == 3
    assert row.data["name"] == sample_record.name

    restored = row.to_inventory_record()
    assert restored.name == sample_record.name
    assert restored.stack == sample_record.stack
    assert restored.migration_status == sample_record.migration_status
    assert restored.gh_migration == sample_record.gh_migration


def test_credentials_configuration() -> None:
    """Test Credentials defaults and custom scopes."""
    creds = Credentials(credentials_path="my-key.json")
    assert creds.credentials_path == "my-key.json"
    assert "https://www.googleapis.com/auth/spreadsheets" in creds.scopes


def test_spreadsheet_alias_and_initialization_error() -> None:
    """Test Spreadshseet typo alias and missing credentials error."""
    assert Spreadshseet is Spreadsheet

    with pytest.raises(
        ValueError, match="Either credentials or a service instance must be provided"
    ):
        Spreadsheet(id="test-id")
