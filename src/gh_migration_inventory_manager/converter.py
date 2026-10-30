"""Conversion adapters between repository discovery models and inventory entities."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timezone
from typing import Any

from gh_migration_inventory_manager.inventory import (
    InventoryRecord,
    MigrationStatus,
    PipelineRecord,
    TeamRecord,
    validate_migration_status,
)
from gh_migration_inventory_manager.scrap import (
    Pipeline,
    Repository,
    TeamPermission,
    format_stacks,
    normalize_role,
    slugify,
)


class RepositoryConverter:
    """Conversion adapter bridging VCS discovery models and inventory records."""

    def __init__(self) -> None:
        """Initialize a new RepositoryConverter instance."""

    def _validate_status(self, status: Any) -> MigrationStatus:
        """Validate that status is a valid MigrationStatus or valid status string."""
        return validate_migration_status(status)

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


def _format_delimited_string(raw: Any) -> str:
    """Format a string or iterable of items into a sorted comma-delimited string."""
    if isinstance(raw, str):
        return raw.strip()
    if isinstance(raw, Iterable):
        return ", ".join(
            sorted({str(item).strip() for item in raw if str(item).strip()})
        )
    return str(raw).strip() if raw is not None else ""


class PipelineConverter:
    """Conversion adapter for CI/CD pipeline discovery models and inventory entities."""

    def __init__(self) -> None:
        """Initialize a new PipelineConverter instance."""

    def _validate_status(self, status: Any) -> MigrationStatus:
        """Validate that status is a valid MigrationStatus or valid status string."""
        return validate_migration_status(status)

    def to_pipeline_record(
        self,
        pipeline: Pipeline | Any,
        initial_status: MigrationStatus | str = MigrationStatus.WAITTING,
    ) -> PipelineRecord:
        """Convert a single discovered pipeline model into a PipelineRecord.

        Args:
            pipeline: Discovered Pipeline model (or pipeline-like object).
            initial_status: Initial migration status for the record.

        Returns:
            A PipelineRecord instance configured with mapped attributes and status.

        Raises:
            ValueError: If pipeline is None, lacks a workflow_id/name,
                or initial_status is invalid.
        """
        if pipeline is None:
            raise ValueError("Pipeline cannot be None.")

        workflow_id = getattr(pipeline, "qualified_name", None) or getattr(
            pipeline, "name", None
        )
        clean_id = str(workflow_id).strip() if workflow_id is not None else ""
        if not clean_id:
            raise ValueError("Pipeline workflow_id cannot be empty.")

        platform = str(getattr(pipeline, "platform", "jenkins") or "jenkins").strip()
        pipe_type = getattr(pipeline, "pipeline_type", "")
        pipe_type_str = getattr(pipe_type, "value", str(pipe_type)) if pipe_type else ""

        repo = getattr(pipeline, "repository", None)
        repo_assoc = ""
        repo_url = ""
        branch = str(getattr(pipeline, "branch", "") or "").strip()
        if repo is not None:
            repo_assoc = str(
                getattr(repo, "qualified_name", "") or getattr(repo, "name", "") or ""
            )
            repo_url = str(getattr(repo, "url", "") or "")
            if not branch and getattr(repo, "default_branch", None):
                branch = str(repo.default_branch)

        file_path = str(getattr(pipeline, "file_path", "") or "").strip()
        shared_components = _format_delimited_string(
            getattr(pipeline, "shared_components", None) or []
        )
        execution_env = _format_delimited_string(
            getattr(pipeline, "execution_environment", None) or []
        )

        last_build = getattr(pipeline, "last_build_timestamp", None)
        last_status = str(getattr(pipeline, "last_status", "") or "").strip()
        valid_status = self._validate_status(initial_status)

        return PipelineRecord(
            workflow_id=clean_id,
            platform=platform,
            pipeline_type=pipe_type_str,
            repository_association=repo_assoc,
            repository_url=repo_url,
            file_path=file_path,
            shared_components=shared_components,
            execution_environment=execution_env,
            branch=branch,
            last_build=last_build,
            last_status=last_status,
            migration_status=valid_status,
        )

    def to_pipeline_records(
        self,
        pipelines: Iterable[Pipeline | Any],
        initial_status: MigrationStatus | str = MigrationStatus.WAITTING,
    ) -> list[PipelineRecord]:
        """Convert a collection of pipeline models into PipelineRecord instances.

        Args:
            pipelines: Iterable of discovered pipeline models.
            initial_status: Initial migration status for all records.

        Returns:
            A list of converted PipelineRecord instances.
        """
        if pipelines is None:
            raise ValueError("Pipelines collection cannot be None.")
        return [
            self.to_pipeline_record(pipe, initial_status=initial_status)
            for pipe in pipelines
        ]


class TeamConverter:
    """Conversion adapter bridging team discovery models and inventory entities."""

    def __init__(self) -> None:
        """Initialize a new TeamConverter instance."""

    def to_team_record(
        self,
        permission: TeamPermission | Any,
        initial_status: MigrationStatus | str = MigrationStatus.WAITTING,
    ) -> TeamRecord:
        """Convert a single discovered TeamPermission model into a TeamRecord.

        Args:
            permission: Discovered TeamPermission model (or permission-like object).
            initial_status: Initial migration status for the record.

        Returns:
            A TeamRecord instance configured with mapped attributes and status.

        Raises:
            ValueError: If permission is None, lacks a team_name, lacks a target,
                or initial_status is invalid.
        """
        if permission is None:
            raise ValueError("TeamPermission cannot be None.")

        name = getattr(permission, "team_name", None)
        clean_name = str(name).strip() if name is not None else ""
        if not clean_name:
            raise ValueError("Team name cannot be empty.")

        slug = getattr(permission, "team_slug", None) or getattr(
            permission, "slug", None
        )
        clean_slug = str(slug).strip() if slug is not None else ""
        if not clean_slug:
            clean_slug = slugify(clean_name)

        raw_count = getattr(permission, "member_count", 0)
        try:
            member_count = int(raw_count) if raw_count is not None else 0
        except (ValueError, TypeError):
            member_count = 0

        target = getattr(permission, "target_repo_or_project", "")
        clean_target = str(target).strip() if target is not None else ""
        if not clean_target:
            raise ValueError("Target repository or project cannot be empty.")

        role = getattr(permission, "normalized_role", "Read")
        norm_role = normalize_role(role).value
        origin_role = str(getattr(permission, "origin_role", "") or "").strip()
        valid_status = validate_migration_status(initial_status)

        return TeamRecord(
            team_name=clean_name,
            team_slug=clean_slug,
            member_count=member_count,
            target_repo_or_project=clean_target,
            normalized_role=norm_role,
            origin_role=origin_role,
            migration_status=valid_status,
        )

    def to_team_records(
        self,
        permissions: Iterable[TeamPermission | Any],
        initial_status: MigrationStatus | str = MigrationStatus.WAITTING,
    ) -> list[TeamRecord]:
        """Convert a collection of TeamPermission models into TeamRecord instances.

        Args:
            permissions: Iterable of discovered team permission models.
            initial_status: Initial migration status for all records.

        Returns:
            A list of converted TeamRecord instances.
        """
        if permissions is None:
            raise ValueError("Permissions collection cannot be None.")
        valid_status = validate_migration_status(initial_status)
        return [
            self.to_team_record(p, initial_status=valid_status) for p in permissions
        ]


__all__ = ["PipelineConverter", "RepositoryConverter", "TeamConverter"]
