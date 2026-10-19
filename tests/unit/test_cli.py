from __future__ import annotations

import io
import json
from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from gh_migration_inventory_manager.cli import (
    build_parser,
    main,
    run_cli,
)
from gh_migration_inventory_manager.inventory import InventoryRecord, MigrationStatus
from gh_migration_inventory_manager.scrap import (
    Repository,
    RepositoryScraper,
    ScraperAuthenticationError,
    ScraperConnectionError,
    ScraperError,
    ScraperProvider,
    ScraperRegistry,
    Stack,
)


@pytest.fixture
def mock_provider() -> MagicMock:
    """Fixture providing a mock ScraperProvider for testing."""
    provider = MagicMock(spec=ScraperProvider)
    provider.platform_name = "mock-plat"
    provider.description = "Mock platform description"

    def configure(parser: Any) -> None:
        parser.add_argument("--repo-url", required=True, help="Repository URL")

    provider.configure_parser.side_effect = configure
    return provider


@pytest.fixture
def test_registry(mock_provider: MagicMock) -> ScraperRegistry:
    """Fixture providing a ScraperRegistry populated with mock_provider."""
    registry = ScraperRegistry()
    registry.register(mock_provider)
    return registry


def test_given_registered_scraper_provider_when_building_parser_then_registers_platform_subcommands(
    test_registry: ScraperRegistry,
) -> None:
    """Test build_parser dynamically creates subparsers from registry providers."""
    parser = build_parser(test_registry)
    args = parser.parse_args(
        [
            "scrape",
            "mock-plat",
            "--repo-url",
            "https://mock.example.com",
            "--spreadsheet-id",
            "sheet-123",
            "--credentials-path",
            "key.json",
        ]
    )

    assert args.command == "scrape"
    assert args.platform == "mock-plat"
    assert args.repo_url == "https://mock.example.com"
    assert args.spreadsheet_id == "sheet-123"
    assert args.credentials_path == "key.json"
    assert args.sheet_name == "Inventario"
    assert args.initial_status == "waitting"
    assert args.dry_run is False
    assert args.format == "table"


def test_given_default_registry_when_building_parser_then_includes_default_svn_arguments() -> (
    None
):
    """Test default build_parser correctly binds built-in SVN provider arguments."""
    parser = build_parser()
    args = parser.parse_args(
        [
            "scrape",
            "svn",
            "--url",
            "http://svn.example.com/repos/app",
            "--username",
            "svnuser",
            "--password",
            "svnpass",
            "--layout",
            "multi_project",
            "--dry-run",
        ]
    )

    assert args.command == "scrape"
    assert args.platform == "svn"
    assert args.url == "http://svn.example.com/repos/app"
    assert args.username == "svnuser"
    assert args.password == "svnpass"
    assert args.layout == "multi_project"
    assert args.dry_run is True


def test_given_dry_run_flag_when_running_cli_then_does_not_access_google_sheets(
    test_registry: ScraperRegistry, mock_provider: MagicMock
) -> None:
    """Test running CLI with --dry-run avoids Google Sheets persistence."""
    fake_repos = [
        Repository(
            name="app-core",
            project="banking",
            stacks=[Stack.JAVA],
            last_commit=datetime(2026, 10, 18, 10, 0, tzinfo=timezone.utc),
            default_branch="trunk",
        )
    ]
    mock_scraper = MagicMock(spec=RepositoryScraper)
    mock_scraper.scrape.return_value = fake_repos
    mock_provider.create_scraper.return_value = mock_scraper

    parser = build_parser(test_registry)
    args = parser.parse_args(
        ["scrape", "mock-plat", "--repo-url", "http://test", "--dry-run"]
    )

    out_stream = io.StringIO()
    exit_code = run_cli(args, registry=test_registry, out_stream=out_stream)

    assert exit_code == 0
    output = out_stream.getvalue()
    assert "app-core" in output
    assert "banking" in output
    assert "Java" in output
    assert "[DRY RUN]" in output
    assert "Total discovered: 1" in output


def test_given_json_format_flag_when_running_cli_then_outputs_valid_json(
    test_registry: ScraperRegistry, mock_provider: MagicMock
) -> None:
    """Test running CLI with --format json outputs valid JSON payload."""
    fake_repos = [
        Repository(
            name="json-app",
            project="json-proj",
            stacks=[Stack.PYTHON],
            last_commit=datetime(2026, 10, 15, tzinfo=timezone.utc),
        )
    ]
    mock_scraper = MagicMock(spec=RepositoryScraper)
    mock_scraper.scrape.return_value = fake_repos
    mock_provider.create_scraper.return_value = mock_scraper

    parser = build_parser(test_registry)
    args = parser.parse_args(
        [
            "scrape",
            "mock-plat",
            "--repo-url",
            "http://test",
            "--dry-run",
            "--format",
            "json",
        ]
    )

    out_stream = io.StringIO()
    err_stream = io.StringIO()
    exit_code = run_cli(
        args, registry=test_registry, out_stream=out_stream, err_stream=err_stream
    )

    assert exit_code == 0
    parsed = json.loads(out_stream.getvalue())
    assert len(parsed) == 1
    assert parsed[0]["name"] == "json-app"
    assert parsed[0]["project"] == "json-proj"
    assert parsed[0]["stacks"] == ["python"]
    assert "[DRY RUN]" in err_stream.getvalue()


def test_given_valid_args_and_credentials_when_running_cli_then_synchronizes_repositories_with_google_sheets(
    test_registry: ScraperRegistry, mock_provider: MagicMock, tmp_path: Any
) -> None:
    """Test full CLI flow with Google Sheets persistence."""
    creds_file = tmp_path / "service_account.json"
    creds_file.write_text('{"type": "service_account"}')

    fake_repos = [
        Repository(
            name="cloud-app",
            project="infra",
            stacks=[Stack.TERRAFORM],
        )
    ]
    mock_scraper = MagicMock(spec=RepositoryScraper)
    mock_scraper.scrape.return_value = fake_repos
    mock_provider.create_scraper.return_value = mock_scraper

    parser = build_parser(test_registry)
    args = parser.parse_args(
        [
            "scrape",
            "mock-plat",
            "--repo-url",
            "http://test",
            "--spreadsheet-id",
            "sheet-456",
            "--credentials-path",
            str(creds_file),
            "--sheet-name",
            "Inventario",
        ]
    )

    out_stream = io.StringIO()
    with (
        patch("gh_migration_inventory_manager.google.Spreadsheet") as mock_sheet_cls,
        patch("gh_migration_inventory_manager.cli.Inventory") as mock_inv_cls,
    ):
        mock_inv = mock_inv_cls.return_value
        mock_inv.sync_repositories.return_value = [
            InventoryRecord(name="cloud-app", project="infra", stack="Terraform")
        ]

        exit_code = run_cli(args, registry=test_registry, out_stream=out_stream)

        assert exit_code == 0
        mock_sheet_cls.assert_called_once()
        mock_inv.sync_repositories.assert_called_once_with(
            fake_repos, initial_status=MigrationStatus.WAITTING.value
        )
        assert "Successfully synchronized 1 repositories" in out_stream.getvalue()


def test_given_missing_spreadsheet_id_when_running_cli_sync_then_returns_error_code(
    test_registry: ScraperRegistry, mock_provider: MagicMock
) -> None:
    """Test run_cli without spreadsheet ID in non-dry-run mode returns error code 2."""
    mock_scraper = MagicMock(spec=RepositoryScraper)
    mock_scraper.scrape.return_value = []
    mock_provider.create_scraper.return_value = mock_scraper

    parser = build_parser(test_registry)
    args = parser.parse_args(
        [
            "scrape",
            "mock-plat",
            "--repo-url",
            "http://test",
            "--credentials-path",
            "key.json",
        ]
    )

    err_stream = io.StringIO()
    exit_code = run_cli(args, registry=test_registry, err_stream=err_stream)

    assert exit_code == 2
    assert "Missing Google Spreadsheet ID" in err_stream.getvalue()


def test_given_missing_credentials_path_when_running_cli_sync_then_returns_error_code(
    test_registry: ScraperRegistry, mock_provider: MagicMock
) -> None:
    """Test run_cli without credentials path in non-dry-run mode returns error."""
    mock_scraper = MagicMock(spec=RepositoryScraper)
    mock_scraper.scrape.return_value = []
    mock_provider.create_scraper.return_value = mock_scraper

    parser = build_parser(test_registry)
    args = parser.parse_args(
        [
            "scrape",
            "mock-plat",
            "--repo-url",
            "http://test",
            "--spreadsheet-id",
            "id-123",
        ]
    )

    err_stream = io.StringIO()
    exit_code = run_cli(args, registry=test_registry, err_stream=err_stream)

    assert exit_code == 2
    assert "Missing Google credentials path" in err_stream.getvalue()


def test_given_non_existent_credentials_path_when_running_cli_sync_then_returns_error_code(
    test_registry: ScraperRegistry, mock_provider: MagicMock
) -> None:
    """Test run_cli with non-existent credentials path returns error code 2."""
    mock_scraper = MagicMock(spec=RepositoryScraper)
    mock_scraper.scrape.return_value = []
    mock_provider.create_scraper.return_value = mock_scraper

    parser = build_parser(test_registry)
    args = parser.parse_args(
        [
            "scrape",
            "mock-plat",
            "--repo-url",
            "http://test",
            "--spreadsheet-id",
            "id-123",
            "--credentials-path",
            "/non/existent/path/key.json",
        ]
    )

    err_stream = io.StringIO()
    exit_code = run_cli(args, registry=test_registry, err_stream=err_stream)

    assert exit_code == 2
    assert "Google credentials file not found" in err_stream.getvalue()


def test_given_no_cli_arguments_when_invoking_main_then_prints_help_and_exits_successfully(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Test main without arguments prints help message and returns 0."""
    exit_code = main([])
    assert exit_code == 0
    captured = capsys.readouterr()
    assert "usage: gh-migration-inventory" in captured.out


@pytest.mark.parametrize(
    ("exception_cls", "expected_code", "expected_msg"),
    [
        (ScraperAuthenticationError, 1, "Authentication Error"),
        (ScraperConnectionError, 1, "Connection Error"),
        (ScraperError, 1, "Scraper Error"),
        (ValueError, 2, "Configuration Error"),
        (RuntimeError, 1, "Unexpected Error"),
    ],
)
def test_given_scraper_error_when_invoking_main_then_logs_error_and_returns_exit_code_1(
    exception_cls: type[Exception],
    expected_code: int,
    expected_msg: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Test main handles exceptions gracefully and maps to proper exit codes."""
    with patch(
        "gh_migration_inventory_manager.cli.run_cli",
        side_effect=exception_cls("test failure"),
    ):
        exit_code = main(
            ["scrape", "svn", "--url", "http://svn.example.com", "--dry-run"]
        )
        assert exit_code == expected_code
        captured = capsys.readouterr()
        assert expected_msg in captured.err


def test_given_keyboard_interrupt_when_invoking_main_then_returns_exit_code_130() -> (
    None
):
    """Test main returns 130 on KeyboardInterrupt."""
    with patch(
        "gh_migration_inventory_manager.cli.run_cli", side_effect=KeyboardInterrupt
    ):
        exit_code = main(
            ["scrape", "svn", "--url", "http://svn.example.com", "--dry-run"]
        )
        assert exit_code == 130
