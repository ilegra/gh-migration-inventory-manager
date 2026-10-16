"""Integration test verifying library usage and live Google Sheets flow."""

import os
import uuid

import pytest

from gh_migration_inventory_manager import (
    Credentials,
    Inventory,
    InventoryRecord,
    Iventory,
    MigrationStatus,
    Row,
    Spreadsheet,
    Spreadshseet,
    __version__,
)

_SPREADSHEET_ID = "1Z0PAjv8KK6WBrS2356gtR3VE0ulD1-8LM4DPQiG-Nkk"


def _get_credentials_path() -> str | None:
    for candidate in (
        os.environ.get("GCP_KEY_PATH"),
        os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"),
        "gcp-key.json",
        "gcp-api-keys.json",
    ):
        if candidate and os.path.exists(candidate):
            return candidate
    return None


def test_public_api_exports() -> None:
    """Verify that all public symbols are accessible from top-level package."""
    assert __version__ == "0.1.0"
    assert Inventory is not None
    assert Iventory is not None
    assert Spreadsheet is not None
    assert Spreadshseet is not None
    assert Row is not None
    assert Credentials is not None
    assert InventoryRecord is not None
    assert MigrationStatus is not None


def test_live_google_sheets_integration_flow() -> None:
    """Live integration test against Google Sheets with automated canary cleanup."""
    creds_path = _get_credentials_path()
    if not creds_path:
        pytest.skip("GCP credentials file not found; skipping live integration test.")

    credentials = Credentials(credentials_path=creds_path)
    spreadsheet = Spreadsheet(
        id=_SPREADSHEET_ID,
        credentials=credentials,
        sheet_name="Inventario",
    )
    inventory = Inventory(spreadsheet=spreadsheet)

    # 1. Validate read of existing inventory records
    api_gateway_rows = spreadsheet.find_by("name", "api-gateway")
    assert len(api_gateway_rows) >= 1
    existing_record = api_gateway_rows[0].to_inventory_record()
    assert existing_record.name == "api-gateway"
    assert existing_record.stack == "Java"

    # 2. Add canary repository entry
    canary_id = f"test-canary-{uuid.uuid4().hex[:8]}"
    created_row_index: int | None = None

    try:
        record = inventory.update_progress(canary_id, MigrationStatus.ACTIVE_IN_BOTH)
        assert record.name == canary_id
        assert record.migration_status == MigrationStatus.ACTIVE_IN_BOTH
        assert record.gh_migratation is not None
        assert record.origin_deactivation is None

        # Verify it can be retrieved from sheet
        found_rows = spreadsheet.find_by("name", canary_id)
        assert len(found_rows) == 1
        created_row_index = found_rows[0].row_index

        # 3. Update canary entry to MIGRATED
        migrated_record = inventory.update_progress(canary_id, MigrationStatus.MIGRATED)
        assert migrated_record.name == canary_id
        assert migrated_record.migration_status == MigrationStatus.MIGRATED
        assert migrated_record.origin_deactivation is not None

    finally:
        # Cleanup canary row from sheet
        if created_row_index is not None:
            spreadsheet.clear_row(created_row_index)
