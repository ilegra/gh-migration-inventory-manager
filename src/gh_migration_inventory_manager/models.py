"""Data models and entities for GitHub Migration Inventory Manager."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

_PT_MONTHS: dict[int, str] = {
    1: "Janeiro",
    2: "Fevereiro",
    3: "Março",
    4: "Abril",
    5: "Maio",
    6: "Junho",
    7: "Julho",
    8: "Agosto",
    9: "Setembro",
    10: "Outubro",
    11: "Novembro",
    12: "Dezembro",
}


def format_migration_month(dt: datetime | None) -> str:
    """Format a datetime into a localized month/year string (e.g. 'Outubro/2026')."""
    if dt is None:
        return ""
    month_name = _PT_MONTHS.get(dt.month, "")
    return f"{month_name}/{dt.year}" if month_name else ""


class MigrationStatus(str, Enum):
    """Status representing the state of an inventory item during migration."""

    WAITTING = "waitting"
    WAITING = "waitting"
    ACTIVE_IN_BOTH = "active_in_both"
    MIGRATED = "migrated"
    SKIPPED = "skipped"

    def to_sheet_label(self) -> str:
        """Map the status enum to the localized spreadsheet display string."""
        match self:
            case MigrationStatus.MIGRATED:
                return "Migrados (Somente GitHub)"
            case MigrationStatus.ACTIVE_IN_BOTH:
                return "Migrando (Ativos em Ambas)"
            case MigrationStatus.SKIPPED:
                return "Ignorado"
            case _:
                return "Aguardando Migração"


@dataclass
class InventoryRecord:
    """Represents an item in the inventory: a repository migrated or to be migrated."""

    name: str
    stack: str = ""
    last_commit: datetime | None = None
    gh_migratation: datetime | None = None
    origin_deactivation: datetime | None = None
    migration_status: MigrationStatus = MigrationStatus.WAITTING

    @property
    def gh_migration(self) -> datetime | None:
        """Alias for gh_migratation."""
        return self.gh_migratation

    @gh_migration.setter
    def gh_migration(self, value: datetime | None) -> None:
        self.gh_migratation = value

    @property
    def migration_month(self) -> str:
        """Return the formatted migration month (e.g. 'Outubro/2026')."""
        return format_migration_month(self.gh_migratation)
