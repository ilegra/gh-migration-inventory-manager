"""Unit tests for Inventory (Iventory) and inventory models/entities."""

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from gh_migration_inventory_manager.google import Row, RowConverter, Spreadsheet
from gh_migration_inventory_manager.inventory import (
    Inventory,
    InventoryRecord,
    Iventory,
    MigrationStatus,
    format_migration_month,
)

# ============================================================================
# Model & Entity Tests
# ============================================================================


def test_models_do_not_contain_from_sheet_label() -> None:
    """Ensure business rule from_sheet_label is not in models."""
    assert not hasattr(MigrationStatus, "from_sheet_label")


@pytest.mark.parametrize(
    ("status", "expected_label"),
    [
        (MigrationStatus.MIGRATED, "Migrados (Somente GitHub)"),
        (MigrationStatus.ACTIVE_IN_BOTH, "Migrando (Ativos em Ambas)"),
        (MigrationStatus.SKIPPED, "Ignorado"),
        (MigrationStatus.WAITTING, "Aguardando Migração"),
        (MigrationStatus.WAITING, "Aguardando Migração"),
    ],
)
def test_migration_status_to_sheet_label(
    status: MigrationStatus, expected_label: str
) -> None:
    """Test mapping MigrationStatus to sheet display label."""
    assert status.to_sheet_label() == expected_label


def test_inventory_record_properties() -> None:
    """Test InventoryRecord properties and aliases."""
    record = InventoryRecord(
        name="org/repo",
        gh_migratation=datetime(2026, 10, 15, tzinfo=timezone.utc),
    )
    assert record.gh_migration == record.gh_migratation
    assert record.migration_month == "Outubro/2026"

    record.gh_migration = datetime(2026, 11, 1, tzinfo=timezone.utc)
    assert record.gh_migratation == datetime(2026, 11, 1, tzinfo=timezone.utc)


def test_format_migration_month() -> None:
    """Test format_migration_month formatting."""
    assert format_migration_month(None) == ""
    assert format_migration_month(datetime(2026, 1, 1)) == "Janeiro/2026"
    assert format_migration_month(datetime(2026, 12, 31)) == "Dezembro/2026"


# ============================================================================
# Inventory Service Tests
# ============================================================================


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

    assert record.name == "org/new-service"
    assert record.stack == ""
    assert record.last_commit is None
    assert record.gh_migratation is not None
    assert record.origin_deactivation is None
    assert record.migration_status == MigrationStatus.ACTIVE_IN_BOTH
    assert record.migration_month != ""

    mock_spreadsheet.find_by.assert_called_once_with("name", "org/new-service")
    mock_spreadsheet.add.assert_called_once()


def test_update_progress_creates_new_record_with_project() -> None:
    """Test adding a new entry with an explicit project."""
    mock_spreadsheet = MagicMock(spec=Spreadsheet)
    mock_spreadsheet.find_by.return_value = []
    mock_spreadsheet.add.side_effect = lambda row: row

    inventory = Inventory(spreadsheet=mock_spreadsheet)
    record = inventory.update_progress(
        "org/project-service",
        MigrationStatus.ACTIVE_IN_BOTH,
        project="platform-core",
    )

    assert record.name == "org/project-service"
    assert record.project == "platform-core"
    assert record.migration_status == MigrationStatus.ACTIVE_IN_BOTH


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
        project="billing-app",
        stack="Python",
        last_commit=datetime(2026, 9, 10, tzinfo=timezone.utc),
        gh_migratation=original_migration_date,
        origin_deactivation=None,
        migration_status=MigrationStatus.ACTIVE_IN_BOTH,
    )
    existing_row = RowConverter.from_inventory_record(existing_record, row_index=4)
    mock_spreadsheet.find_by.return_value = [existing_row]

    def fake_update(row: Row) -> Row:
        return row

    mock_spreadsheet.update_row.side_effect = fake_update

    inventory = Inventory(spreadsheet=mock_spreadsheet)
    updated_record = inventory.update_progress(
        "org/existing-service", MigrationStatus.MIGRATED
    )

    assert updated_record.name == "org/existing-service"
    assert updated_record.project == "billing-app"
    assert updated_record.stack == "Python"
    assert updated_record.migration_status == MigrationStatus.MIGRATED
    assert updated_record.gh_migratation == original_migration_date
    assert updated_record.origin_deactivation is not None
    assert updated_record.migration_month == "Setembro/2026"

    mock_spreadsheet.update_row.assert_called_once()

    # Also test updating project on existing record
    updated_with_new_project = inventory.update_progress(
        "org/existing-service", MigrationStatus.MIGRATED, project="billing-core"
    )
    assert updated_with_new_project.project == "billing-core"


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
        RowConverter.from_inventory_record(existing_record, row_index=6)
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

    inv2 = Iventory(spredsheet=mock_spreadsheet)
    assert inv2.spreadsheet is mock_spreadsheet

    with pytest.raises(ValueError, match="A Spreadsheet instance must be provided"):
        Inventory()


@pytest.mark.parametrize(
    "invalid_status",
    [
        "invalid_status",
        "completed",
        "in_progress",
        "pending",
        "failed",
        "unknown",
    ],
)
def test_update_progress_invalid_status_string_raises(
    invalid_status: str,
) -> None:
    """Test that providing an invalid status string raises ValueError."""
    mock_spreadsheet = MagicMock(spec=Spreadsheet)
    inventory = Inventory(spreadsheet=mock_spreadsheet)

    with pytest.raises(ValueError) as exc_info:
        inventory.update_progress("org/repo", invalid_status)

    err_msg = str(exc_info.value)
    assert f"Invalid migration status: '{invalid_status}'" in err_msg
    assert "Expected a valid MigrationStatus or one of:" in err_msg
    assert "active_in_both" in err_msg
    assert "migrated" in err_msg
    assert "skipped" in err_msg
    assert "waitting" in err_msg


@pytest.mark.parametrize("empty_status", ["", "   "])
def test_update_progress_empty_status_string_raises(empty_status: str) -> None:
    """Test that empty or whitespace status string raises ValueError."""
    mock_spreadsheet = MagicMock(spec=Spreadsheet)
    inventory = Inventory(spreadsheet=mock_spreadsheet)

    with pytest.raises(ValueError) as exc_info:
        inventory.update_progress("org/repo", empty_status)

    assert "status cannot be empty" in str(exc_info.value)


def test_update_progress_none_status_raises() -> None:
    """Test that passing None as status raises ValueError."""
    mock_spreadsheet = MagicMock(spec=Spreadsheet)
    inventory = Inventory(spreadsheet=mock_spreadsheet)

    with pytest.raises(ValueError) as exc_info:
        inventory.update_progress("org/repo", None)  # type: ignore[arg-type]

    assert "Status cannot be None" in str(exc_info.value)


@pytest.mark.parametrize(
    "invalid_type_status",
    [123, 45.6, ["migrated"], {"status": "migrated"}],
)
def test_update_progress_invalid_status_type_raises(
    invalid_type_status: object,
) -> None:
    """Test that passing a non-string, non-enum object raises ValueError."""
    mock_spreadsheet = MagicMock(spec=Spreadsheet)
    inventory = Inventory(spreadsheet=mock_spreadsheet)

    with pytest.raises(ValueError) as exc_info:
        inventory.update_progress("org/repo", invalid_type_status)  # type: ignore[arg-type]

    assert "Invalid migration status type" in str(exc_info.value)


@pytest.mark.parametrize(
    ("status_input", "expected_enum"),
    [
        ("migrated", MigrationStatus.MIGRATED),
        ("MIGRATED", MigrationStatus.MIGRATED),
        ("active_in_both", MigrationStatus.ACTIVE_IN_BOTH),
        ("ACTIVE_IN_BOTH", MigrationStatus.ACTIVE_IN_BOTH),
        ("waitting", MigrationStatus.WAITTING),
        ("waiting", MigrationStatus.WAITTING),
        ("WAITING", MigrationStatus.WAITTING),
        ("skipped", MigrationStatus.SKIPPED),
        ("SKIPPED", MigrationStatus.SKIPPED),
    ],
)
def test_update_progress_accepts_valid_string_status(
    status_input: str, expected_enum: MigrationStatus
) -> None:
    """Test that valid status strings are properly parsed and handled."""
    mock_spreadsheet = MagicMock(spec=Spreadsheet)
    mock_spreadsheet.find_by.return_value = []
    mock_spreadsheet.add.side_effect = lambda row: row

    inventory = Inventory(spreadsheet=mock_spreadsheet)
    record = inventory.update_progress("org/repo", status_input)

    assert record.migration_status == expected_enum


@pytest.mark.parametrize(
    ("label", "expected_status"),
    [
        ("Migrados (Somente GitHub)", MigrationStatus.MIGRATED),
        ("somente github", MigrationStatus.MIGRATED),
        ("migrated", MigrationStatus.MIGRATED),
        ("migrados", MigrationStatus.MIGRATED),
        ("Migrando (Ativos em Ambas)", MigrationStatus.ACTIVE_IN_BOTH),
        ("ativos em ambas", MigrationStatus.ACTIVE_IN_BOTH),
        ("migrando", MigrationStatus.ACTIVE_IN_BOTH),
        ("active_in_both", MigrationStatus.ACTIVE_IN_BOTH),
        ("Ignorado", MigrationStatus.SKIPPED),
        ("ignorado", MigrationStatus.SKIPPED),
        ("não migrar", MigrationStatus.SKIPPED),
        ("skipped", MigrationStatus.SKIPPED),
        ("Aguardando Migração", MigrationStatus.WAITTING),
        ("aguardando", MigrationStatus.WAITTING),
        ("waitting", MigrationStatus.WAITTING),
        ("waiting", MigrationStatus.WAITTING),
        ("", MigrationStatus.WAITTING),
        ("   ", MigrationStatus.WAITTING),
    ],
)
def test_inventory_from_sheet_label_valid(
    label: str, expected_status: MigrationStatus
) -> None:
    """Test Inventory._from_sheet_label with valid labels."""
    assert Inventory._from_sheet_label(label) == expected_status


@pytest.mark.parametrize(
    "invalid_label",
    [
        "unknown_status",
        "invalid",
        "completed",
        "pending",
        "random string",
    ],
)
def test_inventory_from_sheet_label_invalid_raises(invalid_label: str) -> None:
    """Test Inventory._from_sheet_label raises ValueError for invalid labels."""
    expected_match = f"Invalid migration status label: '{invalid_label}'"
    with pytest.raises(ValueError, match=expected_match):
        Inventory._from_sheet_label(invalid_label)
