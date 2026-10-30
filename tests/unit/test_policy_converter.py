"""Unit tests for PolicyConverter conversion adapter."""

from __future__ import annotations

import pytest

from gh_migration_inventory_manager.converter import PolicyConverter
from gh_migration_inventory_manager.inventory import MigrationStatus, PolicyRecord
from gh_migration_inventory_manager.scrap import BranchPolicy, NormalizedRuleType


def test_given_valid_branch_policy_when_converting_then_maps_all_fields() -> None:
    """Verify standard happy-path conversion from BranchPolicy to PolicyRecord."""
    converter = PolicyConverter()
    policy = BranchPolicy(
        policy_scope="repo:payment-service",
        target_ref_pattern="refs/heads/main",
        normalized_rule_type=NormalizedRuleType.REQUIRE_PULL_REQUEST,
        rule_parameters={"min_approvals": 2, "require_codeowners": True},
        bypass_team_slugs=["Release Leads", "Core Devs"],
        origin_raw_policy='{"id": 101, "type": "pull-request-only"}',
    )

    record = converter.to_policy_record(policy)

    assert isinstance(record, PolicyRecord)
    assert record.policy_scope == "repo:payment-service"
    assert record.target_ref_pattern == "refs/heads/main"
    assert record.normalized_rule_type == "RequirePullRequest"
    assert record.rule_parameters == "min_approvals: 2; require_codeowners: true"
    assert record.bypass_team_slugs == "core-devs, release-leads"
    assert record.origin_raw_policy == '{"id": 101, "type": "pull-request-only"}'
    assert record.migration_status == MigrationStatus.WAITTING


def test_given_bypass_team_names_as_comma_string_when_converting_then_canonicalizes_slugs() -> (
    None
):
    """Verify comma-separated bypass team strings are normalized via slugify and sorted."""
    converter = PolicyConverter()
    policy = BranchPolicy(
        policy_scope="project:CORE",
        target_ref_pattern="refs/heads/release/*",
        normalized_rule_type=NormalizedRuleType.BLOCK_FORCE_PUSH,
        rule_parameters="linear_history: true",
        bypass_team_slugs="Platform Admins, DevOps Squad",  # type: ignore[arg-type]
    )

    record = converter.to_policy_record(policy)

    assert record.bypass_team_slugs == "devops-squad, platform-admins"


def test_given_dict_origin_raw_policy_when_converting_then_serializes_to_json() -> None:
    """Verify dictionary origin raw policy is serialised to sorted JSON string."""
    converter = PolicyConverter()
    policy = BranchPolicy(
        policy_scope="repo:auth",
        target_ref_pattern="refs/heads/main",
        normalized_rule_type=NormalizedRuleType.PREVENT_BRANCH_DELETION,
        origin_raw_policy={"active": True, "type": "no-deletes"},  # type: ignore[arg-type]
    )

    record = converter.to_policy_record(policy)

    assert record.origin_raw_policy == '{"active": true, "type": "no-deletes"}'


def test_given_custom_initial_status_when_converting_then_applies_status() -> None:
    """Verify custom migration status is preserved during conversion."""
    converter = PolicyConverter()
    policy = BranchPolicy(
        policy_scope="repo:auth",
        target_ref_pattern="refs/heads/main",
        normalized_rule_type=NormalizedRuleType.BLOCK_FORCE_PUSH,
    )

    record = converter.to_policy_record(
        policy, initial_status=MigrationStatus.ACTIVE_IN_BOTH
    )

    assert record.migration_status == MigrationStatus.ACTIVE_IN_BOTH


def test_given_collection_of_policies_when_converting_then_returns_list_of_records() -> (
    None
):
    """Verify to_policy_records converts an iterable of BranchPolicy instances."""
    converter = PolicyConverter()
    policies = [
        BranchPolicy(
            policy_scope="repo:app",
            target_ref_pattern="refs/heads/main",
            normalized_rule_type=NormalizedRuleType.REQUIRE_PULL_REQUEST,
        ),
        BranchPolicy(
            policy_scope="repo:app",
            target_ref_pattern="refs/heads/main",
            normalized_rule_type=NormalizedRuleType.BLOCK_FORCE_PUSH,
        ),
    ]

    records = converter.to_policy_records(policies)

    assert len(records) == 2
    assert records[0].normalized_rule_type == "RequirePullRequest"
    assert records[1].normalized_rule_type == "BlockForcePush"


def test_given_none_policy_when_converting_then_raises_value_error() -> None:
    """Verify ValueError is raised when policy model is None."""
    converter = PolicyConverter()
    with pytest.raises(ValueError, match="BranchPolicy cannot be None"):
        converter.to_policy_record(None)


def test_given_empty_scope_when_converting_then_raises_value_error() -> None:
    """Verify ValueError is raised when policy scope is empty."""
    converter = PolicyConverter()
    policy = BranchPolicy(
        policy_scope="",
        target_ref_pattern="refs/heads/main",
        normalized_rule_type=NormalizedRuleType.REQUIRE_PULL_REQUEST,
    )
    with pytest.raises(ValueError, match="Policy scope cannot be empty"):
        converter.to_policy_record(policy)


def test_given_empty_target_ref_when_converting_then_raises_value_error() -> None:
    """Verify ValueError is raised when target ref pattern is empty."""
    converter = PolicyConverter()
    policy = BranchPolicy(
        policy_scope="repo:app",
        target_ref_pattern="",
        normalized_rule_type=NormalizedRuleType.REQUIRE_PULL_REQUEST,
    )
    with pytest.raises(ValueError, match="Target ref pattern cannot be empty"):
        converter.to_policy_record(policy)


def test_given_none_collection_when_converting_then_raises_value_error() -> None:
    """Verify ValueError is raised when policy collection is None."""
    converter = PolicyConverter()
    with pytest.raises(ValueError, match="Policies collection cannot be None"):
        converter.to_policy_records(None)  # type: ignore[arg-type]
