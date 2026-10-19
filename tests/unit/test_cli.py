"""Unit tests for repository scraping CLI module."""

from __future__ import annotations

import io
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from gh_migration_inventory_manager.cli import (
    build_parser,
    main,
    run_cli,
)
from gh_migration_inventory_manager.converter import RepositoryConverter
from gh_migration_inventory_manager.google import (
    Spreadsheet,
    SpreadsheetAccessError,
    SpreadsheetError,
    SpreadsheetNotFoundError,
)
from gh_migration_inventory_manager.inventory import InventoryRecord
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


def test_given_deprecated_format_flag_when_building_parser_then_rejects_argument(
    test_registry: ScraperRegistry,
) -> None:
    """Test that removed --format parameter is rejected by argument parser."""
    parser = build_parser(test_registry)
    with pytest.raises(SystemExit):
        parser.parse_args(
            [
                "scrape",
                "mock-plat",
                "--repo-url",
                "https://mock.example.com",
                "--format",
                "json",
            ]
        )


def test_given_deprecated_dry_run_flag_when_building_parser_then_rejects_argument(
    test_registry: ScraperRegistry,
) -> None:
    """Test that removed --dry-run parameter is rejected by argument parser."""
    parser = build_parser(test_registry)
    with pytest.raises(SystemExit):
        parser.parse_args(
            [
                "scrape",
                "mock-plat",
                "--repo-url",
                "https://mock.example.com",
                "--dry-run",
            ]
        )


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
        ]
    )

    assert args.command == "scrape"
    assert args.platform == "svn"
    assert args.url == "http://svn.example.com/repos/app"
    assert args.username == "svnuser"
    assert args.password == "svnpass"
    assert args.layout == "multi_project"


def test_given_valid_args_and_credentials_when_running_cli_then_synchronizes_repositories_with_google_sheets(
    test_registry: ScraperRegistry, mock_provider: MagicMock, tmp_path: Any
) -> None:
    """Test full CLI flow validating spreadsheet and returning its reference."""
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
    with patch("gh_migration_inventory_manager.cli.Inventory") as mock_inv_cls:
        mock_sheet = MagicMock(spec=Spreadsheet)
        mock_sheet.id = "sheet-456"
        mock_inv = mock_inv_cls.return_value
        mock_inv.spreadsheet = mock_sheet
        mock_inv.sync_records.return_value = [
            InventoryRecord(name="cloud-app", project="infra", stack="Terraform")
        ]

        result = run_cli(args, registry=test_registry, out_stream=out_stream)

        assert result is not None
        assert result.id == "sheet-456"
        mock_inv_cls.assert_called_once_with(
            spreadsheet=None,
            spreadsheet_id="sheet-456",
            credentials_path=str(creds_file),
            sheet_name="Inventario",
            converter=mock_inv_cls.call_args[1]["converter"],
        )
        mock_inv.sync_records.assert_called_once()
        synced_records = mock_inv.sync_records.call_args[0][0]
        assert len(synced_records) == 1
        assert synced_records[0].name == "cloud-app"
        assert synced_records[0].stack == "Terraform"

        assert (
            "https://docs.google.com/spreadsheets/d/sheet-456" in out_stream.getvalue()
        )


def test_given_spreadsheet_parameter_when_running_cli_then_uses_and_returns_provided_spreadsheet(
    test_registry: ScraperRegistry, mock_provider: MagicMock
) -> None:
    """Test passing spreadsheet directly as parameter to run_cli."""
    fake_repos = [Repository(name="direct-app", project="direct")]
    mock_scraper = MagicMock(spec=RepositoryScraper)
    mock_scraper.scrape.return_value = fake_repos
    mock_provider.create_scraper.return_value = mock_scraper

    parser = build_parser(test_registry)
    args = parser.parse_args(["scrape", "mock-plat", "--repo-url", "http://test"])

    mock_sheet = MagicMock(spec=Spreadsheet)
    mock_sheet.id = "passed-sheet-id"

    out_stream = io.StringIO()
    with patch("gh_migration_inventory_manager.cli.Inventory") as mock_inv_cls:
        mock_inv = mock_inv_cls.return_value
        mock_inv.spreadsheet = mock_sheet
        result = run_cli(
            args,
            registry=test_registry,
            spreadsheet=mock_sheet,
            out_stream=out_stream,
        )

        assert result is mock_sheet
        mock_inv_cls.assert_called_once_with(
            spreadsheet=mock_sheet,
            spreadsheet_id=None,
            credentials_path=None,
            sheet_name="Inventario",
            converter=mock_inv_cls.call_args[1]["converter"],
        )
        mock_inv.sync_records.assert_called_once()
        assert (
            "https://docs.google.com/spreadsheets/d/passed-sheet-id"
            in out_stream.getvalue()
        )


def test_given_non_existent_spreadsheet_when_running_cli_then_reports_error_and_returns_none(
    test_registry: ScraperRegistry, mock_provider: MagicMock
) -> None:
    """Test run_cli catches SpreadsheetNotFoundError during validation."""
    mock_scraper = MagicMock(spec=RepositoryScraper)
    mock_scraper.scrape.return_value = [Repository(name="app")]
    mock_provider.create_scraper.return_value = mock_scraper

    parser = build_parser(test_registry)
    args = parser.parse_args(["scrape", "mock-plat", "--repo-url", "http://test"])

    mock_sheet = MagicMock(spec=Spreadsheet)
    mock_sheet.id = "invalid-sheet"
    mock_sheet.validate.side_effect = SpreadsheetNotFoundError(
        "Spreadsheet not found: 'invalid-sheet'."
    )

    err_stream = io.StringIO()
    result = run_cli(
        args,
        registry=test_registry,
        spreadsheet=mock_sheet,
        err_stream=err_stream,
    )

    assert result is None
    assert "Google Spreadsheet not found" in err_stream.getvalue()


def test_given_spreadsheet_access_error_when_running_cli_then_reports_error_and_returns_none(
    test_registry: ScraperRegistry, mock_provider: MagicMock
) -> None:
    """Test run_cli catches SpreadsheetAccessError during validation."""
    mock_scraper = MagicMock(spec=RepositoryScraper)
    mock_scraper.scrape.return_value = [Repository(name="app")]
    mock_provider.create_scraper.return_value = mock_scraper

    parser = build_parser(test_registry)
    args = parser.parse_args(["scrape", "mock-plat", "--repo-url", "http://test"])

    mock_sheet = MagicMock(spec=Spreadsheet)
    mock_sheet.id = "forbidden-sheet"
    mock_sheet.validate.side_effect = SpreadsheetAccessError(
        "Access denied to spreadsheet."
    )

    err_stream = io.StringIO()
    result = run_cli(
        args,
        registry=test_registry,
        spreadsheet=mock_sheet,
        err_stream=err_stream,
    )

    assert result is None
    assert "Access denied to Google Spreadsheet" in err_stream.getvalue()


def test_given_missing_spreadsheet_id_when_running_cli_sync_then_returns_none(
    test_registry: ScraperRegistry, mock_provider: MagicMock
) -> None:
    """Test run_cli without spreadsheet ID in non-dry-run mode returns None and logs error."""
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
    result = run_cli(args, registry=test_registry, err_stream=err_stream)

    assert result is None
    assert "Missing Google Spreadsheet ID" in err_stream.getvalue()


def test_given_missing_credentials_path_when_running_cli_sync_then_returns_none(
    test_registry: ScraperRegistry, mock_provider: MagicMock
) -> None:
    """Test run_cli without credentials path in non-dry-run mode returns None and logs error."""
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
    result = run_cli(args, registry=test_registry, err_stream=err_stream)

    assert result is None
    assert "Missing Google credentials path" in err_stream.getvalue()


def test_given_non_existent_credentials_path_when_running_cli_sync_then_returns_none(
    test_registry: ScraperRegistry, mock_provider: MagicMock
) -> None:
    """Test run_cli with non-existent credentials path returns None and logs error."""
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
    result = run_cli(args, registry=test_registry, err_stream=err_stream)

    assert result is None
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
        (SpreadsheetAccessError, 1, "Access Error"),
        (SpreadsheetNotFoundError, 2, "Spreadsheet Error"),
        (SpreadsheetError, 2, "Spreadsheet Error"),
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
        exit_code = main(["scrape", "svn", "--url", "http://svn.example.com"])
        assert exit_code == expected_code
        captured = capsys.readouterr()
        assert expected_msg in captured.err


def test_given_failed_run_cli_when_invoking_main_then_returns_exit_code_2() -> None:
    """Test main returns 2 when run_cli returns None."""
    with patch("gh_migration_inventory_manager.cli.run_cli", return_value=None):
        exit_code = main(
            [
                "scrape",
                "svn",
                "--url",
                "http://svn.example.com",
                "--spreadsheet-id",
                "x",
            ]
        )
        assert exit_code == 2


def test_given_keyboard_interrupt_when_invoking_main_then_returns_exit_code_130() -> (
    None
):
    """Test main returns 130 on KeyboardInterrupt."""
    with patch(
        "gh_migration_inventory_manager.cli.run_cli", side_effect=KeyboardInterrupt
    ):
        exit_code = main(["scrape", "svn", "--url", "http://svn.example.com"])
        assert exit_code == 130


def test_given_custom_converter_when_running_cli_then_uses_injected_converter(
    test_registry: ScraperRegistry, mock_provider: MagicMock
) -> None:
    """Test run_cli uses the injected RepositoryConverter instance."""
    mock_scraper = MagicMock(spec=RepositoryScraper)
    mock_scraper.scrape.return_value = [Repository(name="injected-repo")]
    mock_provider.create_scraper.return_value = mock_scraper

    mock_converter = MagicMock(spec=RepositoryConverter)
    mock_converter.to_inventory_records.return_value = [
        InventoryRecord(name="injected-repo")
    ]

    mock_spreadsheet = MagicMock(spec=Spreadsheet)
    mock_spreadsheet.id = "mock-sheet-id"

    parser = build_parser(test_registry)
    args = parser.parse_args(["scrape", "mock-plat", "--repo-url", "http://x"])

    out = io.StringIO()
    result = run_cli(
        args,
        registry=test_registry,
        spreadsheet=mock_spreadsheet,
        converter=mock_converter,
        out_stream=out,
    )

    mock_converter.to_inventory_records.assert_called_once()
    assert result is mock_spreadsheet
    assert "https://docs.google.com/spreadsheets/d/mock-sheet-id" in out.getvalue()
