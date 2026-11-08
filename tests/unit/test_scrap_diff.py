"""Unit tests for the isolated Diff Engine (scrap_diff.py)."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from gh_migration_inventory_manager.google import Row
from gh_migration_inventory_manager.inventory import (
    InventoryRecord,
    MigrationStatus,
    PipelineRecord,
    PolicyRecord,
    TeamRecord,
)
from gh_migration_inventory_manager.scrap_diff import (
    PipelineDiffer,
    PolicyDiffer,
    RecordDiff,
    RecordUpdate,
    RepositoryDiffer,
    TeamDiffer,
)

# ============================================================================
# RecordDiff & RecordUpdate Data Container Tests
# ============================================================================


def test_given_empty_record_diff_when_querying_properties_then_returns_zero_and_false() -> (
    None
):
    diff: RecordDiff[InventoryRecord] = RecordDiff()
    assert diff.total_processed == 0
    assert not diff.has_changes
    assert diff.to_add == []
    assert diff.to_update == []
    assert diff.unchanged == []


def test_given_diff_with_additions_when_querying_properties_then_has_changes_is_true() -> (
    None
):
    rec = InventoryRecord(name="new-app")
    diff: RecordDiff[InventoryRecord] = RecordDiff(to_add=[rec])
    assert diff.total_processed == 1
    assert diff.has_changes


def test_given_diff_with_updates_when_querying_properties_then_has_changes_is_true() -> (
    None
):
    rec = InventoryRecord(name="existing-app")
    update = RecordUpdate(record=rec, row_index=4)
    diff: RecordDiff[InventoryRecord] = RecordDiff(to_update=[update])
    assert diff.total_processed == 1
    assert diff.has_changes
    assert update.row_index == 4


# ============================================================================
# RepositoryDiffer Tests
# ============================================================================


def test_given_mix_of_new_and_modified_repositories_when_computing_diff_then_partitions_correctly() -> (
    None
):
    differ = RepositoryDiffer()

    existing_rows = [
        Row(
            row_index=2,
            data={
                "name": "repo-a",
                "project": "proj-1",
                "stack": "Java",
                "last_commit": datetime(2026, 1, 1, tzinfo=timezone.utc),
                "migration_status": MigrationStatus.MIGRATED,
                "gh_migratation": datetime(2026, 1, 5, tzinfo=timezone.utc),
                "origin_deactivation": datetime(2026, 1, 10, tzinfo=timezone.utc),
            },
        ),
        Row(
            row_index=3,
            data={
                "name": "repo-b",
                "project": "proj-1",
                "stack": "Python",
                "last_commit": datetime(2026, 2, 1, tzinfo=timezone.utc),
                "migration_status": MigrationStatus.ACTIVE_IN_BOTH,
            },
        ),
    ]

    incoming = [
        # repo-a has updated stack and last_commit -> should be in to_update
        InventoryRecord(
            name="repo-a",
            project="proj-1",
            stack="Java, Kotlin",
            last_commit=datetime(2026, 3, 1, tzinfo=timezone.utc),
        ),
        # repo-b is unchanged -> should be in unchanged
        InventoryRecord(
            name="repo-b",
            project="proj-1",
            stack="Python",
            last_commit=datetime(2026, 2, 1, tzinfo=timezone.utc),
        ),
        # repo-c is new -> should be in to_add
        InventoryRecord(
            name="repo-c",
            project="proj-2",
            stack="TypeScript",
        ),
    ]

    diff = differ.compute_diff(existing_rows, incoming)

    assert len(diff.to_add) == 1
    assert diff.to_add[0].name == "repo-c"
    assert diff.to_add[0].migration_status == MigrationStatus.WAITTING

    assert len(diff.to_update) == 1
    assert diff.to_update[0].record.name == "repo-a"
    assert diff.to_update[0].record.stack == "Java, Kotlin"
    assert diff.to_update[0].row_index == 2

    assert len(diff.unchanged) == 1
    assert diff.unchanged[0].name == "repo-b"


def test_given_existing_repository_with_migrated_status_when_computing_diff_then_strictly_preserves_status_and_migration_dates() -> (
    None
):
    differ = RepositoryDiffer()

    original_mig_date = datetime(2026, 5, 10, tzinfo=timezone.utc)
    original_deact_date = datetime(2026, 5, 20, tzinfo=timezone.utc)

    existing_rows = [
        Row(
            row_index=7,
            data={
                "name": "critical-service",
                "project": "core",
                "stack": "Go",
                "migration_status": MigrationStatus.MIGRATED,
                "gh_migratation": original_mig_date,
                "origin_deactivation": original_deact_date,
            },
        )
    ]

    incoming = [
        InventoryRecord(
            name="critical-service",
            project="core",
            stack="Go, Rust",  # metadata changed
            migration_status=MigrationStatus.WAITTING,  # should NOT overwrite MIGRATED!
        )
    ]

    diff = differ.compute_diff(existing_rows, incoming)

    assert len(diff.to_update) == 1
    updated = diff.to_update[0].record
    assert updated.stack == "Go, Rust"
    assert updated.migration_status == MigrationStatus.MIGRATED
    assert updated.gh_migratation == original_mig_date
    assert updated.origin_deactivation == original_deact_date


def test_given_case_variant_names_when_computing_diff_then_matches_case_insensitively() -> (
    None
):
    differ = RepositoryDiffer()

    existing_rows = [
        Row(
            row_index=5,
            data={
                "name": "Payment-Service",
                "project": "FINTECH",
                "stack": "Java",
            },
        )
    ]

    incoming = [
        InventoryRecord(
            name="payment-service",
            project="fintech",
            stack="Java",
        )
    ]

    diff = differ.compute_diff(existing_rows, incoming)

    assert len(diff.unchanged) == 1
    assert diff.unchanged[0].name == "Payment-Service"
    assert not diff.has_changes


def test_given_corrupt_or_blank_existing_rows_when_computing_diff_then_ignores_invalid_rows() -> (
    None
):
    differ = RepositoryDiffer()

    existing_rows = [
        None,
        Row(row_index=None, data={}),
        Row(row_index=2, data={"name": ""}),
        Row(row_index=3, data={"name": "valid-app", "project": "p1"}),
    ]

    incoming = [InventoryRecord(name="valid-app", project="p1")]
    diff = differ.compute_diff(existing_rows, incoming)

    assert len(diff.unchanged) == 1
    assert diff.unchanged[0].name == "valid-app"


def test_given_none_records_collection_when_computing_repository_diff_then_raises_value_error() -> (
    None
):
    differ = RepositoryDiffer()
    with pytest.raises(ValueError, match="Records collection cannot be None"):
        differ.compute_diff([], None)  # type: ignore[arg-type]


def test_given_none_existing_rows_when_computing_repository_diff_then_raises_value_error() -> (
    None
):
    differ = RepositoryDiffer()
    with pytest.raises(ValueError, match="Existing rows collection cannot be None"):
        differ.compute_diff(None, [])  # type: ignore[arg-type]


def test_given_record_with_empty_name_when_computing_repository_diff_then_raises_value_error() -> (
    None
):
    differ = RepositoryDiffer()
    with pytest.raises(ValueError, match="Repository name cannot be empty"):
        differ.compute_diff([], [InventoryRecord(name="   ")])


# ============================================================================
# PipelineDiffer Tests
# ============================================================================


def test_given_pipeline_records_when_computing_diff_then_identifies_additions_updates_and_unchanged() -> (
    None
):
    differ = PipelineDiffer()

    existing_rows = [
        Row(
            row_index=2,
            data={
                "workflow_id": "job-1",
                "pipeline_type": "declarative",
                "branch": "master",
                "migration_status": MigrationStatus.MIGRATED,
            },
        ),
        Row(
            row_index=3,
            data={
                "workflow_id": "job-2",
                "pipeline_type": "scripted",
                "branch": "main",
                "migration_status": MigrationStatus.WAITTING,
            },
        ),
    ]

    incoming = [
        # job-1 has updated branch -> to_update, preserving MIGRATED
        PipelineRecord(
            workflow_id="job-1",
            pipeline_type="declarative",
            branch="main",
            migration_status=MigrationStatus.WAITTING,
        ),
        # job-2 is identical -> unchanged
        PipelineRecord(
            workflow_id="job-2",
            pipeline_type="scripted",
            branch="main",
            migration_status=MigrationStatus.WAITTING,
        ),
        # job-3 is new -> to_add
        PipelineRecord(
            workflow_id="job-3",
            pipeline_type="multibranch",
        ),
    ]

    diff = differ.compute_diff(existing_rows, incoming)

    assert len(diff.to_add) == 1
    assert diff.to_add[0].workflow_id == "job-3"

    assert len(diff.to_update) == 1
    assert diff.to_update[0].record.workflow_id == "job-1"
    assert diff.to_update[0].record.branch == "main"
    assert diff.to_update[0].record.migration_status == MigrationStatus.MIGRATED
    assert diff.to_update[0].row_index == 2

    assert len(diff.unchanged) == 1
    assert diff.unchanged[0].workflow_id == "job-2"


def test_given_none_records_collection_when_computing_pipeline_diff_then_raises_value_error() -> (
    None
):
    differ = PipelineDiffer()
    with pytest.raises(ValueError, match="Records collection cannot be None"):
        differ.compute_diff([], None)  # type: ignore[arg-type]


def test_given_none_existing_rows_when_computing_pipeline_diff_then_raises_value_error() -> (
    None
):
    differ = PipelineDiffer()
    with pytest.raises(ValueError, match="Existing rows collection cannot be None"):
        differ.compute_diff(None, [])  # type: ignore[arg-type]


def test_given_pipeline_with_empty_id_when_computing_diff_then_raises_value_error() -> (
    None
):
    differ = PipelineDiffer()
    with pytest.raises(ValueError, match="Pipeline workflow_id cannot be empty"):
        differ.compute_diff([], [PipelineRecord(workflow_id="  ")])


# ============================================================================
# TeamDiffer Tests
# ============================================================================


def test_given_team_records_when_computing_diff_then_identifies_role_changes_and_preserves_status() -> (
    None
):
    differ = TeamDiffer()

    existing_rows = [
        Row(
            row_index=5,
            data={
                "team_name": "Devs",
                "team_slug": "devs",
                "member_count": 5,
                "target_repo_or_project": "proj-1",
                "normalized_role": "Read",
                "origin_role": "ro",
                "migration_status": MigrationStatus.MIGRATED,
            },
        )
    ]

    incoming = [
        # Role upgraded from Read to Write -> to_update
        TeamRecord(
            team_name="Devs",
            team_slug="devs",
            member_count=5,
            target_repo_or_project="proj-1",
            normalized_role="Write",
            origin_role="rw",
        ),
        # New team -> to_add
        TeamRecord(
            team_name="Ops",
            team_slug="ops",
            member_count=3,
            target_repo_or_project="proj-1",
            normalized_role="Admin",
            origin_role="admin",
        ),
    ]

    diff = differ.compute_diff(existing_rows, incoming)

    assert len(diff.to_add) == 1
    assert diff.to_add[0].team_slug == "ops"

    assert len(diff.to_update) == 1
    assert diff.to_update[0].record.normalized_role == "Write"
    assert diff.to_update[0].record.migration_status == MigrationStatus.MIGRATED
    assert diff.to_update[0].row_index == 5


def test_given_team_with_empty_slug_when_computing_diff_then_raises_value_error() -> (
    None
):
    differ = TeamDiffer()
    with pytest.raises(ValueError, match="Team slug cannot be empty"):
        differ.compute_diff(
            [],
            [TeamRecord(team_name="Devs", team_slug=" ", target_repo_or_project="p1")],
        )


def test_given_team_with_empty_target_when_computing_diff_then_raises_value_error() -> (
    None
):
    differ = TeamDiffer()
    with pytest.raises(
        ValueError, match="Target repository or project cannot be empty"
    ):
        differ.compute_diff(
            [],
            [
                TeamRecord(
                    team_name="Devs", team_slug="devs", target_repo_or_project=" "
                )
            ],
        )


# ============================================================================
# PolicyDiffer Tests
# ============================================================================


def test_given_policy_records_when_computing_diff_then_identifies_rule_parameter_changes() -> (
    None
):
    differ = PolicyDiffer()

    existing_rows = [
        Row(
            row_index=12,
            data={
                "policy_scope": "repo:proj/app",
                "target_ref_pattern": "refs/heads/main",
                "normalized_rule_type": "RequirePullRequest",
                "rule_parameters": "min_approvals: 1",
                "migration_status": MigrationStatus.MIGRATED,
            },
        )
    ]

    incoming = [
        # min_approvals increased to 2 -> to_update
        PolicyRecord(
            policy_scope="repo:proj/app",
            target_ref_pattern="refs/heads/main",
            normalized_rule_type="RequirePullRequest",
            rule_parameters="min_approvals: 2",
        ),
        # New policy -> to_add
        PolicyRecord(
            policy_scope="repo:proj/app",
            target_ref_pattern="refs/heads/main",
            normalized_rule_type="BlockForcePush",
        ),
    ]

    diff = differ.compute_diff(existing_rows, incoming)

    assert len(diff.to_add) == 1
    assert diff.to_add[0].normalized_rule_type == "BlockForcePush"

    assert len(diff.to_update) == 1
    assert diff.to_update[0].record.rule_parameters == "min_approvals: 2"
    assert diff.to_update[0].record.migration_status == MigrationStatus.MIGRATED
    assert diff.to_update[0].row_index == 12


def test_given_policy_with_empty_fields_when_computing_diff_then_raises_value_error() -> (
    None
):
    differ = PolicyDiffer()
    with pytest.raises(ValueError, match="Policy scope cannot be empty"):
        differ.compute_diff(
            [],
            [
                PolicyRecord(
                    policy_scope=" ",
                    target_ref_pattern="main",
                    normalized_rule_type="RequirePullRequest",
                )
            ],
        )

    with pytest.raises(ValueError, match="Target ref pattern cannot be empty"):
        differ.compute_diff(
            [],
            [
                PolicyRecord(
                    policy_scope="repo:a",
                    target_ref_pattern=" ",
                    normalized_rule_type="RequirePullRequest",
                )
            ],
        )

    with pytest.raises(ValueError, match="Normalized rule type cannot be empty"):
        differ.compute_diff(
            [],
            [
                PolicyRecord(
                    policy_scope="repo:a",
                    target_ref_pattern="main",
                    normalized_rule_type=" ",
                )
            ],
        )
