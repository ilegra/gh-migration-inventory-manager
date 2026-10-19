"""Unit tests verifying repository model to inventory record conversion."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from gh_migration_inventory_manager.converter import RepositoryConverter
from gh_migration_inventory_manager.inventory import InventoryRecord, MigrationStatus
from gh_migration_inventory_manager.scrap import Repository, Stack


@pytest.fixture
def converter() -> RepositoryConverter:
    """Fixture providing an instance of RepositoryConverter."""
    return RepositoryConverter()


def test_given_repository_with_stacks_when_converting_then_returns_inventory_record_with_formatted_stack(
    converter: RepositoryConverter,
) -> None:
    """Verify single repository with stacks converts to InventoryRecord with formatted stacks."""
    repo = Repository(
        name="billing-service",
        project="fintech",
        stacks=[Stack.PYTHON, Stack.TYPESCRIPT],
        last_commit=datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc),
    )

    record = converter.to_inventory_record(repo)

    assert isinstance(record, InventoryRecord)
    assert record.name == "billing-service"
    assert record.project == "fintech"
    assert record.stack == "Python, TypeScript"
    assert record.last_commit == datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc)
    assert record.migration_status == MigrationStatus.WAITTING


def test_given_repository_with_custom_initial_status_when_converting_then_sets_status_and_timestamps(
    converter: RepositoryConverter,
) -> None:
    """Verify converting repository with MIGRATED status sets status and migration timestamps."""
    repo = Repository(name="migrated-app", project="core")

    record = converter.to_inventory_record(
        repo, initial_status=MigrationStatus.MIGRATED
    )

    assert record.name == "migrated-app"
    assert record.project == "core"
    assert record.migration_status == MigrationStatus.MIGRATED
    assert record.gh_migratation is not None
    assert record.origin_deactivation is not None


def test_given_repository_with_active_in_both_status_when_converting_then_sets_gh_migration_only(
    converter: RepositoryConverter,
) -> None:
    """Verify converting repository with ACTIVE_IN_BOTH sets gh_migratation timestamp only."""
    repo = Repository(name="active-app", project="core")

    record = converter.to_inventory_record(
        repo, initial_status=MigrationStatus.ACTIVE_IN_BOTH
    )

    assert record.migration_status == MigrationStatus.ACTIVE_IN_BOTH
    assert record.gh_migratation is not None
    assert record.origin_deactivation is None


def test_given_repository_collection_when_converting_batch_then_returns_all_inventory_records(
    converter: RepositoryConverter,
) -> None:
    """Verify batch conversion of Repository models into InventoryRecord list."""
    repos = [
        Repository(name="repo-1", project="p1", stacks=[Stack.JAVA]),
        Repository(name="repo-2", project="p2", stacks=[Stack.BASH]),
    ]

    records = converter.to_inventory_records(repos)

    assert len(records) == 2
    assert records[0].name == "repo-1"
    assert records[0].stack == "Java"
    assert records[1].name == "repo-2"
    assert records[1].stack == "Bash"


def test_given_duck_typed_repository_when_converting_then_extracts_attributes(
    converter: RepositoryConverter,
) -> None:
    """Verify duck-typed repository objects with string stack or stack objects convert properly."""

    class DuckRepo:
        name = "duck-app"
        project = "duck-proj"
        stack = "C#"
        last_commit = None

    record = converter.to_inventory_record(DuckRepo())

    assert record.name == "duck-app"
    assert record.project == "duck-proj"
    assert record.stack == "C#"
    assert record.last_commit is None


def test_given_none_repository_when_converting_then_raises_value_error(
    converter: RepositoryConverter,
) -> None:
    """Verify ValueError is raised when repository is None."""
    with pytest.raises(ValueError, match="Repository cannot be None"):
        converter.to_inventory_record(None)


def test_given_empty_repository_name_when_converting_then_raises_value_error(
    converter: RepositoryConverter,
) -> None:
    """Verify ValueError is raised when repository name is empty or whitespace."""
    repo = Repository(name="   ")
    with pytest.raises(ValueError, match="Repository name cannot be empty"):
        converter.to_inventory_record(repo)


def test_given_none_repositories_collection_when_converting_then_raises_value_error(
    converter: RepositoryConverter,
) -> None:
    """Verify ValueError is raised when repositories collection is None."""
    with pytest.raises(ValueError, match="Repositories collection cannot be None"):
        converter.to_inventory_records(None)  # type: ignore[arg-type]


def test_given_invalid_status_when_converting_then_raises_value_error(
    converter: RepositoryConverter,
) -> None:
    """Verify ValueError is raised when invalid initial_status is supplied."""
    repo = Repository(name="valid-name")
    with pytest.raises(ValueError, match="Invalid migration status"):
        converter.to_inventory_record(repo, initial_status="invalid_status")


def test_given_multiple_converter_instances_when_converting_then_each_instance_works_independently() -> (
    None
):
    """Verify multiple RepositoryConverter instances can be instantiated and operate independently."""
    converter1 = RepositoryConverter()
    converter2 = RepositoryConverter()

    repo1 = Repository(name="repo-a", project="p-a")
    repo2 = Repository(name="repo-b", project="p-b")

    record1 = converter1.to_inventory_record(repo1)
    record2 = converter2.to_inventory_record(repo2)

    assert record1.name == "repo-a"
    assert record2.name == "repo-b"
