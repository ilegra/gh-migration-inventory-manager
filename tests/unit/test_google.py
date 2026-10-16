"""Unit tests for Google Sheets integration module."""

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from gh_migration_inventory_manager.google import (
    Credentials,
    Row,
    RowConverter,
    Spreadsheet,
    Spreadshseet,
)
from gh_migration_inventory_manager.inventory import (
    InventoryRecord,
    MigrationStatus,
)


def test_should_add_new_row_when_input_is_valid(
    mock_spreadsheet: Spreadsheet, mock_sheets_service: MagicMock
) -> None:
    """Test adding a new row asserts every returned attribute."""
    mock_append = (
        mock_sheets_service.spreadsheets.return_value.values.return_value.append
    )
    mock_append.return_value.execute.return_value = {
        "updates": {"updatedRange": "Inventario!A10:H10"}
    }

    record = InventoryRecord(
        name="backend-service",
        project="core-platform",
        stack="Go",
        last_commit=datetime(2026, 10, 15, tzinfo=timezone.utc),
        gh_migratation=datetime(2026, 10, 1, tzinfo=timezone.utc),
        origin_deactivation=None,
        migration_status=MigrationStatus.ACTIVE_IN_BOTH,
    )
    row = RowConverter.from_inventory_record(record)

    added_row = mock_spreadsheet.add(row)

    assert added_row.row_index == 10
    assert added_row.data["project"] == "core-platform"
    assert added_row.data["name"] == "backend-service"
    assert added_row.data["stack"] == "Go"
    assert added_row.data["migration_status"] == MigrationStatus.ACTIVE_IN_BOTH
    assert added_row.data["migration_month"] == "Outubro/2026"
    assert added_row.values[0] == "core-platform"
    assert added_row.values[1] == "backend-service"
    assert added_row.values[2] == "Go"
    assert added_row.values[6] == "Migrando (Ativos em Ambas)"
    assert added_row.values[7] == "Outubro/2026"

    mock_append.assert_called_once()


def test_should_update_current_row_when_input_is_valid(
    mock_spreadsheet: Spreadsheet, mock_sheets_service: MagicMock
) -> None:
    """Test updating an existing row targets correct index and updates attributes."""
    mock_update = (
        mock_sheets_service.spreadsheets.return_value.values.return_value.update
    )
    mock_update.return_value.execute.return_value = {"updatedRange": "Inventario!A5:H5"}

    record = InventoryRecord(
        name="frontend-app",
        project="web-portal",
        stack="TypeScript",
        last_commit=datetime(2026, 10, 12, tzinfo=timezone.utc),
        gh_migratation=datetime(2026, 10, 5, tzinfo=timezone.utc),
        origin_deactivation=datetime(2026, 10, 12, tzinfo=timezone.utc),
        migration_status=MigrationStatus.MIGRATED,
    )
    row = RowConverter.from_inventory_record(record, row_index=5)

    updated = mock_spreadsheet.update_row(row)

    assert updated.row_index == 5
    assert updated.data["project"] == "web-portal"
    assert updated.data["name"] == "frontend-app"
    assert updated.data["stack"] == "TypeScript"
    assert updated.data["migration_status"] == MigrationStatus.MIGRATED
    assert updated.values[0] == "web-portal"
    assert updated.values[1] == "frontend-app"
    assert updated.values[6] == "Migrados (Somente GitHub)"

    mock_update.assert_called_once()


def test_should_return_empty_object_when_row_is_not_found(
    mock_spreadsheet: Spreadsheet, mock_sheets_service: MagicMock
) -> None:
    """Test querying a non-existent repository returns empty list."""
    mock_get = mock_sheets_service.spreadsheets.return_value.values.return_value.get
    mock_get.return_value.execute.return_value = {
        "values": [
            [
                "portal",
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
                "core-team",
                "api-gateway",
                "Java",
                "10/10/2026",
                "01/10/2026",
                "10/10/2026",
                "Migrados (Somente GitHub)",
                "Outubro/2026",
            ],
            [
                "web-team",
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
    assert row.data["project"] == "core-team"
    assert row.data["name"] == "api-gateway"
    assert row.data["stack"] == "Java"
    assert row.data["migration_status"] == MigrationStatus.MIGRATED
    assert row.data["migration_month"] == "Outubro/2026"

    # Also verify find by project column
    project_results = mock_spreadsheet.find_by("project", "core-team")
    assert len(project_results) == 1
    assert project_results[0].data["name"] == "api-gateway"


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


def test_row_converter_roundtrip(sample_record: InventoryRecord) -> None:
    """Test roundtrip conversion between InventoryRecord and Row via RowConverter."""
    row = RowConverter.from_inventory_record(sample_record, row_index=3)
    assert row.row_index == 3
    assert row.data["name"] == sample_record.name
    assert row.data["project"] == sample_record.project

    restored = RowConverter.to_inventory_record(row)
    assert restored.name == sample_record.name
    assert restored.project == sample_record.project
    assert restored.stack == sample_record.stack
    assert restored.migration_status == sample_record.migration_status
    assert restored.gh_migration == sample_record.gh_migration
    assert restored.last_commit == sample_record.last_commit
    assert restored.origin_deactivation == sample_record.origin_deactivation


def test_row_converter_from_inventory_record_maps_all_fields() -> None:
    """Test RowConverter.from_inventory_record maps every record field into Row."""
    record = InventoryRecord(
        name="full-repo",
        project="full-project",
        stack="Rust",
        last_commit=datetime(2026, 8, 1, tzinfo=timezone.utc),
        gh_migratation=datetime(2026, 8, 15, tzinfo=timezone.utc),
        origin_deactivation=datetime(2026, 8, 20, tzinfo=timezone.utc),
        migration_status=MigrationStatus.MIGRATED,
    )
    row = RowConverter.from_inventory_record(record, row_index=42)
    assert row.row_index == 42
    assert row.data["project"] == "full-project"
    assert row.data["name"] == "full-repo"
    assert row.data["stack"] == "Rust"
    assert row.data["last_commit"] == datetime(2026, 8, 1, tzinfo=timezone.utc)
    assert row.data["gh_migratation"] == datetime(2026, 8, 15, tzinfo=timezone.utc)
    assert row.data["origin_deactivation"] == datetime(2026, 8, 20, tzinfo=timezone.utc)
    assert row.data["migration_status"] == MigrationStatus.MIGRATED
    assert row.data["migration_month"] == "Agosto/2026"
    assert row.values == []


def test_row_converter_from_inventory_record_default_row_index() -> None:
    """Test RowConverter.from_inventory_record defaults row_index to None."""
    record = InventoryRecord(name="repo-no-index")
    row = RowConverter.from_inventory_record(record)
    assert row.row_index is None


def test_row_converter_to_inventory_record_maps_all_fields() -> None:
    """Test RowConverter.to_inventory_record correctly builds InventoryRecord."""
    now = datetime(2026, 10, 15, tzinfo=timezone.utc)
    row = Row(
        row_index=1,
        data={
            "project": "sample-project",
            "name": "sample-service",
            "stack": "Go",
            "last_commit": now,
            "gh_migratation": now,
            "origin_deactivation": now,
            "migration_status": MigrationStatus.MIGRATED,
        },
    )
    record = RowConverter.to_inventory_record(row)
    assert record.project == "sample-project"
    assert record.name == "sample-service"
    assert record.stack == "Go"
    assert record.last_commit == now
    assert record.gh_migratation == now
    assert record.origin_deactivation == now
    assert record.migration_status == MigrationStatus.MIGRATED


@pytest.mark.parametrize(
    ("raw_status", "expected_enum"),
    [
        ("Migrados (Somente GitHub)", MigrationStatus.MIGRATED),
        ("Migrando (Ativos em Ambas)", MigrationStatus.ACTIVE_IN_BOTH),
        ("Ignorado", MigrationStatus.SKIPPED),
        ("Aguardando Migração", MigrationStatus.WAITTING),
        ("migrated", MigrationStatus.MIGRATED),
        ("active_in_both", MigrationStatus.ACTIVE_IN_BOTH),
        ("skipped", MigrationStatus.SKIPPED),
        ("waitting", MigrationStatus.WAITTING),
        ("", MigrationStatus.WAITTING),
    ],
)
def test_row_converter_to_inventory_record_with_string_migration_status(
    raw_status: str, expected_enum: MigrationStatus
) -> None:
    """Test RowConverter.to_inventory_record converts string status to enum."""
    row = Row(data={"name": "test-repo", "migration_status": raw_status})
    record = RowConverter.to_inventory_record(row)
    assert record.migration_status == expected_enum


@pytest.mark.parametrize(
    ("date_str", "expected_dt"),
    [
        ("15/10/2026", datetime(2026, 10, 15)),
        ("2026-10-15", datetime(2026, 10, 15)),
        ("2026-10-15T08:30:00", datetime(2026, 10, 15, 8, 30, 0)),
    ],
)
def test_row_converter_to_inventory_record_with_string_dates(
    date_str: str, expected_dt: datetime
) -> None:
    """Test RowConverter.to_inventory_record parses string date formats."""
    row = Row(
        data={
            "name": "dates-repo",
            "last_commit": date_str,
            "gh_migratation": date_str,
            "origin_deactivation": date_str,
        }
    )
    record = RowConverter.to_inventory_record(row)
    assert record.last_commit == expected_dt
    assert record.gh_migratation == expected_dt
    assert record.origin_deactivation == expected_dt


def test_row_converter_to_inventory_record_with_gh_migration_alias() -> None:
    """Test RowConverter.to_inventory_record resolves gh_migration key alias."""
    dt = datetime(2026, 9, 1, tzinfo=timezone.utc)
    row = Row(data={"name": "alias-repo", "gh_migration": dt})
    record = RowConverter.to_inventory_record(row)
    assert record.gh_migratation == dt
    assert record.gh_migration == dt


def test_row_converter_to_inventory_record_defaults_missing_fields() -> None:
    """Test RowConverter.to_inventory_record applies clean defaults when empty."""
    row = Row()
    record = RowConverter.to_inventory_record(row)
    assert record.name == ""
    assert record.stack == ""
    assert record.last_commit is None
    assert record.gh_migratation is None
    assert record.origin_deactivation is None
    assert record.migration_status == MigrationStatus.WAITTING


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


def test_dataclasses_do_not_contain_business_rules() -> None:
    """Ensure dataclasses do not contain business rules or conversion methods."""
    assert not hasattr(Row, "to_sheet_row")
    assert not hasattr(Row, "from_sheet_row")
    assert not hasattr(Row, "to_inventory_record")
    assert not hasattr(Row, "from_inventory_record")
    assert not hasattr(Credentials, "get_google_credentials")


@pytest.mark.parametrize(
    ("status_input", "expected_sheet_label"),
    [
        (MigrationStatus.MIGRATED, "Migrados (Somente GitHub)"),
        (MigrationStatus.ACTIVE_IN_BOTH, "Migrando (Ativos em Ambas)"),
        (MigrationStatus.SKIPPED, "Ignorado"),
        (MigrationStatus.WAITTING, "Aguardando Migração"),
        (MigrationStatus.WAITING, "Aguardando Migração"),
        ("somente github", "Migrados (Somente GitHub)"),
        ("migrando", "Migrando (Ativos em Ambas)"),
        ("ignorado", "Ignorado"),
        ("", "Aguardando Migração"),
    ],
)
def test_should_format_migration_status_when_adding_row(
    mock_spreadsheet: Spreadsheet,
    mock_sheets_service: MagicMock,
    status_input: object,
    expected_sheet_label: str,
) -> None:
    """Test converting migration status to sheet label via public add method."""
    mock_append = (
        mock_sheets_service.spreadsheets.return_value.values.return_value.append
    )
    mock_append.return_value.execute.return_value = {
        "updates": {"updatedRange": "Inventario!A10:H10"}
    }
    row = Row(data={"name": "test-repo", "migration_status": status_input})
    added = mock_spreadsheet.add(row)
    assert added.values[6] == expected_sheet_label


def test_should_format_dates_and_empty_values_when_adding_row(
    mock_spreadsheet: Spreadsheet, mock_sheets_service: MagicMock
) -> None:
    """Test formatting dates and empty values via public add method."""
    mock_append = (
        mock_sheets_service.spreadsheets.return_value.values.return_value.append
    )
    mock_append.return_value.execute.return_value = {
        "updates": {"updatedRange": "Inventario!A10:H10"}
    }
    row = Row(
        data={
            "name": "date-repo",
            "last_commit": datetime(2026, 5, 20, tzinfo=timezone.utc),
            "gh_migration": datetime(2026, 6, 15, tzinfo=timezone.utc),
            "origin_deactivation": None,
        }
    )
    added = mock_spreadsheet.add(row)
    assert added.values[3] == "20/05/2026"
    assert added.values[4] == "15/06/2026"
    assert added.values[5] == ""
    assert added.values[7] == "Junho/2026"


def test_should_preserve_explicit_migration_month_when_adding_row(
    mock_spreadsheet: Spreadsheet, mock_sheets_service: MagicMock
) -> None:
    """Test explicit migration_month is preserved via public add method."""
    mock_append = (
        mock_sheets_service.spreadsheets.return_value.values.return_value.append
    )
    mock_append.return_value.execute.return_value = {
        "updates": {"updatedRange": "Inventario!A10:H10"}
    }
    row = Row(
        data={
            "name": "custom-month-repo",
            "gh_migration": datetime(2026, 6, 15, tzinfo=timezone.utc),
            "migration_month": "Personalizado/2026",
        }
    )
    added = mock_spreadsheet.add(row)
    assert added.values[7] == "Personalizado/2026"


@pytest.mark.parametrize(
    ("date_str", "expected_date"),
    [
        ("20/10/2026", datetime(2026, 10, 20)),
        ("2026-10-20", datetime(2026, 10, 20)),
        ("2026-10-20T14:30:00", datetime(2026, 10, 20, 14, 30, 0)),
        ("", None),
        ("   ", None),
        ("not-a-valid-date", None),
    ],
)
def test_should_parse_dates_when_finding_row(
    mock_spreadsheet: Spreadsheet,
    mock_sheets_service: MagicMock,
    date_str: str,
    expected_date: datetime | None,
) -> None:
    """Test date parsing rules via public find_by method."""
    mock_get = mock_sheets_service.spreadsheets.return_value.values.return_value.get
    mock_get.return_value.execute.return_value = {
        "values": [
            ["core", "repo-date", "Python", date_str, "", "", "Aguardando Migração", ""]
        ]
    }
    results = mock_spreadsheet.find_by("name", "repo-date")
    assert len(results) == 1
    assert results[0].data["last_commit"] == expected_date


def test_should_pad_short_row_when_finding_row(
    mock_spreadsheet: Spreadsheet, mock_sheets_service: MagicMock
) -> None:
    """Test padding rows with fewer columns via public find_by method."""
    mock_get = mock_sheets_service.spreadsheets.return_value.values.return_value.get
    mock_get.return_value.execute.return_value = {
        "values": [["my-proj", "short-repo", "Python"]]
    }
    results = mock_spreadsheet.find_by("name", "short-repo")
    assert len(results) == 1
    row = results[0]
    assert row.data["project"] == "my-proj"
    assert row.data["name"] == "short-repo"
    assert row.data["stack"] == "Python"
    assert row.data["last_commit"] is None
    assert row.data["gh_migratation"] is None
    assert row.data["origin_deactivation"] is None
    assert row.data["migration_status"] == MigrationStatus.WAITTING
    assert row.data["migration_month"] == ""


@pytest.mark.parametrize(
    ("sheet_label", "expected_status"),
    [
        ("Migrados (Somente GitHub)", MigrationStatus.MIGRATED),
        ("Migrando (Ativos em Ambas)", MigrationStatus.ACTIVE_IN_BOTH),
        ("Ignorado", MigrationStatus.SKIPPED),
        ("Aguardando Migração", MigrationStatus.WAITTING),
        ("", MigrationStatus.WAITTING),
    ],
)
def test_should_parse_migration_status_labels_when_finding_row(
    mock_spreadsheet: Spreadsheet,
    mock_sheets_service: MagicMock,
    sheet_label: str,
    expected_status: MigrationStatus,
) -> None:
    """Test parsing sheet status labels via public find_by method."""
    mock_get = mock_sheets_service.spreadsheets.return_value.values.return_value.get
    mock_get.return_value.execute.return_value = {
        "values": [["core", "status-repo", "Java", "", "", "", sheet_label, ""]]
    }
    results = mock_spreadsheet.find_by("name", "status-repo")
    assert len(results) == 1
    assert results[0].data["migration_status"] == expected_status


def test_spreadsheet_loads_credentials_via_service_account(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test Spreadsheet initialization loads credentials via service account."""
    mock_from_file = MagicMock()
    mock_build = MagicMock()
    monkeypatch.setattr(
        "gh_migration_inventory_manager.google.service_account.Credentials.from_service_account_file",
        mock_from_file,
    )
    monkeypatch.setattr("gh_migration_inventory_manager.google.build", mock_build)

    creds = Credentials(
        credentials_path="/fake/creds.json",
        scopes=["https://www.googleapis.com/auth/spreadsheets"],
    )
    sheet = Spreadsheet(id="my-sheet-id", credentials=creds)

    mock_from_file.assert_called_once_with(
        "/fake/creds.json", scopes=["https://www.googleapis.com/auth/spreadsheets"]
    )
    mock_build.assert_called_once_with(
        "sheets", "v4", credentials=mock_from_file.return_value, cache_discovery=False
    )
    assert sheet.id == "my-sheet-id"
