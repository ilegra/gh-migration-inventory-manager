"""Unit tests for InventoryGraph and correlated profile models."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from gh_migration_inventory_manager.inventory import (
    CorrelatedProjectProfile,
    CorrelatedRepositoryProfile,
    Inventory,
    InventoryGraph,
    InventoryRecord,
    MigrationStatus,
    PipelineInventory,
    PipelineRecord,
    PolicyInventory,
    PolicyRecord,
    TeamInventory,
    TeamRecord,
)


@pytest.fixture
def sample_repositories() -> list[InventoryRecord]:
    """Sample inventory repository records."""
    return [
        InventoryRecord(
            name="order-api",
            project="commerce",
            migration_status=MigrationStatus.MIGRATED,
        ),
        InventoryRecord(
            name="payment-svc",
            project="finance",
            migration_status=MigrationStatus.WAITTING,
        ),
    ]


@pytest.fixture
def sample_pipelines() -> list[PipelineRecord]:
    """Sample pipeline records."""
    return [
        PipelineRecord(
            workflow_id="commerce/order-api/build",
            platform="jenkins",
            project="commerce",
            repository="order-api",
            repository_association="commerce/order-api",
        ),
        PipelineRecord(
            workflow_id="finance/payment-svc/build",
            platform="jenkins",
            project="finance",
            repository="payment-svc",
            repository_association="finance/payment-svc",
        ),
        PipelineRecord(
            workflow_id="infra/orphan-job",
            platform="jenkins",
            project="infra",
            repository="unknown-repo",
            repository_association="infra/unknown-repo",
        ),
        PipelineRecord(
            workflow_id="global/cron-cleanup",
            platform="jenkins",
            project="",
            repository="",
            repository_association="",
        ),
    ]


@pytest.fixture
def sample_teams() -> list[TeamRecord]:
    """Sample team topology records."""
    return [
        TeamRecord(
            team_name="Commerce Devs",
            team_slug="commerce-devs",
            project="commerce",
            repository="order-api",
            target_repo_or_project="/commerce/order-api",
            normalized_role="Write",
        ),
        TeamRecord(
            team_name="Commerce Admins",
            team_slug="commerce-admins",
            project="commerce",
            repository="",
            target_repo_or_project="/commerce",
            normalized_role="Admin",
        ),
    ]


@pytest.fixture
def sample_policies() -> list[PolicyRecord]:
    """Sample governance policy records."""
    return [
        PolicyRecord(
            policy_scope="repo:commerce/order-api",
            target_ref_pattern="refs/heads/main",
            normalized_rule_type="RequirePullRequest",
            project="commerce",
            repository="order-api",
        ),
        PolicyRecord(
            policy_scope="project:commerce",
            target_ref_pattern="refs/heads/main",
            normalized_rule_type="BlockForcePush",
            project="commerce",
            repository="",
        ),
        PolicyRecord(
            policy_scope="global",
            target_ref_pattern="refs/heads/main",
            normalized_rule_type="PreventBranchDeletion",
            project="",
            repository="",
        ),
    ]


# ============================================================================
# Scenario 1: Successful Collaboration
# ============================================================================


def test_given_valid_records_when_building_graph_from_records_then_returns_graph_instance(
    sample_repositories: list[InventoryRecord],
    sample_pipelines: list[PipelineRecord],
    sample_teams: list[TeamRecord],
    sample_policies: list[PolicyRecord],
) -> None:
    """Verify InventoryGraph.from_records creates an instance with all record collections populated."""
    graph = InventoryGraph.from_records(
        repositories=sample_repositories,
        pipelines=sample_pipelines,
        teams=sample_teams,
        policies=sample_policies,
    )

    assert isinstance(graph, InventoryGraph)
    assert len(graph.repositories) == 2
    assert len(graph.pipelines) == 4
    assert len(graph.teams) == 2
    assert len(graph.policies) == 3


def test_given_valid_inventories_when_building_graph_from_inventories_then_queries_all_inventories(
    sample_repositories: list[InventoryRecord],
    sample_pipelines: list[PipelineRecord],
    sample_teams: list[TeamRecord],
    sample_policies: list[PolicyRecord],
) -> None:
    """Verify InventoryGraph.from_inventories queries each inventory service and aggregates records."""
    mock_repo_inv = MagicMock(spec=Inventory)
    mock_repo_inv.get_all_repositories.return_value = sample_repositories

    mock_pipe_inv = MagicMock(spec=PipelineInventory)
    mock_pipe_inv.get_all_pipelines.return_value = sample_pipelines

    mock_team_inv = MagicMock(spec=TeamInventory)
    mock_team_inv.get_all_teams.return_value = sample_teams

    mock_policy_inv = MagicMock(spec=PolicyInventory)
    mock_policy_inv.get_all_policies.return_value = sample_policies

    graph = InventoryGraph.from_inventories(
        repository_inventory=mock_repo_inv,
        pipeline_inventory=mock_pipe_inv,
        team_inventory=mock_team_inv,
        policy_inventory=mock_policy_inv,
    )

    assert isinstance(graph, InventoryGraph)
    mock_repo_inv.get_all_repositories.assert_called_once()
    mock_pipe_inv.get_all_pipelines.assert_called_once()
    mock_team_inv.get_all_teams.assert_called_once()
    mock_policy_inv.get_all_policies.assert_called_once()
    assert len(graph.repositories) == 2
    assert len(graph.pipelines) == 4


# ============================================================================
# Scenario 2: Internal Behavior & State
# ============================================================================


def test_given_graph_with_repo_when_querying_repository_profile_then_correlates_repo_pipelines_teams_and_policies(
    sample_repositories: list[InventoryRecord],
    sample_pipelines: list[PipelineRecord],
    sample_teams: list[TeamRecord],
    sample_policies: list[PolicyRecord],
) -> None:
    """Verify get_repository_profile accurately stitches repository, pipelines, teams, and policies."""
    graph = InventoryGraph.from_records(
        repositories=sample_repositories,
        pipelines=sample_pipelines,
        teams=sample_teams,
        policies=sample_policies,
    )

    profile = graph.get_repository_profile("commerce", "order-api")

    assert isinstance(profile, CorrelatedRepositoryProfile)
    assert profile.project_slug == "commerce"
    assert profile.repo_slug == "order-api"
    assert profile.repository is not None
    assert profile.repository.name == "order-api"

    # Pipelines: 1 correlated pipeline
    assert len(profile.pipelines) == 1
    assert profile.pipelines[0].workflow_id == "commerce/order-api/build"

    # Teams: 1 repo team + 1 project-level team
    assert len(profile.teams) == 2
    team_names = {t.team_name for t in profile.teams}
    assert "Commerce Devs" in team_names
    assert "Commerce Admins" in team_names

    # Policies: 1 repo policy + 1 project policy + 1 global policy = 3 effective policies
    assert len(profile.effective_policies) == 3
    rule_types = {p.normalized_rule_type for p in profile.effective_policies}
    assert "RequirePullRequest" in rule_types
    assert "BlockForcePush" in rule_types
    assert "PreventBranchDeletion" in rule_types


def test_given_graph_with_project_when_querying_project_profile_then_aggregates_project_entities(
    sample_repositories: list[InventoryRecord],
    sample_pipelines: list[PipelineRecord],
    sample_teams: list[TeamRecord],
    sample_policies: list[PolicyRecord],
) -> None:
    """Verify get_project_profile groups all project-scoped repositories, policies, pipelines, and teams."""
    graph = InventoryGraph.from_records(
        repositories=sample_repositories,
        pipelines=sample_pipelines,
        teams=sample_teams,
        policies=sample_policies,
    )

    profile = graph.get_project_profile("commerce")

    assert isinstance(profile, CorrelatedProjectProfile)
    assert profile.project_slug == "commerce"
    assert len(profile.repositories) == 1
    assert profile.repositories[0].name == "order-api"
    assert len(profile.pipelines) == 1
    assert len(profile.teams) == 2
    # Project-level policies only
    assert len(profile.project_policies) == 1
    assert profile.project_policies[0].normalized_rule_type == "BlockForcePush"


def test_given_graph_with_unlinked_pipelines_when_finding_unlinked_workflows_then_returns_orphaned_workflows(
    sample_repositories: list[InventoryRecord],
    sample_pipelines: list[PipelineRecord],
) -> None:
    """Verify find_unlinked_workflows identifies workflows whose repository is not indexed."""
    graph = InventoryGraph.from_records(
        repositories=sample_repositories,
        pipelines=sample_pipelines,
        teams=[],
        policies=[],
    )

    unlinked = graph.find_unlinked_workflows()

    assert len(unlinked) == 2
    unlinked_ids = {u.workflow_id for u in unlinked}
    assert "infra/orphan-job" in unlinked_ids
    assert "global/cron-cleanup" in unlinked_ids


def test_given_graph_with_global_policies_when_finding_global_policies_then_returns_global_only(
    sample_policies: list[PolicyRecord],
) -> None:
    """Verify find_global_policies retrieves only policies with global scope."""
    graph = InventoryGraph.from_records(
        repositories=[],
        pipelines=[],
        teams=[],
        policies=sample_policies,
    )

    globals_found = graph.find_global_policies()

    assert len(globals_found) == 1
    assert globals_found[0].normalized_rule_type == "PreventBranchDeletion"
    assert globals_found[0].policy_scope == "global"


def test_given_orphan_policies_without_project_or_repo_when_finding_global_policies_then_returns_orphan_policies() -> (
    None
):
    """Verify find_global_policies catches orphan policies with empty project and repo regardless of scope string."""
    orphan_policy_1 = PolicyRecord(
        policy_scope="global",
        target_ref_pattern="refs/heads/*",
        normalized_rule_type="PreventBranchDeletion",
        project="",
        repository="",
    )
    orphan_policy_2 = PolicyRecord(
        policy_scope="",
        target_ref_pattern="refs/heads/main",
        normalized_rule_type="RequirePullRequest",
        project="",
        repository="",
    )
    bound_policy = PolicyRecord(
        policy_scope="repo:CORE/payments",
        target_ref_pattern="refs/heads/main",
        normalized_rule_type="BlockForcePush",
        project="CORE",
        repository="payments",
    )

    graph = InventoryGraph.from_records(
        repositories=[],
        pipelines=[],
        teams=[],
        policies=[orphan_policy_1, orphan_policy_2, bound_policy],
    )

    orphans = graph.find_global_policies()
    assert len(orphans) == 2
    assert orphan_policy_1 in orphans
    assert orphan_policy_2 in orphans
    assert bound_policy not in orphans


def test_given_graph_with_standalone_pipelines_when_finding_standalone_workflows_then_filters_workflows_with_no_repo(
    sample_pipelines: list[PipelineRecord],
) -> None:
    """Verify find_standalone_workflows isolates workflows having no repository linkage."""
    graph = InventoryGraph.from_records(
        repositories=[],
        pipelines=sample_pipelines,
        teams=[],
        policies=[],
    )

    standalone = graph.find_standalone_workflows()

    assert len(standalone) == 1
    assert standalone[0].workflow_id == "global/cron-cleanup"


# ============================================================================
# Scenario 3: Dependency Failure Handling
# ============================================================================


def test_given_inventory_failure_when_building_from_inventories_then_propagates_exception() -> (
    None
):
    """Verify exceptions from underlying inventories propagate without being swallowed."""
    mock_repo_inv = MagicMock(spec=Inventory)
    mock_repo_inv.get_all_repositories.side_effect = RuntimeError("Sheets API down")

    mock_pipe_inv = MagicMock(spec=PipelineInventory)
    mock_team_inv = MagicMock(spec=TeamInventory)
    mock_policy_inv = MagicMock(spec=PolicyInventory)

    with pytest.raises(RuntimeError, match="Sheets API down"):
        InventoryGraph.from_inventories(
            repository_inventory=mock_repo_inv,
            pipeline_inventory=mock_pipe_inv,
            team_inventory=mock_team_inv,
            policy_inventory=mock_policy_inv,
        )


# ============================================================================
# Scenario 4: Invalid Input Handling
# ============================================================================


def test_given_empty_project_slug_when_getting_repository_profile_then_raises_value_error() -> (
    None
):
    """Verify ValueError is raised when querying a repository profile with empty project slug."""
    graph = InventoryGraph()
    with pytest.raises(ValueError, match="Project slug cannot be empty"):
        graph.get_repository_profile("", "repo")


def test_given_empty_repo_slug_when_getting_repository_profile_then_raises_value_error() -> (
    None
):
    """Verify ValueError is raised when querying a repository profile with empty repository slug."""
    graph = InventoryGraph()
    with pytest.raises(ValueError, match="Repository slug cannot be empty"):
        graph.get_repository_profile("proj", "")


def test_given_empty_project_slug_when_getting_project_profile_then_raises_value_error() -> (
    None
):
    """Verify ValueError is raised when querying a project profile with empty project slug."""
    graph = InventoryGraph()
    with pytest.raises(ValueError, match="Project slug cannot be empty"):
        graph.get_project_profile("")
