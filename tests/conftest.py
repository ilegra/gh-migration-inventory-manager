"""Pytest configuration and shared fixtures."""

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from gh_migration_inventory_manager.google import (
    Credentials,
    Row,
    RowConverter,
    Spreadsheet,
)
from gh_migration_inventory_manager.models import InventoryRecord, MigrationStatus


@pytest.fixture
def sample_record() -> InventoryRecord:
    """Fixture providing a sample InventoryRecord."""
    return InventoryRecord(
        name="test-org/sample-repo",
        stack="Python",
        last_commit=datetime(2026, 10, 10, tzinfo=timezone.utc),
        gh_migratation=datetime(2026, 10, 1, tzinfo=timezone.utc),
        origin_deactivation=None,
        migration_status=MigrationStatus.ACTIVE_IN_BOTH,
    )


@pytest.fixture
def sample_row(sample_record: InventoryRecord) -> Row:
    """Fixture providing a sample Row derived from sample_record."""
    return RowConverter.from_inventory_record(sample_record, row_index=2)


@pytest.fixture
def mock_sheets_service() -> MagicMock:
    """Fixture providing a mocked Google Sheets API service."""
    service = MagicMock()
    return service


@pytest.fixture
def mock_spreadsheet(mock_sheets_service: MagicMock) -> Spreadsheet:
    """Fixture providing a Spreadsheet instance using the mocked service."""
    return Spreadsheet(
        id="test-sheet-id",
        credentials=Credentials(credentials_path="dummy-path.json"),
        sheet_name="Inventario",
        service=mock_sheets_service,
    )
