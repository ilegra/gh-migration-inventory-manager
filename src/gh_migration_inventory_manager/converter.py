"""Conversion adapters between repository discovery models and inventory entities."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from gh_migration_inventory_manager.inventory import (
    Inventory,
    InventoryRecord,
    MigrationStatus,
)
from gh_migration_inventory_manager.scrap import Repository, format_stacks


class RepositoryConverter:
    """Conversion utility bridging VCS discovery models and inventory records."""

    @staticmethod
    def to_inventory_record(
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
        valid_status = Inventory._validate_status(initial_status)

        record = InventoryRecord(
            name=clean_name,
            project=project,
            stack=stack,
            last_commit=last_commit,
            migration_status=valid_status,
        )
        Inventory._apply_status_timestamps(record, valid_status)
        return record

    @classmethod
    def to_inventory_records(
        cls,
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
            cls.to_inventory_record(repo, initial_status=initial_status)
            for repo in repositories
        ]

    @staticmethod
    def from_inventory_record(record: InventoryRecord) -> Repository:
        """Convert an InventoryRecord back into a canonical Repository domain model.

        Args:
            record: The inventory record to convert.

        Returns:
            A Repository domain model instance.

        Raises:
            ValueError: If record is None or record name is empty.
        """
        if record is None:
            raise ValueError("InventoryRecord cannot be None.")
        clean_name = str(getattr(record, "name", "") or "").strip()
        if not clean_name:
            raise ValueError("Record name cannot be empty.")

        return Repository(
            name=clean_name,
            project=record.project or None,
            last_commit=record.last_commit,
        )


__all__ = ["RepositoryConverter"]
