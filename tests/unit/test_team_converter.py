"""Unit tests for TeamConverter conversion adapter."""

from __future__ import annotations

import pytest

from gh_migration_inventory_manager.converter import TeamConverter
from gh_migration_inventory_manager.inventory import MigrationStatus, TeamRecord
from gh_migration_inventory_manager.scrap import NormalizedRole, TeamPermission


def test_given_valid_team_permission_when_converting_then_maps_all_seven_fields() -> (
    None
):
    """Verify standard happy-path conversion from TeamPermission to TeamRecord."""
    converter = TeamConverter()
    permission = TeamPermission(
        team_name="Core Devs",
        member_count=5,
        target_repo_or_project="/standard-app/trunk",
        normalized_role=NormalizedRole.WRITE,
        origin_role="rw",
        team_slug="core-devs",
    )

    record = converter.to_team_record(permission)

    assert isinstance(record, TeamRecord)
    assert record.team_name == "Core Devs"
    assert record.team_slug == "core-devs"
    assert record.member_count == 5
    assert record.target_repo_or_project == "/standard-app/trunk"
    assert record.normalized_role == "Write"
    assert record.origin_role == "rw"
    assert record.migration_status == MigrationStatus.WAITTING


def test_given_permission_without_explicit_slug_when_converting_then_derives_canonical_slug() -> (
    None
):
    """Verify team slug is auto-computed when not explicitly specified."""
    converter = TeamConverter()
    permission = TeamPermission(
        team_name="Payments & Billing Squad",
        member_count=8,
        target_repo_or_project="/billing",
        normalized_role=NormalizedRole.ADMIN,
        origin_role="rw",
        team_slug=None,
    )

    record = converter.to_team_record(permission)

    assert record.team_slug == "payments-billing-squad"
    assert record.normalized_role == "Admin"


def test_given_custom_initial_status_when_converting_then_applies_status_to_record() -> (
    None
):
    """Verify initial_status parameter overrides default WAITTING status."""
    converter = TeamConverter()
    permission = TeamPermission(
        team_name="leads",
        member_count=2,
        target_repo_or_project="/tags",
        normalized_role=NormalizedRole.MAINTAIN,
        origin_role="rw",
    )

    record = converter.to_team_record(
        permission, initial_status=MigrationStatus.MIGRATED
    )

    assert record.migration_status == MigrationStatus.MIGRATED


def test_given_permission_collection_when_converting_batch_then_returns_all_records() -> (
    None
):
    """Verify batch conversion via to_team_records."""
    converter = TeamConverter()
    permissions = [
        TeamPermission("devs", 3, "/trunk", NormalizedRole.WRITE, "rw"),
        TeamPermission("ops", 2, "/infra", NormalizedRole.ADMIN, "rw"),
    ]

    records = converter.to_team_records(permissions)

    assert len(records) == 2
    assert records[0].team_name == "devs"
    assert records[1].team_name == "ops"


def test_given_none_permission_when_converting_then_raises_value_error() -> None:
    """Verify None permission parameter raises ValueError."""
    converter = TeamConverter()
    with pytest.raises(ValueError, match="TeamPermission cannot be None"):
        converter.to_team_record(None)


def test_given_empty_team_name_when_converting_then_raises_value_error() -> None:
    """Verify permission with empty team name raises ValueError."""
    converter = TeamConverter()
    permission = TeamPermission(
        team_name="",
        member_count=1,
        target_repo_or_project="/trunk",
        normalized_role=NormalizedRole.READ,
        origin_role="r",
    )

    with pytest.raises(ValueError, match="Team name cannot be empty"):
        converter.to_team_record(permission)


def test_given_empty_target_scope_when_converting_then_raises_value_error() -> None:
    """Verify permission with empty target scope raises ValueError."""
    converter = TeamConverter()
    permission = TeamPermission(
        team_name="devs",
        member_count=1,
        target_repo_or_project="",
        normalized_role=NormalizedRole.READ,
        origin_role="r",
    )

    with pytest.raises(
        ValueError, match="Target repository or project cannot be empty"
    ):
        converter.to_team_record(permission)


def test_given_invalid_initial_status_when_converting_then_raises_value_error() -> None:
    """Verify invalid initial_status raises ValueError."""
    converter = TeamConverter()
    permission = TeamPermission(
        team_name="devs",
        member_count=1,
        target_repo_or_project="/trunk",
        normalized_role=NormalizedRole.READ,
        origin_role="r",
    )

    with pytest.raises(ValueError, match="Invalid migration status"):
        converter.to_team_record(permission, initial_status="invalid_status_xyz")


def test_given_none_collection_when_converting_records_then_raises_value_error() -> (
    None
):
    """Verify None collection parameter raises ValueError."""
    converter = TeamConverter()
    with pytest.raises(ValueError, match="Permissions collection cannot be None"):
        converter.to_team_records(None)  # type: ignore[arg-type]
