"""Unit tests for BitbucketClient repository endpoints, BitbucketRepositoryScraper, and BitbucketScraperProvider."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock

import pytest
import urllib3

from gh_migration_inventory_manager.bitbucket_scrap import (
    BitbucketClient,
    BitbucketRepositoryScraper,
    BitbucketScraperProvider,
    BitbucketTransport,
)
from gh_migration_inventory_manager.scrap import (
    BasicAuthenticator,
    ScraperAuthenticationError,
    ScraperConnectionError,
    ScraperError,
    Stack,
    TokenAuthenticator,
)


def _make_response(status: int, data: object) -> urllib3.BaseHTTPResponse:
    """Helper creating a mocked urllib3 response."""
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


def test_given_valid_projects_and_repos_when_scraping_then_returns_canonical_repository_instances() -> (
    None
):
    """Test BitbucketRepositoryScraper discovers projects and repos and maps all domain fields."""
    mock_client = MagicMock(spec=BitbucketTransport)
    mock_client.get_projects.return_value = [
        {"key": "CORE", "name": "Core Platform"},
    ]
    mock_client.get_repositories.return_value = [
        {
            "id": 101,
            "slug": "auth-service",
            "name": "auth-service",
            "description": "Authentication microservice",
            "archived": False,
            "public": True,
            "scmId": "git",
            "links": {
                "clone": [
                    {
                        "name": "http",
                        "href": "https://bitbucket.corp.local/scm/core/auth-service.git",
                    },
                    {
                        "name": "ssh",
                        "href": "ssh://git@bitbucket.corp.local:7999/core/auth-service.git",
                    },
                ]
            },
        }
    ]
    mock_client.get_default_branch.return_value = "main"
    mock_client.get_last_commit.return_value = {
        "id": "commit-123",
        "committerTimestamp": 1609459200000,
    }
    mock_client.get_repository_files.return_value = [
        "pom.xml",
        "src/main/java/App.java",
    ]

    scraper = BitbucketRepositoryScraper(
        base_url="https://bitbucket.corp.local",
        client=mock_client,
    )
    repositories = scraper.scrape()

    assert len(repositories) == 1
    repo = repositories[0]
    assert repo.name == "auth-service"
    assert repo.project == "CORE"
    assert repo.organization == "bitbucket.corp.local"
    assert repo.url == "https://bitbucket.corp.local/scm/core/auth-service.git"
    assert repo.default_branch == "main"
    assert repo.last_commit == datetime(2021, 1, 1, 0, 0, tzinfo=timezone.utc)
    assert repo.stacks == [Stack.JAVA]
    assert repo.metadata["id"] == 101
    assert repo.metadata["public"] is True
    assert repo.metadata["archived"] is False
    assert repo.metadata["project_name"] == "Core Platform"


@pytest.mark.parametrize(
    ("files", "expected_stacks"),
    [
        (["pom.xml", "src/Main.java"], [Stack.JAVA]),
        (["build.gradle", "gradlew"], [Stack.JAVA]),
        (["requirements.txt", "main.py"], [Stack.PYTHON]),
        (["pyproject.toml"], [Stack.PYTHON]),
        (["package.json", "index.ts"], [Stack.TYPESCRIPT]),
        (["tsconfig.json", "App.tsx"], [Stack.TYPESCRIPT]),
        (["App.csproj", "Solution.sln"], [Stack.C_SHARP, Stack.DOTNET_CORE]),
        (["main.tf", "variables.tfvars"], [Stack.TERRAFORM]),
        (["terragrunt.hcl"], [Stack.TERRAFORM]),
        (["Dockerfile", "deploy.sh"], [Stack.BASH]),
        (["Makefile"], [Stack.BASH]),
        (
            ["pom.xml", "requirements.txt", "Dockerfile"],
            [Stack.BASH, Stack.JAVA, Stack.PYTHON],
        ),
        (["unknown.txt", "notes.md"], [Stack.UNKNOWN]),
        ([], [Stack.UNKNOWN]),
    ],
)
def test_given_root_manifest_files_when_scraping_then_detects_stacks_accurately(
    files: list[str], expected_stacks: list[Stack]
) -> None:
    """Test BitbucketRepositoryScraper detects technology stack signatures from manifest files."""
    mock_client = MagicMock(spec=BitbucketTransport)
    mock_client.get_projects.return_value = [{"key": "PRJ", "name": "Project"}]
    mock_client.get_repositories.return_value = [
        {"slug": "test-repo", "name": "test-repo", "archived": False}
    ]
    mock_client.get_default_branch.return_value = "main"
    mock_client.get_last_commit.return_value = None
    mock_client.get_repository_files.return_value = files

    scraper = BitbucketRepositoryScraper(
        base_url="https://bitbucket.corp.local",
        client=mock_client,
    )
    repositories = scraper.scrape()

    assert len(repositories) == 1
    assert repositories[0].stacks == expected_stacks


def test_given_single_project_filter_when_scraping_then_scrapes_only_specified_project() -> (
    None
):
    """Test providing project_key restricts scraping to that project without querying project list."""
    mock_client = MagicMock(spec=BitbucketTransport)
    mock_client.get_repositories.return_value = [
        {"slug": "pay-gateway", "name": "pay-gateway", "archived": False}
    ]
    mock_client.get_default_branch.return_value = "master"
    mock_client.get_last_commit.return_value = None
    mock_client.get_repository_files.return_value = []

    scraper = BitbucketRepositoryScraper(
        base_url="https://bitbucket.corp.local",
        project_key="PAY",
        client=mock_client,
    )
    repositories = scraper.scrape()

    assert len(repositories) == 1
    assert repositories[0].project == "PAY"
    mock_client.get_projects.assert_not_called()
    mock_client.get_repositories.assert_called_once_with("PAY", limit=100)


# ==============================================================================
# Scenario 2: Internal Behavior & State
# ==============================================================================


def test_given_multiple_pages_of_projects_and_repos_when_scraping_then_paginates_and_aggregates_all() -> (
    None
):
    """Test BitbucketClient handles windowed pagination across multiple project and repository pages."""
    mock_pool = MagicMock(spec=urllib3.PoolManager)

    # Page 1 of projects
    proj_p1 = {
        "values": [{"key": "PRJ1", "name": "Project 1"}],
        "isLastPage": False,
        "nextPageStart": 1,
    }
    # Page 2 of projects
    proj_p2 = {"values": [{"key": "PRJ2", "name": "Project 2"}], "isLastPage": True}

    # Repos for PRJ1 (2 pages)
    repo_p1 = {
        "values": [{"slug": "repo1", "name": "repo1"}],
        "isLastPage": False,
        "nextPageStart": 1,
    }
    repo_p2 = {"values": [{"slug": "repo2", "name": "repo2"}], "isLastPage": True}

    # Repos for PRJ2 (1 page)
    repo_p3 = {"values": [{"slug": "repo3", "name": "repo3"}], "isLastPage": True}

    def pool_side_effect(
        method: str, url: str, **kwargs: Any
    ) -> urllib3.BaseHTTPResponse:
        fields = kwargs.get("fields", {})
        start = fields.get("start", 0)
        if "projects/PRJ1/repos" in url:
            return _make_response(200, repo_p1 if start == 0 else repo_p2)
        if "projects/PRJ2/repos" in url:
            return _make_response(200, repo_p3)
        if "rest/api/1.0/projects" in url:
            return _make_response(200, proj_p1 if start == 0 else proj_p2)
        return _make_response(404, {})

    mock_pool.request.side_effect = pool_side_effect

    client = BitbucketClient(
        base_url="https://bitbucket.corp.local",
        pool_manager=mock_pool,
    )

    projects = client.get_projects()
    assert len(projects) == 2
    assert [p["key"] for p in projects] == ["PRJ1", "PRJ2"]

    repos_prj1 = client.get_repositories("PRJ1")
    assert len(repos_prj1) == 2
    assert [r["slug"] for r in repos_prj1] == ["repo1", "repo2"]

    repos_prj2 = client.get_repositories("PRJ2")
    assert len(repos_prj2) == 1
    assert [r["slug"] for r in repos_prj2] == ["repo3"]


def test_given_archived_repositories_and_include_archived_false_when_scraping_then_excludes_archived() -> (
    None
):
    """Test BitbucketRepositoryScraper excludes archived repositories by default."""
    mock_client = MagicMock(spec=BitbucketTransport)
    mock_client.get_projects.return_value = [{"key": "CORE"}]
    mock_client.get_repositories.return_value = [
        {"slug": "active-repo", "archived": False},
        {"slug": "archived-repo", "archived": True},
    ]
    mock_client.get_default_branch.return_value = "main"
    mock_client.get_last_commit.return_value = None
    mock_client.get_repository_files.return_value = []

    scraper = BitbucketRepositoryScraper(
        base_url="https://bitbucket.corp.local",
        client=mock_client,
        include_archived=False,
    )
    repositories = scraper.scrape()

    assert len(repositories) == 1
    assert repositories[0].name == "active-repo"


def test_given_archived_repositories_and_include_archived_true_when_scraping_then_includes_archived() -> (
    None
):
    """Test BitbucketRepositoryScraper includes archived repositories when include_archived is True."""
    mock_client = MagicMock(spec=BitbucketTransport)
    mock_client.get_projects.return_value = [{"key": "CORE"}]
    mock_client.get_repositories.return_value = [
        {"slug": "active-repo", "archived": False},
        {"slug": "archived-repo", "archived": True},
    ]
    mock_client.get_default_branch.return_value = "main"
    mock_client.get_last_commit.return_value = None
    mock_client.get_repository_files.return_value = []

    scraper = BitbucketRepositoryScraper(
        base_url="https://bitbucket.corp.local",
        client=mock_client,
        include_archived=True,
    )
    repositories = scraper.scrape()

    assert len(repositories) == 2
    slugs = [r.name for r in repositories]
    assert "active-repo" in slugs
    assert "archived-repo" in slugs


def test_given_empty_repository_without_commits_or_branches_when_scraping_then_handles_gracefully_with_none_values() -> (
    None
):
    """Test BitbucketRepositoryScraper handles empty repositories where branch/commit queries 404."""
    mock_client = MagicMock(spec=BitbucketTransport)
    mock_client.get_projects.return_value = [{"key": "CORE"}]
    mock_client.get_repositories.return_value = [
        {"slug": "empty-repo", "archived": False}
    ]
    mock_client.get_default_branch.return_value = None
    mock_client.get_last_commit.return_value = None
    mock_client.get_repository_files.return_value = []

    scraper = BitbucketRepositoryScraper(
        base_url="https://bitbucket.corp.local",
        client=mock_client,
    )
    repositories = scraper.scrape()

    assert len(repositories) == 1
    repo = repositories[0]
    assert repo.name == "empty-repo"
    assert repo.default_branch is None
    assert repo.last_commit is None
    assert repo.stacks == [Stack.UNKNOWN]


def test_given_various_clone_links_when_scraping_then_prefers_http_clone_url_and_falls_back_to_ssh() -> (
    None
):
    """Test clone URL resolution prioritizes HTTP over SSH and falls back to constructed URL."""
    mock_client = MagicMock(spec=BitbucketTransport)
    mock_client.get_projects.return_value = [{"key": "CORE"}]
    mock_client.get_repositories.return_value = [
        # 1. Both HTTP and SSH -> pick HTTP
        {
            "slug": "repo-both",
            "links": {
                "clone": [
                    {"name": "ssh", "href": "ssh://git@bb.local/core/repo-both.git"},
                    {"name": "http", "href": "https://bb.local/scm/core/repo-both.git"},
                ]
            },
        },
        # 2. Only SSH -> pick SSH
        {
            "slug": "repo-ssh",
            "links": {
                "clone": [
                    {"name": "ssh", "href": "ssh://git@bb.local/core/repo-ssh.git"},
                ]
            },
        },
        # 3. No clone links -> fallback constructed URL
        {
            "slug": "repo-none",
            "links": {},
        },
    ]
    mock_client.get_default_branch.return_value = "main"
    mock_client.get_last_commit.return_value = None
    mock_client.get_repository_files.return_value = []

    scraper = BitbucketRepositoryScraper(
        base_url="https://bb.local",
        client=mock_client,
        organization="custom-org",
    )
    repositories = scraper.scrape()

    assert len(repositories) == 3
    assert repositories[0].url == "https://bb.local/scm/core/repo-both.git"
    assert repositories[1].url == "ssh://git@bb.local/core/repo-ssh.git"
    assert repositories[2].url == "https://bb.local/scm/core/repo-none.git"
    assert repositories[0].organization == "custom-org"


def test_given_bitbucket_provider_when_configuring_parser_then_adds_expected_arguments() -> (
    None
):
    """Test BitbucketScraperProvider configures all necessary CLI options."""
    provider = BitbucketScraperProvider()
    assert provider.platform_name == "bitbucket"
    assert provider.default_sheet_name == "Inventario"

    parser = argparse.ArgumentParser()
    provider.configure_parser(parser)

    # Parse sample CLI args
    args = parser.parse_args(
        [
            "--base-url",
            "https://bitbucket.corp.local",
            "--project",
            "CORE",
            "--token",
            "my-pat-token",
            "--page-size",
            "50",
            "--include-archived",
            "--organization",
            "MyOrg",
        ]
    )
    assert args.base_url == "https://bitbucket.corp.local"
    assert args.project_key == "CORE"
    assert args.token == "my-pat-token"
    assert args.page_size == 50
    assert args.include_archived is True
    assert args.organization == "MyOrg"


def test_given_cli_args_when_creating_scraper_then_builds_bitbucket_repository_scraper_with_auth() -> (
    None
):
    """Test BitbucketScraperProvider instantiates BitbucketRepositoryScraper with resolved credentials."""
    provider = BitbucketScraperProvider()

    # 1. Token auth
    token_args = argparse.Namespace(
        base_url="https://bitbucket.corp.local",
        project_key="CORE",
        token="pat-xyz",
        username=None,
        password=None,
        page_size=25,
        include_archived=True,
        organization="MyOrg",
    )
    scraper = provider.create_scraper(token_args)
    assert isinstance(scraper, BitbucketRepositoryScraper)
    assert scraper.base_url == "https://bitbucket.corp.local"
    assert scraper.project_key == "CORE"
    assert scraper.page_size == 25
    assert scraper.include_archived is True
    assert scraper.organization == "MyOrg"
    assert isinstance(scraper.client, BitbucketClient)
    assert isinstance(scraper.client.auth, TokenAuthenticator)

    # 2. Basic auth
    basic_args = argparse.Namespace(
        url="https://bitbucket.corp.local",
        base_url=None,
        project_key=None,
        token=None,
        username="bb-user",
        password="bb-password",
        page_size=100,
        include_archived=False,
        organization=None,
    )
    scraper2 = provider.create_scraper(basic_args)
    assert isinstance(scraper2.client, BitbucketClient)
    assert isinstance(scraper2.client.auth, BasicAuthenticator)


def test_given_cloud_workspace_when_fetching_repositories_then_uses_cloud_endpoints() -> (
    None
):
    """Test BitbucketClient adapts paths and extracts metadata when connecting to Bitbucket Cloud."""
    mock_pool = MagicMock(spec=urllib3.PoolManager)

    def pool_side_effect(
        method: str, url: str, **kwargs: Any
    ) -> urllib3.BaseHTTPResponse:
        if "2.0/workspaces" in url:
            return _make_response(200, {"values": [{"slug": "my-workspace"}]})
        if "2.0/repositories/my-workspace/cloud-repo/commits" in url:
            return _make_response(
                200,
                {"values": [{"date": "2022-05-15T10:30:00+00:00", "hash": "abc1234"}]},
            )
        if "2.0/repositories/my-workspace/cloud-repo/src" in url:
            return _make_response(
                200,
                {"values": [{"path": "requirements.txt"}, {"path": "main.py"}]},
            )
        if "2.0/repositories/my-workspace/cloud-repo" in url:
            return _make_response(
                200,
                {"mainbranch": {"name": "main"}, "slug": "cloud-repo"},
            )
        if "2.0/repositories/my-workspace" in url:
            return _make_response(
                200,
                {
                    "values": [
                        {
                            "slug": "cloud-repo",
                            "name": "cloud-repo",
                            "mainbranch": {"name": "main"},
                        }
                    ]
                },
            )
        return _make_response(404, {})

    mock_pool.request.side_effect = pool_side_effect

    client = BitbucketClient(
        base_url="https://api.bitbucket.org",
        pool_manager=mock_pool,
    )
    assert client.is_cloud is True

    workspaces = client.get_projects()
    assert len(workspaces) == 1
    assert workspaces[0]["slug"] == "my-workspace"

    repos = client.get_repositories("my-workspace")
    assert len(repos) == 1
    assert repos[0]["slug"] == "cloud-repo"

    default_branch = client.get_default_branch("my-workspace", "cloud-repo")
    assert default_branch == "main"

    commit = client.get_last_commit("my-workspace", "cloud-repo")
    assert commit is not None
    assert commit["hash"] == "abc1234"

    files = client.get_repository_files("my-workspace", "cloud-repo")
    assert "requirements.txt" in files


# ==============================================================================
# Scenario 3: Dependency Failure Handling
# ==============================================================================


def test_given_http_401_from_bitbucket_when_scraping_then_raises_scraper_authentication_error() -> (
    None
):
    """Test HTTP 401 Unauthorized raises ScraperAuthenticationError."""
    mock_pool = MagicMock(spec=urllib3.PoolManager)
    mock_pool.request.return_value = _make_response(401, {"message": "Unauthorized"})

    client = BitbucketClient(
        base_url="https://bitbucket.corp.local",
        pool_manager=mock_pool,
    )
    scraper = BitbucketRepositoryScraper(
        base_url="https://bitbucket.corp.local",
        client=client,
    )

    with pytest.raises(ScraperAuthenticationError, match="authentication failed"):
        scraper.scrape()


def test_given_http_403_from_bitbucket_when_scraping_then_raises_scraper_authentication_error() -> (
    None
):
    """Test HTTP 403 Forbidden raises ScraperAuthenticationError."""
    mock_pool = MagicMock(spec=urllib3.PoolManager)
    mock_pool.request.return_value = _make_response(403, {"message": "Forbidden"})

    client = BitbucketClient(
        base_url="https://bitbucket.corp.local",
        pool_manager=mock_pool,
    )

    with pytest.raises(ScraperAuthenticationError):
        client.get_repositories("CORE")


def test_given_connection_timeout_from_bitbucket_when_scraping_then_raises_scraper_connection_error() -> (
    None
):
    """Test connection timeout or network failure raises ScraperConnectionError."""
    mock_pool = MagicMock(spec=urllib3.PoolManager)
    mock_pool.request.side_effect = urllib3.exceptions.ConnectTimeoutError(
        "Connection timed out"
    )

    client = BitbucketClient(
        base_url="https://bitbucket.corp.local",
        pool_manager=mock_pool,
    )

    with pytest.raises(ScraperConnectionError, match="Failed to connect"):
        client.get_projects()


def test_given_max_retries_exceeded_when_calling_api_then_raises_scraper_connection_error() -> (
    None
):
    """Test BitbucketClient raises ScraperConnectionError when MaxRetryError occurs."""
    mock_pool = MagicMock(spec=urllib3.PoolManager)
    mock_pool.request.side_effect = urllib3.exceptions.MaxRetryError(
        pool=MagicMock(),
        url="https://bitbucket.corp.local/rest/api/1.0/projects",
        reason=Exception("503 Service Unavailable"),
    )

    client = BitbucketClient(
        base_url="https://bitbucket.corp.local",
        pool_manager=mock_pool,
    )

    with pytest.raises(ScraperConnectionError, match="after retries"):
        client.get_projects()


# ==============================================================================
# Scenario 4: Invalid Input Handling
# ==============================================================================


@pytest.mark.parametrize("invalid_url", ["", "   ", " \t "])
def test_given_empty_base_url_when_initializing_scraper_then_raises_value_error(
    invalid_url: str,
) -> None:
    """Test empty or whitespace base_url raises ValueError."""
    with pytest.raises(ValueError, match="Bitbucket base URL cannot be empty"):
        BitbucketRepositoryScraper(base_url=invalid_url)


def test_given_scraper_without_client_when_scraping_then_raises_scraper_error() -> None:
    """Test scraping without injected client raises ScraperError."""
    scraper = BitbucketRepositoryScraper(
        base_url="https://bitbucket.corp.local",
        client=None,
    )
    with pytest.raises(ScraperError, match="Bitbucket client not configured"):
        scraper.scrape()


@pytest.mark.parametrize("invalid_key", ["", "   ", None])
def test_given_empty_project_key_when_fetching_repositories_then_raises_value_error(
    invalid_key: Any,
) -> None:
    """Test empty project key raises ValueError."""
    client = BitbucketClient(base_url="https://bitbucket.corp.local")
    with pytest.raises(ValueError, match="Project key cannot be empty"):
        client.get_repositories(invalid_key)


@pytest.mark.parametrize("invalid_slug", ["", "   ", None])
def test_given_empty_repo_slug_when_fetching_details_then_raises_value_error(
    invalid_slug: Any,
) -> None:
    """Test empty repo slug raises ValueError for details methods."""
    client = BitbucketClient(base_url="https://bitbucket.corp.local")
    with pytest.raises(ValueError, match="Repository slug cannot be empty"):
        client.get_default_branch("CORE", invalid_slug)
    with pytest.raises(ValueError, match="Repository slug cannot be empty"):
        client.get_last_commit("CORE", invalid_slug)
    with pytest.raises(ValueError, match="Repository slug cannot be empty"):
        client.get_repository_files("CORE", invalid_slug)
