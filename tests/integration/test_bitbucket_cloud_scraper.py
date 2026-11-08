"""Integration tests verifying Bitbucket Cloud platform scrapers against a live local REST 2.0 simulator."""

from __future__ import annotations

from typing import Any

from gh_migration_inventory_manager.bitbucket_cloud_scrap import (
    BitbucketCloudClient,
    BitbucketCloudPolicyScraper,
    BitbucketCloudRepositoryScraper,
    BitbucketCloudTeamScraper,
)
from gh_migration_inventory_manager.scrap import (
    NormalizedRole,
    NormalizedRuleType,
    Stack,
    TokenAuthenticator,
)


def test_given_workspace_arg_when_scraping_then_never_calls_workspaces_endpoint(
    bitbucket_cloud_server: dict[str, Any],
) -> None:
    """Verify passing --workspace bypasses GET /2.0/workspaces HTTP call."""
    base_url = bitbucket_cloud_server["base_url"]
    workspace = bitbucket_cloud_server["workspace"]
    token = bitbucket_cloud_server["token"]
    counts = bitbucket_cloud_server["request_counts"]

    initial_ws_count = counts["workspaces"]

    auth = TokenAuthenticator(token=token)
    client = BitbucketCloudClient(base_url=base_url, auth=auth)
    scraper = BitbucketCloudRepositoryScraper(workspace=workspace, client=client)

    repos = scraper.scrape()

    assert len(repos) >= 2
    assert counts["workspaces"] == initial_ws_count  # Zero calls to /workspaces!


def test_given_live_cloud_server_when_scraping_repos_then_discovers_all_seeded_records_with_project_keys(
    bitbucket_cloud_server: dict[str, Any],
) -> None:
    """Verify scraper accurately extracts 3-tier hierarchy: workspace as org, real project key, repo slug."""
    base_url = bitbucket_cloud_server["base_url"]
    workspace = bitbucket_cloud_server["workspace"]
    token = bitbucket_cloud_server["token"]

    auth = TokenAuthenticator(token=token)
    client = BitbucketCloudClient(base_url=base_url, auth=auth)
    scraper = BitbucketCloudRepositoryScraper(workspace=workspace, client=client)

    repos = scraper.scrape()

    assert len(repos) >= 2
    core_api = next(
        (r for r in repos if r.name == bitbucket_cloud_server["repo_slug"]), None
    )
    assert core_api is not None
    assert core_api.organization == workspace
    assert core_api.project == bitbucket_cloud_server["project_key"]
    assert core_api.default_branch == "main"
    assert Stack.JAVA in core_api.stacks
    assert core_api.last_commit is not None


def test_given_live_cloud_server_when_scraping_policies_then_maps_branch_restrictions(
    bitbucket_cloud_server: dict[str, Any],
) -> None:
    """Verify live branch restrictions query maps rules to canonical BranchPolicy objects."""
    base_url = bitbucket_cloud_server["base_url"]
    workspace = bitbucket_cloud_server["workspace"]
    token = bitbucket_cloud_server["token"]

    auth = TokenAuthenticator(token=token)
    client = BitbucketCloudClient(base_url=base_url, auth=auth)
    scraper = BitbucketCloudPolicyScraper(
        workspace=workspace,
        repo_slug=bitbucket_cloud_server["repo_slug"],
        client=client,
    )

    policies = scraper.scrape()

    assert len(policies) == 3
    rule_types = {p.normalized_rule_type for p in policies}
    assert NormalizedRuleType.REQUIRE_PULL_REQUEST in rule_types
    assert NormalizedRuleType.REQUIRE_STATUS_CHECKS in rule_types
    assert NormalizedRuleType.PREVENT_BRANCH_DELETION in rule_types


def test_given_live_cloud_server_when_scraping_teams_then_discovers_workspace_and_repo_permissions(
    bitbucket_cloud_server: dict[str, Any],
) -> None:
    """Verify live group permissions discovery captures workspace and repository group scopes."""
    base_url = bitbucket_cloud_server["base_url"]
    workspace = bitbucket_cloud_server["workspace"]
    token = bitbucket_cloud_server["token"]

    auth = TokenAuthenticator(token=token)
    client = BitbucketCloudClient(base_url=base_url, auth=auth)
    scraper = BitbucketCloudTeamScraper(
        workspace=workspace, client=client, include_repo_level=True
    )

    teams = scraper.scrape()

    assert len(teams) >= 3
    roles = {t.team_name: t.normalized_role for t in teams}
    assert roles.get("admins") == NormalizedRole.ADMIN
    assert roles.get("developers") == NormalizedRole.READ
    assert roles.get("api-leads") == NormalizedRole.WRITE
