"""Conversion adapters between repository discovery models and inventory entities."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timezone
from typing import Any

from gh_migration_inventory_manager.inventory import (
    InventoryRecord,
    MigrationStatus,
)
from gh_migration_inventory_manager.scrap import Repository, format_stacks


class RepositoryConverter:
    """Conversion adapter bridging VCS discovery models and inventory records."""

    def __init__(self) -> None:
        """Initialize a new RepositoryConverter instance."""

    def _validate_status(self, status: Any) -> MigrationStatus:
        """Validate that status is a valid MigrationStatus or valid status string."""
        if isinstance(status, MigrationStatus):
            return status

        valid_display = ", ".join(
            f"'{v}'" for v in sorted({s.value for s in MigrationStatus})
        )

        if status is None:
            raise ValueError(
                "Invalid migration status: None. Status cannot be None. "
                f"Expected a valid MigrationStatus or one of: {valid_display}."
            )

        if isinstance(status, str):
            clean = status.strip()
            if not clean:
                raise ValueError(
                    "Invalid migration status: status cannot be empty. "
                    f"Expected a valid MigrationStatus or one of: {valid_display}."
                )

            try:
                return MigrationStatus(clean.lower())
            except ValueError:
                pass

            try:
                return MigrationStatus[clean.upper()]
            except KeyError:
                pass

            raise ValueError(
                f"Invalid migration status: '{status}'. "
                f"Expected a valid MigrationStatus or one of: {valid_display}."
            )

        raise ValueError(
            f"Invalid migration status type: '{type(status).__name__}'. "
            f"Expected MigrationStatus or one of: {valid_display}."
        )

    def _apply_status_timestamps(
        self, record: InventoryRecord, status: MigrationStatus
    ) -> None:
        """Update migration timestamps based on transition status."""
        if status in (MigrationStatus.ACTIVE_IN_BOTH, MigrationStatus.MIGRATED):
            now = datetime.now(timezone.utc)
            if record.gh_migratation is None:
                record.gh_migratation = now
            if (
                status == MigrationStatus.MIGRATED
                and record.origin_deactivation is None
            ):
                record.origin_deactivation = now
        elif status in (MigrationStatus.WAITTING, MigrationStatus.SKIPPED):
            record.gh_migratation = None
            record.origin_deactivation = None

    def to_inventory_record(
        self,
        repository: Repository | Any,
        initial_status: MigrationStatus | str = MigrationStatus.WAITTING,
    ) -> InventoryRecord:
        """Convert a single discovered repository model into an InventoryRecord.

        Args:
            repository: Discovered repository model (or repository-like object).
            initial_status: Initial migration status for the record.

        Returns:
            An InventoryRecord instance configured with formatted stack and status.

        Raises:
            ValueError: If repository is None, lacks a name, or
                initial_status is invalid.
        """
        if repository is None:
            raise ValueError("Repository cannot be None.")

        name = getattr(repository, "name", None)
        clean_name = str(name).strip() if name is not None else ""
        if not clean_name:
            raise ValueError("Repository name cannot be empty.")

        project = str(getattr(repository, "project", "") or "").strip()

        if hasattr(repository, "stacks") and isinstance(repository.stacks, list):
            stack = format_stacks(repository.stacks)
        elif hasattr(repository, "stack"):
            stack = str(getattr(repository, "stack", "") or "").strip()
        else:
            stack = ""

        last_commit = getattr(repository, "last_commit", None)
        valid_status = self._validate_status(initial_status)

        record = InventoryRecord(
            name=clean_name,
            project=project,
            stack=stack,
            last_commit=last_commit,
            migration_status=valid_status,
        )
        self._apply_status_timestamps(record, valid_status)
        return record

    def to_inventory_records(
        self,
        repositories: Iterable[Repository | Any],
        initial_status: MigrationStatus | str = MigrationStatus.WAITTING,
    ) -> list[InventoryRecord]:
        """Convert a collection of repository models into InventoryRecord instances.

        Args:
            repositories: Iterable of discovered repository models.
            initial_status: Initial migration status for all records.

        Returns:
            A list of converted InventoryRecord instances.
        """
        if repositories is None:
            raise ValueError("Repositories collection cannot be None.")
        return [
            self.to_inventory_record(repo, initial_status=initial_status)
            for repo in repositories
        ]


__all__ = ["RepositoryConverter"]
