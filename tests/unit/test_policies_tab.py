"""Unit tests for Google Sheets Policies tab and PolicyRowConverter."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from gh_migration_inventory_manager.google import (
    POLICIES_HEADERS,
    Credentials,
    PolicyRowConverter,
    Row,
    Spreadsheet,
    SpreadsheetError,
)
from gh_migration_inventory_manager.inventory import MigrationStatus, PolicyRecord


@pytest.fixture
def sample_policy_record() -> PolicyRecord:
    """Fixture providing a sample PolicyRecord."""
    return PolicyRecord(
        policy_scope="repo:payment-service",
        target_ref_pattern="refs/heads/main",
        normalized_rule_type="RequirePullRequest",
        rule_parameters="min_approvals: 2; require_codeowners: true",
        bypass_team_slugs="core-devs, release-leads",
        origin_raw_policy='{"id": 101, "type": "pull-request-only"}',
        migration_status=MigrationStatus.ACTIVE_IN_BOTH,
    )


@pytest.fixture
def mock_policies_spreadsheet(mock_sheets_service: MagicMock) -> Spreadsheet:
    """Fixture providing a Spreadsheet client bound to the 'Policies' tab."""
    return Spreadsheet(
        id="policies-sheet-id",
        credentials=Credentials(credentials_path="dummy-path.json"),
        sheet_name="Policies",
        service=mock_sheets_service,
    )


def test_given_policy_record_when_converting_to_row_then_maps_all_seven_columns(
    sample_policy_record: PolicyRecord,
    mock_policies_spreadsheet: Spreadsheet,
) -> None:
    """Test PolicyRowConverter creates a Row and Spreadsheet serializes all 7 columns."""
    row = PolicyRowConverter.from_policy_record(sample_policy_record, row_index=4)

    assert row.row_index == 4
    assert row.data["policy_scope"] == "repo:payment-service"
    assert row.data["target_ref_pattern"] == "refs/heads/main"
    assert row.data["normalized_rule_type"] == "RequirePullRequest"
    assert row.data["rule_parameters"] == "min_approvals: 2; require_codeowners: true"
    assert row.data["bypass_team_slugs"] == "core-devs, release-leads"
    assert row.data["origin_raw_policy"] == '{"id": 101, "type": "pull-request-only"}'
    assert row.data["migration_status"] == MigrationStatus.ACTIVE_IN_BOTH

    sheet_cells = mock_policies_spreadsheet._to_sheet_row(row)
    assert len(sheet_cells) == 7
    assert sheet_cells[0] == "repo:payment-service"
    assert sheet_cells[1] == "refs/heads/main"
    assert sheet_cells[2] == "RequirePullRequest"
    assert sheet_cells[3] == "min_approvals: 2; require_codeowners: true"
    assert sheet_cells[4] == "core-devs, release-leads"
    assert sheet_cells[5] == '{"id": 101, "type": "pull-request-only"}'
    assert sheet_cells[6] == "Migrando (Ativos em Ambas)"


def test_given_row_when_converting_to_policy_record_then_populates_record() -> None:
    """Test PolicyRowConverter converts a Row back to a PolicyRecord."""
    row = Row(
        row_index=5,
        data={
            "policy_scope": "project:CORE",
            "target_ref_pattern": "refs/heads/release/*",
            "normalized_rule_type": "BlockForcePush",
            "rule_parameters": "linear_history: true",
            "bypass_team_slugs": "release-leads",
            "origin_raw_policy": "fast-forward-only",
            "migration_status": "Migrados (Somente GitHub)",
        },
    )

    record = PolicyRowConverter.to_policy_record(row)

    assert isinstance(record, PolicyRecord)
    assert record.policy_scope == "project:CORE"
    assert record.target_ref_pattern == "refs/heads/release/*"
    assert record.normalized_rule_type == "BlockForcePush"
    assert record.rule_parameters == "linear_history: true"
    assert record.bypass_team_slugs == "release-leads"
    assert record.origin_raw_policy == "fast-forward-only"
    assert record.migration_status == MigrationStatus.MIGRATED


def test_given_none_policy_record_when_converting_to_row_then_raises_value_error() -> (
    None
):
    """Verify PolicyRowConverter raises ValueError when record is None."""
    with pytest.raises(ValueError, match="PolicyRecord cannot be None"):
        PolicyRowConverter.from_policy_record(None)  # type: ignore[arg-type]


def test_given_none_row_when_converting_to_policy_record_then_raises_value_error() -> (
    None
):
    """Verify PolicyRowConverter raises ValueError when row is None."""
    with pytest.raises(ValueError, match="Row cannot be None"):
        PolicyRowConverter.to_policy_record(None)  # type: ignore[arg-type]


def test_given_policies_sheet_name_when_finding_row_then_queries_seven_column_range(
    mock_policies_spreadsheet: Spreadsheet,
    mock_sheets_service: MagicMock,
) -> None:
    """Test find_by queries A2:G range when tab is Policies."""
    values_get = mock_sheets_service.spreadsheets().values().get
    values_get.return_value.execute.return_value = {
        "values": [
            [
                "repo:frontend",
                "refs/heads/main",
                "RequirePullRequest",
                "min_approvals: 1",
                "core-devs",
                "{}",
                "Aguardando Migração",
            ]
        ]
    }

    rows = mock_policies_spreadsheet.find_by("policy_scope", "repo:frontend")

    assert len(rows) == 1
    assert rows[0].row_index == 2
    assert rows[0].data["policy_scope"] == "repo:frontend"
    assert rows[0].data["normalized_rule_type"] == "RequirePullRequest"
    values_get.assert_called_with(
        spreadsheetId="policies-sheet-id",
        range="Policies!A2:G",
    )


@pytest.mark.parametrize(
    ("alias", "expected_col_idx"),
    [
        ("policy_scope", 0),
        ("escopo da política", 0),
        ("escopo", 0),
        ("scope", 0),
        ("target_ref_pattern", 1),
        ("padrão de ref / branch", 1),
        ("branch", 1),
        ("ref", 1),
        ("normalized_rule_type", 2),
        ("tipo de regra (github)", 2),
        ("rule_type", 2),
        ("rule", 2),
        ("rule_parameters", 3),
        ("parâmetros da regra", 3),
        ("parameters", 3),
        ("bypass_team_slugs", 4),
        ("slugs de times de bypass", 4),
        ("bypass_teams", 4),
        ("origin_raw_policy", 5),
        ("política de origem (raw)", 5),
        ("migration_status", 6),
        ("status da migração", 6),
        ("status", 6),
    ],
)
def test_given_column_alias_when_finding_by_then_resolves_correct_policies_column(
    mock_policies_spreadsheet: Spreadsheet,
    mock_sheets_service: MagicMock,
    alias: str,
    expected_col_idx: int,
) -> None:
    """Verify all English and Portuguese column aliases map to correct column index."""
    row_vals = [""] * 7
    target_val = "waitting" if expected_col_idx == 6 else "target-val"
    row_vals[expected_col_idx] = target_val
    values_get = mock_sheets_service.spreadsheets().values().get
    values_get.return_value.execute.return_value = {"values": [row_vals]}

    rows = mock_policies_spreadsheet.find_by(alias, target_val)

    assert len(rows) == 1


def test_given_invalid_column_name_when_finding_row_then_raises_value_error(
    mock_policies_spreadsheet: Spreadsheet,
) -> None:
    """Verify find_by raises ValueError on invalid column identifier."""
    with pytest.raises(ValueError, match="Unknown column identifier"):
        mock_policies_spreadsheet.find_by("invalid_column_xyz", "val")


def test_given_ensure_sheet_when_policies_tab_missing_then_adds_sheet_with_headers(
    mock_policies_spreadsheet: Spreadsheet,
    mock_sheets_service: MagicMock,
) -> None:
    """Verify ensure_sheet creates missing Policies tab with POLICIES_HEADERS."""
    get_meta = mock_sheets_service.spreadsheets().get
    get_meta.return_value.execute.return_value = {
        "sheets": [{"properties": {"title": "Inventario"}}]
    }

    mock_policies_spreadsheet.ensure_sheet("Policies")

    batch_update = mock_sheets_service.spreadsheets().batchUpdate
    batch_update.assert_called_once_with(
        spreadsheetId="policies-sheet-id",
        body={"requests": [{"addSheet": {"properties": {"title": "Policies"}}}]},
    )

    update_vals = mock_sheets_service.spreadsheets().values().update
    update_vals.assert_called_once_with(
        spreadsheetId="policies-sheet-id",
        range="Policies!A1:G1",
        valueInputOption="USER_ENTERED",
        body={"values": [POLICIES_HEADERS]},
    )


def test_given_clear_tab_when_called_then_clears_data_rows_range(
    mock_policies_spreadsheet: Spreadsheet,
    mock_sheets_service: MagicMock,
) -> None:
    """Verify clear_tab clears range A2:G in Policies tab."""
    get_meta = mock_sheets_service.spreadsheets().get
    get_meta.return_value.execute.return_value = {
        "sheets": [{"properties": {"title": "Policies"}}]
    }

    mock_policies_spreadsheet.clear_tab("Policies")

    clear_vals = mock_sheets_service.spreadsheets().values().clear
    clear_vals.assert_called_once_with(
        spreadsheetId="policies-sheet-id",
        range="Policies!A2:G",
    )


def test_given_api_error_when_clearing_tab_then_raises_spreadsheet_error(
    mock_policies_spreadsheet: Spreadsheet,
    mock_sheets_service: MagicMock,
) -> None:
    """Verify clear_tab translates API failures to SpreadsheetError."""
    get_meta = mock_sheets_service.spreadsheets().get
    get_meta.return_value.execute.return_value = {
        "sheets": [{"properties": {"title": "Policies"}}]
    }
    clear_vals = mock_sheets_service.spreadsheets().values().clear
    clear_vals.return_value.execute.side_effect = RuntimeError("API down")

    with pytest.raises(SpreadsheetError, match="Error clearing sheet"):
        mock_policies_spreadsheet.clear_tab("Policies")
