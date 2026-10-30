"""Unit tests for branch governance policy domain models, enums, and normalization helpers."""

from __future__ import annotations

import pytest

from gh_migration_inventory_manager.scrap import (
    NormalizedRuleType,
    format_rule_parameters,
    normalize_rule_type,
)


@pytest.mark.parametrize(
    ("input_rule", "expected_enum"),
    [
        (
            NormalizedRuleType.REQUIRE_PULL_REQUEST,
            NormalizedRuleType.REQUIRE_PULL_REQUEST,
        ),
        ("RequirePullRequest", NormalizedRuleType.REQUIRE_PULL_REQUEST),
        ("require_pull_request", NormalizedRuleType.REQUIRE_PULL_REQUEST),
        ("require-pull-request", NormalizedRuleType.REQUIRE_PULL_REQUEST),
        ("pull_request", NormalizedRuleType.REQUIRE_PULL_REQUEST),
        ("pull_request_only", NormalizedRuleType.REQUIRE_PULL_REQUEST),
        ("pull-request-only", NormalizedRuleType.REQUIRE_PULL_REQUEST),
        (NormalizedRuleType.BLOCK_FORCE_PUSH, NormalizedRuleType.BLOCK_FORCE_PUSH),
        ("BlockForcePush", NormalizedRuleType.BLOCK_FORCE_PUSH),
        ("block_force_push", NormalizedRuleType.BLOCK_FORCE_PUSH),
        ("block-force-push", NormalizedRuleType.BLOCK_FORCE_PUSH),
        ("fast_forward_only", NormalizedRuleType.BLOCK_FORCE_PUSH),
        ("fast-forward-only", NormalizedRuleType.BLOCK_FORCE_PUSH),
        ("read_only", NormalizedRuleType.BLOCK_FORCE_PUSH),
        ("read-only", NormalizedRuleType.BLOCK_FORCE_PUSH),
        (
            NormalizedRuleType.PREVENT_BRANCH_DELETION,
            NormalizedRuleType.PREVENT_BRANCH_DELETION,
        ),
        ("PreventBranchDeletion", NormalizedRuleType.PREVENT_BRANCH_DELETION),
        ("prevent_branch_deletion", NormalizedRuleType.PREVENT_BRANCH_DELETION),
        ("prevent-branch-deletion", NormalizedRuleType.PREVENT_BRANCH_DELETION),
        ("no_deletes", NormalizedRuleType.PREVENT_BRANCH_DELETION),
        ("no-deletes", NormalizedRuleType.PREVENT_BRANCH_DELETION),
        (
            NormalizedRuleType.REQUIRE_STATUS_CHECKS,
            NormalizedRuleType.REQUIRE_STATUS_CHECKS,
        ),
        ("RequireStatusChecks", NormalizedRuleType.REQUIRE_STATUS_CHECKS),
        ("require_status_checks", NormalizedRuleType.REQUIRE_STATUS_CHECKS),
        ("require-status-checks", NormalizedRuleType.REQUIRE_STATUS_CHECKS),
        ("status_checks", NormalizedRuleType.REQUIRE_STATUS_CHECKS),
        ("build_validation", NormalizedRuleType.REQUIRE_STATUS_CHECKS),
        (
            NormalizedRuleType.REQUIRE_LINEAR_HISTORY,
            NormalizedRuleType.REQUIRE_LINEAR_HISTORY,
        ),
        ("RequireLinearHistory", NormalizedRuleType.REQUIRE_LINEAR_HISTORY),
        ("require_linear_history", NormalizedRuleType.REQUIRE_LINEAR_HISTORY),
        ("linear_history", NormalizedRuleType.REQUIRE_LINEAR_HISTORY),
        (
            NormalizedRuleType.REQUIRE_SIGNED_COMMITS,
            NormalizedRuleType.REQUIRE_SIGNED_COMMITS,
        ),
        ("RequireSignedCommits", NormalizedRuleType.REQUIRE_SIGNED_COMMITS),
        ("require_signed_commits", NormalizedRuleType.REQUIRE_SIGNED_COMMITS),
        ("signed_commits", NormalizedRuleType.REQUIRE_SIGNED_COMMITS),
    ],
)
def test_given_valid_rule_type_when_normalizing_then_returns_canonical_enum(
    input_rule: str | NormalizedRuleType,
    expected_enum: NormalizedRuleType,
) -> None:
    """Verify normalize_rule_type resolves valid rule strings and enums."""
    assert normalize_rule_type(input_rule) == expected_enum


@pytest.mark.parametrize(
    "invalid_input",
    ["", "   ", None, "unknown_rule_type", "invalid", 123],
)
def test_given_invalid_or_empty_rule_type_when_normalizing_then_raises_value_error(
    invalid_input: object,
) -> None:
    """Verify normalize_rule_type rejects invalid, empty, or unmapped inputs."""
    with pytest.raises(
        ValueError, match="Rule type cannot be empty|Invalid normalized rule type"
    ):
        normalize_rule_type(invalid_input)


def test_given_parameter_dictionary_when_formatting_then_returns_sorted_semicolon_string() -> (
    None
):
    """Verify format_rule_parameters serializes dictionary to sorted semicolon string."""
    params = {
        "require_codeowners": True,
        "min_approvals": 2,
        "dismiss_stale": False,
        "strict": True,
    }
    expected = (
        "dismiss_stale: false; min_approvals: 2; require_codeowners: true; strict: true"
    )
    assert format_rule_parameters(params) == expected


def test_given_string_parameter_when_formatting_then_returns_stripped_string() -> None:
    """Verify format_rule_parameters returns stripped string when input is already a string."""
    raw = "  min_approvals: 2; strict: true  "
    assert format_rule_parameters(raw) == "min_approvals: 2; strict: true"


def test_given_none_or_empty_parameters_when_formatting_then_returns_empty_string() -> (
    None
):
    """Verify format_rule_parameters handles None and empty dict correctly."""
    assert format_rule_parameters(None) == ""
    assert format_rule_parameters({}) == ""
