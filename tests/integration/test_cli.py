"""Integration tests verifying CLI execution against live SVN Docker container and live Google Sheets."""

from __future__ import annotations

import io
import uuid
from typing import Any

import pytest

from gh_migration_inventory_manager.cli import build_parser, main, run_cli
from gh_migration_inventory_manager.google import Spreadsheet


def test_given_invalid_svn_credentials_when_running_cli_then_logs_auth_error_and_exits_with_code_1(
    svn_server: dict[str, Any],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Verify CLI maps live SVN authentication failure to exit code 1."""
    repo_url = f"{svn_server['url']}/standard-app"

    exit_code = main(
        [
            "scrape",
            "svn",
            "--url",
            repo_url,
            "--username",
            svn_server["username"],
            "--password",
            "wrong_password_xyz",
        ]
    )

    assert exit_code == 1
    captured = capsys.readouterr()
    assert "Authentication Error" in captured.err


def test_given_nonexistent_svn_url_when_running_cli_then_logs_scraper_error_and_exits_with_code_1(
    svn_server: dict[str, Any],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Verify CLI maps live SVN 404/not found errors to exit code 1."""
    invalid_url = f"{svn_server['url']}/non-existent-repo-404"

    exit_code = main(
        [
            "scrape",
            "svn",
            "--url",
            invalid_url,
            "--username",
            svn_server["username"],
            "--password",
            svn_server["password"],
        ]
    )

    assert exit_code == 1
    captured = capsys.readouterr()
    assert "Scraper Error" in captured.err


def test_given_live_svn_repos_and_sheets_credentials_when_running_cli_sync_then_synchronizes_inventory(
    svn_server: dict[str, Any],
    gcp_credentials_path: str,
    live_spreadsheet: Spreadsheet,
) -> None:
    """Verify full CLI flow scraping live SVN and persisting to live Google Spreadsheet."""
    repo_url = f"{svn_server['url']}/standard-app"
    test_project = f"test-cli-{uuid.uuid4().hex[:6]}"
    parser = build_parser()
    args = parser.parse_args(
        [
            "scrape",
            "svn",
            "--url",
            repo_url,
            "--username",
            svn_server["username"],
            "--password",
            svn_server["password"],
            "--spreadsheet-id",
            live_spreadsheet.id,
            "--credentials-path",
            gcp_credentials_path,
            "--sheet-name",
            live_spreadsheet.sheet_name,
            "--project",
            test_project,
        ]
    )

    out_stream = io.StringIO()
    created_row_indices: list[int] = []

    try:
        result = run_cli(args, out_stream=out_stream)

        assert result == 1
        assert out_stream.getvalue().strip() == "1"

        # Verify entry exists in live Google Sheets
        matching_rows = live_spreadsheet.find_by("name", "standard-app")
        test_rows = [r for r in matching_rows if r.data.get("project") == test_project]
        assert len(test_rows) >= 1
        created_row_indices.extend(
            r.row_index for r in test_rows if r.row_index is not None
        )

        persisted = test_rows[0]
        assert persisted.data["name"] == "standard-app"
        assert persisted.data["project"] == test_project
        assert persisted.data["stack"] == "Java"
    finally:
        for idx in created_row_indices:
            live_spreadsheet.clear_row(idx)


def test_given_live_spreadsheet_instance_parameter_when_running_cli_then_persists_to_provided_spreadsheet(
    svn_server: dict[str, Any],
    live_spreadsheet: Spreadsheet,
) -> None:
    """Verify passing live Spreadsheet instance directly to run_cli persists correctly."""
    repo_url = f"{svn_server['url']}/standard-app"
    test_project = f"test-param-{uuid.uuid4().hex[:6]}"
    parser = build_parser()
    args = parser.parse_args(
        [
            "scrape",
            "svn",
            "--url",
            repo_url,
            "--username",
            svn_server["username"],
            "--password",
            svn_server["password"],
            "--project",
            test_project,
        ]
    )

    out_stream = io.StringIO()
    created_row_indices: list[int] = []

    try:
        result = run_cli(
            args,
            spreadsheet=live_spreadsheet,
            out_stream=out_stream,
        )

        assert result == 1
        assert out_stream.getvalue().strip() == "1"

        matching_rows = live_spreadsheet.find_by("name", "standard-app")
        test_rows = [r for r in matching_rows if r.data.get("project") == test_project]
        assert len(test_rows) >= 1
        created_row_indices.extend(
            r.row_index for r in test_rows if r.row_index is not None
        )
    finally:
        for idx in created_row_indices:
            live_spreadsheet.clear_row(idx)


def test_given_non_existent_spreadsheet_id_when_running_cli_sync_then_validates_and_reports_error(
    svn_server: dict[str, Any],
    gcp_credentials_path: str,
) -> None:
    """Verify live validation against Google Sheets API rejects non-existent spreadsheet ID."""
    repo_url = f"{svn_server['url']}/standard-app"
    fake_id = f"non-existent-sheet-id-404-{uuid.uuid4().hex[:8]}"

    parser = build_parser()
    args = parser.parse_args(
        [
            "scrape",
            "svn",
            "--url",
            repo_url,
            "--username",
            svn_server["username"],
            "--password",
            svn_server["password"],
            "--spreadsheet-id",
            fake_id,
            "--credentials-path",
            gcp_credentials_path,
        ]
    )

    err_stream = io.StringIO()
    result = run_cli(args, err_stream=err_stream)

    assert result is None
    error_output = err_stream.getvalue()
    assert "Inventory not found" in error_output


def test_given_invalid_empty_spreadsheet_id_when_running_cli_sync_then_reports_configuration_error(
    svn_server: dict[str, Any],
    gcp_credentials_path: str,
) -> None:
    """Verify empty spreadsheet ID is rejected before API execution."""
    repo_url = f"{svn_server['url']}/standard-app"

    parser = build_parser()
    args = parser.parse_args(
        [
            "scrape",
            "svn",
            "--url",
            repo_url,
            "--username",
            svn_server["username"],
            "--password",
            svn_server["password"],
            "--spreadsheet-id",
            "   ",
            "--credentials-path",
            gcp_credentials_path,
        ]
    )

    err_stream = io.StringIO()
    result = run_cli(args, err_stream=err_stream)

    assert result is None
    assert "Missing Google Spreadsheet ID" in err_stream.getvalue()


def test_given_unreachable_jenkins_url_when_running_cli_then_logs_scraper_connection_error_and_exits_with_code_1(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Verify CLI maps unreachable Jenkins connection error to exit code 1."""
    unreachable_url = "http://127.0.0.1:59998"
    exit_code = main(
        [
            "scrape",
            "jenkins",
            "--url",
            unreachable_url,
        ]
    )

    assert exit_code == 1
    captured = capsys.readouterr()
    assert "Connection Error" in captured.err


def test_given_live_jenkins_server_when_running_cli_then_inventories_workflows(
    jenkins_server: dict[str, Any],
) -> None:
    """Verify CLI successfully scrapes workflows from containerized Jenkins and outputs count."""
    from unittest.mock import MagicMock

    from gh_migration_inventory_manager.google import Row

    base_url = jenkins_server["base_url"]
    parser = build_parser()
    args = parser.parse_args(
        [
            "scrape",
            "jenkins",
            "--url",
            base_url,
        ]
    )

    mock_sheet = MagicMock()
    mock_sheet.find_by.return_value = []
    mock_sheet.add.side_effect = lambda row: Row(
        row_index=5,
        data=row.data,
        values=list(row.data.values()),
    )

    out_stream = io.StringIO()
    result = run_cli(
        args,
        spreadsheet=mock_sheet,
        out_stream=out_stream,
    )

    assert result is not None
    assert result >= 2
    assert int(out_stream.getvalue().strip()) == result
