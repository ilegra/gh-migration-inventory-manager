"""Unit tests for Inventory (Iventory) and inventory models/entities."""

from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from gh_migration_inventory_manager.converter import RepositoryConverter
from gh_migration_inventory_manager.google import (
    Credentials,
    Row,
    RowConverter,
    Spreadsheet,
    SpreadsheetAccessError,
    SpreadsheetError,
    SpreadsheetNotFoundError,
)
from gh_migration_inventory_manager.inventory import (
    Inventory,
    InventoryRecord,
    Iventory,
    MigrationStatus,
    format_migration_month,
)
from gh_migration_inventory_manager.scrap import Repository

# ============================================================================
# Model & Entity Tests
# ============================================================================


def test_given_migration_status_enum_when_inspecting_attributes_then_does_not_contain_sheet_label_methods() -> (
    None
):
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
def test_given_migration_status_when_calling_to_sheet_label_then_returns_expected_display_string(
    status: MigrationStatus, expected_label: str
) -> None:
    """Test mapping MigrationStatus to sheet display label."""
    assert status.to_sheet_label() == expected_label


def test_given_inventory_record_when_accessing_migration_month_then_computes_formatted_date_string() -> (
    None
):
    """Test InventoryRecord properties and aliases."""
    record = InventoryRecord(
        name="org/repo",
        gh_migratation=datetime(2026, 10, 15, tzinfo=timezone.utc),
    )
    assert record.gh_migration == record.gh_migratation
    assert record.migration_month == "Outubro/2026"

    record.gh_migration = datetime(2026, 11, 1, tzinfo=timezone.utc)
    assert record.gh_migratation == datetime(2026, 11, 1, tzinfo=timezone.utc)


def test_given_none_or_datetime_when_formatting_migration_month_then_returns_formatted_string() -> (
    None
):
    """Test format_migration_month formatting."""
    assert format_migration_month(None) == ""
    assert format_migration_month(datetime(2026, 1, 1)) == "Janeiro/2026"
    assert format_migration_month(datetime(2026, 12, 31)) == "Dezembro/2026"


# ============================================================================
# Inventory Service Tests
# ============================================================================


def test_given_unregistered_repository_when_updating_progress_then_creates_new_record_with_defaults() -> (
    None
):
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


def test_given_repository_with_project_when_updating_progress_then_creates_record_scoped_to_project() -> (
    None
):
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


def test_given_waiting_status_when_updating_progress_then_creates_record_without_migration_date() -> (
    None
):
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


def test_given_migrated_status_when_updating_progress_then_sets_migration_date() -> (
    None
):
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


def test_given_existing_waiting_record_when_updating_to_migrated_then_sets_migration_date() -> (
    None
):
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


def test_given_migrated_record_when_reverting_to_waiting_then_resets_dates_to_none() -> (
    None
):
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


def test_given_skipped_status_when_updating_progress_then_preserves_status_without_dates() -> (
    None
):
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


def test_given_empty_repository_name_when_updating_progress_then_raises_value_error() -> (
    None
):
    """Test empty repository name raises ValueError."""
    mock_spreadsheet = MagicMock(spec=Spreadsheet)
    inventory = Inventory(spreadsheet=mock_spreadsheet)

    with pytest.raises(ValueError, match="Repository name cannot be empty"):
        inventory.update_progress("   ", MigrationStatus.ACTIVE_IN_BOTH)


def test_given_custom_backend_when_initializing_inventory_then_binds_backend_correctly() -> (
    None
):
    """Test Iventory class alias and spredsheet parameter compatibility."""
    assert Iventory is Inventory

    mock_spreadsheet = MagicMock(spec=Spreadsheet)
    inv1 = Inventory(spreadsheet=mock_spreadsheet)
    assert inv1.spreadsheet is mock_spreadsheet

    inv2 = Iventory(spredsheet=mock_spreadsheet)
    assert inv2.spreadsheet is mock_spreadsheet

    inv3 = Inventory()
    with pytest.raises(ValueError, match="Missing Google Spreadsheet ID"):
        inv3.sync_records([])


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
def test_given_invalid_status_string_when_updating_progress_then_raises_value_error(
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
def test_given_empty_status_string_when_updating_progress_then_raises_value_error(
    empty_status: str,
) -> None:
    """Test that empty or whitespace status string raises ValueError."""
    mock_spreadsheet = MagicMock(spec=Spreadsheet)
    inventory = Inventory(spreadsheet=mock_spreadsheet)

    with pytest.raises(ValueError) as exc_info:
        inventory.update_progress("org/repo", empty_status)

    assert "status cannot be empty" in str(exc_info.value)


def test_given_none_status_when_updating_progress_then_raises_value_error() -> None:
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
def test_given_invalid_status_type_when_updating_progress_then_raises_type_error(
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
def test_given_valid_string_status_when_updating_progress_then_normalizes_to_migration_status(
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
        ("#REF!", MigrationStatus.WAITTING),
        ("#N/A", MigrationStatus.WAITTING),
        ("#VALUE!", MigrationStatus.WAITTING),
        ("#ERROR!", MigrationStatus.WAITTING),
    ],
)
def test_given_localized_sheet_label_when_parsing_status_then_returns_correct_migration_status(
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
def test_given_invalid_sheet_label_when_parsing_status_then_raises_value_error(
    invalid_label: str,
) -> None:
    """Test Inventory._from_sheet_label raises ValueError for invalid labels."""
    expected_match = f"Invalid migration status label: '{invalid_label}'"
    with pytest.raises(ValueError, match=expected_match):
        Inventory._from_sheet_label(invalid_label)


# ============================================================================
# Repository Registration & Synchronization Tests
# ============================================================================


def test_given_existing_repository_in_backend_when_getting_repository_then_returns_inventory_record() -> (
    None
):
    """Test get_repository returns converted InventoryRecord when row exists."""
    mock_spreadsheet = MagicMock(spec=Spreadsheet)
    existing_row = Row(
        row_index=2,
        data={
            "name": "found-repo",
            "project": "core",
            "stack": "Python",
            "migration_status": MigrationStatus.MIGRATED,
        },
    )
    mock_spreadsheet.find_by.return_value = [existing_row]

    inventory = Inventory(spreadsheet=mock_spreadsheet)
    record = inventory.get_repository("found-repo")

    assert record is not None
    assert record.name == "found-repo"
    assert record.project == "core"
    assert record.stack == "Python"
    assert record.migration_status == MigrationStatus.MIGRATED
    mock_spreadsheet.find_by.assert_called_once_with("name", "found-repo")


def test_given_project_filter_when_getting_repository_then_matches_exact_project() -> (
    None
):
    """Test get_repository selects row matching specified project."""
    mock_spreadsheet = MagicMock(spec=Spreadsheet)
    row_proj_a = Row(
        row_index=2,
        data={"name": "shared-repo", "project": "proj-a", "stack": "Java"},
    )
    row_proj_b = Row(
        row_index=3,
        data={"name": "shared-repo", "project": "proj-b", "stack": "Python"},
    )
    mock_spreadsheet.find_by.return_value = [row_proj_a, row_proj_b]

    inventory = Inventory(spreadsheet=mock_spreadsheet)
    record = inventory.get_repository("shared-repo", project="proj-b")

    assert record is not None
    assert record.name == "shared-repo"
    assert record.project == "proj-b"
    assert record.stack == "Python"


def test_given_missing_repository_when_getting_repository_then_returns_none() -> None:
    """Test get_repository returns None when repository does not exist."""
    mock_spreadsheet = MagicMock(spec=Spreadsheet)
    mock_spreadsheet.find_by.return_value = []

    inventory = Inventory(spreadsheet=mock_spreadsheet)
    assert inventory.get_repository("missing-repo") is None


def test_given_empty_repository_name_when_getting_repository_then_raises_value_error() -> (
    None
):
    """Test get_repository raises ValueError when name is empty."""
    mock_spreadsheet = MagicMock(spec=Spreadsheet)
    inventory = Inventory(spreadsheet=mock_spreadsheet)

    with pytest.raises(ValueError, match="Repository name cannot be empty"):
        inventory.get_repository("   ")


def test_given_repository_primitives_when_registering_repository_then_creates_new_record() -> (
    None
):
    """Test register_repository creates a new row with provided primitive values."""
    mock_spreadsheet = MagicMock(spec=Spreadsheet)
    mock_spreadsheet.find_by.return_value = []
    mock_spreadsheet.add.side_effect = lambda row: row

    commit_time = datetime(2026, 10, 15, tzinfo=timezone.utc)
    inventory = Inventory(spreadsheet=mock_spreadsheet)

    record = inventory.register_repository(
        name="library-app",
        project="platform-suite",
        stack="Python",
        last_commit=commit_time,
        initial_status=MigrationStatus.WAITTING,
    )

    assert record.name == "library-app"
    assert record.project == "platform-suite"
    assert record.stack == "Python"
    assert record.last_commit == commit_time
    assert record.migration_status == MigrationStatus.WAITTING
    assert record.gh_migratation is None
    assert record.origin_deactivation is None
    mock_spreadsheet.add.assert_called_once()


def test_given_migrated_status_when_registering_repository_then_sets_migration_date() -> (
    None
):
    """Test register_repository with MIGRATED status automatically sets dates."""
    mock_spreadsheet = MagicMock(spec=Spreadsheet)
    mock_spreadsheet.find_by.return_value = []
    mock_spreadsheet.add.side_effect = lambda row: row

    inventory = Inventory(spreadsheet=mock_spreadsheet)
    record = inventory.register_repository(
        name="already-migrated",
        project="core",
        initial_status=MigrationStatus.MIGRATED,
    )

    assert record.name == "already-migrated"
    assert record.migration_status == MigrationStatus.MIGRATED
    assert record.gh_migratation is not None
    assert record.origin_deactivation is not None


def test_given_existing_repository_when_registering_then_preserves_status_and_migration_dates() -> (
    None
):
    """Test register_repository strictly preserves migration status and dates."""
    mock_spreadsheet = MagicMock(spec=Spreadsheet)

    original_migration_date = datetime(2026, 8, 1, tzinfo=timezone.utc)
    original_deactivation_date = datetime(2026, 8, 15, tzinfo=timezone.utc)
    existing_row = Row(
        row_index=7,
        data={
            "name": "existing-app",
            "project": "old-proj",
            "stack": "Java",
            "last_commit": datetime(2026, 7, 1, tzinfo=timezone.utc),
            "gh_migratation": original_migration_date,
            "origin_deactivation": original_deactivation_date,
            "migration_status": MigrationStatus.MIGRATED,
        },
    )
    mock_spreadsheet.find_by.return_value = [existing_row]
    mock_spreadsheet.update_row.side_effect = lambda row: row

    inventory = Inventory(spreadsheet=mock_spreadsheet)
    new_commit = datetime(2026, 10, 18, tzinfo=timezone.utc)

    updated = inventory.register_repository(
        name="existing-app",
        project="new-proj",
        stack="Java, Kotlin",
        last_commit=new_commit,
        initial_status=MigrationStatus.WAITTING,  # Should NOT overwrite MIGRATED!
    )

    assert updated.name == "existing-app"
    assert updated.project == "new-proj"
    assert updated.stack == "Java, Kotlin"
    assert updated.last_commit == new_commit
    # Strictly preserved:
    assert updated.migration_status == MigrationStatus.MIGRATED
    assert updated.gh_migratation == original_migration_date
    assert updated.origin_deactivation == original_deactivation_date
    mock_spreadsheet.update_row.assert_called_once()
    mock_spreadsheet.add.assert_not_called()


def test_given_repository_domain_object_when_registering_then_creates_matching_record() -> (
    None
):
    """Test register_repository duck-types repository-like objects."""
    mock_spreadsheet = MagicMock(spec=Spreadsheet)
    mock_spreadsheet.find_by.return_value = []
    mock_spreadsheet.add.side_effect = lambda row: row

    class DummyStack:
        def __init__(self, val: str) -> None:
            self.value = val

    class DummyRepo:
        name = "duck-typed-repo"
        project = "duck-proj"
        stacks = [DummyStack("python"), DummyStack("bash")]
        last_commit = datetime(2026, 9, 20, tzinfo=timezone.utc)

    inventory = Inventory(spreadsheet=mock_spreadsheet)
    record = inventory.register_repository(DummyRepo())

    assert record.name == "duck-typed-repo"
    assert record.project == "duck-proj"
    assert "Python" in record.stack
    assert "Bash" in record.stack
    assert record.last_commit == datetime(2026, 9, 20, tzinfo=timezone.utc)
    assert record.migration_status == MigrationStatus.WAITTING


def test_given_batch_of_repositories_when_syncing_then_registers_all_in_inventory() -> (
    None
):
    """Test sync_repositories synchronizes an iterable of repositories."""
    mock_spreadsheet = MagicMock(spec=Spreadsheet)
    mock_spreadsheet.find_by.return_value = []
    mock_spreadsheet.add.side_effect = lambda row: row

    inventory = Inventory(spreadsheet=mock_spreadsheet)
    records = inventory.sync_repositories(
        [
            Repository(name="batch-1", project="p1"),
            Repository(name="batch-2", project="p2"),
        ]
    )

    assert len(records) == 2
    assert records[0].name == "batch-1"
    assert records[1].name == "batch-2"
    assert mock_spreadsheet.add.call_count == 2


def test_given_custom_converter_when_initializing_inventory_then_uses_injected_instance() -> (
    None
):
    """Test Inventory uses an injected RepositoryConverter instance for domain conversions."""
    mock_spreadsheet = MagicMock(spec=Spreadsheet)
    mock_spreadsheet.find_by.return_value = []
    mock_spreadsheet.add.side_effect = lambda row: row

    mock_converter = MagicMock(spec=RepositoryConverter)
    custom_record = InventoryRecord(
        name="custom-converted-repo",
        project="custom-team",
        stack="Rust",
        migration_status=MigrationStatus.WAITTING,
    )
    mock_converter.to_inventory_record.return_value = custom_record

    inventory = Inventory(spreadsheet=mock_spreadsheet, converter=mock_converter)
    repo = Repository(name="custom-converted-repo")

    record = inventory.register_repository(repo)

    mock_converter.to_inventory_record.assert_called_once_with(
        repo, initial_status=MigrationStatus.WAITTING
    )
    assert record.name == "custom-converted-repo"
    assert record.project == "custom-team"
    assert record.stack == "Rust"


# ============================================================================
# Inventory Construction & sync_records Validation Tests
# ============================================================================


def test_given_valid_spreadsheet_id_and_credentials_path_when_syncing_records_then_builds_objects_and_synchronizes(
    tmp_path: Any,
) -> None:
    """Verify sync_records builds Credentials and Spreadsheet and synchronizes."""
    creds_file = tmp_path / "valid_creds.json"
    creds_file.write_text('{"type": "service_account"}')

    mock_sheet = MagicMock(spec=Spreadsheet)
    mock_sheet.id = "my-sheet-123"
    mock_sheet.find_by.return_value = []
    mock_sheet.add.side_effect = lambda row: row

    with patch(
        "gh_migration_inventory_manager.google.Spreadsheet", return_value=mock_sheet
    ):
        inventory = Inventory(
            spreadsheet_id="my-sheet-123",
            credentials_path=str(creds_file),
            sheet_name="Inventario",
        )
        record = InventoryRecord(name="migrated-app")
        synced = inventory.sync_records([record])

        assert len(synced) == 1
        assert synced[0].name == "migrated-app"
        assert inventory.spreadsheet is mock_sheet
        mock_sheet.validate.assert_called_once()
        assert inventory.credentials.credentials_path == str(creds_file)


def test_given_missing_spreadsheet_id_when_syncing_records_then_raises_value_error() -> (
    None
):
    """Verify sync_records raises ValueError when spreadsheet_id is missing."""
    inventory = Inventory(credentials_path="/tmp/fake_key.json")
    with pytest.raises(ValueError, match="Missing Google Spreadsheet ID"):
        inventory.sync_records([])


def test_given_empty_spreadsheet_id_when_syncing_records_then_raises_value_error() -> (
    None
):
    """Verify sync_records raises ValueError when spreadsheet_id is whitespace."""
    inventory = Inventory(spreadsheet_id="   ", credentials_path="/tmp/fake_key.json")
    with pytest.raises(ValueError, match="Missing Google Spreadsheet ID"):
        inventory.sync_records([])


def test_given_missing_credentials_path_when_syncing_records_then_raises_value_error() -> (
    None
):
    """Verify sync_records raises ValueError when credentials_path is missing."""
    inventory = Inventory(spreadsheet_id="sheet-123")
    with pytest.raises(ValueError, match="Missing Google credentials path"):
        inventory.sync_records([])


def test_given_empty_credentials_path_when_syncing_records_then_raises_value_error() -> (
    None
):
    """Verify sync_records raises ValueError when credentials_path is whitespace."""
    inventory = Inventory(spreadsheet_id="sheet-123", credentials_path="   ")
    with pytest.raises(ValueError, match="Missing Google credentials path"):
        inventory.sync_records([])


def test_given_nonexistent_credentials_path_when_syncing_records_then_raises_file_not_found_error() -> (
    None
):
    """Verify sync_records raises FileNotFoundError when credentials file does not exist."""
    inventory = Inventory(
        spreadsheet_id="sheet-123",
        credentials_path="/non/existent/path/service_key.json",
    )
    with pytest.raises(FileNotFoundError, match="Google credentials file not found"):
        inventory.sync_records([])


def test_given_inconsistent_spreadsheet_id_and_instance_when_syncing_records_then_raises_value_error() -> (
    None
):
    """Verify sync_records raises ValueError when spreadsheet.id does not match spreadsheet_id."""
    mock_sheet = MagicMock(spec=Spreadsheet)
    mock_sheet.id = "sheet-A"

    inventory = Inventory(spreadsheet=mock_sheet, spreadsheet_id="sheet-B")
    with pytest.raises(ValueError, match="Inconsistent spreadsheet configuration"):
        inventory.sync_records([])


def test_given_inconsistent_credentials_path_and_instance_when_syncing_records_then_raises_value_error() -> (
    None
):
    """Verify sync_records raises ValueError when credentials_path does not match Credentials object."""
    creds = Credentials(credentials_path="/path/A.json")
    inventory = Inventory(
        spreadsheet_id="sheet-123",
        credentials=creds,
        credentials_path="/path/B.json",
    )
    with pytest.raises(ValueError, match="Inconsistent credentials configuration"):
        inventory.sync_records([])


def test_given_spreadsheet_not_found_when_syncing_records_then_raises_spreadsheet_not_found_error() -> (
    None
):
    """Verify sync_records propagates SpreadsheetNotFoundError from spreadsheet validation."""
    mock_sheet = MagicMock(spec=Spreadsheet)
    mock_sheet.validate.side_effect = SpreadsheetNotFoundError("Sheet not found")

    inventory = Inventory(spreadsheet=mock_sheet)
    with pytest.raises(SpreadsheetNotFoundError, match="Sheet not found"):
        inventory.sync_records([])


def test_given_spreadsheet_access_denied_when_syncing_records_then_raises_spreadsheet_access_error() -> (
    None
):
    """Verify sync_records propagates SpreadsheetAccessError from spreadsheet validation."""
    mock_sheet = MagicMock(spec=Spreadsheet)
    mock_sheet.validate.side_effect = SpreadsheetAccessError("Access denied")

    inventory = Inventory(spreadsheet=mock_sheet)
    with pytest.raises(SpreadsheetAccessError, match="Access denied"):
        inventory.sync_records([])


def test_given_spreadsheet_api_error_when_syncing_records_then_raises_spreadsheet_error() -> (
    None
):
    """Verify sync_records propagates general SpreadsheetError from spreadsheet validation."""
    mock_sheet = MagicMock(spec=Spreadsheet)
    mock_sheet.validate.side_effect = SpreadsheetError("Backend service unavailable")

    inventory = Inventory(spreadsheet=mock_sheet)
    with pytest.raises(SpreadsheetError, match="Backend service unavailable"):
        inventory.sync_records([])
