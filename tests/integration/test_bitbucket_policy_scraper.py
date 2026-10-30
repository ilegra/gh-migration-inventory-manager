"""Integration tests verifying BitbucketPolicyScraper against containerized Bitbucket service."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from gh_migration_inventory_manager import TokenAuthenticator
from gh_migration_inventory_manager.bitbucket_scrap import (
    BitbucketClient,
    BitbucketPolicyScraper,
)
from gh_migration_inventory_manager.converter import PolicyConverter
from gh_migration_inventory_manager.google import Row
from gh_migration_inventory_manager.inventory import MigrationStatus, PolicyInventory
from gh_migration_inventory_manager.scrap import (
    NormalizedRuleType,
    ScraperAuthenticationError,
)


def test_given_live_bitbucket_server_when_scraping_then_discovers_branch_restrictions_and_pr_settings(
    bitbucket_server: dict[str, Any],
) -> None:
    """Verify scraper discovers seeded branch restrictions and PR settings from Bitbucket."""
    base_url = bitbucket_server["base_url"]
    auth = TokenAuthenticator(bitbucket_server["discovery_token"])
    client = BitbucketClient(base_url=base_url, auth=auth)
    scraper = BitbucketPolicyScraper(
        client=client,
        project_key=bitbucket_server["project_key"],
        repo_slug=bitbucket_server["repo_slug"],
    )

    policies = scraper.scrape()

    assert len(policies) == 4

    # 1. Branch restriction: pull-request-only on main
    pr_restriction = next(
        p
        for p in policies
        if p.target_ref_pattern == "refs/heads/main"
        and p.normalized_rule_type == NormalizedRuleType.REQUIRE_PULL_REQUEST
        and isinstance(p.rule_parameters, dict)
        and p.rule_parameters.get("enforce_pr") is True
    )
    assert pr_restriction.policy_scope == f"repo:{bitbucket_server['repo_slug']}"
    assert pr_restriction.platform == "bitbucket"
    assert "core-leads" in pr_restriction.bypass_team_slugs

    # 2. Branch restriction: fast-forward-only on release/*
    ff_restriction = next(
        p
        for p in policies
        if p.target_ref_pattern == "refs/heads/release/*"
        and p.normalized_rule_type == NormalizedRuleType.BLOCK_FORCE_PUSH
    )
    assert ff_restriction.policy_scope == f"repo:{bitbucket_server['repo_slug']}"
    assert ff_restriction.rule_parameters == {"linear_history": True}

    # 3. Branch restriction: no-deletes on *
    delete_restriction = next(
        p
        for p in policies
        if p.target_ref_pattern == "refs/heads/*"
        and p.normalized_rule_type == NormalizedRuleType.PREVENT_BRANCH_DELETION
    )
    assert delete_restriction.policy_scope == f"repo:{bitbucket_server['repo_slug']}"
    assert delete_restriction.rule_parameters == {"prevent_deletion": True}

    # 4. Pull request settings on default branch (main)
    pr_setting_policy = next(
        p
        for p in policies
        if p.target_ref_pattern == "refs/heads/main"
        and p.normalized_rule_type == NormalizedRuleType.REQUIRE_PULL_REQUEST
        and isinstance(p.rule_parameters, dict)
        and "min_approvals" in p.rule_parameters
    )
    assert isinstance(pr_setting_policy.rule_parameters, dict)
    assert pr_setting_policy.rule_parameters["min_approvals"] == 2
    assert pr_setting_policy.rule_parameters["require_all_tasks_complete"] is True
    assert pr_setting_policy.rule_parameters["require_all_approvers"] is True


def test_given_invalid_credentials_when_scraping_live_bitbucket_then_raises_scraper_authentication_error(
    bitbucket_server: dict[str, Any],
) -> None:
    """Verify connecting with invalid token raises ScraperAuthenticationError."""
    base_url = bitbucket_server["base_url"]
    auth = TokenAuthenticator("invalid_token_bb_401")
    client = BitbucketClient(base_url=base_url, auth=auth)
    scraper = BitbucketPolicyScraper(
        client=client,
        project_key=bitbucket_server["project_key"],
        repo_slug=bitbucket_server["repo_slug"],
    )

    with pytest.raises(
        ScraperAuthenticationError, match="Bitbucket authentication failed"
    ):
        scraper.scrape()


def test_given_live_bitbucket_server_when_syncing_with_policy_inventory_then_persists_records(
    bitbucket_server: dict[str, Any],
) -> None:
    """Verify discovered Bitbucket policies integrate end-to-end with PolicyInventory."""
    base_url = bitbucket_server["base_url"]
    auth = TokenAuthenticator(bitbucket_server["discovery_token"])
    client = BitbucketClient(base_url=base_url, auth=auth)
    scraper = BitbucketPolicyScraper(
        client=client,
        project_key=bitbucket_server["project_key"],
        repo_slug=bitbucket_server["repo_slug"],
    )

    policies = scraper.scrape()
    assert len(policies) == 4

    converter = PolicyConverter()
    records = converter.to_policy_records(
        policies, initial_status=MigrationStatus.WAITTING
    )

    # In-memory mock spreadsheet client simulating live Google Sheets backend
    mock_sheet = MagicMock()
    mock_sheet.find_by.return_value = []
    mock_sheet.add.side_effect = lambda row: Row(
        row_index=15,
        data=row.data,
        values=list(row.data.values()),
    )

    inv = PolicyInventory(spreadsheet=mock_sheet, converter=converter)
    synced = inv.sync_records(records)

    assert len(synced) == 4
    for record in synced:
        assert record.migration_status == MigrationStatus.WAITTING
        assert record.policy_scope == f"repo:{bitbucket_server['repo_slug']}"
    assert any(r.target_ref_pattern == "refs/heads/main" for r in synced)
    assert any(r.target_ref_pattern == "refs/heads/release/*" for r in synced)
