"""Unit tests for BitbucketClient and BitbucketPolicyScraper."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest
import urllib3

from gh_migration_inventory_manager.bitbucket_scrap import (
    BitbucketClient,
    BitbucketPolicyScraper,
    BitbucketTransport,
)
from gh_migration_inventory_manager.scrap import (
    BasicAuthenticator,
    NormalizedRuleType,
    ScraperAuthenticationError,
    ScraperConnectionError,
    ScraperError,
    TokenAuthenticator,
)


def _make_response(status: int, data: object) -> urllib3.BaseHTTPResponse:
    raw_data = (
        json.dumps(data).encode("utf-8")
        if isinstance(data, dict | list)
        else (data or b"")
    )
    mock_resp = MagicMock(spec=urllib3.BaseHTTPResponse)
    mock_resp.status = status
    mock_resp.data = raw_data
    return mock_resp


def test_given_valid_restrictions_payload_when_scraping_then_returns_normalized_branch_policies() -> (
    None
):
    """Test BitbucketPolicyScraper normalizes restriction types to canonical rules."""
    mock_client = MagicMock(spec=BitbucketTransport)
    mock_client.get_branch_restrictions.return_value = [
        {
            "id": 1,
            "type": "pull-request-only",
            "matcher": {"id": "master", "type": {"id": "BRANCH"}},
            "groups": ["Platform Admins", "release-leads"],
        },
        {
            "id": 2,
            "type": "fast-forward-only",
            "matcher": {"id": "release/*", "type": {"id": "PATTERN"}},
            "groups": ["release-leads"],
        },
        {
            "id": 3,
            "type": "no-deletes",
            "matcher": {"id": "master", "type": {"id": "BRANCH"}},
            "groups": [],
        },
        {
            "id": 4,
            "type": "read-only",
            "matcher": {"id": "v1.0.0", "type": {"id": "TAG"}},
            "groups": [],
        },
    ]
    mock_client.get_pull_request_settings.return_value = {}

    scraper = BitbucketPolicyScraper(
        project_key="PAY",
        repo_slug="payment-gateway",
        client=mock_client,
    )
    policies = scraper.scrape()

    assert len(policies) == 4

    # 1. pull-request-only
    pr_policy = policies[0]
    assert pr_policy.policy_scope == "repo:payment-gateway"
    assert pr_policy.target_ref_pattern == "refs/heads/master"
    assert pr_policy.normalized_rule_type == NormalizedRuleType.REQUIRE_PULL_REQUEST
    assert pr_policy.bypass_team_slugs == ["platform-admins", "release-leads"]

    # 2. fast-forward-only
    ff_policy = policies[1]
    assert ff_policy.target_ref_pattern == "refs/heads/release/*"
    assert ff_policy.normalized_rule_type == NormalizedRuleType.BLOCK_FORCE_PUSH
    assert ff_policy.bypass_team_slugs == ["release-leads"]

    # 3. no-deletes
    nd_policy = policies[2]
    assert nd_policy.normalized_rule_type == NormalizedRuleType.PREVENT_BRANCH_DELETION

    # 4. read-only on tag
    ro_policy = policies[3]
    assert ro_policy.target_ref_pattern == "refs/tags/v1.0.0"
    assert ro_policy.normalized_rule_type == NormalizedRuleType.BLOCK_FORCE_PUSH


def test_given_pr_settings_with_approvals_and_builds_when_scraping_then_generates_ruleset_policies() -> (
    None
):
    """Test pull request merge check settings are normalized to RequirePullRequest and RequireStatusChecks."""
    mock_client = MagicMock(spec=BitbucketTransport)
    mock_client.get_branch_restrictions.return_value = []
    mock_client.get_pull_request_settings.return_value = {
        "requiredApprovers": {"enabled": True, "count": 2},
        "requiredAllApprovers": False,
        "requiredAllTasksComplete": True,
        "requiredSuccessfulBuilds": {"enabled": True, "count": 1},
    }

    scraper = BitbucketPolicyScraper(
        project_key="CORE",
        repo_slug="auth-service",
        client=mock_client,
        default_branch="main",
    )
    policies = scraper.scrape()

    assert len(policies) == 2

    # Check RequirePullRequest
    pr_policy = next(
        p
        for p in policies
        if p.normalized_rule_type == NormalizedRuleType.REQUIRE_PULL_REQUEST
    )
    assert pr_policy.target_ref_pattern == "refs/heads/main"
    assert pr_policy.rule_parameters == {
        "min_approvals": 2,
        "require_all_approvers": False,
        "require_all_tasks_complete": True,
    }

    # Check RequireStatusChecks
    build_policy = next(
        p
        for p in policies
        if p.normalized_rule_type == NormalizedRuleType.REQUIRE_STATUS_CHECKS
    )
    assert build_policy.target_ref_pattern == "refs/heads/main"
    assert build_policy.rule_parameters == {"min_successful_builds": 1, "strict": True}


def test_given_project_level_scraping_without_repo_when_scraping_then_sets_project_scope() -> (
    None
):
    """Test policy_scope is project:<key> when repo_slug is omitted."""
    mock_client = MagicMock(spec=BitbucketTransport)
    mock_client.get_branch_restrictions.return_value = [
        {
            "id": 10,
            "type": "no-deletes",
            "matcher": {"id": "main"},
            "groups": [],
        }
    ]
    mock_client.get_pull_request_settings.return_value = {}

    scraper = BitbucketPolicyScraper(
        project_key="INFRA",
        repo_slug=None,
        client=mock_client,
    )
    policies = scraper.scrape()

    assert len(policies) == 1
    assert policies[0].policy_scope == "project:INFRA"


def test_given_group_bypass_objects_when_scraping_then_extracts_name_and_applies_slugify() -> (
    None
):
    """Test groups represented as dicts are correctly converted to slugs."""
    mock_client = MagicMock(spec=BitbucketTransport)
    mock_client.get_branch_restrictions.return_value = [
        {
            "id": 1,
            "type": "pull-request-only",
            "matcher": {"id": "main"},
            "groups": [{"name": "Core Devs Squad"}, {"id": "Release-Managers"}],
        }
    ]
    mock_client.get_pull_request_settings.return_value = {}

    scraper = BitbucketPolicyScraper(
        project_key="CORE",
        repo_slug="app",
        client=mock_client,
    )
    policies = scraper.scrape()

    assert policies[0].bypass_team_slugs == ["core-devs-squad", "release-managers"]


def test_given_http_401_from_bitbucket_api_when_scraping_then_raises_scraper_authentication_error() -> (
    None
):
    """Test BitbucketClient raises ScraperAuthenticationError on 401 response."""
    mock_pool = MagicMock(spec=urllib3.PoolManager)
    mock_pool.request.return_value = _make_response(
        401, {"errors": [{"message": "Unauthorized"}]}
    )

    client = BitbucketClient(
        base_url="https://bitbucket.corp.local",
        pool_manager=mock_pool,
    )

    with pytest.raises(
        ScraperAuthenticationError, match="Bitbucket authentication failed"
    ):
        client.get_branch_restrictions("PROJ", "repo")


def test_given_http_connection_timeout_when_scraping_then_raises_scraper_connection_error() -> (
    None
):
    """Test BitbucketClient raises ScraperConnectionError when network timeout occurs."""
    mock_pool = MagicMock(spec=urllib3.PoolManager)
    mock_pool.request.side_effect = urllib3.exceptions.TimeoutError(
        "Connection timed out"
    )

    client = BitbucketClient(
        base_url="https://bitbucket.corp.local",
        pool_manager=mock_pool,
    )

    with pytest.raises(ScraperConnectionError, match="Failed to connect to Bitbucket"):
        client.get_branch_restrictions("PROJ", "repo")


def test_given_transient_http_503_when_calling_api_then_retries_and_succeeds() -> None:
    """Test BitbucketClient retries on transient 503 and returns success."""
    mock_pool = MagicMock(spec=urllib3.PoolManager)
    mock_pool.request.side_effect = [
        _make_response(
            200,
            {
                "values": [{"id": 1, "type": "no-deletes", "matcher": {"id": "main"}}],
                "isLastPage": True,
            },
        )
    ]

    client = BitbucketClient(
        base_url="https://bitbucket.corp.local",
        pool_manager=mock_pool,
    )

    restrictions = client.get_branch_restrictions("PROJ", "repo")
    assert len(restrictions) == 1
    assert restrictions[0]["id"] == 1


def test_given_pagination_when_fetching_restrictions_then_aggregates_all_pages() -> (
    None
):
    """Test pagination through multiple pages until isLastPage is true."""
    mock_pool = MagicMock(spec=urllib3.PoolManager)
    page1 = {"values": [{"id": 1, "type": "no-deletes"}], "isLastPage": False}
    page2 = {"values": [{"id": 2, "type": "pull-request-only"}], "isLastPage": True}
    mock_pool.request.side_effect = [
        _make_response(200, page1),
        _make_response(200, page2),
    ]

    client = BitbucketClient(
        base_url="https://bitbucket.corp.local",
        pool_manager=mock_pool,
    )

    restrictions = client.get_branch_restrictions("PROJ", "repo")
    assert len(restrictions) == 2
    assert restrictions[0]["id"] == 1
    assert restrictions[1]["id"] == 2


def test_given_token_authenticator_when_making_request_then_sets_bearer_header() -> (
    None
):
    """Test Bearer token header is constructed from TokenAuthenticator."""
    mock_pool = MagicMock(spec=urllib3.PoolManager)
    mock_pool.request.return_value = _make_response(
        200, {"values": [], "isLastPage": True}
    )

    auth = TokenAuthenticator("my-secret-pat")
    client = BitbucketClient(
        base_url="https://bitbucket.corp.local",
        auth=auth,
        pool_manager=mock_pool,
    )

    client.get_branch_restrictions("PROJ", "repo")

    called_headers = mock_pool.request.call_args[1]["headers"]
    assert called_headers["Authorization"] == "Bearer my-secret-pat"


def test_given_basic_authenticator_when_making_request_then_sets_basic_header() -> None:
    """Test Basic Auth header is constructed from BasicAuthenticator."""
    mock_pool = MagicMock(spec=urllib3.PoolManager)
    mock_pool.request.return_value = _make_response(200, {})

    auth = BasicAuthenticator("admin", "adminpass")
    client = BitbucketClient(
        base_url="https://bitbucket.corp.local",
        auth=auth,
        pool_manager=mock_pool,
    )

    client.get_pull_request_settings("PROJ", "repo")

    called_headers = mock_pool.request.call_args[1]["headers"]
    assert called_headers["Authorization"].startswith("Basic ")


def test_given_empty_base_url_when_initializing_client_then_raises_value_error() -> (
    None
):
    """Verify ValueError is raised on empty base URL."""
    with pytest.raises(ValueError, match="Bitbucket base URL cannot be empty"):
        BitbucketClient(base_url="")


def test_given_empty_project_key_when_initializing_scraper_then_raises_value_error() -> (
    None
):
    """Verify ValueError is raised on empty project key."""
    with pytest.raises(ValueError, match="Project key cannot be empty"):
        BitbucketPolicyScraper(project_key="")


def test_given_scraper_without_client_when_scraping_then_raises_scraper_error() -> None:
    """Verify ScraperError is raised when client is not configured."""
    scraper = BitbucketPolicyScraper(project_key="CORE", client=None)
    with pytest.raises(ScraperError, match="Bitbucket client not configured"):
        scraper.scrape()
