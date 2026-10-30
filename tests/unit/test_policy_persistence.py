"""Unit tests for PolicyInventory domain orchestration and lifecycle persistence."""

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
    PolicyInventory,
    PolicyRecord,
)


@pytest.fixture
def mock_spreadsheet() -> MagicMock:
    """Fixture providing a mock Spreadsheet client for PolicyInventory."""
    sheet = MagicMock(spec=Spreadsheet)
    sheet.id = "test-spreadsheet-id"
    sheet.sheet_name = "Policies"
    return sheet


@pytest.fixture
def sample_policy_record() -> PolicyRecord:
    """Fixture providing a sample PolicyRecord."""
    return PolicyRecord(
        policy_scope="repo:payment-service",
        target_ref_pattern="refs/heads/main",
        normalized_rule_type="RequirePullRequest",
        rule_parameters="min_approvals: 2",
        bypass_team_slugs="core-devs",
        origin_raw_policy="raw-json",
        migration_status=MigrationStatus.WAITTING,
    )


def test_given_unregistered_policy_when_registering_then_creates_new_row_in_sheet(
    mock_spreadsheet: MagicMock,
    sample_policy_record: PolicyRecord,
) -> None:
    """Test registering a new policy appends a new row in Google Sheets."""
    mock_spreadsheet.find_by.return_value = []
    mock_spreadsheet.add.return_value = Row(
        row_index=2,
        data={
            "policy_scope": "repo:payment-service",
            "target_ref_pattern": "refs/heads/main",
            "normalized_rule_type": "RequirePullRequest",
            "rule_parameters": "min_approvals: 2",
            "bypass_team_slugs": "core-devs",
            "origin_raw_policy": "raw-json",
            "migration_status": MigrationStatus.WAITTING,
        },
    )

    inventory = PolicyInventory(spreadsheet=mock_spreadsheet)
    saved = inventory.register_record(sample_policy_record)

    mock_spreadsheet.add.assert_called_once()
    assert saved.policy_scope == "repo:payment-service"
    assert saved.target_ref_pattern == "refs/heads/main"
    assert saved.normalized_rule_type == "RequirePullRequest"
    assert saved.migration_status == MigrationStatus.WAITTING


def test_given_existing_policy_with_updates_when_registering_then_preserves_status_and_updates_row(
    mock_spreadsheet: MagicMock,
    sample_policy_record: PolicyRecord,
) -> None:
    """Test updating an existing policy preserves its migration_status."""
    mock_spreadsheet.find_by.return_value = [
        Row(
            row_index=5,
            data={
                "policy_scope": "repo:payment-service",
                "target_ref_pattern": "refs/heads/main",
                "normalized_rule_type": "RequirePullRequest",
                "rule_parameters": "min_approvals: 1",
                "bypass_team_slugs": "",
                "origin_raw_policy": "old-json",
                "migration_status": MigrationStatus.MIGRATED,
            },
        )
    ]
    mock_spreadsheet.update_row.return_value = Row(
        row_index=5,
        data={
            "policy_scope": "repo:payment-service",
            "target_ref_pattern": "refs/heads/main",
            "normalized_rule_type": "RequirePullRequest",
            "rule_parameters": "min_approvals: 2",
            "bypass_team_slugs": "core-devs",
            "origin_raw_policy": "raw-json",
            "migration_status": MigrationStatus.MIGRATED,
        },
    )

    inventory = PolicyInventory(spreadsheet=mock_spreadsheet)
    saved = inventory.register_record(sample_policy_record)

    mock_spreadsheet.update_row.assert_called_once()
    assert saved.migration_status == MigrationStatus.MIGRATED
    assert saved.rule_parameters == "min_approvals: 2"


def test_given_existing_policy_with_identical_data_when_registering_then_avoids_update(
    mock_spreadsheet: MagicMock,
    sample_policy_record: PolicyRecord,
) -> None:
    """Test re-registering identical record avoids calling update_row."""
    mock_spreadsheet.find_by.return_value = [
        Row(
            row_index=5,
            data={
                "policy_scope": "repo:payment-service",
                "target_ref_pattern": "refs/heads/main",
                "normalized_rule_type": "RequirePullRequest",
                "rule_parameters": "min_approvals: 2",
                "bypass_team_slugs": "core-devs",
                "origin_raw_policy": "raw-json",
                "migration_status": MigrationStatus.WAITTING,
            },
        )
    ]

    inventory = PolicyInventory(spreadsheet=mock_spreadsheet)
    saved = inventory.register_record(sample_policy_record)

    mock_spreadsheet.update_row.assert_not_called()
    mock_spreadsheet.add.assert_not_called()
    assert saved.policy_scope == "repo:payment-service"


def test_given_existing_policy_when_updating_progress_then_updates_sheet(
    mock_spreadsheet: MagicMock,
) -> None:
    """Test updating progress for an existing policy updates the row in sheet."""
    mock_spreadsheet.find_by.return_value = [
        Row(
            row_index=3,
            data={
                "policy_scope": "repo:payment-service",
                "target_ref_pattern": "refs/heads/main",
                "normalized_rule_type": "RequirePullRequest",
                "rule_parameters": "min_approvals: 2",
                "bypass_team_slugs": "core-devs",
                "origin_raw_policy": "raw",
                "migration_status": MigrationStatus.WAITTING,
            },
        )
    ]
    mock_spreadsheet.update_row.return_value = Row(
        row_index=3,
        data={
            "policy_scope": "repo:payment-service",
            "target_ref_pattern": "refs/heads/main",
            "normalized_rule_type": "RequirePullRequest",
            "rule_parameters": "min_approvals: 2",
            "bypass_team_slugs": "core-devs",
            "origin_raw_policy": "raw",
            "migration_status": MigrationStatus.MIGRATED,
        },
    )

    inventory = PolicyInventory(spreadsheet=mock_spreadsheet)
    updated = inventory.update_progress(
        "repo:payment-service",
        "refs/heads/main",
        "RequirePullRequest",
        MigrationStatus.MIGRATED,
    )

    assert updated.migration_status == MigrationStatus.MIGRATED
    mock_spreadsheet.update_row.assert_called_once()


def test_given_unregistered_policy_when_updating_progress_then_creates_stub_record(
    mock_spreadsheet: MagicMock,
) -> None:
    """Test updating progress for an unrecorded policy creates a stub record."""
    mock_spreadsheet.find_by.return_value = []
    mock_spreadsheet.add.return_value = Row(
        row_index=4,
        data={
            "policy_scope": "project:NEW",
            "target_ref_pattern": "refs/heads/release/*",
            "normalized_rule_type": "BlockForcePush",
            "rule_parameters": "",
            "bypass_team_slugs": "",
            "origin_raw_policy": "",
            "migration_status": MigrationStatus.ACTIVE_IN_BOTH,
        },
    )

    inventory = PolicyInventory(spreadsheet=mock_spreadsheet)
    saved = inventory.update_progress(
        "project:NEW",
        "refs/heads/release/*",
        "BlockForcePush",
        "active_in_both",
    )

    mock_spreadsheet.add.assert_called_once()
    assert saved.migration_status == MigrationStatus.ACTIVE_IN_BOTH


def test_given_existing_composite_key_when_getting_policy_then_returns_record(
    mock_spreadsheet: MagicMock,
) -> None:
    """Test get_policy returns populated record when composite key matches."""
    mock_spreadsheet.find_by.return_value = [
        Row(
            row_index=2,
            data={
                "policy_scope": "repo:app",
                "target_ref_pattern": "refs/heads/main",
                "normalized_rule_type": "PreventBranchDeletion",
                "rule_parameters": "",
                "bypass_team_slugs": "",
                "origin_raw_policy": "",
                "migration_status": MigrationStatus.WAITTING,
            },
        )
    ]

    inventory = PolicyInventory(spreadsheet=mock_spreadsheet)
    record = inventory.get_policy(
        "repo:app", "refs/heads/main", "PreventBranchDeletion"
    )

    assert record is not None
    assert record.normalized_rule_type == "PreventBranchDeletion"


def test_given_non_existing_composite_key_when_getting_policy_then_returns_none(
    mock_spreadsheet: MagicMock,
) -> None:
    """Test get_policy returns None when composite key is not found."""
    mock_spreadsheet.find_by.return_value = []
    inventory = PolicyInventory(spreadsheet=mock_spreadsheet)

    assert (
        inventory.get_policy("repo:unknown", "refs/heads/main", "BlockForcePush")
        is None
    )


def test_given_batch_of_policy_records_when_syncing_then_persists_all(
    mock_spreadsheet: MagicMock,
    sample_policy_record: PolicyRecord,
) -> None:
    """Test sync_records synchronizes all provided records."""
    mock_spreadsheet.find_by.return_value = []
    mock_spreadsheet.add.return_value = Row(
        row_index=2,
        data={
            "policy_scope": "repo:payment-service",
            "target_ref_pattern": "refs/heads/main",
            "normalized_rule_type": "RequirePullRequest",
            "rule_parameters": "min_approvals: 2",
            "bypass_team_slugs": "core-devs",
            "origin_raw_policy": "raw-json",
            "migration_status": MigrationStatus.WAITTING,
        },
    )

    inventory = PolicyInventory(spreadsheet=mock_spreadsheet)
    synced = inventory.sync_records([sample_policy_record])

    assert len(synced) == 1
    mock_spreadsheet.add.assert_called_once()


def test_given_none_record_when_registering_then_raises_value_error(
    mock_spreadsheet: MagicMock,
) -> None:
    """Verify register_record raises ValueError when record is None."""
    inventory = PolicyInventory(spreadsheet=mock_spreadsheet)
    with pytest.raises(ValueError, match="PolicyRecord cannot be None"):
        inventory.register_record(None)  # type: ignore[arg-type]


def test_given_empty_scope_when_registering_then_raises_value_error(
    mock_spreadsheet: MagicMock,
) -> None:
    """Verify register_record raises ValueError when scope is empty."""
    inventory = PolicyInventory(spreadsheet=mock_spreadsheet)
    rec = PolicyRecord(
        policy_scope="",
        target_ref_pattern="refs/heads/main",
        normalized_rule_type="BlockForcePush",
    )
    with pytest.raises(ValueError, match="Policy scope cannot be empty"):
        inventory.register_record(rec)


def test_given_empty_target_ref_when_registering_then_raises_value_error(
    mock_spreadsheet: MagicMock,
) -> None:
    """Verify register_record raises ValueError when target ref pattern is empty."""
    inventory = PolicyInventory(spreadsheet=mock_spreadsheet)
    rec = PolicyRecord(
        policy_scope="repo:app",
        target_ref_pattern="",
        normalized_rule_type="BlockForcePush",
    )
    with pytest.raises(ValueError, match="Target ref pattern cannot be empty"):
        inventory.register_record(rec)


def test_given_empty_rule_type_when_registering_then_raises_value_error(
    mock_spreadsheet: MagicMock,
) -> None:
    """Verify register_record raises ValueError when rule type is empty."""
    inventory = PolicyInventory(spreadsheet=mock_spreadsheet)
    rec = PolicyRecord(
        policy_scope="repo:app",
        target_ref_pattern="refs/heads/main",
        normalized_rule_type="",
    )
    with pytest.raises(ValueError, match="Normalized rule type cannot be empty"):
        inventory.register_record(rec)


def test_given_none_collection_when_syncing_then_raises_value_error(
    mock_spreadsheet: MagicMock,
) -> None:
    """Verify sync_records raises ValueError when collection is None."""
    inventory = PolicyInventory(spreadsheet=mock_spreadsheet)
    with pytest.raises(ValueError, match="Records collection cannot be None"):
        inventory.sync_records(None)  # type: ignore[arg-type]


def test_given_spreadsheet_http_404_when_validating_then_raises_inventory_not_found_error() -> (
    None
):
    """Verify SpreadsheetNotFoundError is mapped to InventoryNotFoundError."""
    sheet = MagicMock(spec=Spreadsheet)
    sheet.id = "bad-id"
    sheet.sheet_name = "Policies"
    sheet.validate.side_effect = SpreadsheetNotFoundError("Spreadsheet not found")

    inventory = PolicyInventory(spreadsheet=sheet, spreadsheet_id="bad-id")
    with pytest.raises(InventoryNotFoundError):
        inventory.register_record(
            PolicyRecord(
                policy_scope="repo:app",
                target_ref_pattern="refs/heads/main",
                normalized_rule_type="BlockForcePush",
            )
        )


def test_given_spreadsheet_http_403_when_validating_then_raises_inventory_access_error() -> (
    None
):
    """Verify SpreadsheetAccessError is mapped to InventoryAccessError."""
    sheet = MagicMock(spec=Spreadsheet)
    sheet.id = "forbidden-id"
    sheet.sheet_name = "Policies"
    sheet.validate.side_effect = SpreadsheetAccessError("Access denied")

    inventory = PolicyInventory(spreadsheet=sheet, spreadsheet_id="forbidden-id")
    with pytest.raises(InventoryAccessError):
        inventory.register_record(
            PolicyRecord(
                policy_scope="repo:app",
                target_ref_pattern="refs/heads/main",
                normalized_rule_type="BlockForcePush",
            )
        )
