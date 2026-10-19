"""Command-line interface for repository discovery and inventory synchronization."""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

from gh_migration_inventory_manager.inventory import Inventory, MigrationStatus
from gh_migration_inventory_manager.scrap import (
    Repository,
    ScraperAuthenticationError,
    ScraperConnectionError,
    ScraperError,
    ScraperRegistry,
    default_scraper_registry,
    format_stacks,
)


def _render_table(repos: list[Repository]) -> str:
    """Render a formatted ASCII table of discovered repositories."""
    if not repos:
        return "No repositories discovered."

    headers = ["Repository", "Project", "Stack(s)", "Last Commit", "Branch"]
    rows: list[list[str]] = []
    for r in repos:
        commit_str = r.last_commit.strftime("%Y-%m-%d %H:%M") if r.last_commit else "-"
        branch_str = r.default_branch or "-"
        stacks_str = format_stacks(r.stacks) or "-"
        rows.append([r.name, r.project or "-", stacks_str, commit_str, branch_str])

    col_widths = [
        max(len(cell) for cell in col) for col in zip(headers, *rows, strict=False)
    ]

    def format_row(cells: list[str]) -> str:
        return " | ".join(cell.ljust(col_widths[i]) for i, cell in enumerate(cells))

    separator = "-+-".join("-" * w for w in col_widths)
    output_lines = [
        format_row(headers),
        separator,
        *(format_row(row) for row in rows),
        "",
        f"Total discovered: {len(repos)}",
    ]
    return "\n".join(output_lines)


def _render_json(repos: list[Repository]) -> str:
    """Render JSON formatted output of discovered repositories."""
    data = [
        {
            "name": r.name,
            "project": r.project,
            "organization": r.organization,
            "path": r.path,
            "url": r.url,
            "default_branch": r.default_branch,
            "stacks": [s.value for s in r.stacks],
            "last_commit": r.last_commit.isoformat() if r.last_commit else None,
            "metadata": r.metadata,
        }
        for r in repos
    ]
    return json.dumps(data, indent=2)


def build_parser(
    registry: ScraperRegistry | None = None,
) -> argparse.ArgumentParser:
    """Build and configure the command-line argument parser."""
    reg = registry if registry is not None else default_scraper_registry

    parser = argparse.ArgumentParser(
        prog="gh-migration-inventory",
        description=(
            "GitHub Migration Inventory Manager - discover repositories from VCS "
            "platforms and synchronize with Google Sheets."
        ),
    )

    subparsers = parser.add_subparsers(
        dest="command",
        title="commands",
        description="Available commands",
    )

    scrape_parser = subparsers.add_parser(
        "scrape",
        help="Scrape repositories from a VCS platform and synchronize with inventory",
    )

    platform_subparsers = scrape_parser.add_subparsers(
        dest="platform",
        title="platforms",
        description="Supported VCS platforms",
        required=True,
    )

    status_choices = sorted({s.value for s in MigrationStatus} | {"waiting"})

    # Persistence options shared across platforms
    persistence_parent = argparse.ArgumentParser(add_help=False)
    persistence_parent.add_argument(
        "--spreadsheet-id",
        default=os.environ.get("SPREADSHEET_ID")
        or os.environ.get("GOOGLE_SPREADSHEET_ID"),
        help="Google Spreadsheet ID (or SPREADSHEET_ID env var)",
    )
    persistence_parent.add_argument(
        "--credentials-path",
        default=os.environ.get("GCP_KEY_PATH")
        or os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"),
        help="Path to GCP service account JSON key (or GCP_KEY_PATH env var)",
    )
    persistence_parent.add_argument(
        "--sheet-name",
        default=os.environ.get("INVENTORY_SHEET_NAME", "Inventario"),
        help="Google Sheets tab name (default: Inventario)",
    )
    persistence_parent.add_argument(
        "--initial-status",
        choices=status_choices,
        default=MigrationStatus.WAITTING.value,
        help="Initial status for newly discovered repositories (default: waitting)",
    )
    persistence_parent.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview discovered repositories without persisting to Google Sheets",
    )
    persistence_parent.add_argument(
        "--format",
        choices=["table", "json"],
        default="table",
        help="Output format for discovered repositories (default: table)",
    )

    for platform_name in reg.list_platforms():
        provider = reg.get(platform_name)
        p_subparser = platform_subparsers.add_parser(
            provider.platform_name,
            parents=[persistence_parent],
            help=provider.description,
            description=provider.description,
        )
        provider.configure_parser(p_subparser)

    return parser


def run_cli(
    args: argparse.Namespace,
    registry: ScraperRegistry | None = None,
    out_stream: Any = None,
    err_stream: Any = None,
) -> int:
    """Execute the CLI operation based on parsed arguments."""
    out = out_stream if out_stream is not None else sys.stdout
    err = err_stream if err_stream is not None else sys.stderr
    reg = registry if registry is not None else default_scraper_registry

    if getattr(args, "command", None) != "scrape" or not getattr(
        args, "platform", None
    ):
        return 0

    provider = reg.get(args.platform)
    scraper = provider.create_scraper(args)

    repos = scraper.scrape()

    if getattr(args, "format", "table") == "json":
        print(_render_json(repos), file=out)
    else:
        print(_render_table(repos), file=out)

    if getattr(args, "dry_run", False):
        dry_msg = (
            f"\n[DRY RUN] Discovered {len(repos)} repositories. "
            "No changes written to Google Sheets."
        )
        if getattr(args, "format", "table") == "json":
            print(dry_msg.strip(), file=err)
        else:
            print(dry_msg, file=out)
        return 0

    spreadsheet_id = getattr(args, "spreadsheet_id", None)
    if not spreadsheet_id:
        print(
            "Error: Missing Google Spreadsheet ID. Pass --spreadsheet-id or set "
            "the SPREADSHEET_ID environment variable.",
            file=err,
        )
        return 2

    credentials_path = getattr(args, "credentials_path", None)
    if not credentials_path:
        print(
            "Error: Missing Google credentials path. Pass --credentials-path or set "
            "the GCP_KEY_PATH environment variable.",
            file=err,
        )
        return 2

    try:
        from gh_migration_inventory_manager.google import Credentials, Spreadsheet

        credentials = Credentials(credentials_path=credentials_path)
        spreadsheet = Spreadsheet(
            id=spreadsheet_id,
            credentials=credentials,
            sheet_name=getattr(args, "sheet_name", "Inventario"),
        )
    except (FileNotFoundError, OSError):
        print(
            f"Error: Google credentials file not found: '{credentials_path}'.",
            file=err,
        )
        return 2

    inventory = Inventory(spreadsheet=spreadsheet)

    initial_status = getattr(args, "initial_status", MigrationStatus.WAITTING)
    synced_records = inventory.sync_repositories(repos, initial_status=initial_status)

    print(
        f"\nSuccessfully synchronized {len(synced_records)} repositories "
        f"(Spreadsheet: {spreadsheet_id}, Sheet: '{args.sheet_name}').",
        file=out,
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    """Main CLI entry point."""
    parser = build_parser()
    args_list = argv if argv is not None else sys.argv[1:]

    if not args_list:
        parser.print_help()
        return 0

    try:
        args = parser.parse_args(args_list)
        return run_cli(args)
    except ScraperAuthenticationError as exc:
        print(f"Authentication Error: {exc}", file=sys.stderr)
        return 1
    except ScraperConnectionError as exc:
        print(f"Connection Error: {exc}", file=sys.stderr)
        return 1
    except ScraperError as exc:
        print(f"Scraper Error: {exc}", file=sys.stderr)
        return 1
    except ValueError as exc:
        print(f"Configuration Error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130
    except Exception as exc:  # pylint: disable=broad-except
        print(f"Unexpected Error: {exc}", file=sys.stderr)
        return 1
