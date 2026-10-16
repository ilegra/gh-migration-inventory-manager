"""Unit tests for models and MigrationStatus."""

import pytest

from gh_migration_inventory_manager.models import MigrationStatus


@pytest.mark.parametrize(
    ("label", "expected_status"),
    [
        ("Migrados (Somente GitHub)", MigrationStatus.MIGRATED),
        ("somente github", MigrationStatus.MIGRATED),
        ("migrated", MigrationStatus.MIGRATED),
        ("migrados", MigrationStatus.MIGRATED),
        ("Migrando (Ativos em Ambas)", MigrationStatus.ACTIVE_IN_BOTH),
        ("ativos em ambas", MigrationStatus.ACTIVE_IN_BOTH),
        ("migrando", MigrationStatus.ACTIVE_IN_BOTH),
        ("active_in_both", MigrationStatus.ACTIVE_IN_BOTH),
        ("Ignorado", MigrationStatus.SKIPPED),
        ("ignorado", MigrationStatus.SKIPPED),
        ("não migrar", MigrationStatus.SKIPPED),
        ("skipped", MigrationStatus.SKIPPED),
        ("Aguardando Migração", MigrationStatus.WAITTING),
        ("aguardando", MigrationStatus.WAITTING),
        ("waitting", MigrationStatus.WAITTING),
        ("waiting", MigrationStatus.WAITTING),
        ("", MigrationStatus.WAITTING),
        ("   ", MigrationStatus.WAITTING),
    ],
)
def test_from_sheet_label_valid_labels(
    label: str, expected_status: MigrationStatus
) -> None:
    """Test that recognized spreadsheet labels map to the correct MigrationStatus."""
    assert MigrationStatus.from_sheet_label(label) == expected_status


@pytest.mark.parametrize(
    "invalid_label",
    [
        "unknown_status",
        "invalid",
        "completed",
        "pending",
        "random string",
    ],
)
def test_from_sheet_label_invalid_labels_raise_value_error(
    invalid_label: str,
) -> None:
    """Test that unrecognized spreadsheet labels raise ValueError."""
    expected_match = f"Invalid migration status label: '{invalid_label}'"
    with pytest.raises(ValueError, match=expected_match):
        MigrationStatus.from_sheet_label(invalid_label)
