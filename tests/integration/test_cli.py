"""Integration tests verifying CLI execution against live SVN Docker container."""

from __future__ import annotations

import io
import json
from typing import Any
from unittest.mock import patch

import pytest

from gh_migration_inventory_manager.cli import main, run_cli
from gh_migration_inventory_manager.inventory import InventoryRecord, MigrationStatus


def test_given_live_svn_standard_repo_when_running_cli_dry_run_table_then_outputs_table_with_detected_stacks(
    svn_server: dict[str, Any],
) -> None:
    """Verify live CLI invocation scraping standard SVN repository in dry-run table mode."""
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
            svn_server["password"],
            "--dry-run",
        ]
    )

    assert exit_code == 0


def test_given_live_svn_standard_repo_when_running_cli_dry_run_table_via_run_cli_then_asserts_stdout(
    svn_server: dict[str, Any],
) -> None:
    """Verify stdout contents when scraping standard SVN repository via CLI."""
    repo_url = f"{svn_server['url']}/standard-app"
    from gh_migration_inventory_manager.cli import build_parser

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
            "--organization",
            "ilegra",
            "--project",
            "banking",
            "--dry-run",
        ]
    )

    out_stream = io.StringIO()
    exit_code = run_cli(args, out_stream=out_stream)

    assert exit_code == 0
    output = out_stream.getvalue()
    assert "Repository" in output
    assert "Project" in output
    assert "standard-app" in output
    assert "banking" in output
    assert "Java" in output
    assert "trunk" in output
    assert "[DRY RUN]" in output
    assert "Total discovered: 1" in output


def test_given_live_svn_standard_repo_when_running_cli_dry_run_json_then_outputs_valid_json_payload(
    svn_server: dict[str, Any],
) -> None:
    """Verify live CLI invocation with --format json produces valid JSON with detected stacks."""
    repo_url = f"{svn_server['url']}/standard-app"
    from gh_migration_inventory_manager.cli import build_parser

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
            "--format",
            "json",
            "--dry-run",
        ]
    )

    out_stream = io.StringIO()
    err_stream = io.StringIO()
    exit_code = run_cli(args, out_stream=out_stream, err_stream=err_stream)

    assert exit_code == 0
    payload = json.loads(out_stream.getvalue())
    assert len(payload) == 1
    repo = payload[0]
    assert repo["name"] == "standard-app"
    assert repo["stacks"] == ["java"]
    assert repo["default_branch"] == "trunk"
    assert repo["metadata"]["layout"] == "standard"
    assert "[DRY RUN]" in err_stream.getvalue()


def test_given_live_svn_multi_project_repo_when_running_cli_then_outputs_all_subproject_repositories(
    svn_server: dict[str, Any],
) -> None:
    """Verify live CLI invocation with multi_project layout detects all subprojects."""
    repo_url = f"{svn_server['url']}/multi-app"
    from gh_migration_inventory_manager.cli import build_parser

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
            "--layout",
            "multi_project",
            "--format",
            "json",
            "--dry-run",
        ]
    )

    out_stream = io.StringIO()
    exit_code = run_cli(args, out_stream=out_stream)

    assert exit_code == 0
    payload = json.loads(out_stream.getvalue())
    assert len(payload) == 2
    names = {r["name"] for r in payload}
    assert names == {"auth-service", "billing-service"}

    auth_repo = next(r for r in payload if r["name"] == "auth-service")
    assert auth_repo["stacks"] == ["python"]

    billing_repo = next(r for r in payload if r["name"] == "billing-service")
    assert billing_repo["stacks"] == ["typescript"]


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
            "--dry-run",
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
            "--dry-run",
        ]
    )

    assert exit_code == 1
    captured = capsys.readouterr()
    assert "Scraper Error" in captured.err


def test_given_live_svn_repos_and_sheets_credentials_when_running_cli_sync_then_synchronizes_inventory(
    svn_server: dict[str, Any],
    tmp_path: Any,
) -> None:
    """Verify full CLI flow scraping live SVN and persisting to inventory backend."""
    creds_file = tmp_path / "gcp-credentials.json"
    creds_file.write_text('{"type": "service_account"}')

    repo_url = f"{svn_server['url']}/standard-app"
    from gh_migration_inventory_manager.cli import build_parser

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
            "sheet-xyz-123",
            "--credentials-path",
            str(creds_file),
            "--sheet-name",
            "Inventario",
            "--initial-status",
            "waiting",
        ]
    )

    out_stream = io.StringIO()
    with (
        patch("gh_migration_inventory_manager.google.Spreadsheet") as mock_sheet_cls,
        patch("gh_migration_inventory_manager.cli.Inventory") as mock_inv_cls,
    ):
        mock_inv = mock_inv_cls.return_value
        mock_inv.sync_repositories.return_value = [
            InventoryRecord(
                name="standard-app",
                stack="Java",
                migration_status=MigrationStatus.WAITTING,
            )
        ]

        exit_code = run_cli(args, out_stream=out_stream)

        assert exit_code == 0
        mock_sheet_cls.assert_called_once()
        mock_inv.sync_repositories.assert_called_once()

        synced_repos = mock_inv.sync_repositories.call_args[0][0]
        assert len(synced_repos) == 1
        assert synced_repos[0].name == "standard-app"

        output = out_stream.getvalue()
        assert "Successfully synchronized 1 repositories" in output
