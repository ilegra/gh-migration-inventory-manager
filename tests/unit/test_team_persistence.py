"""Unit tests for TeamInventory domain orchestration and lifecycle persistence."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from gh_migration_inventory_manager.google import (
    Row,
    Spreadsheet,
    SpreadsheetAccessError,
    SpreadsheetNotFoundError,
)
from gh_migration_inventory_manager.inventory import (
    InventoryAccessError,
    InventoryNotFoundError,
    MigrationStatus,
    TeamInventory,
    TeamRecord,
)


@pytest.fixture
def mock_spreadsheet() -> MagicMock:
    """Fixture providing a mock Spreadsheet client for TeamInventory."""
    sheet = MagicMock(spec=Spreadsheet)
    sheet.id = "test-spreadsheet-id"
    sheet.sheet_name = "Topology"
    return sheet


@pytest.fixture
def sample_team_record() -> TeamRecord:
    """Fixture providing a sample TeamRecord."""
    return TeamRecord(
        team_name="core-devs",
        team_slug="core-devs",
        member_count=3,
        target_repo_or_project="/standard-app/trunk",
        normalized_role="Write",
        origin_role="rw",
        migration_status=MigrationStatus.WAITTING,
    )


def test_given_unregistered_team_when_registering_then_creates_new_row_in_sheet(
    mock_spreadsheet: MagicMock,
    sample_team_record: TeamRecord,
) -> None:
    """Test registering a new team permission appends a new row in Google Sheets."""
    mock_spreadsheet.find_by.return_value = []
    mock_spreadsheet.add.return_value = Row(
        row_index=2,
        data={
            "team_name": "core-devs",
            "team_slug": "core-devs",
            "member_count": 3,
            "target_repo_or_project": "/standard-app/trunk",
            "normalized_role": "Write",
            "origin_role": "rw",
            "migration_status": "Aguardando Migração",
        },
    )

    inventory = TeamInventory(spreadsheet=mock_spreadsheet)
    saved = inventory.register_record(sample_team_record)

    assert saved.team_slug == "core-devs"
    assert saved.member_count == 3
    mock_spreadsheet.add.assert_called_once()
    mock_spreadsheet.update_row.assert_not_called()


def test_given_existing_team_when_registering_then_preserves_migration_status(
    mock_spreadsheet: MagicMock,
    sample_team_record: TeamRecord,
) -> None:
    """Test registering an existing team permission preserves its migration status."""
    existing_row = Row(
        row_index=5,
        data={
            "team_name": "core-devs",
            "team_slug": "core-devs",
            "member_count": 2,
            "target_repo_or_project": "/standard-app/trunk",
            "normalized_role": "Write",
            "origin_role": "rw",
            "migration_status": "Migrando (Ativos em Ambas)",
        },
    )
    mock_spreadsheet.find_by.return_value = [existing_row]
    mock_spreadsheet.update_row.return_value = Row(
        row_index=5,
        data={
            "team_name": "core-devs",
            "team_slug": "core-devs",
            "member_count": 3,
            "target_repo_or_project": "/standard-app/trunk",
            "normalized_role": "Write",
            "origin_role": "rw",
            "migration_status": "Migrando (Ativos em Ambas)",
        },
    )

    inventory = TeamInventory(spreadsheet=mock_spreadsheet)
    updated = inventory.register_record(sample_team_record)

    assert updated.member_count == 3
    assert updated.migration_status == MigrationStatus.ACTIVE_IN_BOTH
    mock_spreadsheet.update_row.assert_called_once()
    mock_spreadsheet.add.assert_not_called()


def test_given_existing_team_when_updating_progress_then_updates_row_in_sheet(
    mock_spreadsheet: MagicMock,
) -> None:
    """Test update_progress modifies status and saves updated row."""
    existing_row = Row(
        row_index=3,
        data={
            "team_name": "ops",
            "team_slug": "ops",
            "member_count": 2,
            "target_repo_or_project": "/infra",
            "normalized_role": "Admin",
            "origin_role": "rw",
            "migration_status": "Aguardando Migração",
        },
    )
    mock_spreadsheet.find_by.return_value = [existing_row]
    mock_spreadsheet.update_row.return_value = Row(
        row_index=3,
        data={
            "team_name": "ops",
            "team_slug": "ops",
            "member_count": 2,
            "target_repo_or_project": "/infra",
            "normalized_role": "Admin",
            "origin_role": "rw",
            "migration_status": "Migrados (Somente GitHub)",
        },
    )

    inventory = TeamInventory(spreadsheet=mock_spreadsheet)
    updated = inventory.update_progress("ops", "/infra", MigrationStatus.MIGRATED)

    assert updated.migration_status == MigrationStatus.MIGRATED
    mock_spreadsheet.update_row.assert_called_once()


def test_given_unregistered_team_when_updating_progress_then_creates_new_row(
    mock_spreadsheet: MagicMock,
) -> None:
    """Test update_progress creates a new record if team is not found."""
    mock_spreadsheet.find_by.return_value = []
    mock_spreadsheet.add.return_value = Row(
        row_index=4,
        data={
            "team_name": "leads",
            "team_slug": "leads",
            "member_count": 0,
            "target_repo_or_project": "/tags",
            "normalized_role": "Read",
            "origin_role": "",
            "migration_status": "Migrando (Ativos em Ambas)",
        },
    )

    inventory = TeamInventory(spreadsheet=mock_spreadsheet)
    created = inventory.update_progress(
        "leads", "/tags", MigrationStatus.ACTIVE_IN_BOTH
    )

    assert created.team_slug == "leads"
    assert created.migration_status == MigrationStatus.ACTIVE_IN_BOTH
    mock_spreadsheet.add.assert_called_once()


def test_given_existing_team_slug_and_target_when_getting_team_then_returns_record(
    mock_spreadsheet: MagicMock,
) -> None:
    """Test get_team_permission returns populated TeamRecord on match."""
    existing_row = Row(
        row_index=2,
        data={
            "team_name": "devs",
            "team_slug": "devs",
            "member_count": 4,
            "target_repo_or_project": "/trunk",
            "normalized_role": "Write",
            "origin_role": "rw",
            "migration_status": "Aguardando Migração",
        },
    )
    mock_spreadsheet.find_by.return_value = [existing_row]

    inventory = TeamInventory(spreadsheet=mock_spreadsheet)
    record = inventory.get_team_permission("devs", "/trunk")

    assert record is not None
    assert record.team_slug == "devs"
    assert record.member_count == 4


def test_given_non_existing_team_when_getting_team_then_returns_none(
    mock_spreadsheet: MagicMock,
) -> None:
    """Test get_team_permission returns None when no matching row is found."""
    mock_spreadsheet.find_by.return_value = []

    inventory = TeamInventory(spreadsheet=mock_spreadsheet)
    assert inventory.get_team_permission("unknown-team", "/trunk") is None


def test_given_batch_of_team_records_when_syncing_then_persists_all(
    mock_spreadsheet: MagicMock,
) -> None:
    """Test sync_records iterates and registers all records."""
    mock_spreadsheet.find_by.return_value = []
    mock_spreadsheet.add.side_effect = lambda r: Row(
        row_index=2,
        data={
            "team_name": r.data["team_name"],
            "team_slug": r.data["team_slug"],
            "member_count": r.data["member_count"],
            "target_repo_or_project": r.data["target_repo_or_project"],
            "normalized_role": r.data["normalized_role"],
            "origin_role": r.data["origin_role"],
            "migration_status": "Aguardando Migração",
        },
    )

    inventory = TeamInventory(spreadsheet=mock_spreadsheet)
    records = [
        TeamRecord(team_name="team1", team_slug="team1", target_repo_or_project="/p1"),
        TeamRecord(team_name="team2", team_slug="team2", target_repo_or_project="/p2"),
    ]

    synced = inventory.sync_records(records)
    assert len(synced) == 2
    assert mock_spreadsheet.add.call_count == 2


def test_given_none_record_when_registering_then_raises_value_error(
    mock_spreadsheet: MagicMock,
) -> None:
    """Test register_record raises ValueError when record is None."""
    inventory = TeamInventory(spreadsheet=mock_spreadsheet)
    with pytest.raises(ValueError, match="TeamRecord cannot be None"):
        inventory.register_record(None)  # type: ignore[arg-type]


def test_given_empty_team_slug_when_registering_then_raises_value_error(
    mock_spreadsheet: MagicMock,
) -> None:
    """Test register_record raises ValueError when team_slug is empty."""
    inventory = TeamInventory(spreadsheet=mock_spreadsheet)
    record = TeamRecord(team_name="test", team_slug="", target_repo_or_project="/trunk")
    with pytest.raises(ValueError, match="Team slug cannot be empty"):
        inventory.register_record(record)


def test_given_empty_target_when_registering_then_raises_value_error(
    mock_spreadsheet: MagicMock,
) -> None:
    """Test register_record raises ValueError when target_repo_or_project is empty."""
    inventory = TeamInventory(spreadsheet=mock_spreadsheet)
    record = TeamRecord(team_name="test", team_slug="test", target_repo_or_project="")
    with pytest.raises(
        ValueError, match="Target repository or project cannot be empty"
    ):
        inventory.register_record(record)


def test_given_empty_slug_when_updating_progress_then_raises_value_error(
    mock_spreadsheet: MagicMock,
) -> None:
    """Test update_progress raises ValueError when team_slug is empty."""
    inventory = TeamInventory(spreadsheet=mock_spreadsheet)
    with pytest.raises(ValueError, match="Team slug cannot be empty"):
        inventory.update_progress("", "/trunk", MigrationStatus.MIGRATED)


def test_given_empty_target_when_updating_progress_then_raises_value_error(
    mock_spreadsheet: MagicMock,
) -> None:
    """Test update_progress raises ValueError when target is empty."""
    inventory = TeamInventory(spreadsheet=mock_spreadsheet)
    with pytest.raises(
        ValueError, match="Target repository or project cannot be empty"
    ):
        inventory.update_progress("devs", "", MigrationStatus.MIGRATED)


def test_given_none_records_collection_when_syncing_then_raises_value_error(
    mock_spreadsheet: MagicMock,
) -> None:
    """Test sync_records raises ValueError when records is None."""
    inventory = TeamInventory(spreadsheet=mock_spreadsheet)
    with pytest.raises(ValueError, match="Records collection cannot be None"):
        inventory.sync_records(None)  # type: ignore[arg-type]


def test_given_spreadsheet_http_404_when_validating_then_raises_inventory_not_found_error(
    mock_spreadsheet: MagicMock,
) -> None:
    """Test spreadsheet 404 is mapped to InventoryNotFoundError."""
    mock_spreadsheet.validate.side_effect = SpreadsheetNotFoundError("Sheet not found")
    inventory = TeamInventory(spreadsheet=mock_spreadsheet)

    with pytest.raises(InventoryNotFoundError, match="Sheet not found"):
        inventory.get_team_permission("devs", "/trunk")


def test_given_spreadsheet_http_403_when_validating_then_raises_inventory_access_error(
    mock_spreadsheet: MagicMock,
) -> None:
    """Test spreadsheet 403 is mapped to InventoryAccessError."""
    mock_spreadsheet.validate.side_effect = SpreadsheetAccessError("Access denied")
    inventory = TeamInventory(spreadsheet=mock_spreadsheet)

    with pytest.raises(InventoryAccessError, match="Access denied"):
        inventory.get_team_permission("devs", "/trunk")


def test_given_inconsistent_spreadsheet_id_when_building_then_raises_value_error(
    mock_spreadsheet: MagicMock,
) -> None:
    """Test inconsistent spreadsheet_id raises ValueError."""
    mock_spreadsheet.id = "id-1"
    inventory = TeamInventory(spreadsheet=mock_spreadsheet, spreadsheet_id="id-2")

    with pytest.raises(ValueError, match="Inconsistent spreadsheet configuration"):
        inventory.get_team_permission("devs", "/trunk")


def test_given_missing_spreadsheet_id_when_initializing_then_raises_value_error() -> (
    None
):
    """Test missing spreadsheet_id without pre-built spreadsheet raises ValueError."""
    inventory = TeamInventory()
    with pytest.raises(ValueError, match="Missing Google Spreadsheet ID"):
        inventory.get_team_permission("devs", "/trunk")
