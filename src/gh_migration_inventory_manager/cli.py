"""Command-line interface for repository discovery and inventory synchronization."""

from __future__ import annotations

import argparse
import sys
from typing import Any

import gh_migration_inventory_manager.bitbucket_scrap  # noqa: F401
import gh_migration_inventory_manager.jenkins_scrap  # noqa: F401
import gh_migration_inventory_manager.svn_scrap  # noqa: F401
from gh_migration_inventory_manager.converter import (
    PipelineConverter,
    PolicyConverter,
    RepositoryConverter,
    TeamConverter,
)
from gh_migration_inventory_manager.inventory import (
    Inventory,
    InventoryAccessError,
    InventoryError,
    InventoryNotFoundError,
    MigrationStatus,
    PipelineInventory,
    PolicyInventory,
    TeamInventory,
)
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
            "GitHub Migration Inventory Manager - discover repositories and CI/CD "
            "pipelines from platforms and synchronize with Google Sheets."
        ),
    )

    subparsers = parser.add_subparsers(
        dest="command",
        title="commands",
        description="Available commands",
    )

    scrape_parser = subparsers.add_parser(
        "scrape",
        help=(
            "Scrape repositories or CI/CD pipelines from an enterprise "
            "platform and synchronize with inventory"
        ),
    )

    platform_subparsers = scrape_parser.add_subparsers(
        dest="platform",
        title="platforms",
        description="Supported VCS and CI/CD platforms",
        required=True,
    )

    status_choices = sorted({s.value for s in MigrationStatus} | {"waiting"})

    for platform_name in reg.list_platforms():
        provider = reg.get(platform_name)
        p_subparser = platform_subparsers.add_parser(
            provider.platform_name,
            help=provider.description,
            description=provider.description,
        )
        p_subparser.add_argument(
            "--spreadsheet-id",
            default=None,
            help="Google Spreadsheet ID",
        )
        p_subparser.add_argument(
            "--credentials-path",
            default=None,
            help="Path to GCP service account JSON key",
        )
        default_sheet = getattr(provider, "default_sheet_name", "Inventario")
        p_subparser.add_argument(
            "--sheet-name",
            default=default_sheet,
            help=f"Google Sheets tab name (default: {default_sheet})",
        )
        p_subparser.add_argument(
            "--initial-status",
            choices=status_choices,
            default=MigrationStatus.WAITTING.value,
            help="Initial status for newly discovered items (default: waitting)",
        )
        provider.configure_parser(p_subparser)

    return parser


def run_cli(
    args: argparse.Namespace,
    registry: ScraperRegistry | None = None,
    inventory: Inventory | None = None,
    converter: RepositoryConverter | None = None,
    out_stream: Any = None,
    err_stream: Any = None,
    *,
    spreadsheet: Any = None,
    pipeline_converter: Any = None,
    pipeline_inventory: Any = None,
    team_converter: Any = None,
    team_inventory: Any = None,
    policy_converter: Any = None,
    policy_inventory: Any = None,
) -> int | None:
    """Execute the CLI operation based on parsed arguments.

    Returns the number of items inventoried on success, or None on failure.
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
    items = scraper.scrape()

    initial_status = getattr(args, "initial_status", MigrationStatus.WAITTING)
    active_inv: Any = None

    is_pipeline = (
        getattr(scraper, "target_type", None) == "pipeline"
        or getattr(scraper, "scraper_type", None) == "pipeline"
        or getattr(provider, "target_type", None) == "pipeline"
    )
    is_team = (
        getattr(scraper, "target_type", None) == "team"
        or getattr(scraper, "scraper_type", None) == "team"
        or getattr(provider, "target_type", None) == "team"
    )
    is_policy = (
        getattr(scraper, "target_type", None) == "policy"
        or getattr(scraper, "scraper_type", None) == "policy"
        or getattr(provider, "target_type", None) == "policy"
    )

    def _extract_target(exc: Exception) -> str:
        return (
            getattr(args, "spreadsheet_id", None)
            or getattr(active_inv, "spreadsheet_id", None)
            or getattr(inventory, "spreadsheet_id", None)
            or (str(exc).split("'")[1] if "'" in str(exc) else "")
        )

    try:
        inv_cls: (
            type[PipelineInventory]
            | type[TeamInventory]
            | type[PolicyInventory]
            | type[Inventory]
        )
        active_converter: Any
        if is_pipeline:
            sheet_name = getattr(args, "sheet_name", None) or "Workflows"
            active_converter = (
                pipeline_converter
                if pipeline_converter is not None
                else (converter if converter is not None else PipelineConverter())
            )
            records = active_converter.to_pipeline_records(
                items, initial_status=initial_status
            )
            inv_cls = PipelineInventory
            injected_inv = (
                pipeline_inventory if pipeline_inventory is not None else inventory
            )
        elif is_team:
            sheet_name = getattr(args, "sheet_name", None) or "Topology"
            active_converter = (
                team_converter
                if team_converter is not None
                else (converter if converter is not None else TeamConverter())
            )
            records = active_converter.to_team_records(
                items, initial_status=initial_status
            )
            inv_cls = TeamInventory
            injected_inv = team_inventory if team_inventory is not None else inventory
        elif is_policy:
            sheet_name = getattr(args, "sheet_name", None) or "Policies"
            active_converter = policy_converter or converter or PolicyConverter()
            records = active_converter.to_policy_records(
                items, initial_status=initial_status
            )
            inv_cls = PolicyInventory
            injected_inv = policy_inventory or inventory
        else:
            sheet_name = getattr(args, "sheet_name", None) or "Inventario"
            active_converter = conv
            records = active_converter.to_inventory_records(
                items, initial_status=initial_status
            )
            inv_cls = Inventory
            injected_inv = inventory

        if injected_inv is not None and not hasattr(injected_inv, "sync_records"):
            active_inv = inv_cls(spreadsheet=injected_inv, converter=active_converter)
        elif injected_inv is None:
            active_inv = inv_cls(
                spreadsheet=spreadsheet,
                spreadsheet_id=getattr(args, "spreadsheet_id", None),
                credentials_path=getattr(args, "credentials_path", None),
                sheet_name=sheet_name,
                converter=active_converter,
            )
        else:
            active_inv = injected_inv

        synced_records = active_inv.sync_records(records)
    except InventoryNotFoundError as exc:
        target = _extract_target(exc)
        print(
            f"Error: Inventory not found: '{target}'.",
            file=err,
        )
        return None
    except InventoryAccessError as exc:
        target = _extract_target(exc)
        print(
            f"Error: Access denied to inventory: '{target}'.",
            file=err,
        )
        return None
    except FileNotFoundError:
        creds_path = getattr(args, "credentials_path", None)
        print(
            f"Error: Google credentials file not found: '{creds_path}'.",
            file=err,
        )
        return None
    except ValueError as exc:
        msg = str(exc)
        if "Missing Google Spreadsheet ID" in msg:
            print(
                "Error: Missing Google Spreadsheet ID. Pass --spreadsheet-id.",
                file=err,
            )
        elif "Missing Google credentials path" in msg:
            print(
                "Error: Missing Google credentials path. Pass --credentials-path.",
                file=err,
            )
        else:
            print(f"Configuration Error: {exc}", file=err)
        return None
    except InventoryError as exc:
        print(f"Inventory Error: {exc}", file=err)
        return None

    count = len(synced_records)
    print(count, file=out)
    return count


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
        if result is None:
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
    except InventoryAccessError as exc:
        print(f"Access Error: {exc}", file=sys.stderr)
        return 1
    except InventoryNotFoundError as exc:
        print(f"Inventory Error: {exc}", file=sys.stderr)
        return 2
    except InventoryError as exc:
        print(f"Inventory Error: {exc}", file=sys.stderr)
        return 2
    except ValueError as exc:
        print(f"Configuration Error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130
    except Exception as exc:  # pylint: disable=broad-except
        print(f"Unexpected Error: {exc}", file=sys.stderr)
        return 1
