"""Unit tests for BitbucketClient group permissions and BitbucketTeamScraper."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest
import urllib3

from gh_migration_inventory_manager.bitbucket_scrap import (
    BitbucketClient,
    BitbucketTeamScraper,
    BitbucketTeamsScraperProvider,
    BitbucketTransport,
)
from gh_migration_inventory_manager.scrap import (
    BasicAuthenticator,
    NormalizedRole,
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


# ==============================================================================
# Scenario 1: Successful Collaboration
# ==============================================================================


def test_given_valid_project_and_repo_permissions_when_scraping_then_returns_canonical_team_permissions() -> (
    None
):
    """Test scraper extracts project and repo group permissions into TeamPermission."""
    mock_client = MagicMock(spec=BitbucketTransport)
    mock_client.get_project_group_permissions.return_value = [
        {
            "group": {"name": "dev-leads"},
            "permission": "PROJECT_ADMIN",
        },
        {
            "group": "platform-devs",
            "permission": "PROJECT_WRITE",
        },
    ]
    mock_client.get_repositories.return_value = [{"slug": "payment-gateway"}]
    mock_client.get_repository_group_permissions.return_value = [
        {
            "group": {"name": "qa-engineers"},
            "permission": "REPO_READ",
        }
    ]
    mock_client.get_group_member_count.side_effect = lambda g: {
        "dev-leads": 4,
        "platform-devs": 12,
        "qa-engineers": 3,
    }.get(g, 0)

    scraper = BitbucketTeamScraper(
        project_key="PAY",
        client=mock_client,
    )
    permissions = scraper.scrape()

    assert len(permissions) == 3

    # 1. Project Admin
    p_admin = next(p for p in permissions if p.team_name == "dev-leads")
    assert p_admin.team_slug == "dev-leads"
    assert p_admin.member_count == 4
    assert p_admin.target_repo_or_project == "project:PAY"
    assert p_admin.normalized_role == NormalizedRole.ADMIN
    assert p_admin.origin_role == "PROJECT_ADMIN"
    assert p_admin.metadata["project_key"] == "PAY"

    # 2. Project Write
    p_write = next(p for p in permissions if p.team_name == "platform-devs")
    assert p_write.team_slug == "platform-devs"
    assert p_write.member_count == 12
    assert p_write.target_repo_or_project == "project:PAY"
    assert p_write.normalized_role == NormalizedRole.WRITE
    assert p_write.origin_role == "PROJECT_WRITE"

    # 3. Repo Read
    r_read = next(p for p in permissions if p.team_name == "qa-engineers")
    assert r_read.team_slug == "qa-engineers"
    assert r_read.member_count == 3
    assert r_read.target_repo_or_project == "repo:PAY/payment-gateway"
    assert r_read.normalized_role == NormalizedRole.READ
    assert r_read.origin_role == "REPO_READ"
    assert r_read.metadata["repo_slug"] == "payment-gateway"


def test_given_include_repo_level_false_when_scraping_then_extracts_only_project_level_permissions() -> (
    None
):
    """Test include_repo_level=False ignores repository group permissions."""
    mock_client = MagicMock(spec=BitbucketTransport)
    mock_client.get_project_group_permissions.return_value = [
        {"group": {"name": "admins"}, "permission": "PROJECT_ADMIN"}
    ]
    mock_client.get_group_member_count.return_value = 2

    scraper = BitbucketTeamScraper(
        project_key="CORE",
        client=mock_client,
        include_repo_level=False,
    )
    permissions = scraper.scrape()

    assert len(permissions) == 1
    assert permissions[0].target_repo_or_project == "project:CORE"
    mock_client.get_repositories.assert_not_called()
    mock_client.get_repository_group_permissions.assert_not_called()


def test_given_project_filter_when_scraping_then_extracts_only_specified_project() -> (
    None
):
    """Test passing project_key skips get_projects call and scrapes only that project."""
    mock_client = MagicMock(spec=BitbucketTransport)
    mock_client.get_project_group_permissions.return_value = [
        {"group": "leads", "permission": "PROJECT_ADMIN"}
    ]
    mock_client.get_repositories.return_value = []
    mock_client.get_group_member_count.return_value = 1

    scraper = BitbucketTeamScraper(
        project_key="SPECIFIC",
        client=mock_client,
    )
    permissions = scraper.scrape()

    assert len(permissions) == 1
    mock_client.get_projects.assert_not_called()
    mock_client.get_project_group_permissions.assert_called_once_with(
        "SPECIFIC", limit=100
    )


def test_given_all_projects_when_scraping_then_iterates_all_discovered_projects() -> (
    None
):
    """Test scraping without project_key iterates over all discovered projects."""
    mock_client = MagicMock(spec=BitbucketTransport)
    mock_client.get_projects.return_value = [
        {"key": "PRJ1"},
        {"key": "PRJ2"},
    ]
    mock_client.get_project_group_permissions.side_effect = [
        [{"group": "team-a", "permission": "PROJECT_READ"}],
        [{"group": "team-b", "permission": "PROJECT_WRITE"}],
    ]
    mock_client.get_repositories.return_value = []
    mock_client.get_group_member_count.return_value = 5

    scraper = BitbucketTeamScraper(
        project_key=None,
        client=mock_client,
    )
    permissions = scraper.scrape()

    assert len(permissions) == 2
    mock_client.get_projects.assert_called_once_with(limit=100)
    targets = {p.target_repo_or_project for p in permissions}
    assert targets == {"project:PRJ1", "project:PRJ2"}


def test_given_specific_repo_slug_when_scraping_then_extracts_only_specified_repo() -> (
    None
):
    """Test passing repo_slug scopes repository discovery strictly to that repository."""
    mock_client = MagicMock(spec=BitbucketTransport)
    mock_client.get_project_group_permissions.return_value = []
    mock_client.get_repository_group_permissions.return_value = [
        {"group": "service-owners", "permission": "REPO_ADMIN"}
    ]
    mock_client.get_group_member_count.return_value = 3

    scraper = BitbucketTeamScraper(
        project_key="PAY",
        repo_slug="custom-service",
        client=mock_client,
    )
    permissions = scraper.scrape()

    assert len(permissions) == 1
    assert permissions[0].target_repo_or_project == "repo:PAY/custom-service"
    mock_client.get_repositories.assert_not_called()
    mock_client.get_repository_group_permissions.assert_called_once_with(
        "PAY", "custom-service", limit=100
    )


# ==============================================================================
# Scenario 2: Internal Behavior & State
# ==============================================================================


@pytest.mark.parametrize(
    ("raw_perm", "expected_role"),
    [
        ("PROJECT_ADMIN", NormalizedRole.ADMIN),
        ("REPO_ADMIN", NormalizedRole.ADMIN),
        ("admin", NormalizedRole.ADMIN),
        ("ADMIN", NormalizedRole.ADMIN),
        ("PROJECT_WRITE", NormalizedRole.WRITE),
        ("REPO_WRITE", NormalizedRole.WRITE),
        ("write", NormalizedRole.WRITE),
        ("WRITE", NormalizedRole.WRITE),
        ("PROJECT_READ", NormalizedRole.READ),
        ("REPO_READ", NormalizedRole.READ),
        ("read", NormalizedRole.READ),
        ("READ", NormalizedRole.READ),
    ],
)
def test_given_bitbucket_origin_roles_when_scraping_then_normalizes_roles_accurately(
    raw_perm: str, expected_role: NormalizedRole
) -> None:
    """Test cross-platform role normalization matrix maps to canonical NormalizedRole."""
    mock_client = MagicMock(spec=BitbucketTransport)
    mock_client.get_project_group_permissions.return_value = [
        {"group": "test-group", "permission": raw_perm}
    ]
    mock_client.get_repositories.return_value = []
    mock_client.get_group_member_count.return_value = 1

    scraper = BitbucketTeamScraper(project_key="TEST", client=mock_client)
    permissions = scraper.scrape()

    assert len(permissions) == 1
    assert permissions[0].normalized_role == expected_role
    assert permissions[0].origin_role == raw_perm


def test_given_special_character_and_mixed_case_group_names_when_scraping_then_slugifies_team_slug() -> (
    None
):
    """Test group names with spaces, special characters, and uppercase are slugified."""
    mock_client = MagicMock(spec=BitbucketTransport)
    mock_client.get_project_group_permissions.return_value = [
        {"group": "Platform Admins (Core)", "permission": "PROJECT_ADMIN"},
        {"group": "QA_Engineers 2", "permission": "PROJECT_READ"},
    ]
    mock_client.get_repositories.return_value = []
    mock_client.get_group_member_count.return_value = 1

    scraper = BitbucketTeamScraper(project_key="TEST", client=mock_client)
    permissions = scraper.scrape()

    assert len(permissions) == 2
    slug_map = {p.team_name: p.team_slug for p in permissions}
    assert slug_map["Platform Admins (Core)"] == "platform-admins-core"
    assert slug_map["QA_Engineers 2"] == "qa_engineers-2"


def test_given_duplicate_group_permissions_on_same_target_when_scraping_then_deduplicates_by_composite_key() -> (
    None
):
    """Test multiple permissions for the same group on same target keep highest role."""
    mock_client = MagicMock(spec=BitbucketTransport)
    mock_client.get_project_group_permissions.return_value = [
        {"group": "devs", "permission": "PROJECT_READ"},
        {"group": "devs", "permission": "PROJECT_WRITE"},
    ]
    mock_client.get_repositories.return_value = []
    mock_client.get_group_member_count.return_value = 5

    scraper = BitbucketTeamScraper(project_key="TEST", client=mock_client)
    permissions = scraper.scrape()

    assert len(permissions) == 1
    assert permissions[0].team_name == "devs"
    assert permissions[0].normalized_role == NormalizedRole.WRITE


def test_given_cached_group_member_count_when_scraping_multiple_targets_then_reuses_count_without_duplicate_calls() -> (
    None
):
    """Test group member count caching prevents redundant API calls for shared groups."""
    mock_client = MagicMock(spec=BitbucketTransport)
    mock_client.get_project_group_permissions.return_value = [
        {"group": "shared-devs", "permission": "PROJECT_WRITE"}
    ]
    mock_client.get_repositories.return_value = [
        {"slug": "repo1"},
        {"slug": "repo2"},
    ]
    mock_client.get_repository_group_permissions.side_effect = [
        [{"group": "shared-devs", "permission": "REPO_ADMIN"}],
        [{"group": "shared-devs", "permission": "REPO_READ"}],
    ]
    mock_client.get_group_member_count.return_value = 8

    scraper = BitbucketTeamScraper(project_key="TEST", client=mock_client)
    permissions = scraper.scrape()

    assert len(permissions) == 3
    mock_client.get_group_member_count.assert_called_once_with("shared-devs")


def test_given_target_scopes_when_scraping_then_formats_project_and_repo_scopes_correctly() -> (
    None
):
    """Test target scope formatting adheres to project:<KEY> and repo:<KEY>/<SLUG>."""
    mock_client = MagicMock(spec=BitbucketTransport)
    mock_client.get_project_group_permissions.return_value = [
        {"group": "g1", "permission": "PROJECT_READ"}
    ]
    mock_client.get_repositories.return_value = [{"slug": "r1"}]
    mock_client.get_repository_group_permissions.return_value = [
        {"group": "g2", "permission": "REPO_WRITE"}
    ]
    mock_client.get_group_member_count.return_value = 1

    scraper = BitbucketTeamScraper(project_key="PRJ", client=mock_client)
    permissions = scraper.scrape()

    p_map = {p.team_name: p.target_repo_or_project for p in permissions}
    assert p_map["g1"] == "project:PRJ"
    assert p_map["g2"] == "repo:PRJ/r1"


# ==============================================================================
# Scenario 3: Dependency Failure Handling
# ==============================================================================


def test_given_connection_timeout_from_client_when_scraping_then_raises_scraper_connection_error() -> (
    None
):
    """Test client network timeout propagates as ScraperConnectionError."""
    mock_client = MagicMock(spec=BitbucketTransport)
    mock_client.get_project_group_permissions.side_effect = ScraperConnectionError(
        "Connection timed out"
    )

    scraper = BitbucketTeamScraper(project_key="PRJ", client=mock_client)
    with pytest.raises(ScraperConnectionError, match="Connection timed out"):
        scraper.scrape()


def test_given_http_401_or_403_from_client_when_scraping_then_raises_scraper_authentication_error() -> (
    None
):
    """Test client HTTP 401/403 propagates as ScraperAuthenticationError."""
    mock_client = MagicMock(spec=BitbucketTransport)
    mock_client.get_project_group_permissions.side_effect = ScraperAuthenticationError(
        "Forbidden"
    )

    scraper = BitbucketTeamScraper(project_key="PRJ", client=mock_client)
    with pytest.raises(ScraperAuthenticationError, match="Forbidden"):
        scraper.scrape()


def test_given_missing_client_when_scraping_then_raises_scraper_error() -> None:
    """Test scraping without configuring a transport client raises ScraperError."""
    scraper = BitbucketTeamScraper(project_key="PRJ", client=None)
    with pytest.raises(ScraperError, match="Bitbucket client not configured"):
        scraper.scrape()


def test_given_member_count_api_forbidden_when_scraping_then_falls_back_to_zero_member_count() -> (
    None
):
    """Test 403 on admin member count endpoint falls back to 0 without failing scrape."""
    mock_client = MagicMock(spec=BitbucketTransport)
    mock_client.get_project_group_permissions.return_value = [
        {"group": "devs", "permission": "PROJECT_WRITE"}
    ]
    mock_client.get_repositories.return_value = []
    mock_client.get_group_member_count.side_effect = ScraperAuthenticationError(
        "Non-admin caller forbidden from admin membership API"
    )

    scraper = BitbucketTeamScraper(project_key="PRJ", client=mock_client)
    permissions = scraper.scrape()

    assert len(permissions) == 1
    assert permissions[0].member_count == 0


# ==============================================================================
# Scenario 4: Invalid Input Handling
# ==============================================================================


def test_given_empty_base_url_when_initializing_client_then_raises_value_error() -> (
    None
):
    """Test initializing BitbucketClient with empty base_url raises ValueError."""
    with pytest.raises(ValueError, match="Bitbucket base URL cannot be empty"):
        BitbucketClient(base_url="")


def test_given_empty_group_name_in_payload_when_scraping_then_raises_value_error() -> (
    None
):
    """Test group permission payload missing group name raises ValueError."""
    mock_client = MagicMock(spec=BitbucketTransport)
    mock_client.get_project_group_permissions.return_value = [
        {"group": {"name": ""}, "permission": "PROJECT_ADMIN"}
    ]
    mock_client.get_repositories.return_value = []

    scraper = BitbucketTeamScraper(project_key="PRJ", client=mock_client)
    with pytest.raises(
        ValueError, match="Group permission entry lacks valid group name"
    ):
        scraper.scrape()


def test_given_unrecognized_permission_string_when_scraping_then_raises_value_error() -> (
    None
):
    """Test unrecognized Bitbucket permission role raises ValueError."""
    mock_client = MagicMock(spec=BitbucketTransport)
    mock_client.get_project_group_permissions.return_value = [
        {"group": "devs", "permission": "INVALID_ROLE"}
    ]
    mock_client.get_repositories.return_value = []

    scraper = BitbucketTeamScraper(project_key="PRJ", client=mock_client)
    with pytest.raises(
        ValueError, match="Unrecognized Bitbucket permission role: 'INVALID_ROLE'"
    ):
        scraper.scrape()


def test_given_empty_permission_string_when_scraping_then_raises_value_error() -> None:
    """Test empty permission role in payload raises ValueError."""
    mock_client = MagicMock(spec=BitbucketTransport)
    mock_client.get_project_group_permissions.return_value = [
        {"group": "devs", "permission": ""}
    ]
    mock_client.get_repositories.return_value = []

    scraper = BitbucketTeamScraper(project_key="PRJ", client=mock_client)
    with pytest.raises(
        ValueError, match="Group permission entry lacks valid permission"
    ):
        scraper.scrape()


def test_given_malformed_group_entry_when_scraping_then_raises_value_error() -> None:
    """Test non-dict group permission item raises ValueError."""
    mock_client = MagicMock(spec=BitbucketTransport)
    mock_client.get_project_group_permissions.return_value = ["malformed_string"]
    mock_client.get_repositories.return_value = []

    scraper = BitbucketTeamScraper(project_key="PRJ", client=mock_client)
    with pytest.raises(
        ValueError, match="Invalid group permission entry: 'malformed_string'"
    ):
        scraper.scrape()


def test_given_empty_project_key_when_calling_client_method_then_raises_value_error() -> (
    None
):
    """Test BitbucketClient.get_project_group_permissions validates project_key."""
    client = BitbucketClient(base_url="https://bitbucket.example.com")
    with pytest.raises(ValueError, match="Project key cannot be empty"):
        client.get_project_group_permissions("")


def test_given_empty_repo_slug_when_calling_client_method_then_raises_value_error() -> (
    None
):
    """Test BitbucketClient.get_repository_group_permissions validates repo_slug."""
    client = BitbucketClient(base_url="https://bitbucket.example.com")
    with pytest.raises(ValueError, match="Repository slug cannot be empty"):
        client.get_repository_group_permissions("PROJ", "")


def test_given_empty_group_name_when_getting_member_count_then_raises_value_error() -> (
    None
):
    """Test BitbucketClient.get_group_member_count validates group_name."""
    client = BitbucketClient(base_url="https://bitbucket.example.com")
    with pytest.raises(ValueError, match="Group name cannot be empty"):
        client.get_group_member_count("")


# ==============================================================================
# Client & Transport Layer Unit Tests
# ==============================================================================


def test_given_server_dc_when_getting_project_group_permissions_then_requests_correct_endpoint() -> (
    None
):
    """Test BitbucketClient queries Server/DC project permissions endpoint."""
    mock_pool = MagicMock(spec=urllib3.PoolManager)
    mock_pool.request.return_value = _make_response(
        200,
        {
            "isLastPage": True,
            "values": [{"group": {"name": "admins"}, "permission": "PROJECT_ADMIN"}],
        },
    )

    client = BitbucketClient(
        base_url="https://bitbucket.company.com",
        pool_manager=mock_pool,
    )
    result = client.get_project_group_permissions("MIG")

    assert len(result) == 1
    assert result[0]["group"]["name"] == "admins"
    call_args = mock_pool.request.call_args
    assert (
        "https://bitbucket.company.com/rest/api/1.0/projects/MIG/permissions/groups"
        in call_args[0][1]
    )


def test_given_server_dc_when_getting_repository_group_permissions_then_requests_correct_endpoint() -> (
    None
):
    """Test BitbucketClient queries Server/DC repo permissions endpoint."""
    mock_pool = MagicMock(spec=urllib3.PoolManager)
    mock_pool.request.return_value = _make_response(
        200,
        {
            "isLastPage": True,
            "values": [{"group": {"name": "devs"}, "permission": "REPO_WRITE"}],
        },
    )

    client = BitbucketClient(
        base_url="https://bitbucket.company.com",
        pool_manager=mock_pool,
    )
    result = client.get_repository_group_permissions("MIG", "payment")

    assert len(result) == 1
    assert result[0]["group"]["name"] == "devs"
    call_args = mock_pool.request.call_args
    assert (
        "https://bitbucket.company.com/rest/api/1.0/projects/MIG/repos/payment/permissions/groups"
        in call_args[0][1]
    )


def test_given_cloud_when_getting_project_group_permissions_then_requests_cloud_endpoint() -> (
    None
):
    """Test BitbucketClient on bitbucket.org queries 2.0 workspace groups endpoint."""
    mock_pool = MagicMock(spec=urllib3.PoolManager)
    mock_pool.request.return_value = _make_response(
        200,
        {
            "values": [{"group": {"name": "cloud-admins"}, "permission": "admin"}],
            "next": None,
        },
    )

    client = BitbucketClient(
        base_url="https://api.bitbucket.org",
        pool_manager=mock_pool,
    )
    result = client.get_project_group_permissions("my-workspace")

    assert len(result) == 1
    assert result[0]["group"]["name"] == "cloud-admins"
    call_args = mock_pool.request.call_args
    assert (
        "https://api.bitbucket.org/2.0/workspaces/my-workspace/permissions/groups"
        in call_args[0][1]
    )


def test_given_group_name_when_getting_member_count_then_requests_more_members_endpoint() -> (
    None
):
    """Test BitbucketClient queries admin/groups/more-members for Server/DC."""
    mock_pool = MagicMock(spec=urllib3.PoolManager)
    mock_pool.request.return_value = _make_response(
        200,
        {
            "isLastPage": True,
            "values": [{"name": "alice"}, {"name": "bob"}],
        },
    )

    client = BitbucketClient(
        base_url="https://bitbucket.company.com",
        pool_manager=mock_pool,
    )
    count = client.get_group_member_count("dev-leads")

    assert count == 2
    call_args = mock_pool.request.call_args
    assert (
        "https://bitbucket.company.com/rest/api/1.0/admin/groups/more-members"
        in call_args[0][1]
    )


def test_given_cloud_when_getting_member_count_then_returns_zero() -> None:
    """Test BitbucketClient on Cloud returns 0 for member count without admin API."""
    client = BitbucketClient(base_url="https://bitbucket.org")
    assert client.get_group_member_count("any-group") == 0


def test_given_pagination_when_getting_member_count_then_aggregates_values() -> None:
    """Test BitbucketClient paginates multiple pages of members to compute count."""
    mock_pool = MagicMock(spec=urllib3.PoolManager)
    mock_pool.request.side_effect = [
        _make_response(
            200,
            {
                "isLastPage": False,
                "nextPageStart": 2,
                "values": [{"name": "alice"}, {"name": "bob"}],
            },
        ),
        _make_response(
            200,
            {
                "isLastPage": True,
                "values": [{"name": "carol"}],
            },
        ),
    ]

    client = BitbucketClient(
        base_url="https://bitbucket.company.com",
        pool_manager=mock_pool,
    )
    count = client.get_group_member_count("large-group")

    assert count == 3
    assert mock_pool.request.call_count == 2


# ==============================================================================
# Provider Unit Tests
# ==============================================================================


def test_given_valid_args_when_creating_scraper_then_instantiates_bitbucket_team_scraper() -> (
    None
):
    """Test BitbucketTeamsScraperProvider.create_scraper builds BitbucketTeamScraper."""
    provider = BitbucketTeamsScraperProvider()
    assert provider.platform_name == "bitbucket-teams"
    assert provider.target_type == "team"
    assert provider.default_sheet_name == "Topology"

    args = MagicMock()
    args.base_url = "https://bitbucket.company.com"
    args.project_key = "CORE"
    args.repo_slug = None
    args.username = "user"
    args.password = "pass"
    args.token = None
    args.include_repo_level = True
    args.page_size = 50
    args.timeout = 10.0
    args.retries = 2
    args.backoff_factor = 0.1

    scraper = provider.create_scraper(args)

    assert isinstance(scraper, BitbucketTeamScraper)
    assert scraper.project_key == "CORE"
    assert scraper.include_repo_level is True
    assert scraper.page_size == 50
    client = scraper.client
    assert isinstance(client, BitbucketClient)
    assert isinstance(client.auth, BasicAuthenticator)


def test_given_token_auth_when_creating_scraper_then_configures_token_authenticator() -> (
    None
):
    """Test BitbucketTeamsScraperProvider configures TokenAuthenticator when token passed."""
    provider = BitbucketTeamsScraperProvider()
    args = MagicMock()
    args.base_url = "https://bitbucket.company.com"
    args.project_key = None
    args.repo_slug = None
    args.username = None
    args.password = None
    args.token = "test-bearer-token"
    args.include_repo_level = False
    args.page_size = 100
    args.timeout = None
    args.retries = None
    args.backoff_factor = None

    scraper = provider.create_scraper(args)

    assert isinstance(scraper, BitbucketTeamScraper)
    assert scraper.include_repo_level is False
    client = scraper.client
    assert isinstance(client, BitbucketClient)
    assert isinstance(client.auth, TokenAuthenticator)
