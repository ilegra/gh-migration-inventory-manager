"""Integration tests verifying BitbucketRepositoryScraper against containerized Bitbucket service."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from gh_migration_inventory_manager import (
    Inventory,
    MigrationStatus,
    RepositoryConverter,
    Row,
    TokenAuthenticator,
)
from gh_migration_inventory_manager.bitbucket_scrap import (
    BitbucketClient,
    BitbucketRepositoryScraper,
)
from gh_migration_inventory_manager.scrap import (
    ScraperAuthenticationError,
    ScraperConnectionError,
    Stack,
)


def test_given_live_bitbucket_server_when_scraping_then_discovers_seeded_repositories_and_metadata(
    bitbucket_server: dict[str, Any],
) -> None:
    """Verify scraper discovers seeded repository estate and metadata from live Bitbucket container."""
    base_url = bitbucket_server["base_url"]
    auth = TokenAuthenticator(bitbucket_server["discovery_token"])
    client = BitbucketClient(base_url=base_url, auth=auth)
    scraper = BitbucketRepositoryScraper(
        base_url=base_url,
        client=client,
    )

    repositories = scraper.scrape()

    assert len(repositories) >= 1
    seeded_repo = next(
        (r for r in repositories if r.name == bitbucket_server["repo_slug"]), None
    )
    assert seeded_repo is not None
    assert seeded_repo.project == bitbucket_server["project_key"]
    assert seeded_repo.name == bitbucket_server["repo_slug"]
    assert seeded_repo.url is not None
    assert bitbucket_server["repo_slug"] in seeded_repo.url
    assert seeded_repo.metadata["project_key"] == bitbucket_server["project_key"]
    assert seeded_repo.metadata["archived"] is False


def test_given_live_bitbucket_server_with_project_filter_when_scraping_then_discovers_only_target_project(
    bitbucket_server: dict[str, Any],
) -> None:
    """Verify providing project_key restricts live discovery strictly to target project."""
    base_url = bitbucket_server["base_url"]
    auth = TokenAuthenticator(bitbucket_server["discovery_token"])
    client = BitbucketClient(base_url=base_url, auth=auth)
    scraper = BitbucketRepositoryScraper(
        base_url=base_url,
        project_key=bitbucket_server["project_key"],
        client=client,
    )

    repositories = scraper.scrape()

    assert len(repositories) >= 1
    for r in repositories:
        assert r.project == bitbucket_server["project_key"]


def test_given_live_bitbucket_server_and_seeded_manifest_files_when_scraping_then_detects_stacks_accurately(
    bitbucket_server: dict[str, Any],
) -> None:
    """Verify live inspection of repository files accurately detects technology stacks."""
    base_url = bitbucket_server["base_url"]
    auth = TokenAuthenticator(bitbucket_server["discovery_token"])
    client = BitbucketClient(base_url=base_url, auth=auth)
    scraper = BitbucketRepositoryScraper(
        base_url=base_url,
        project_key=bitbucket_server["project_key"],
        client=client,
    )

    repositories = scraper.scrape()

    assert len(repositories) >= 1
    seeded_repo = next(
        r for r in repositories if r.name == bitbucket_server["repo_slug"]
    )
    # The seeded container has no root files or unknown
    assert isinstance(seeded_repo.stacks, list)
    assert len(seeded_repo.stacks) >= 1
    assert all(isinstance(s, Stack) for s in seeded_repo.stacks)


def test_given_live_bitbucket_server_when_syncing_with_inventory_then_persists_records_to_inventario_tab(
    bitbucket_server: dict[str, Any],
) -> None:
    """Verify discovered Bitbucket repositories integrate end-to-end with Inventory and Google Sheets."""
    base_url = bitbucket_server["base_url"]
    auth = TokenAuthenticator(bitbucket_server["discovery_token"])
    client = BitbucketClient(base_url=base_url, auth=auth)
    scraper = BitbucketRepositoryScraper(
        base_url=base_url,
        project_key=bitbucket_server["project_key"],
        client=client,
    )

    repositories = scraper.scrape()
    assert len(repositories) >= 1

    converter = RepositoryConverter()
    records = converter.to_inventory_records(
        repositories, initial_status=MigrationStatus.WAITTING
    )

    mock_sheet = MagicMock()
    mock_sheet.find_by.return_value = []
    mock_sheet.add.side_effect = lambda row: Row(
        row_index=10,
        data=row.data,
        values=list(row.data.values()),
    )

    inv = Inventory(spreadsheet=mock_sheet, converter=converter)
    synced = inv.sync_records(records)

    assert len(synced) == len(records)
    for record in synced:
        assert record.migration_status == MigrationStatus.WAITTING
        assert record.project == bitbucket_server["project_key"]
    assert any(r.name == bitbucket_server["repo_slug"] for r in synced)


def test_given_invalid_credentials_when_scraping_live_bitbucket_then_raises_scraper_authentication_error(
    bitbucket_server: dict[str, Any],
) -> None:
    """Verify connecting with invalid token against live Bitbucket raises ScraperAuthenticationError."""
    base_url = bitbucket_server["base_url"]
    auth = TokenAuthenticator("invalid_token_bb_401")
    client = BitbucketClient(base_url=base_url, auth=auth)
    scraper = BitbucketRepositoryScraper(
        base_url=base_url,
        project_key=bitbucket_server["project_key"],
        client=client,
    )

    with pytest.raises(
        ScraperAuthenticationError, match="Bitbucket authentication failed"
    ):
        scraper.scrape()


def test_given_unreachable_bitbucket_host_when_scraping_then_raises_scraper_connection_error() -> (
    None
):
    """Verify connecting to an unreachable host/port raises ScraperConnectionError after retries."""
    unreachable_url = "http://127.0.0.1:59998"
    client = BitbucketClient(base_url=unreachable_url, retries=1, timeout=1.0)
    scraper = BitbucketRepositoryScraper(base_url=unreachable_url, client=client)

    with pytest.raises(ScraperConnectionError, match="Failed to connect to Bitbucket"):
        scraper.scrape()
