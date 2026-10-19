"""Unit tests for ScraperProvider protocol, ScraperRegistry, and format_stacks."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from gh_migration_inventory_manager.scrap import (
    Repository,
    RepositoryScraper,
    ScraperProvider,
    ScraperRegistry,
    Stack,
    format_stacks,
)


def test_given_stack_combinations_when_formatting_stacks_then_returns_delimited_string() -> (
    None
):
    """Test format_stacks converts lists of Stack enum into readable strings."""
    assert format_stacks([Stack.JAVA]) == "Java"
    assert format_stacks([Stack.PYTHON, Stack.TYPESCRIPT]) == "Python, TypeScript"
    assert format_stacks([Stack.C_SHARP, Stack.DOTNET_CORE]) == "C#, .NET Core"
    assert format_stacks([Stack.BASH, Stack.TERRAFORM]) == "Bash, Terraform"
    assert format_stacks([Stack.UNKNOWN]) == ""
    assert format_stacks([Stack.JAVA, Stack.UNKNOWN]) == "Java"
    assert format_stacks([]) == ""


def test_given_registered_scraper_provider_when_getting_by_name_then_returns_provider() -> (
    None
):
    """Test registering and retrieving a provider by platform name."""
    registry = ScraperRegistry()

    provider = MagicMock(spec=ScraperProvider)
    provider.platform_name = "test-platform"
    provider.description = "Test platform scraper"

    registry.register(provider)
    assert registry.get("test-platform") is provider
    assert registry.get("TEST-PLATFORM") is provider
    assert registry.list_platforms() == ["test-platform"]


def test_given_unregistered_platform_name_when_getting_provider_then_raises_key_error() -> (
    None
):
    """Test querying an unknown platform raises KeyError with available platforms."""
    registry = ScraperRegistry()

    provider = MagicMock(spec=ScraperProvider)
    provider.platform_name = "svn"
    registry.register(provider)

    with pytest.raises(KeyError) as exc_info:
        registry.get("bitbucket")

    assert "Unsupported platform: 'bitbucket'" in str(exc_info.value)
    assert "svn" in str(exc_info.value)


@pytest.mark.parametrize("invalid_name", ["", "   ", None])
def test_given_invalid_or_empty_platform_name_when_registering_provider_then_raises_value_error(
    invalid_name: Any,
) -> None:
    """Test registering a provider with invalid platform name raises ValueError."""
    registry = ScraperRegistry()
    provider = MagicMock(spec=ScraperProvider)
    provider.platform_name = invalid_name

    with pytest.raises(ValueError):
        registry.register(provider)


def test_given_custom_provider_conforming_to_protocol_when_executed_then_returns_expected_repositories() -> (
    None
):
    """Demonstrate how a new platform integrates seamlessly via provider."""

    class MockBitbucketScraper:
        def scrape(self) -> list[Repository]:
            return [
                Repository(name="bb-repo", project="bb-proj", stacks=[Stack.PYTHON])
            ]

    class BitbucketScraperProvider:
        @property
        def platform_name(self) -> str:
            return "bitbucket"

        @property
        def description(self) -> str:
            return "Bitbucket Cloud/Server scraper"

        def configure_parser(self, parser: Any) -> None:
            parser.add_argument("--workspace", required=True, help="Workspace ID")
            parser.add_argument("--project", help="Project key")

        def create_scraper(self, args: Any) -> RepositoryScraper:
            return MockBitbucketScraper()

    bb_provider = BitbucketScraperProvider()
    assert isinstance(bb_provider, ScraperProvider)

    registry = ScraperRegistry()
    registry.register(bb_provider)

    retrieved = registry.get("bitbucket")
    assert retrieved is bb_provider

    mock_parser = MagicMock()
    retrieved.configure_parser(mock_parser)
    assert mock_parser.add_argument.call_count == 2

    scraper = retrieved.create_scraper(MagicMock())
    repos = scraper.scrape()
    assert len(repos) == 1
    assert repos[0].name == "bb-repo"


def test_given_svn_scraper_and_provider_when_checking_inheritance_then_subtypes_canonical_contracts() -> (
    None
):
    """Verify SvnScraperProvider and SvnRepositoryScraper implement contracts."""
    from gh_migration_inventory_manager.svn_scrap import (
        SvnRepositoryScraper,
        SvnScraperProvider,
    )

    assert ScraperProvider in SvnScraperProvider.__mro__
    assert issubclass(SvnRepositoryScraper, RepositoryScraper)

    provider = SvnScraperProvider()
    assert isinstance(provider, ScraperProvider)
    assert provider.platform_name == "svn"
    assert "Subversion" in provider.description

    mock_parser = MagicMock()
    provider.configure_parser(mock_parser)
    assert mock_parser.add_argument.called

    mock_args = MagicMock(
        url="http://svn.example.com/repo",
        username=None,
        password=None,
        token=None,
        layout="auto",
        organization=None,
        project=None,
    )
    scraper = provider.create_scraper(mock_args)
    assert isinstance(scraper, SvnRepositoryScraper)
    assert isinstance(scraper, RepositoryScraper)
