"""Unit tests for Google Sheets Topology tab and TeamRowConverter."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from gh_migration_inventory_manager.google import (
    TOPOLOGY_HEADERS,
    Credentials,
    Row,
    Spreadsheet,
    SpreadsheetError,
    TeamRowConverter,
)
from gh_migration_inventory_manager.inventory import MigrationStatus, TeamRecord


@pytest.fixture
def sample_team_record() -> TeamRecord:
    """Fixture providing a sample TeamRecord."""
    return TeamRecord(
        team_name="Core Devs",
        team_slug="core-devs",
        member_count=5,
        target_repo_or_project="/standard-app/trunk",
        normalized_role="Write",
        origin_role="rw",
        migration_status=MigrationStatus.ACTIVE_IN_BOTH,
    )


@pytest.fixture
def mock_topology_spreadsheet(mock_sheets_service: MagicMock) -> Spreadsheet:
    """Fixture providing a Spreadsheet client bound to the 'Topology' tab."""
    return Spreadsheet(
        id="topology-sheet-id",
        credentials=Credentials(credentials_path="dummy-path.json"),
        sheet_name="Topology",
        service=mock_sheets_service,
    )


def test_given_team_record_when_converting_to_row_then_maps_all_seven_columns(
    sample_team_record: TeamRecord,
    mock_topology_spreadsheet: Spreadsheet,
    mock_sheets_service: MagicMock,
) -> None:
    """Test TeamRowConverter creates a Row and Spreadsheet serializes all 7 columns."""
    row = TeamRowConverter.from_team_record(sample_team_record, row_index=3)

    assert row.row_index == 3
    assert row.data["team_name"] == "Core Devs"
    assert row.data["team_slug"] == "core-devs"
    assert row.data["member_count"] == 5
    assert row.data["target_repo_or_project"] == "/standard-app/trunk"
    assert row.data["normalized_role"] == "Write"
    assert row.data["origin_role"] == "rw"
    assert row.data["migration_status"] == MigrationStatus.ACTIVE_IN_BOTH

    mock_append = (
        mock_sheets_service.spreadsheets.return_value.values.return_value.append
    )
    mock_append.return_value.execute.return_value = {
        "updates": {"updatedRange": "Topology!A3:G3"}
    }

    added_row = mock_topology_spreadsheet.add(row)
    assert len(added_row.values) == 7
    assert added_row.values[0] == "Core Devs"
    assert added_row.values[1] == "core-devs"
    assert added_row.values[2] == "5"
    assert added_row.values[3] == "/standard-app/trunk"
    assert added_row.values[4] == "Write"
    assert added_row.values[5] == "rw"
    assert added_row.values[6] == "Migrando (Ativos em Ambas)"


def test_given_row_when_converting_to_team_record_then_populates_team_record() -> None:
    """Test TeamRowConverter parses raw Row data back into TeamRecord."""
    row = Row(
        row_index=4,
        data={
            "team_name": "Ops Team",
            "team_slug": "ops-team",
            "member_count": 3,
            "target_repo_or_project": "/infra",
            "normalized_role": "Admin",
            "origin_role": "rw",
            "migration_status": "Migrados (Somente GitHub)",
        },
    )

    record = TeamRowConverter.to_team_record(row)

    assert record.team_name == "Ops Team"
    assert record.team_slug == "ops-team"
    assert record.member_count == 3
    assert record.target_repo_or_project == "/infra"
    assert record.normalized_role == "Admin"
    assert record.origin_role == "rw"
    assert record.migration_status == MigrationStatus.MIGRATED


def test_given_none_team_record_when_converting_to_row_then_raises_value_error() -> (
    None
):
    """Test from_team_record raises ValueError when record is None."""
    with pytest.raises(ValueError, match="TeamRecord cannot be None"):
        TeamRowConverter.from_team_record(None)  # type: ignore[arg-type]


def test_given_none_row_when_converting_to_team_record_then_raises_value_error() -> (
    None
):
    """Test to_team_record raises ValueError when row is None."""
    with pytest.raises(ValueError, match="Row cannot be None"):
        TeamRowConverter.to_team_record(None)  # type: ignore[arg-type]


def test_given_topology_sheet_name_when_finding_row_then_queries_seven_column_range(
    mock_topology_spreadsheet: Spreadsheet,
    mock_sheets_service: MagicMock,
) -> None:
    """Test that finding rows on a Topology sheet queries A2:G range."""
    mock_get = mock_sheets_service.spreadsheets.return_value.values.return_value.get
    mock_get.return_value.execute.return_value = {
        "values": [
            [
                "Devs",
                "devs",
                "4",
                "/trunk",
                "Write",
                "rw",
                "Aguardando Migração",
            ]
        ]
    }

    rows = mock_topology_spreadsheet.find_by("team_slug", "devs")

    assert len(rows) == 1
    assert rows[0].row_index == 2
    assert rows[0].data["team_slug"] == "devs"
    assert rows[0].data["member_count"] == 4
    mock_get.assert_called_with(
        spreadsheetId="topology-sheet-id",
        range="Topology!A2:G",
    )


def test_given_column_alias_when_finding_by_then_resolves_correct_topology_column(
    mock_topology_spreadsheet: Spreadsheet,
    mock_sheets_service: MagicMock,
) -> None:
    """Test find_by handles column aliases like 'slug' and 'membros'."""
    mock_get = mock_sheets_service.spreadsheets.return_value.values.return_value.get
    mock_get.return_value.execute.return_value = {"values": []}

    mock_topology_spreadsheet.find_by("slug do time", "devs")
    mock_get.assert_called_with(
        spreadsheetId="topology-sheet-id",
        range="Topology!A2:G",
    )


def test_given_invalid_column_name_when_finding_row_then_raises_value_error(
    mock_topology_spreadsheet: Spreadsheet,
) -> None:
    """Test find_by raises ValueError for unknown column name."""
    with pytest.raises(ValueError, match="Unknown column identifier"):
        mock_topology_spreadsheet.find_by("non_existent_column_xyz", "value")


def test_given_ensure_sheet_when_topology_tab_missing_then_adds_sheet_with_headers(
    mock_sheets_service: MagicMock,
) -> None:
    """Test ensure_sheet creates Topology tab with 7 headers when missing."""
    mock_meta = mock_sheets_service.spreadsheets.return_value.get.return_value.execute
    mock_meta.return_value = {"sheets": [{"properties": {"title": "Inventario"}}]}

    sheet = Spreadsheet(
        id="topology-sheet-id",
        credentials=Credentials(credentials_path="dummy-path.json"),
        sheet_name="Topology",
        service=mock_sheets_service,
    )
    sheet.ensure_sheet()

    mock_batch = mock_sheets_service.spreadsheets.return_value.batchUpdate
    mock_batch.assert_called_once()

    mock_update = (
        mock_sheets_service.spreadsheets.return_value.values.return_value.update
    )
    mock_update.assert_called_once_with(
        spreadsheetId="topology-sheet-id",
        range="Topology!A1:G1",
        valueInputOption="USER_ENTERED",
        body={"values": [TOPOLOGY_HEADERS]},
    )


def test_given_clear_tab_when_called_then_clears_data_rows_range(
    mock_topology_spreadsheet: Spreadsheet,
    mock_sheets_service: MagicMock,
) -> None:
    """Test clear_tab clears range A2:G to reset spreadsheet state."""
    mock_meta = mock_sheets_service.spreadsheets.return_value.get.return_value.execute
    mock_meta.return_value = {"sheets": [{"properties": {"title": "Topology"}}]}

    mock_clear = mock_sheets_service.spreadsheets.return_value.values.return_value.clear
    mock_clear.return_value.execute.return_value = {}

    mock_topology_spreadsheet.clear_tab()

    mock_clear.assert_called_once_with(
        spreadsheetId="topology-sheet-id",
        range="Topology!A2:G",
    )


def test_given_api_error_when_clearing_tab_then_raises_spreadsheet_error(
    mock_topology_spreadsheet: Spreadsheet,
    mock_sheets_service: MagicMock,
) -> None:
    """Test clear_tab translates API exceptions into SpreadsheetError."""
    mock_meta = mock_sheets_service.spreadsheets.return_value.get.return_value.execute
    mock_meta.return_value = {"sheets": [{"properties": {"title": "Topology"}}]}
    mock_clear = mock_sheets_service.spreadsheets.return_value.values.return_value.clear
    mock_clear.side_effect = RuntimeError("API Network Error")

    with pytest.raises(SpreadsheetError, match="Error clearing sheet"):
        mock_topology_spreadsheet.clear_tab()
