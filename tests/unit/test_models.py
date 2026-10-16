"""Unit tests for models and MigrationStatus."""

from datetime import datetime, timezone

import pytest

from gh_migration_inventory_manager.models import (
    InventoryRecord,
    MigrationStatus,
    format_migration_month,
)


def test_models_do_not_contain_from_sheet_label() -> None:
    """Ensure business rule from_sheet_label is not in models."""
    assert not hasattr(MigrationStatus, "from_sheet_label")


@pytest.mark.parametrize(
    ("status", "expected_label"),
    [
        (MigrationStatus.MIGRATED, "Migrados (Somente GitHub)"),
        (MigrationStatus.ACTIVE_IN_BOTH, "Migrando (Ativos em Ambas)"),
        (MigrationStatus.SKIPPED, "Ignorado"),
        (MigrationStatus.WAITTING, "Aguardando Migração"),
        (MigrationStatus.WAITING, "Aguardando Migração"),
    ],
)
def test_migration_status_to_sheet_label(
    status: MigrationStatus, expected_label: str
) -> None:
    """Test mapping MigrationStatus to sheet display label."""
    assert status.to_sheet_label() == expected_label


def test_inventory_record_properties() -> None:
    """Test InventoryRecord properties and aliases."""
    record = InventoryRecord(
        name="org/repo",
        gh_migratation=datetime(2026, 10, 15, tzinfo=timezone.utc),
    )
    assert record.gh_migration == record.gh_migratation
    assert record.migration_month == "Outubro/2026"

    record.gh_migration = datetime(2026, 11, 1, tzinfo=timezone.utc)
    assert record.gh_migratation == datetime(2026, 11, 1, tzinfo=timezone.utc)


def test_format_migration_month() -> None:
    """Test format_migration_month formatting."""
    assert format_migration_month(None) == ""
    assert format_migration_month(datetime(2026, 1, 1)) == "Janeiro/2026"
    assert format_migration_month(datetime(2026, 12, 31)) == "Dezembro/2026"
