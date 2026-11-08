"""Unit tests for Bitbucket Cloud REST 2.0 platform scrapers and transport client."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest
import urllib3

from gh_migration_inventory_manager.bitbucket_cloud_scrap import (
    DEFAULT_BITBUCKET_CLOUD_BASE_URL,
    BitbucketCloudClient,
    BitbucketCloudPoliciesScraperProvider,
    BitbucketCloudPolicyScraper,
    BitbucketCloudRepositoryScraper,
    BitbucketCloudScraperProvider,
    BitbucketCloudTeamScraper,
    BitbucketCloudTeamsScraperProvider,
    _build_bitbucket_cloud_client,
    _detect_stacks,
    _parse_commit_timestamp,
)
from gh_migration_inventory_manager.scrap import (
    BasicAuthenticator,
    NormalizedRole,
    NormalizedRuleType,
    ScraperAuthenticationError,
    ScraperConnectionError,
    ScraperError,
    Stack,
    TokenAuthenticator,
)


def _make_mock_response(status: int, data: bytes) -> urllib3.BaseHTTPResponse:
    resp = MagicMock(spec=urllib3.BaseHTTPResponse)
    resp.status = status
    resp.data = data
    return resp


class TestBitbucketCloudClient:
    """Unit tests for BitbucketCloudClient transport protocol and pagination."""

    def test_given_empty_base_url_when_initializing_client_then_raises_value_error(
        self,
    ) -> None:
        with pytest.raises(
            ValueError, match="Bitbucket Cloud base URL cannot be empty"
        ):
            BitbucketCloudClient(base_url="  ")

    def test_given_token_authenticator_when_initializing_then_sets_bearer_header(
        self,
    ) -> None:
        auth = TokenAuthenticator(token="wat_secret_123")
        client = BitbucketCloudClient(auth=auth)
        assert client._default_headers["Authorization"] == "Bearer wat_secret_123"

    def test_given_basic_authenticator_when_initializing_then_sets_basic_header(
        self,
    ) -> None:
        auth = BasicAuthenticator(username="user@corp.com", password="app_password_xyz")
        client = BitbucketCloudClient(auth=auth)
        assert client._default_headers["Authorization"].startswith("Basic ")

    def test_given_http_401_when_requesting_api_then_raises_scraper_authentication_error(
        self,
    ) -> None:
        mock_pool = MagicMock(spec=urllib3.PoolManager)
        mock_pool.request.return_value = _make_mock_response(401, b'{"type": "error"}')

        client = BitbucketCloudClient(pool_manager=mock_pool)
        with pytest.raises(
            ScraperAuthenticationError, match="authentication failed with HTTP 401"
        ):
            client.get_workspaces()

    def test_given_http_403_when_requesting_api_then_raises_scraper_authentication_error(
        self,
    ) -> None:
        mock_pool = MagicMock(spec=urllib3.PoolManager)
        mock_pool.request.return_value = _make_mock_response(403, b'{"type": "error"}')

        client = BitbucketCloudClient(pool_manager=mock_pool)
        with pytest.raises(
            ScraperAuthenticationError, match="authentication failed with HTTP 403"
        ):
            client.get_repositories("my-workspace")

    def test_given_http_404_when_requesting_api_then_returns_none(self) -> None:
        mock_pool = MagicMock(spec=urllib3.PoolManager)
        mock_pool.request.return_value = _make_mock_response(404, b'{"type": "error"}')

        client = BitbucketCloudClient(pool_manager=mock_pool)
        branch = client.get_default_branch("ws", "non-existent")
        assert branch is None

    def test_given_http_500_when_requesting_api_then_raises_scraper_error(self) -> None:
        mock_pool = MagicMock(spec=urllib3.PoolManager)
        mock_pool.request.return_value = _make_mock_response(
            500, b"Internal Server Error"
        )

        client = BitbucketCloudClient(pool_manager=mock_pool)
        with pytest.raises(ScraperError, match="HTTP 500"):
            client.get_workspaces()

    def test_given_network_timeout_when_requesting_api_then_raises_scraper_connection_error(
        self,
    ) -> None:
        mock_pool = MagicMock(spec=urllib3.PoolManager)
        mock_pool.request.side_effect = urllib3.exceptions.TimeoutError(
            "Connection timed out"
        )

        client = BitbucketCloudClient(pool_manager=mock_pool)
        with pytest.raises(ScraperConnectionError, match="Network or transport error"):
            client.get_workspaces()

    def test_given_max_retry_error_when_requesting_api_then_raises_scraper_connection_error(
        self,
    ) -> None:
        mock_pool = MagicMock(spec=urllib3.PoolManager)
        mock_pool.request.side_effect = urllib3.exceptions.MaxRetryError(
            mock_pool, "url"
        )

        client = BitbucketCloudClient(pool_manager=mock_pool)
        with pytest.raises(
            ScraperConnectionError, match="Connection failed after retries"
        ):
            client.get_workspaces()

    def test_given_multi_page_cursor_response_when_paginating_then_follows_next_link(
        self,
    ) -> None:
        mock_pool = MagicMock(spec=urllib3.PoolManager)
        page1 = {
            "pagelen": 2,
            "next": "https://api.bitbucket.org/2.0/repositories/my-ws?page=2",
            "values": [{"slug": "repo1", "name": "Repo 1"}],
        }
        page2 = {
            "pagelen": 2,
            "values": [{"slug": "repo2", "name": "Repo 2"}],
        }

        mock_pool.request.side_effect = [
            _make_mock_response(200, json.dumps(page1).encode("utf-8")),
            _make_mock_response(200, json.dumps(page2).encode("utf-8")),
        ]

        client = BitbucketCloudClient(pool_manager=mock_pool)
        repos = client.get_repositories("my-ws")
        assert len(repos) == 2
        assert repos[0]["slug"] == "repo1"
        assert repos[1]["slug"] == "repo2"
        assert mock_pool.request.call_count == 2

    def test_given_empty_workspace_when_calling_methods_then_raises_value_error(
        self,
    ) -> None:
        client = BitbucketCloudClient()
        with pytest.raises(ValueError, match="Workspace slug cannot be empty"):
            client.get_workspace_projects("")
        with pytest.raises(ValueError, match="Workspace slug cannot be empty"):
            client.get_repositories("  ")
        with pytest.raises(ValueError, match="Workspace slug cannot be empty"):
            client.get_default_branch("", "repo")
        with pytest.raises(ValueError, match="Repository slug cannot be empty"):
            client.get_default_branch("ws", "")
        with pytest.raises(ValueError, match="Workspace slug cannot be empty"):
            client.get_last_commit("", "repo")
        with pytest.raises(ValueError, match="Repository slug cannot be empty"):
            client.get_last_commit("ws", "")
        with pytest.raises(ValueError, match="Workspace slug cannot be empty"):
            client.get_repository_files("", "repo")
        with pytest.raises(ValueError, match="Repository slug cannot be empty"):
            client.get_repository_files("ws", "")
        with pytest.raises(ValueError, match="Workspace slug cannot be empty"):
            client.get_branch_restrictions("", "repo")
        with pytest.raises(ValueError, match="Repository slug cannot be empty"):
            client.get_branch_restrictions("ws", "")
        with pytest.raises(ValueError, match="Workspace slug cannot be empty"):
            client.get_workspace_group_permissions("")
        with pytest.raises(ValueError, match="Workspace slug cannot be empty"):
            client.get_repository_group_permissions("", "repo")
        with pytest.raises(ValueError, match="Repository slug cannot be empty"):
            client.get_repository_group_permissions("ws", "")


class TestBitbucketCloudRepositoryScraper:
    """Unit tests for BitbucketCloudRepositoryScraper hierarchy, stack detection, and filtering."""

    def test_given_workspace_provided_when_scraping_then_bypasses_get_workspaces_http_call(
        self,
    ) -> None:
        mock_client = MagicMock(spec=BitbucketCloudClient)
        mock_client.get_repositories.return_value = [
            {
                "slug": "api-service",
                "name": "API Service",
                "project": {"key": "CORE", "name": "Core Project"},
                "links": {
                    "clone": [
                        {
                            "name": "https",
                            "href": "https://bitbucket.org/corp/api-service.git",
                        }
                    ]
                },
                "mainbranch": {"name": "main"},
            }
        ]
        mock_client.get_last_commit.return_value = {"date": "2026-11-01T10:00:00Z"}
        mock_client.get_repository_files.return_value = ["pom.xml", "src/Main.java"]

        scraper = BitbucketCloudRepositoryScraper(workspace="corp", client=mock_client)
        repos = scraper.scrape()

        mock_client.get_workspaces.assert_not_called()
        mock_client.get_repositories.assert_called_once_with(
            workspace="corp", project_key=None, limit=100
        )
        assert len(repos) == 1
        r = repos[0]
        assert r.name == "api-service"
        assert r.organization == "corp"
        assert r.project == "CORE"
        assert r.default_branch == "main"
        assert Stack.JAVA in r.stacks
        assert r.last_commit is not None

    def test_given_workspace_omitted_when_scraping_then_queries_get_workspaces(
        self,
    ) -> None:
        mock_client = MagicMock(spec=BitbucketCloudClient)
        mock_client.get_workspaces.return_value = [{"slug": "ws1"}, {"slug": "ws2"}]
        mock_client.get_repositories.return_value = []

        scraper = BitbucketCloudRepositoryScraper(workspace=None, client=mock_client)
        scraper.scrape()

        mock_client.get_workspaces.assert_called_once()
        assert mock_client.get_repositories.call_count == 2

    def test_given_archived_repository_and_include_archived_false_when_scraping_then_excludes_it(
        self,
    ) -> None:
        mock_client = MagicMock(spec=BitbucketCloudClient)
        mock_client.get_repositories.return_value = [
            {"slug": "active-repo", "archived": False},
            {"slug": "legacy-repo", "archived": True},
        ]
        mock_client.get_last_commit.return_value = None
        mock_client.get_repository_files.return_value = []

        scraper = BitbucketCloudRepositoryScraper(
            workspace="corp", client=mock_client, include_archived=False
        )
        repos = scraper.scrape()

        assert len(repos) == 1
        assert repos[0].name == "active-repo"

    def test_given_missing_client_when_scraping_then_raises_scraper_error(self) -> None:
        scraper = BitbucketCloudRepositoryScraper(workspace="corp", client=None)
        with pytest.raises(ScraperError, match="Bitbucket Cloud client not configured"):
            scraper.scrape()


class TestBitbucketCloudPolicyScraper:
    """Unit tests for BitbucketCloudPolicyScraper branch restriction translation."""

    def test_given_branch_restrictions_when_scraping_then_normalizes_canonical_rule_types(
        self,
    ) -> None:
        mock_client = MagicMock(spec=BitbucketCloudClient)
        mock_client.get_branch_restrictions.return_value = [
            {
                "kind": "require_approvals_to_merge",
                "pattern": "main",
                "value": 2,
                "groups": [{"name": "Core Leads"}],
            },
            {
                "kind": "require_passing_builds_to_merge",
                "pattern": "main",
                "value": 1,
            },
            {
                "kind": "delete",
                "pattern": "*",
            },
            {
                "kind": "force",
                "pattern": "release/*",
            },
            {
                "kind": "push",
                "pattern": "main",
            },
        ]

        scraper = BitbucketCloudPolicyScraper(
            workspace="corp", project_key="CORE", repo_slug="api", client=mock_client
        )
        policies = scraper.scrape()

        assert len(policies) == 5
        types = [p.normalized_rule_type for p in policies]
        assert NormalizedRuleType.REQUIRE_PULL_REQUEST in types
        assert NormalizedRuleType.REQUIRE_STATUS_CHECKS in types
        assert NormalizedRuleType.PREVENT_BRANCH_DELETION in types
        assert NormalizedRuleType.BLOCK_FORCE_PUSH in types

        # Check approval count rule parameter
        pr_policy = next(
            p
            for p in policies
            if p.normalized_rule_type == NormalizedRuleType.REQUIRE_PULL_REQUEST
            and "min_approvals" in p.rule_parameters
        )
        assert isinstance(pr_policy.rule_parameters, dict)
        assert pr_policy.rule_parameters["min_approvals"] == 2
        assert "core-leads" in pr_policy.bypass_team_slugs


class TestBitbucketCloudTeamScraper:
    """Unit tests for BitbucketCloudTeamScraper group permissions and RBAC."""

    def test_given_workspace_and_repo_permissions_when_scraping_then_normalizes_roles(
        self,
    ) -> None:
        mock_client = MagicMock(spec=BitbucketCloudClient)
        mock_client.get_workspace_group_permissions.return_value = [
            {"group": {"name": "admins"}, "permission": "admin"},
            {"group": {"name": "developers"}, "permission": "write"},
        ]
        mock_client.get_repositories.return_value = [
            {"slug": "core-repo", "project": {"key": "CORE"}}
        ]
        mock_client.get_repository_group_permissions.return_value = [
            {"group": {"name": "qa-testers"}, "permission": "read"}
        ]

        scraper = BitbucketCloudTeamScraper(
            workspace="corp", client=mock_client, include_repo_level=True
        )
        teams = scraper.scrape()

        assert len(teams) == 3
        roles = {t.team_name: t.normalized_role for t in teams}
        assert roles["admins"] == NormalizedRole.ADMIN
        assert roles["developers"] == NormalizedRole.WRITE
        assert roles["qa-testers"] == NormalizedRole.READ


class TestBitbucketCloudProvidersAndCLI:
    """Unit tests for Bitbucket Cloud scraper providers and CLI arg building."""

    def test_given_cli_args_when_building_client_then_configures_properties(
        self,
    ) -> None:
        args = MagicMock()
        args.base_url = DEFAULT_BITBUCKET_CLOUD_BASE_URL
        args.token = "test_token"
        args.username = None
        args.app_password = None
        args.timeout = 20.0
        args.retries = 2
        args.backoff_factor = 0.2

        client, base_url = _build_bitbucket_cloud_client(args)
        assert base_url == DEFAULT_BITBUCKET_CLOUD_BASE_URL
        assert client.timeout == 20.0
        assert client._default_headers["Authorization"] == "Bearer test_token"

    def test_given_provider_when_creating_scrapers_then_instantiates_properly(
        self,
    ) -> None:
        args = MagicMock()
        args.workspace = "corp"
        args.project_key = "CORE"
        args.token = "tok"
        args.username = None
        args.app_password = None
        args.page_size = 50
        args.include_archived = True
        args.organization = "my-org"
        args.repo_slug = "repo1"
        args.default_branch = "develop"
        args.include_repo_level = False

        repo_provider = BitbucketCloudScraperProvider()
        repo_scraper = repo_provider.create_scraper(args)
        assert isinstance(repo_scraper, BitbucketCloudRepositoryScraper)
        assert repo_scraper.workspace == "corp"
        assert repo_scraper.organization == "my-org"

        policy_provider = BitbucketCloudPoliciesScraperProvider()
        policy_scraper = policy_provider.create_scraper(args)
        assert isinstance(policy_scraper, BitbucketCloudPolicyScraper)
        assert policy_scraper.repo_slug == "repo1"
        assert policy_scraper.default_branch == "develop"

        team_provider = BitbucketCloudTeamsScraperProvider()
        team_scraper = team_provider.create_scraper(args)
        assert isinstance(team_scraper, BitbucketCloudTeamScraper)
        assert team_scraper.include_repo_level is False


class TestBitbucketCloudCoverageAndBranching:
    """Additional behavioral unit tests to achieve >90% coverage on bitbucket_cloud_scrap."""

    def test_given_project_key_filter_when_getting_repositories_then_passes_q_param(
        self,
    ) -> None:
        mock_pool = MagicMock(spec=urllib3.PoolManager)
        mock_pool.request.return_value = _make_mock_response(200, b'{"values": []}')
        client = BitbucketCloudClient(pool_manager=mock_pool)
        client.get_repositories("ws", project_key="PROJ")
        call_fields = mock_pool.request.call_args[1]["fields"]
        assert call_fields["q"] == 'project.key="PROJ"'

    def test_given_ssh_and_http_clone_links_when_resolving_clone_url_then_prioritizes_http(
        self,
    ) -> None:
        scraper = BitbucketCloudRepositoryScraper()
        repo = {
            "links": {
                "clone": [
                    {"name": "ssh", "href": "git@bitbucket.org:ws/my-repo.git"},
                    {"name": "https", "href": "https://bitbucket.org/ws/my-repo.git"},
                ]
            }
        }
        url = scraper._resolve_clone_url(repo, "ws", "my-repo")
        assert url == "https://bitbucket.org/ws/my-repo.git"

    def test_given_only_ssh_clone_link_when_resolving_clone_url_then_returns_ssh(
        self,
    ) -> None:
        scraper = BitbucketCloudRepositoryScraper()
        repo = {
            "links": {
                "clone": [
                    {"name": "ssh", "href": "git@bitbucket.org:ws/my-repo.git"},
                ]
            }
        }
        url = scraper._resolve_clone_url(repo, "ws", "my-repo")
        assert url == "git@bitbucket.org:ws/my-repo.git"

    def test_given_no_clone_links_when_resolving_clone_url_then_falls_back_to_constructed_url(
        self,
    ) -> None:
        scraper = BitbucketCloudRepositoryScraper()
        url = scraper._resolve_clone_url({}, "ws", "my-repo")
        assert url == "https://bitbucket.org/ws/my-repo.git"

    def test_given_mainbranch_in_payload_when_resolving_default_branch_then_returns_it(
        self,
    ) -> None:
        scraper = BitbucketCloudRepositoryScraper()
        repo = {"mainbranch": {"name": "develop"}}
        assert scraper._resolve_default_branch(repo, "ws", "my-repo") == "develop"

    def test_given_default_branch_from_client_when_resolving_then_returns_it(
        self,
    ) -> None:
        mock_client = MagicMock(spec=BitbucketCloudClient)
        mock_client.get_default_branch.return_value = "main"
        scraper = BitbucketCloudRepositoryScraper(client=mock_client)
        assert scraper._resolve_default_branch({}, "ws", "my-repo") == "main"

    def test_given_error_from_client_when_resolving_default_branch_then_returns_none(
        self,
    ) -> None:
        mock_client = MagicMock(spec=BitbucketCloudClient)
        mock_client.get_default_branch.side_effect = ScraperError("Failed")
        scraper = BitbucketCloudRepositoryScraper(client=mock_client)
        assert scraper._resolve_default_branch({}, "ws", "my-repo") is None

    def test_given_manifest_files_when_detecting_stacks_then_detects_all_archetypes(
        self,
    ) -> None:
        stacks = _detect_stacks(
            [
                "build.gradle.kts",
                "requirements.txt",
                "package.json",
                "App.csproj",
                "infra.tf",
                "deploy.sh",
            ]
        )
        assert Stack.JAVA in stacks
        assert Stack.PYTHON in stacks
        assert Stack.TYPESCRIPT in stacks
        assert Stack.C_SHARP in stacks
        assert Stack.TERRAFORM in stacks
        assert Stack.BASH in stacks

    def test_given_no_manifest_files_when_detecting_stacks_then_returns_unknown(
        self,
    ) -> None:
        assert _detect_stacks([]) == [Stack.UNKNOWN]

    def test_given_commit_timestamp_when_parsing_then_handles_iso_and_empty(
        self,
    ) -> None:
        dt = _parse_commit_timestamp({"date": "2026-11-01T12:00:00+00:00"})
        assert dt is not None
        assert _parse_commit_timestamp(None) is None
        assert _parse_commit_timestamp({}) is None

    def test_given_json_decode_error_when_requesting_api_then_raises_scraper_error(
        self,
    ) -> None:
        mock_pool = MagicMock(spec=urllib3.PoolManager)
        mock_pool.request.return_value = _make_mock_response(200, b"not json")
        client = BitbucketCloudClient(pool_manager=mock_pool)
        with pytest.raises(ScraperError, match="Failed to parse JSON response"):
            client.get_workspaces()

    def test_given_policy_scraper_without_client_when_scraping_then_raises_scraper_error(
        self,
    ) -> None:
        scraper = BitbucketCloudPolicyScraper(client=None)
        with pytest.raises(ScraperError, match="Bitbucket Cloud client not configured"):
            scraper.scrape()

    def test_given_team_scraper_without_client_when_scraping_then_raises_scraper_error(
        self,
    ) -> None:
        scraper = BitbucketCloudTeamScraper(client=None)
        with pytest.raises(ScraperError, match="Bitbucket Cloud client not configured"):
            scraper.scrape()

    def test_given_policy_scraper_without_workspace_when_scraping_then_discovers_workspaces_and_repos(
        self,
    ) -> None:
        mock_client = MagicMock(spec=BitbucketCloudClient)
        mock_client.get_workspaces.return_value = [{"slug": "ws1"}]
        mock_client.get_repositories.return_value = [
            {"slug": "repo1", "project": {"key": "P1"}}
        ]
        mock_client.get_branch_restrictions.return_value = [
            {"kind": "push", "pattern": "main"}
        ]
        scraper = BitbucketCloudPolicyScraper(client=mock_client)
        policies = scraper.scrape()
        assert len(policies) == 1
        assert policies[0].repository == "repo1"
        assert policies[0].project == "P1"

    def test_given_team_scraper_without_workspace_when_scraping_then_discovers_workspaces_and_repos(
        self,
    ) -> None:
        mock_client = MagicMock(spec=BitbucketCloudClient)
        mock_client.get_workspaces.return_value = [{"slug": "ws1"}]
        mock_client.get_workspace_group_permissions.return_value = [
            {"group": {"name": "admins"}, "permission": "admin"}
        ]
        mock_client.get_repositories.return_value = [
            {"slug": "repo1", "project": {"key": "P1"}}
        ]
        mock_client.get_repository_group_permissions.return_value = [
            {"group": {"name": "devs"}, "permission": "write"}
        ]
        scraper = BitbucketCloudTeamScraper(client=mock_client, include_repo_level=True)
        teams = scraper.scrape()
        assert len(teams) == 2

    def test_given_404_on_subsequent_requests_when_paginating_then_stops(self) -> None:
        mock_pool = MagicMock(spec=urllib3.PoolManager)
        mock_pool.request.side_effect = [
            _make_mock_response(
                200,
                b'{"values": [{"slug": "r1"}], "next": "https://api.bitbucket.org/2.0/next"}',
            ),
            _make_mock_response(404, b'{"type": "error"}'),
        ]
        client = BitbucketCloudClient(pool_manager=mock_pool)
        results = client._paginate("workspaces")
        assert len(results) == 1

    def test_given_repository_files_pagination_when_fetching_then_accumulates_files(
        self,
    ) -> None:
        mock_pool = MagicMock(spec=urllib3.PoolManager)
        page1 = {
            "values": [{"path": "pom.xml"}, "README.md"],
            "next": "https://api.bitbucket.org/2.0/next",
        }
        page2 = {"values": [{"path": "src/App.java"}]}
        mock_pool.request.side_effect = [
            _make_mock_response(200, json.dumps(page1).encode()),
            _make_mock_response(200, json.dumps(page2).encode()),
        ]
        client = BitbucketCloudClient(pool_manager=mock_pool)
        files = client.get_repository_files("ws", "repo", limit=10)
        assert len(files) == 3
        assert "pom.xml" in files
        assert "README.md" in files
        assert "src/App.java" in files

    def test_given_last_commit_when_fetching_then_returns_first_value(self) -> None:
        mock_pool = MagicMock(spec=urllib3.PoolManager)
        mock_pool.request.return_value = _make_mock_response(
            200, b'{"values": [{"hash": "abc1234", "date": "2026-11-01T00:00:00Z"}]}'
        )
        client = BitbucketCloudClient(pool_manager=mock_pool)
        commit = client.get_last_commit("ws", "repo")
        assert commit is not None
        assert commit["hash"] == "abc1234"

    def test_given_policy_scraper_with_repo_slug_when_scraping_then_targets_specific_repo(
        self,
    ) -> None:
        mock_client = MagicMock(spec=BitbucketCloudClient)
        mock_client.get_branch_restrictions.return_value = [
            {"kind": "push", "pattern": "main"}
        ]
        scraper = BitbucketCloudPolicyScraper(
            workspace="ws", repo_slug="specific-repo", client=mock_client
        )
        policies = scraper.scrape()
        mock_client.get_repositories.assert_not_called()
        assert len(policies) == 1
        assert policies[0].repository == "specific-repo"

    def test_given_team_scraper_with_repo_slug_when_scraping_then_targets_specific_repo(
        self,
    ) -> None:
        mock_client = MagicMock(spec=BitbucketCloudClient)
        mock_client.get_workspace_group_permissions.return_value = []
        mock_client.get_repository_group_permissions.return_value = [
            {"group": {"name": "special-team"}, "permission": "admin"}
        ]
        scraper = BitbucketCloudTeamScraper(
            workspace="ws",
            repo_slug="specific-repo",
            client=mock_client,
            include_repo_level=True,
        )
        teams = scraper.scrape()
        assert len(teams) == 1
        assert teams[0].team_name == "special-team"
        assert teams[0].repository == "specific-repo"

    def test_given_http_error_from_urllib3_when_requesting_then_raises_scraper_connection_error(
        self,
    ) -> None:
        mock_pool = MagicMock(spec=urllib3.PoolManager)
        mock_pool.request.side_effect = urllib3.exceptions.HTTPError("Low level error")
        client = BitbucketCloudClient(pool_manager=mock_pool)
        with pytest.raises(ScraperConnectionError, match="Network or transport error"):
            client.get_workspaces()

    def test_given_bad_status_code_when_requesting_then_raises_scraper_error(
        self,
    ) -> None:
        mock_pool = MagicMock(spec=urllib3.PoolManager)
        mock_pool.request.return_value = _make_mock_response(502, b"Bad Gateway")
        client = BitbucketCloudClient(pool_manager=mock_pool)
        with pytest.raises(ScraperError, match="HTTP 502"):
            client.get_workspaces()

    def test_given_url_with_existing_http_when_building_url_then_preserves(
        self,
    ) -> None:
        client = BitbucketCloudClient()
        url = client._build_url("https://custom.api.bitbucket.org/2.0/test")
        assert url == "https://custom.api.bitbucket.org/2.0/test"

    def test_given_clean_url_scheme_missing_when_initializing_then_adds_https(
        self,
    ) -> None:
        client = BitbucketCloudClient(base_url="api.bitbucket.org/2.0")
        assert client.base_url == "https://api.bitbucket.org/2.0"

    def test_given_policy_scraper_with_unrecognized_kind_when_mapping_then_returns_none(
        self,
    ) -> None:
        scraper = BitbucketCloudPolicyScraper()
        res = scraper._map_restriction_to_policy(
            {"kind": "unsupported_rule_type"}, "ws", "proj", "repo"
        )
        assert res is None

    def test_given_policy_scraper_ref_canonicalization_when_given_refs_then_keeps_as_is(
        self,
    ) -> None:
        scraper = BitbucketCloudPolicyScraper()
        assert scraper._canonicalize_ref("refs/heads/feature") == "refs/heads/feature"
        assert scraper._canonicalize_ref("*") == "*"
        assert scraper._canonicalize_ref("") == "*"

    def test_given_team_permission_without_group_or_permission_when_scraping_then_skips(
        self,
    ) -> None:
        mock_client = MagicMock(spec=BitbucketCloudClient)
        mock_client.get_workspace_group_permissions.return_value = [
            {"group": {}, "permission": "read"},
            {"group": {"name": "admins"}, "permission": ""},
        ]
        scraper = BitbucketCloudTeamScraper(
            workspace="ws", client=mock_client, include_repo_level=False
        )
        teams = scraper.scrape()
        assert len(teams) == 0

    def test_given_client_get_workspace_projects_when_called_then_paginates_projects_endpoint(
        self,
    ) -> None:
        mock_pool = MagicMock(spec=urllib3.PoolManager)
        mock_pool.request.return_value = _make_mock_response(
            200, b'{"values": [{"key": "P1"}]}'
        )
        client = BitbucketCloudClient(pool_manager=mock_pool)
        projects = client.get_workspace_projects("my-ws")
        assert len(projects) == 1
        assert projects[0]["key"] == "P1"

    def test_given_policy_scraper_with_error_on_restrictions_when_scraping_then_continues(
        self,
    ) -> None:
        mock_client = MagicMock(spec=BitbucketCloudClient)
        mock_client.get_repositories.return_value = [
            {"slug": "repo1", "project": {"key": "P1"}}
        ]
        mock_client.get_branch_restrictions.side_effect = ScraperError("403 Forbidden")
        scraper = BitbucketCloudPolicyScraper(workspace="ws", client=mock_client)
        assert scraper.scrape() == []

    def test_given_team_scraper_with_error_on_groups_when_scraping_then_continues(
        self,
    ) -> None:
        mock_client = MagicMock(spec=BitbucketCloudClient)
        mock_client.get_workspace_group_permissions.side_effect = ScraperError(
            "403 Forbidden"
        )
        mock_client.get_repositories.return_value = [
            {"slug": "repo1", "project": {"key": "P1"}}
        ]
        mock_client.get_repository_group_permissions.side_effect = ScraperError(
            "403 Forbidden"
        )
        scraper = BitbucketCloudTeamScraper(
            workspace="ws", client=mock_client, include_repo_level=True
        )
        assert scraper.scrape() == []

    def test_given_repo_scraper_last_commit_or_files_error_when_scraping_then_tolerates(
        self,
    ) -> None:
        mock_client = MagicMock(spec=BitbucketCloudClient)
        mock_client.get_repositories.return_value = [
            {"slug": "repo1", "project": {"key": "P1"}}
        ]
        mock_client.get_last_commit.side_effect = ScraperError("Failed")
        mock_client.get_repository_files.side_effect = ScraperError("Failed")
        scraper = BitbucketCloudRepositoryScraper(workspace="ws", client=mock_client)
        repos = scraper.scrape()
        assert len(repos) == 1
        assert repos[0].last_commit is None
        assert repos[0].stacks == [Stack.UNKNOWN]

    def test_given_policy_scraper_with_repo_targets_empty_slug_when_scraping_then_skips(
        self,
    ) -> None:
        mock_client = MagicMock(spec=BitbucketCloudClient)
        mock_client.get_repositories.return_value = [{"name": ""}]
        scraper = BitbucketCloudPolicyScraper(workspace="ws", client=mock_client)
        assert scraper.scrape() == []

    def test_given_team_scraper_with_repo_targets_empty_slug_when_scraping_then_skips(
        self,
    ) -> None:
        mock_client = MagicMock(spec=BitbucketCloudClient)
        mock_client.get_workspace_group_permissions.return_value = []
        mock_client.get_repositories.return_value = [{"name": ""}]
        scraper = BitbucketCloudTeamScraper(
            workspace="ws", client=mock_client, include_repo_level=True
        )
        assert scraper.scrape() == []

    def test_given_repo_scraper_with_empty_slug_in_repo_when_scraping_then_skips(
        self,
    ) -> None:
        mock_client = MagicMock(spec=BitbucketCloudClient)
        mock_client.get_repositories.return_value = [{"name": ""}]
        scraper = BitbucketCloudRepositoryScraper(workspace="ws", client=mock_client)
        assert scraper.scrape() == []

    def test_given_repo_scraper_without_mainbranch_or_client_when_resolving_default_branch_then_none(
        self,
    ) -> None:
        scraper = BitbucketCloudRepositoryScraper(client=None)
        assert scraper._resolve_default_branch({}, "ws", "slug") is None

    def test_given_repo_scraper_with_non_dict_clone_link_when_resolving_then_skips(
        self,
    ) -> None:
        scraper = BitbucketCloudRepositoryScraper()
        repo = {
            "links": {
                "clone": ["not-a-dict", {"name": "http", "href": "https://url.git"}]
            }
        }
        assert scraper._resolve_clone_url(repo, "ws", "slug") == "https://url.git"

    def test_given_extract_bypasses_with_string_and_invalid_when_parsing_then_extracts(
        self,
    ) -> None:
        scraper = BitbucketCloudPolicyScraper()
        bypasses = scraper._extract_bypasses(
            {"groups": ["Admin Group", {"name": "Dev Team"}, None]}
        )
        assert "admin-group" in bypasses
        assert "dev-team" in bypasses
