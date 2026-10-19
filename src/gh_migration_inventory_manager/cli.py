"""Command-line interface for repository discovery and inventory synchronization."""

from __future__ import annotations

import argparse
import os
import sys
from typing import Any

from gh_migration_inventory_manager.converter import RepositoryConverter
from gh_migration_inventory_manager.google import (
    Credentials,
    Spreadsheet,
    SpreadsheetAccessError,
    SpreadsheetError,
    SpreadsheetNotFoundError,
)
from gh_migration_inventory_manager.inventory import Inventory, MigrationStatus
from gh_migration_inventory_manager.scrap import (
    ScraperAuthenticationError,
    ScraperConnectionError,
    ScraperError,
    ScraperRegistry,
    default_scraper_registry,
)


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
    spreadsheet: Spreadsheet | None = None,
    converter: RepositoryConverter | None = None,
    out_stream: Any = None,
    err_stream: Any = None,
) -> Spreadsheet | None:
    """Execute the CLI operation based on parsed arguments.

    Returns the updated Spreadsheet reference on success, or None on failure or dry-run.
    """
    out = out_stream if out_stream is not None else sys.stdout
    err = err_stream if err_stream is not None else sys.stderr
    reg = registry if registry is not None else default_scraper_registry
    conv = converter if converter is not None else RepositoryConverter()

    if getattr(args, "command", None) != "scrape" or not getattr(
        args, "platform", None
    ):
        return None

    provider = reg.get(args.platform)
    scraper = provider.create_scraper(args)
    repos = scraper.scrape()

    initial_status = getattr(args, "initial_status", MigrationStatus.WAITTING)
    records = conv.to_inventory_records(repos, initial_status=initial_status)

    if getattr(args, "dry_run", False):
        print(
            f"[DRY RUN] Discovered {len(records)} repositories. "
            "No changes written to Google Sheets.",
            file=out,
        )
        return spreadsheet

    target_spreadsheet = spreadsheet
    if target_spreadsheet is None:
        spreadsheet_id = getattr(args, "spreadsheet_id", None)
        if not spreadsheet_id or not str(spreadsheet_id).strip():
            print(
                "Error: Missing Google Spreadsheet ID. Pass --spreadsheet-id or set "
                "the SPREADSHEET_ID environment variable.",
                file=err,
            )
            return None

        credentials_path = getattr(args, "credentials_path", None)
        if not credentials_path or not str(credentials_path).strip():
            print(
                "Error: Missing Google credentials path. Pass "
                "--credentials-path or set the GCP_KEY_PATH environment variable.",
                file=err,
            )
            return None

        try:
            credentials = Credentials(credentials_path=credentials_path)
            target_spreadsheet = Spreadsheet(
                id=spreadsheet_id.strip(),
                credentials=credentials,
                sheet_name=getattr(args, "sheet_name", "Inventario"),
            )
        except (FileNotFoundError, OSError):
            print(
                f"Error: Google credentials file not found: '{credentials_path}'.",
                file=err,
            )
            return None

    # Validate mandatory spreadsheet reference
    try:
        target_spreadsheet.validate()
    except SpreadsheetNotFoundError:
        print(
            f"Error: Google Spreadsheet not found: '{target_spreadsheet.id}'.",
            file=err,
        )
        return None
    except SpreadsheetAccessError:
        print(
            f"Error: Access denied to Google Spreadsheet: '{target_spreadsheet.id}'.",
            file=err,
        )
        return None
    except ValueError as exc:
        print(f"Configuration Error: {exc}", file=err)
        return None
    except SpreadsheetError as exc:
        print(f"Spreadsheet Error: {exc}", file=err)
        return None

    inventory = Inventory(spreadsheet=target_spreadsheet, converter=conv)
    inventory.sync_records(records)

    sheet_ref = f"https://docs.google.com/spreadsheets/d/{target_spreadsheet.id}"
    print(sheet_ref, file=out)
    return target_spreadsheet


def main(argv: list[str] | None = None) -> int:
    """Main CLI entry point."""
    parser = build_parser()
    args_list = argv if argv is not None else sys.argv[1:]

    if not args_list:
        parser.print_help()
        return 0

    try:
        args = parser.parse_args(args_list)
        result = run_cli(args)
        if result is None and not getattr(args, "dry_run", False):
            return 2
        return 0
    except ScraperAuthenticationError as exc:
        print(f"Authentication Error: {exc}", file=sys.stderr)
        return 1
    except ScraperConnectionError as exc:
        print(f"Connection Error: {exc}", file=sys.stderr)
        return 1
    except ScraperError as exc:
        print(f"Scraper Error: {exc}", file=sys.stderr)
        return 1
    except SpreadsheetAccessError as exc:
        print(f"Access Error: {exc}", file=sys.stderr)
        return 1
    except SpreadsheetNotFoundError as exc:
        print(f"Spreadsheet Error: {exc}", file=sys.stderr)
        return 2
    except SpreadsheetError as exc:
        print(f"Spreadsheet Error: {exc}", file=sys.stderr)
        return 2
    except ValueError as exc:
        print(f"Configuration Error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130
    except Exception as exc:  # pylint: disable=broad-except
        print(f"Unexpected Error: {exc}", file=sys.stderr)
        return 1
