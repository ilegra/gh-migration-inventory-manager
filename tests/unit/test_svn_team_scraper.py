"""Unit tests for SvnTeamScraper Subversion team topology discovery."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from gh_migration_inventory_manager.scrap import (
    NormalizedRole,
    ScraperAuthenticationError,
    ScraperConnectionError,
    ScraperError,
)
from gh_migration_inventory_manager.svn_scrap import SvnTeamScraper, SvnWebDavClient


def test_given_valid_local_authz_file_when_scraping_then_parses_permissions(
    tmp_path: Path,
) -> None:
    """Test scraping from a local authz file."""
    authz_file = tmp_path / "authz"
    authz_file.write_text(
        "[groups]\ndevs = alice, bob\n[/trunk]\n@devs = rw\n", encoding="utf-8"
    )

    scraper = SvnTeamScraper(authz_path=authz_file)
    permissions = scraper.scrape()

    assert len(permissions) == 1
    assert permissions[0].team_name == "devs"
    assert permissions[0].member_count == 2
    assert permissions[0].normalized_role == NormalizedRole.WRITE


def test_given_valid_remote_url_when_scraping_then_fetches_and_parses_permissions() -> (
    None
):
    """Test scraping from a remote authz URL using SvnWebDavClient."""
    mock_client = MagicMock(spec=SvnWebDavClient)
    mock_client.get_text.return_value = "[groups]\nops = dave\n[/]\n@ops = rw\n"

    scraper = SvnTeamScraper(
        authz_url="https://svn.example.com/svn/authz",
        client=mock_client,
    )
    permissions = scraper.scrape()

    assert len(permissions) == 1
    assert permissions[0].team_name == "ops"
    assert permissions[0].member_count == 1
    assert permissions[0].normalized_role == NormalizedRole.ADMIN
    mock_client.get_text.assert_called_once_with("https://svn.example.com/svn/authz")


def test_given_neither_path_nor_url_when_initializing_then_raises_value_error() -> None:
    """Test initialization raises ValueError if neither path nor URL is passed."""
    with pytest.raises(
        ValueError, match="Either authz_path.*or authz_url.*must be provided"
    ):
        SvnTeamScraper()


def test_given_unsupported_url_scheme_when_initializing_then_raises_value_error() -> (
    None
):
    """Test initialization raises ValueError for non-HTTP schemes."""
    with pytest.raises(ValueError, match="Unsupported URL scheme 'ftp'"):
        SvnTeamScraper(authz_url="ftp://svn.example.com/authz")


def test_given_nonexistent_local_file_when_scraping_then_raises_scraper_error(
    tmp_path: Path,
) -> None:
    """Test scraping non-existent local file raises ScraperError."""
    missing_file = tmp_path / "missing-authz"
    scraper = SvnTeamScraper(authz_path=missing_file)

    with pytest.raises(ScraperError, match="SVN authz file not found"):
        scraper.scrape()


def test_given_unreadable_local_file_when_scraping_then_raises_scraper_error(
    tmp_path: Path,
) -> None:
    """Test read failure on local file raises ScraperError."""
    unreadable_file = tmp_path / "locked-authz"
    unreadable_file.write_text("content", encoding="utf-8")

    scraper = SvnTeamScraper(authz_path=unreadable_file)
    with patch.object(Path, "read_text", side_effect=OSError("Permission denied")):
        with pytest.raises(ScraperError, match="Failed to read SVN authz file"):
            scraper.scrape()


def test_given_remote_http_401_when_scraping_then_raises_scraper_authentication_error() -> (
    None
):
    """Test HTTP 401 on remote authz fetch raises ScraperAuthenticationError."""
    mock_client = MagicMock(spec=SvnWebDavClient)
    mock_client.get_text.side_effect = ScraperAuthenticationError("Unauthorized")

    scraper = SvnTeamScraper(
        authz_url="https://svn.example.com/authz",
        client=mock_client,
    )

    with pytest.raises(ScraperAuthenticationError, match="Unauthorized"):
        scraper.scrape()


def test_given_remote_connection_failure_when_scraping_then_raises_scraper_connection_error() -> (
    None
):
    """Test connection timeout/failure on remote authz fetch raises ScraperConnectionError."""
    mock_client = MagicMock(spec=SvnWebDavClient)
    mock_client.get_text.side_effect = ScraperConnectionError("Network Timeout")

    scraper = SvnTeamScraper(
        authz_url="https://svn.example.com/authz",
        client=mock_client,
    )

    with pytest.raises(ScraperConnectionError, match="Network Timeout"):
        scraper.scrape()


def test_given_remote_http_500_when_scraping_then_raises_scraper_error() -> None:
    """Test HTTP 500 error on remote authz fetch raises ScraperError."""
    mock_client = MagicMock(spec=SvnWebDavClient)
    mock_client.get_text.side_effect = ScraperError("Internal Server Error")

    scraper = SvnTeamScraper(
        authz_url="https://svn.example.com/authz",
        client=mock_client,
    )

    with pytest.raises(ScraperError, match="Internal Server Error"):
        scraper.scrape()
