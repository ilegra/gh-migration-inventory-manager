"""Unit tests for Inventory (Iventory) class."""

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from gh_migration_inventory_manager.core import Inventory, Iventory
from gh_migration_inventory_manager.google import Row, Spreadsheet
from gh_migration_inventory_manager.models import InventoryRecord, MigrationStatus


def test_update_progress_creates_new_record_when_not_in_inventory() -> None:
    """Test adding a new entry in repository when it is not in the inventory."""
    mock_spreadsheet = MagicMock(spec=Spreadsheet)
    mock_spreadsheet.find_by.return_value = []

    def fake_add(row: Row) -> Row:
        row.row_index = 10
        return row

    mock_spreadsheet.add.side_effect = fake_add

    inventory = Inventory(spreadsheet=mock_spreadsheet)
    record = inventory.update_progress(
        "org/new-service", MigrationStatus.ACTIVE_IN_BOTH
    )

    # Assert every returned attribute
    assert record.name == "org/new-service"
    assert record.stack == ""
    assert record.last_commit is None
    assert record.gh_migratation is not None
    assert record.origin_deactivation is None
    assert record.migration_status == MigrationStatus.ACTIVE_IN_BOTH
    assert record.migration_month != ""

    mock_spreadsheet.find_by.assert_called_once_with("name", "org/new-service")
    mock_spreadsheet.add.assert_called_once()


def test_update_progress_creates_new_record_waiting_status() -> None:
    """Test adding a new entry with WAITTING status assumes empty dates."""
    mock_spreadsheet = MagicMock(spec=Spreadsheet)
    mock_spreadsheet.find_by.return_value = []
    mock_spreadsheet.add.side_effect = lambda row: row

    inventory = Inventory(spreadsheet=mock_spreadsheet)
    record = inventory.update_progress("org/waiting-service", MigrationStatus.WAITTING)

    assert record.name == "org/waiting-service"
    assert record.migration_status == MigrationStatus.WAITTING
    assert record.gh_migratation is None
    assert record.origin_deactivation is None
    assert record.migration_month == ""


def test_update_progress_creates_new_record_migrated_status() -> None:
    """Test adding a new entry directly with MIGRATED status assumes both dates."""
    mock_spreadsheet = MagicMock(spec=Spreadsheet)
    mock_spreadsheet.find_by.return_value = []
    mock_spreadsheet.add.side_effect = lambda row: row

    inventory = Inventory(spreadsheet=mock_spreadsheet)
    record = inventory.update_progress("org/migrated-service", MigrationStatus.MIGRATED)

    assert record.name == "org/migrated-service"
    assert record.migration_status == MigrationStatus.MIGRATED
    assert record.gh_migratation is not None
    assert record.origin_deactivation is not None
    assert record.migration_month != ""


def test_update_progress_updates_existing_record_to_migrated() -> None:
    """Test updating existing entry: changes status and assumes origin_deactivation."""
    mock_spreadsheet = MagicMock(spec=Spreadsheet)

    original_migration_date = datetime(2026, 9, 15, tzinfo=timezone.utc)
    existing_record = InventoryRecord(
        name="org/existing-service",
        stack="Python",
        last_commit=datetime(2026, 9, 10, tzinfo=timezone.utc),
        gh_migratation=original_migration_date,
        origin_deactivation=None,
        migration_status=MigrationStatus.ACTIVE_IN_BOTH,
    )
    existing_row = Row.from_inventory_record(existing_record, row_index=4)
    mock_spreadsheet.find_by.return_value = [existing_row]

    def fake_update(row: Row) -> Row:
        return row

    mock_spreadsheet.update_row.side_effect = fake_update

    inventory = Inventory(spreadsheet=mock_spreadsheet)
    updated_record = inventory.update_progress(
        "org/existing-service", MigrationStatus.MIGRATED
    )

    # Assert every returned attribute
    assert updated_record.name == "org/existing-service"
    assert updated_record.stack == "Python"
    assert updated_record.migration_status == MigrationStatus.MIGRATED
    # Preserves original gh_migration date
    assert updated_record.gh_migratation == original_migration_date
    # Assumes origin_deactivation date
    assert updated_record.origin_deactivation is not None
    assert updated_record.migration_month == "Setembro/2026"

    mock_spreadsheet.update_row.assert_called_once()


def test_update_progress_resets_dates_when_reverted_to_waiting() -> None:
    """Test updating existing record back to WAITTING clears migration dates."""
    mock_spreadsheet = MagicMock(spec=Spreadsheet)
    existing_record = InventoryRecord(
        name="org/reverted-service",
        gh_migratation=datetime(2026, 10, 1, tzinfo=timezone.utc),
        origin_deactivation=datetime(2026, 10, 10, tzinfo=timezone.utc),
        migration_status=MigrationStatus.MIGRATED,
    )
    mock_spreadsheet.find_by.return_value = [
        Row.from_inventory_record(existing_record, row_index=6)
    ]
    mock_spreadsheet.update_row.side_effect = lambda row: row

    inventory = Inventory(spreadsheet=mock_spreadsheet)
    record = inventory.update_progress("org/reverted-service", MigrationStatus.WAITTING)

    assert record.migration_status == MigrationStatus.WAITTING
    assert record.gh_migratation is None
    assert record.origin_deactivation is None


def test_update_progress_skipped_status() -> None:
    """Test updating status to SKIPPED."""
    mock_spreadsheet = MagicMock(spec=Spreadsheet)
    mock_spreadsheet.find_by.return_value = []
    mock_spreadsheet.add.side_effect = lambda row: row

    inventory = Inventory(spreadsheet=mock_spreadsheet)
    record = inventory.update_progress("org/skipped-repo", MigrationStatus.SKIPPED)

    assert record.name == "org/skipped-repo"
    assert record.migration_status == MigrationStatus.SKIPPED
    assert record.gh_migratation is None
    assert record.origin_deactivation is None


def test_update_progress_empty_repository_name_raises() -> None:
    """Test empty repository name raises ValueError."""
    mock_spreadsheet = MagicMock(spec=Spreadsheet)
    inventory = Inventory(spreadsheet=mock_spreadsheet)

    with pytest.raises(ValueError, match="Repository name cannot be empty"):
        inventory.update_progress("   ", MigrationStatus.ACTIVE_IN_BOTH)


def test_iventory_alias_and_initialization() -> None:
    """Test Iventory class alias and spredsheet parameter compatibility."""
    assert Iventory is Inventory

    mock_spreadsheet = MagicMock(spec=Spreadsheet)
    inv1 = Inventory(spreadsheet=mock_spreadsheet)
    assert inv1.spreadsheet is mock_spreadsheet

    # Test typo parameter spredsheet
    inv2 = Iventory(spredsheet=mock_spreadsheet)
    assert inv2.spreadsheet is mock_spreadsheet

    with pytest.raises(ValueError, match="A Spreadsheet instance must be provided"):
        Inventory()
