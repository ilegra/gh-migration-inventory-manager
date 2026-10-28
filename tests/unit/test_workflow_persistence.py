"""Unit tests for workflow persistence and Google Sheets integration."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from gh_migration_inventory_manager.google import (
    _WORKFLOW_HEADERS,
    Credentials,
    Row,
    Spreadsheet,
    WorkflowRowConverter,
)
from gh_migration_inventory_manager.inventory import (
    MigrationStatus,
    PipelineInventory,
    PipelineRecord,
)


@pytest.fixture
def sample_pipeline_record() -> PipelineRecord:
    """Fixture providing a sample PipelineRecord."""
    return PipelineRecord(
        workflow_id="deployments/prod-deploy",
        platform="jenkins",
        pipeline_type="declarative",
        repository_association="org/prod-deploy-scripts",
        repository_url="https://github.com/org/prod-deploy-scripts.git",
        file_path="Jenkinsfile",
        branch="main",
        last_build=datetime(2026, 10, 27, 10, 0, 0, tzinfo=timezone.utc),
        last_status="SUCCESS",
        migration_status=MigrationStatus.ACTIVE_IN_BOTH,
    )


@pytest.fixture
def mock_workflow_spreadsheet(mock_sheets_service: MagicMock) -> Spreadsheet:
    """Fixture providing a Spreadsheet client bound to the 'Workflows' tab."""
    return Spreadsheet(
        id="workflows-sheet-id",
        credentials=Credentials(credentials_path="dummy-path.json"),
        sheet_name="Workflows",
        service=mock_sheets_service,
    )


# --- WorkflowRowConverter Tests ---


def test_given_pipeline_record_when_converting_to_row_then_maps_all_ten_columns(
    sample_pipeline_record: PipelineRecord,
    mock_workflow_spreadsheet: Spreadsheet,
    mock_sheets_service: MagicMock,
) -> None:
    """Test WorkflowRowConverter creates a Row and Spreadsheet serializes all 10 columns."""
    row = WorkflowRowConverter.from_pipeline_record(sample_pipeline_record, row_index=5)

    assert row.row_index == 5
    assert row.data["workflow_id"] == "deployments/prod-deploy"
    assert row.data["platform"] == "jenkins"
    assert row.data["pipeline_type"] == "declarative"
    assert row.data["repository_association"] == "org/prod-deploy-scripts"
    assert (
        row.data["repository_url"] == "https://github.com/org/prod-deploy-scripts.git"
    )
    assert row.data["file_path"] == "Jenkinsfile"
    assert row.data["branch"] == "main"
    assert row.data["last_status"] == "SUCCESS"
    assert row.data["migration_status"] == MigrationStatus.ACTIVE_IN_BOTH

    mock_append = (
        mock_sheets_service.spreadsheets.return_value.values.return_value.append
    )
    mock_append.return_value.execute.return_value = {
        "updates": {"updatedRange": "Workflows!A5:J5"}
    }

    added_row = mock_workflow_spreadsheet.add(row)
    assert len(added_row.values) == 10
    assert added_row.values[0] == "deployments/prod-deploy"
    assert added_row.values[1] == "jenkins"
    assert added_row.values[2] == "declarative"
    assert added_row.values[3] == "org/prod-deploy-scripts"
    assert added_row.values[4] == "https://github.com/org/prod-deploy-scripts.git"
    assert added_row.values[5] == "Jenkinsfile"
    assert added_row.values[6] == "main"
    assert "27/10/2026" in added_row.values[7]
    assert added_row.values[8] == "SUCCESS"
    assert added_row.values[9] == "Migrando (Ativos em Ambas)"


def test_given_row_when_converting_to_pipeline_record_then_populates_pipeline_record() -> (
    None
):
    """Test WorkflowRowConverter reconstitutes a PipelineRecord from a Row."""
    row = Row(
        data={
            "workflow_id": "services/auth-service",
            "platform": "jenkins",
            "pipeline_type": "multibranch",
            "repository_association": "fintech/auth-service",
            "repository_url": "https://github.com/fintech/auth-service.git",
            "file_path": "Jenkinsfile",
            "branch": "develop",
            "last_build": "2026-10-25T14:30:00",
            "last_status": "FAILURE",
            "migration_status": "Migrados (Somente GitHub)",
        },
        row_index=3,
    )

    record = WorkflowRowConverter.to_pipeline_record(row)

    assert record.workflow_id == "services/auth-service"
    assert record.platform == "jenkins"
    assert record.pipeline_type == "multibranch"
    assert record.repository_association == "fintech/auth-service"
    assert record.repository_url == "https://github.com/fintech/auth-service.git"
    assert record.file_path == "Jenkinsfile"
    assert record.branch == "develop"
    assert record.last_status == "FAILURE"
    assert record.migration_status == MigrationStatus.MIGRATED
    assert record.last_build == datetime(2026, 10, 25, 14, 30, 0)


def test_given_none_pipeline_record_when_converting_to_row_then_raises_value_error() -> (
    None
):
    """Test WorkflowRowConverter raises ValueError when record is None."""
    with pytest.raises(ValueError, match="PipelineRecord cannot be None"):
        WorkflowRowConverter.from_pipeline_record(None)  # type: ignore[arg-type]


def test_given_none_row_when_converting_to_pipeline_record_then_raises_value_error() -> (
    None
):
    """Test WorkflowRowConverter raises ValueError when row is None."""
    with pytest.raises(ValueError, match="Row cannot be None"):
        WorkflowRowConverter.to_pipeline_record(None)  # type: ignore[arg-type]


def test_given_localized_status_label_when_converting_row_then_maps_to_migration_status() -> (
    None
):
    """Test localized display labels normalize back to proper MigrationStatus enums."""
    row_waiting = Row(
        data={"workflow_id": "w1", "migration_status": "Aguardando Migração"}
    )
    assert (
        WorkflowRowConverter.to_pipeline_record(row_waiting).migration_status
        == MigrationStatus.WAITTING
    )

    row_migrated = Row(
        data={"workflow_id": "w2", "migration_status": "Migrados (Somente GitHub)"}
    )
    assert (
        WorkflowRowConverter.to_pipeline_record(row_migrated).migration_status
        == MigrationStatus.MIGRATED
    )

    row_both = Row(
        data={"workflow_id": "w3", "migration_status": "Migrando (Ativos em Ambas)"}
    )
    assert (
        WorkflowRowConverter.to_pipeline_record(row_both).migration_status
        == MigrationStatus.ACTIVE_IN_BOTH
    )

    row_skipped = Row(data={"workflow_id": "w4", "migration_status": "Ignorado"})
    assert (
        WorkflowRowConverter.to_pipeline_record(row_skipped).migration_status
        == MigrationStatus.SKIPPED
    )


# --- PipelineInventory Tests ---


def test_given_unregistered_pipeline_when_registering_then_creates_new_row_in_sheet(
    sample_pipeline_record: PipelineRecord,
) -> None:
    """Test registering a new pipeline calls spreadsheet.add."""
    mock_sheet = MagicMock(spec=Spreadsheet)
    mock_sheet.find_by.return_value = []
    mock_sheet.add.return_value = Row(
        data={"workflow_id": sample_pipeline_record.workflow_id},
        values=["deployments/prod-deploy"] + [""] * 9,
        row_index=12,
    )

    inv = PipelineInventory(spreadsheet=mock_sheet)
    result = inv.register_record(sample_pipeline_record)

    mock_sheet.find_by.assert_called_once_with(
        "workflow_id", sample_pipeline_record.workflow_id
    )
    mock_sheet.add.assert_called_once()
    assert result.workflow_id == sample_pipeline_record.workflow_id


def test_given_existing_pipeline_when_registering_then_preserves_migration_status(
    sample_pipeline_record: PipelineRecord,
) -> None:
    """Test re-registering an existing pipeline updates metadata but preserves migration status."""
    mock_sheet = MagicMock(spec=Spreadsheet)
    existing_row = Row(
        data={
            "workflow_id": sample_pipeline_record.workflow_id,
            "migration_status": "Migrados (Somente GitHub)",
        },
        values=[sample_pipeline_record.workflow_id]
        + [""] * 8
        + ["Migrados (Somente GitHub)"],
        row_index=4,
    )
    mock_sheet.find_by.return_value = [existing_row]
    mock_sheet.update_row.return_value = existing_row

    inv = PipelineInventory(spreadsheet=mock_sheet)
    # Incoming record has WAITTING status
    sample_pipeline_record.migration_status = MigrationStatus.WAITTING
    result = inv.register_record(sample_pipeline_record)

    mock_sheet.update_row.assert_called_once()
    # Migration status should be preserved as MIGRATED from the existing row
    assert result.migration_status == MigrationStatus.MIGRATED


def test_given_valid_pipeline_when_updating_progress_then_updates_row_in_sheet() -> (
    None
):
    """Test update_progress updates status and calls update_row."""
    mock_sheet = MagicMock(spec=Spreadsheet)
    existing_row = Row(
        data={"workflow_id": "job-100", "migration_status": "Aguardando Migração"},
        values=["job-100"] + [""] * 8 + ["Aguardando Migração"],
        row_index=7,
    )
    mock_sheet.find_by.return_value = [existing_row]
    updated_row = Row(
        data={
            "workflow_id": "job-100",
            "migration_status": "Migrados (Somente GitHub)",
        },
        values=["job-100"] + [""] * 8 + ["Migrados (Somente GitHub)"],
        row_index=7,
    )
    mock_sheet.update_row.return_value = updated_row

    inv = PipelineInventory(spreadsheet=mock_sheet)
    result = inv.update_progress("job-100", MigrationStatus.MIGRATED)

    assert result.migration_status == MigrationStatus.MIGRATED
    mock_sheet.update_row.assert_called_once()


def test_given_existing_workflow_id_when_getting_pipeline_then_returns_record() -> None:
    """Test get_pipeline finds and converts the matching row."""
    mock_sheet = MagicMock(spec=Spreadsheet)
    row = Row(
        data={"workflow_id": "ci/test", "platform": "jenkins"},
        values=["ci/test", "jenkins"] + [""] * 8,
        row_index=2,
    )
    mock_sheet.find_by.return_value = [row]

    inv = PipelineInventory(spreadsheet=mock_sheet)
    result = inv.get_pipeline("ci/test")

    assert result is not None
    assert result.workflow_id == "ci/test"
    assert result.platform == "jenkins"


def test_given_non_existing_workflow_id_when_getting_pipeline_then_returns_none() -> (
    None
):
    """Test get_pipeline returns None when workflow ID is not found."""
    mock_sheet = MagicMock(spec=Spreadsheet)
    mock_sheet.find_by.return_value = []

    inv = PipelineInventory(spreadsheet=mock_sheet)
    result = inv.get_pipeline("non-existent-job")

    assert result is None


def test_given_pipeline_records_when_syncing_records_then_registers_all_records(
    sample_pipeline_record: PipelineRecord,
) -> None:
    """Test sync_records batch processes all provided records."""
    mock_sheet = MagicMock(spec=Spreadsheet)
    mock_sheet.find_by.return_value = []
    mock_sheet.add.return_value = Row(
        data={"workflow_id": sample_pipeline_record.workflow_id},
        values=[sample_pipeline_record.workflow_id] + [""] * 9,
        row_index=2,
    )

    inv = PipelineInventory(spreadsheet=mock_sheet)
    results = inv.sync_records([sample_pipeline_record])

    assert len(results) == 1
    assert results[0].workflow_id == sample_pipeline_record.workflow_id


def test_given_none_record_when_registering_pipeline_then_raises_value_error() -> None:
    """Test registering None raises ValueError."""
    mock_sheet = MagicMock(spec=Spreadsheet)
    inv = PipelineInventory(spreadsheet=mock_sheet)
    with pytest.raises(ValueError, match="PipelineRecord cannot be None"):
        inv.register_record(None)  # type: ignore[arg-type]


def test_given_empty_workflow_id_when_updating_pipeline_progress_then_raises_value_error() -> (
    None
):
    """Test updating progress with empty workflow_id raises ValueError."""
    mock_sheet = MagicMock(spec=Spreadsheet)
    inv = PipelineInventory(spreadsheet=mock_sheet)
    with pytest.raises(ValueError, match="Workflow ID cannot be empty"):
        inv.update_progress("", MigrationStatus.MIGRATED)


# --- Multi-tab Spreadsheet Tests ---


def test_given_workflows_sheet_name_when_finding_row_then_queries_ten_column_range(
    mock_workflow_spreadsheet: Spreadsheet, mock_sheets_service: MagicMock
) -> None:
    """Test find_by on 'Workflows' tab uses A2:J 10-column range."""
    mock_get = mock_sheets_service.spreadsheets.return_value.values.return_value.get
    mock_get.return_value.execute.return_value = {
        "values": [
            [
                "ci/deploy",
                "jenkins",
                "declarative",
                "org/repo",
                "https://github.com/org/repo.git",
                "Jenkinsfile",
                "main",
                "2026-10-27 10:00:00",
                "SUCCESS",
                "Aguardando Migração",
            ]
        ]
    }

    results = mock_workflow_spreadsheet.find_by("workflow_id", "ci/deploy")

    assert len(results) == 1
    assert results[0].data["workflow_id"] == "ci/deploy"
    mock_get.assert_called_once_with(
        spreadsheetId="workflows-sheet-id",
        range="Workflows!A2:J",
    )


def test_given_ensure_sheet_when_workflows_tab_missing_then_adds_sheet_with_headers(
    mock_workflow_spreadsheet: Spreadsheet, mock_sheets_service: MagicMock
) -> None:
    """Test ensure_sheet provisions the 'Workflows' tab and initializes 10 header columns."""
    mock_get = mock_sheets_service.spreadsheets.return_value.get
    mock_get.return_value.execute.return_value = {
        "sheets": [{"properties": {"title": "Inventario"}}]
    }
    mock_batch = mock_sheets_service.spreadsheets.return_value.batchUpdate
    mock_batch.return_value.execute.return_value = {}
    mock_update = (
        mock_sheets_service.spreadsheets.return_value.values.return_value.update
    )
    mock_update.return_value.execute.return_value = {}

    mock_workflow_spreadsheet.ensure_sheet("Workflows")

    mock_batch.assert_called_once()
    mock_update.assert_called_once()
    # Check that _WORKFLOW_HEADERS was written to A1:J1
    call_kwargs = mock_update.call_args[1]
    assert call_kwargs["range"] == "Workflows!A1:J1"
    assert call_kwargs["body"]["values"] == [_WORKFLOW_HEADERS]
