"""Integration tests verifying live SVN repository scraping against Docker container."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from gh_migration_inventory_manager import (
    BasicAuthenticator,
    Inventory,
    MigrationStatus,
    Repository,
    Row,
    ScraperAuthenticationError,
    ScraperError,
    Stack,
    SvnLayoutType,
    SvnRepositoryScraper,
)


def test_given_live_svn_standard_repo_when_scraping_then_discovers_trunk_repository(
    svn_server: dict[str, Any], svn_auth: BasicAuthenticator
) -> None:
    """Verify live scraping of standard SVN layout (trunk, branches, tags)."""
    repo_url = f"{svn_server['url']}/standard-app"

    scraper = SvnRepositoryScraper(
        url=repo_url,
        auth=svn_auth,
        organization="org-corp",
        project="platform-proj",
        layout=SvnLayoutType.STANDARD,
    )

    repos = scraper.scrape()
    assert len(repos) == 1
    repo = repos[0]

    assert repo.name == "standard-app"
    assert repo.organization == "org-corp"
    assert repo.project == "platform-proj"
    assert repo.path == "trunk"
    assert repo.url == f"{repo_url}/trunk"
    assert repo.default_branch == "trunk"
    assert repo.last_commit is not None
    assert repo.stacks == [Stack.JAVA]
    assert repo.metadata["layout"] == "standard"
    assert repo.qualified_name == "org-corp/platform-proj/standard-app"


def test_given_live_svn_multi_project_repo_when_scraping_then_discovers_all_subproject_repositories(
    svn_server: dict[str, Any], svn_auth: BasicAuthenticator
) -> None:
    """Verify live scraping of multi-project SVN repository layout."""
    repo_url = f"{svn_server['url']}/multi-app"

    scraper = SvnRepositoryScraper(
        url=repo_url,
        auth=svn_auth,
        organization="org-corp",
        project="cloud-suite",
        layout=SvnLayoutType.MULTI_PROJECT,
    )

    repos = scraper.scrape()
    assert len(repos) == 2

    auth_service = next(r for r in repos if r.name == "auth-service")
    assert auth_service.organization == "org-corp"
    assert auth_service.project == "cloud-suite"
    assert auth_service.path == "auth-service/trunk"
    assert auth_service.default_branch == "trunk"
    assert auth_service.stacks == [Stack.PYTHON]
    assert auth_service.qualified_name == "org-corp/cloud-suite/auth-service"

    billing_service = next(r for r in repos if r.name == "billing-service")
    assert billing_service.organization == "org-corp"
    assert billing_service.project == "cloud-suite"
    assert billing_service.path == "billing-service/trunk"
    assert billing_service.default_branch == "trunk"
    assert billing_service.stacks == [Stack.TYPESCRIPT]
    assert billing_service.qualified_name == "org-corp/cloud-suite/billing-service"


def test_given_live_svn_flat_repo_when_scraping_then_discovers_flat_repository(
    svn_server: dict[str, Any], svn_auth: BasicAuthenticator
) -> None:
    """Verify live scraping of flat SVN repository layout."""
    repo_url = f"{svn_server['url']}/flat-app"

    scraper = SvnRepositoryScraper(
        url=repo_url,
        auth=svn_auth,
        organization="org-corp",
        layout=SvnLayoutType.FLAT,
    )

    repos = scraper.scrape()
    assert len(repos) == 1
    repo = repos[0]

    assert repo.name == "flat-app"
    assert repo.path == ""
    assert repo.default_branch is None
    assert repo.last_commit is not None
    assert Stack.BASH in repo.stacks
    assert Stack.TERRAFORM in repo.stacks
    assert repo.metadata["layout"] == "flat"


def test_given_live_svn_server_root_when_scraping_collection_then_discovers_all_nested_repositories(
    svn_server: dict[str, Any], svn_auth: BasicAuthenticator
) -> None:
    """Verify live scraping of SVN collection parent discovering repositories."""
    collection_url = svn_server["url"]

    scraper = SvnRepositoryScraper(
        url=collection_url,
        auth=svn_auth,
        layout=SvnLayoutType.COLLECTION,
    )

    repos = scraper.scrape()
    assert len(repos) >= 3
    discovered_names = {r.name for r in repos}
    assert "standard-app" in discovered_names
    assert "flat-app" in discovered_names


def test_given_invalid_credentials_when_scraping_live_svn_then_raises_scraper_authentication_error(
    svn_server: dict[str, Any],
) -> None:
    """Verify that invalid credentials raise ScraperAuthenticationError."""
    invalid_auth = BasicAuthenticator(svn_server["username"], "incorrect_password")
    repo_url = f"{svn_server['url']}/standard-app"

    scraper = SvnRepositoryScraper(url=repo_url, auth=invalid_auth)
    with pytest.raises(ScraperAuthenticationError):
        scraper.scrape()


def test_given_nonexistent_svn_path_when_scraping_live_svn_then_raises_scraper_error(
    svn_server: dict[str, Any], svn_auth: BasicAuthenticator
) -> None:
    """Verify scraping non-existent repository raises ScraperError."""
    invalid_repo_url = f"{svn_server['url']}/non-existent-repository-xyz"

    scraper = SvnRepositoryScraper(url=invalid_repo_url, auth=svn_auth)
    with pytest.raises(ScraperError):
        scraper.scrape()


def test_given_scraped_live_svn_repos_when_syncing_to_inventory_then_registers_and_tracks_progress(
    svn_server: dict[str, Any], svn_auth: BasicAuthenticator
) -> None:
    """Verify scraped SVN repositories bridge into Inventory tracking."""
    repo_url = f"{svn_server['url']}/standard-app"

    scraper = SvnRepositoryScraper(
        url=repo_url,
        auth=svn_auth,
        organization="corp",
        project="core-banking",
    )
    discovered_repos = scraper.scrape()
    assert len(discovered_repos) == 1
    scraped_repo: Repository = discovered_repos[0]

    # Mock spreadsheet to record inventory update
    mock_spreadsheet = MagicMock()
    mock_spreadsheet.find_by.return_value = []
    mock_spreadsheet.add.side_effect = lambda row: Row(
        row_index=5,
        data=dict(row.data),
    )

    inventory = Inventory(spreadsheet=mock_spreadsheet)
    record = inventory.update_progress(
        repository_name=scraped_repo.name,
        status=MigrationStatus.ACTIVE_IN_BOTH,
        project=scraped_repo.project,
    )

    assert record.name == "standard-app"
    assert record.project == "core-banking"
    assert record.migration_status == MigrationStatus.ACTIVE_IN_BOTH
    assert record.gh_migratation is not None
    assert mock_spreadsheet.add.called
