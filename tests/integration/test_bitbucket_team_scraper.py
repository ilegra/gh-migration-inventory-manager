"""Integration tests verifying BitbucketTeamScraper against Bitbucket service."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from gh_migration_inventory_manager import TokenAuthenticator
from gh_migration_inventory_manager.bitbucket_scrap import (
    BitbucketClient,
    BitbucketTeamScraper,
)
from gh_migration_inventory_manager.converter import TeamConverter
from gh_migration_inventory_manager.google import Row
from gh_migration_inventory_manager.inventory import MigrationStatus, TeamInventory
from gh_migration_inventory_manager.scrap import (
    ScraperAuthenticationError,
)


def test_given_live_bitbucket_server_when_scraping_then_discovers_team_permissions(
    bitbucket_server: dict[str, Any],
) -> None:
    """Verify scraper discovers group permissions from Bitbucket Server container."""
    base_url = bitbucket_server["base_url"]
    auth = TokenAuthenticator(bitbucket_server["discovery_token"])
    client = BitbucketClient(base_url=base_url, auth=auth)
    scraper = BitbucketTeamScraper(
        client=client,
        project_key=bitbucket_server["project_key"],
        repo_slug=bitbucket_server["repo_slug"],
    )

    permissions = scraper.scrape()
    assert isinstance(permissions, list)


def test_given_live_bitbucket_server_when_unauthorized_token_used_then_raises_scraper_authentication_error(
    bitbucket_server: dict[str, Any],
) -> None:
    """Verify invalid token raises ScraperAuthenticationError against live server."""
    base_url = bitbucket_server["base_url"]
    invalid_auth = TokenAuthenticator("invalid-expired-token")
    client = BitbucketClient(base_url=base_url, auth=invalid_auth)
    scraper = BitbucketTeamScraper(
        client=client,
        project_key=bitbucket_server["project_key"],
        repo_slug=bitbucket_server["repo_slug"],
    )

    with pytest.raises(ScraperAuthenticationError):
        scraper.scrape()


def test_given_discovered_teams_when_syncing_inventory_then_registers_in_topology_tab(
    bitbucket_server: dict[str, Any],
) -> None:
    """Verify discovered Bitbucket teams integrate end-to-end with TeamInventory."""
    base_url = bitbucket_server["base_url"]
    auth = TokenAuthenticator(bitbucket_server["discovery_token"])
    client = BitbucketClient(base_url=base_url, auth=auth)
    scraper = BitbucketTeamScraper(
        client=client,
        project_key=bitbucket_server["project_key"],
        repo_slug=bitbucket_server["repo_slug"],
    )

    permissions = scraper.scrape()

    converter = TeamConverter()
    records = converter.to_team_records(
        permissions, initial_status=MigrationStatus.WAITTING
    )

    # In-memory mock spreadsheet client simulating live Google Sheets Topology tab
    mock_sheet = MagicMock()
    mock_sheet.find_by.return_value = []
    mock_sheet.add.side_effect = lambda row: Row(
        row_index=10,
        data=row.data,
        values=list(row.data.values()),
    )

    inv = TeamInventory(spreadsheet=mock_sheet, converter=converter)
    synced = inv.sync_records(records)

    assert len(synced) == len(records)
    for record in synced:
        assert record.migration_status == MigrationStatus.WAITTING
