"""In-memory diff computation engine for inventory synchronization."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Generic, TypeVar

from gh_migration_inventory_manager.inventory import (
    InventoryRecord,
    MigrationStatus,
    PipelineRecord,
    PolicyRecord,
    TeamRecord,
    validate_migration_status,
)

T = TypeVar("T")


@dataclass
class RecordUpdate(Generic[T]):
    """Represents an existing record with modified metadata and target row_index."""

    record: T
    row_index: int


@dataclass
class RecordDiff(Generic[T]):
    """Holds partitioned additions, updates, and unchanged records."""

    to_add: list[T] = field(default_factory=list)
    to_update: list[RecordUpdate[T]] = field(default_factory=list)
    unchanged: list[T] = field(default_factory=list)

    @property
    def has_changes(self) -> bool:
        """Return True if there are records to add or update."""
        return bool(self.to_add or self.to_update)

    @property
    def total_processed(self) -> int:
        """Return total count of all evaluated records."""
        return len(self.to_add) + len(self.to_update) + len(self.unchanged)


class RepositoryDiffer:
    """Computes state diff for repository inventory records in complete isolation."""

    @staticmethod
    def _apply_status_timestamps(
        record: InventoryRecord, status: MigrationStatus
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

    def compute_diff(
        self,
        existing_rows: list[Any],
        incoming_records: Iterable[InventoryRecord],
        initial_status: MigrationStatus | str = MigrationStatus.WAITTING,
    ) -> RecordDiff[InventoryRecord]:
        """Compute additions, updates, and unchanged repository records.

        Strictly preserves existing migration_status, gh_migratation, and
        origin_deactivation for records already recorded in the sheet.
        """
        if existing_rows is None:
            raise ValueError("Existing rows collection cannot be None.")
        if incoming_records is None:
            raise ValueError("Records collection cannot be None.")

        from gh_migration_inventory_manager.google import RowConverter

        # Build in-memory index of existing rows
        exact_index: dict[tuple[str, str], tuple[InventoryRecord, int]] = {}
        fallback_index: dict[str, list[tuple[InventoryRecord, int]]] = {}

        for row in existing_rows:
            if row is None or getattr(row, "row_index", None) is None:
                continue
            try:
                rec = RowConverter.to_inventory_record(row)
            except Exception:  # nosec B112
                continue

            r_name = (rec.name or "").strip().lower()
            if not r_name:
                continue
            r_proj = (rec.project or "").strip().lower()
            row_idx = row.row_index

            exact_index[(r_name, r_proj)] = (rec, row_idx)
            fallback_index.setdefault(r_name, []).append((rec, row_idx))

        diff = RecordDiff[InventoryRecord]()

        for incoming in incoming_records:
            if incoming is None:
                raise ValueError("InventoryRecord cannot be None.")

            clean_name = (incoming.name or "").strip()
            if not clean_name:
                raise ValueError("Repository name cannot be empty.")

            clean_proj = (incoming.project or "").strip()
            name_lower = clean_name.lower()
            proj_lower = clean_proj.lower()

            matched: tuple[InventoryRecord, int] | None = None

            # 1. Exact match by (name, project)
            if (name_lower, proj_lower) in exact_index:
                matched = exact_index[(name_lower, proj_lower)]
            # 2. Fallback match by name
            elif name_lower in fallback_index:
                candidates = fallback_index[name_lower]
                if clean_proj:
                    for cand_rec, cand_idx in candidates:
                        if (cand_rec.project or "").strip().lower() == proj_lower:
                            matched = (cand_rec, cand_idx)
                            break
                if matched is None and candidates:
                    matched = candidates[0]

            if matched is not None:
                existing_rec, row_idx = matched
                changed = False

                target_project = existing_rec.project
                if (
                    incoming.project is not None
                    and (existing_rec.project or "").strip().lower()
                    != incoming.project.strip().lower()
                ):
                    target_project = incoming.project
                    changed = True

                target_stack = existing_rec.stack
                if incoming.stack and existing_rec.stack != incoming.stack:
                    target_stack = incoming.stack
                    changed = True

                target_last_commit = existing_rec.last_commit
                if (
                    incoming.last_commit is not None
                    and existing_rec.last_commit != incoming.last_commit
                ):
                    target_last_commit = incoming.last_commit
                    changed = True

                if changed:
                    updated_record = InventoryRecord(
                        name=existing_rec.name,
                        project=target_project,
                        stack=target_stack,
                        last_commit=target_last_commit,
                        gh_migratation=existing_rec.gh_migratation,
                        origin_deactivation=existing_rec.origin_deactivation,
                        migration_status=existing_rec.migration_status,
                    )
                    diff.to_update.append(
                        RecordUpdate(record=updated_record, row_index=row_idx)
                    )
                else:
                    diff.unchanged.append(existing_rec)
            else:
                valid_status = validate_migration_status(
                    incoming.migration_status
                    if incoming.migration_status != MigrationStatus.WAITTING
                    else initial_status
                )
                new_record = InventoryRecord(
                    name=clean_name,
                    project=clean_proj,
                    stack=incoming.stack or "",
                    last_commit=incoming.last_commit,
                    migration_status=valid_status,
                )
                self._apply_status_timestamps(new_record, valid_status)
                diff.to_add.append(new_record)

        return diff


class PipelineDiffer:
    """Computes state diff for CI/CD pipeline records in complete isolation."""

    def compute_diff(
        self,
        existing_rows: list[Any],
        incoming_records: Iterable[PipelineRecord],
    ) -> RecordDiff[PipelineRecord]:
        """Compute additions, updates, and unchanged pipeline records."""
        if existing_rows is None:
            raise ValueError("Existing rows collection cannot be None.")
        if incoming_records is None:
            raise ValueError("Records collection cannot be None.")

        from gh_migration_inventory_manager.google import WorkflowRowConverter

        exact_index: dict[str, tuple[PipelineRecord, int]] = {}

        for row in existing_rows:
            if row is None or getattr(row, "row_index", None) is None:
                continue
            try:
                rec = WorkflowRowConverter.to_pipeline_record(row)
            except Exception:  # nosec B112
                continue
            workflow_id = (rec.workflow_id or "").strip().lower()
            if workflow_id:
                exact_index[workflow_id] = (rec, row.row_index)

        diff = RecordDiff[PipelineRecord]()

        for incoming in incoming_records:
            if incoming is None:
                raise ValueError("PipelineRecord cannot be None.")

            clean_id = str(incoming.workflow_id or "").strip()
            if not clean_id:
                raise ValueError("Pipeline workflow_id cannot be empty.")

            id_lower = clean_id.lower()

            if id_lower in exact_index:
                existing_rec, row_idx = exact_index[id_lower]

                changed = (
                    existing_rec.platform != incoming.platform
                    or existing_rec.pipeline_type != incoming.pipeline_type
                    or existing_rec.repository_association
                    != incoming.repository_association
                    or existing_rec.repository_url != incoming.repository_url
                    or existing_rec.file_path != incoming.file_path
                    or existing_rec.shared_components != incoming.shared_components
                    or existing_rec.execution_environment
                    != incoming.execution_environment
                    or existing_rec.branch != incoming.branch
                    or existing_rec.last_build != incoming.last_build
                    or existing_rec.last_status != incoming.last_status
                    or existing_rec.project != incoming.project
                    or existing_rec.repository != incoming.repository
                )

                if changed:
                    updated_record = PipelineRecord(
                        workflow_id=clean_id,
                        platform=incoming.platform,
                        pipeline_type=incoming.pipeline_type,
                        repository_association=incoming.repository_association,
                        repository_url=incoming.repository_url,
                        file_path=incoming.file_path,
                        shared_components=incoming.shared_components,
                        execution_environment=incoming.execution_environment,
                        branch=incoming.branch,
                        last_build=incoming.last_build,
                        last_status=incoming.last_status,
                        migration_status=existing_rec.migration_status,
                        project=incoming.project,
                        repository=incoming.repository,
                    )
                    diff.to_update.append(
                        RecordUpdate(record=updated_record, row_index=row_idx)
                    )
                else:
                    diff.unchanged.append(existing_rec)
            else:
                diff.to_add.append(incoming)

        return diff


class TeamDiffer:
    """Computes state diff for team topology records in complete isolation."""

    def compute_diff(
        self,
        existing_rows: list[Any],
        incoming_records: Iterable[TeamRecord],
    ) -> RecordDiff[TeamRecord]:
        """Compute additions, updates, and unchanged team topology records."""
        if existing_rows is None:
            raise ValueError("Existing rows collection cannot be None.")
        if incoming_records is None:
            raise ValueError("Records collection cannot be None.")

        from gh_migration_inventory_manager.google import TeamRowConverter

        exact_index: dict[tuple[str, str], tuple[TeamRecord, int]] = {}

        for row in existing_rows:
            if row is None or getattr(row, "row_index", None) is None:
                continue
            try:
                rec = TeamRowConverter.to_team_record(row)
            except Exception:  # nosec B112
                continue
            slug = (rec.team_slug or "").strip().lower()
            target = (rec.target_repo_or_project or "").strip().lower()
            if slug and target:
                exact_index[(slug, target)] = (rec, row.row_index)

        diff = RecordDiff[TeamRecord]()

        for incoming in incoming_records:
            if incoming is None:
                raise ValueError("TeamRecord cannot be None.")

            clean_slug = str(incoming.team_slug or "").strip()
            if not clean_slug:
                raise ValueError("Team slug cannot be empty.")

            clean_target = str(incoming.target_repo_or_project or "").strip()
            if not clean_target:
                raise ValueError("Target repository or project cannot be empty.")

            key = (clean_slug.lower(), clean_target.lower())

            if key in exact_index:
                existing_rec, row_idx = exact_index[key]

                changed = (
                    existing_rec.team_name != incoming.team_name
                    or existing_rec.member_count != incoming.member_count
                    or existing_rec.normalized_role != incoming.normalized_role
                    or existing_rec.origin_role != incoming.origin_role
                )

                if changed:
                    updated_record = TeamRecord(
                        team_name=incoming.team_name,
                        member_count=incoming.member_count,
                        target_repo_or_project=clean_target,
                        normalized_role=incoming.normalized_role,
                        origin_role=incoming.origin_role,
                        team_slug=clean_slug,
                        migration_status=existing_rec.migration_status,
                        project=incoming.project,
                        repository=incoming.repository,
                    )
                    diff.to_update.append(
                        RecordUpdate(record=updated_record, row_index=row_idx)
                    )
                else:
                    diff.unchanged.append(existing_rec)
            else:
                diff.to_add.append(incoming)

        return diff


class PolicyDiffer:
    """Computes state diff for branch policies in complete isolation."""

    @staticmethod
    def _validate_policy_key(
        policy_scope: Any,
        target_ref_pattern: Any,
        normalized_rule_type: Any,
    ) -> tuple[str, str, str]:
        """Validate and sanitize policy composite key."""
        clean_scope = str(policy_scope).strip() if policy_scope is not None else ""
        if not clean_scope:
            raise ValueError("Policy scope cannot be empty.")

        clean_target = (
            str(target_ref_pattern).strip() if target_ref_pattern is not None else ""
        )
        if not clean_target:
            raise ValueError("Target ref pattern cannot be empty.")

        if normalized_rule_type is None or not str(normalized_rule_type).strip():
            raise ValueError("Normalized rule type cannot be empty.")

        try:
            from gh_migration_inventory_manager.scrap import normalize_rule_type

            clean_rule = normalize_rule_type(normalized_rule_type).value
        except (ValueError, TypeError):
            clean_rule = str(normalized_rule_type).strip()

        return clean_scope, clean_target, clean_rule

    def compute_diff(
        self,
        existing_rows: list[Any],
        incoming_records: Iterable[PolicyRecord],
    ) -> RecordDiff[PolicyRecord]:
        """Compute additions, updates, and unchanged branch policy records."""
        if existing_rows is None:
            raise ValueError("Existing rows collection cannot be None.")
        if incoming_records is None:
            raise ValueError("Records collection cannot be None.")

        from gh_migration_inventory_manager.google import PolicyRowConverter

        exact_index: dict[tuple[str, str, str], tuple[PolicyRecord, int]] = {}

        for row in existing_rows:
            if row is None or getattr(row, "row_index", None) is None:
                continue
            try:
                rec = PolicyRowConverter.to_policy_record(row)
                c_scope, c_target, c_rule = self._validate_policy_key(
                    rec.policy_scope, rec.target_ref_pattern, rec.normalized_rule_type
                )
                exact_index[(c_scope.lower(), c_target.lower(), c_rule.lower())] = (
                    rec,
                    row.row_index,
                )
            except Exception:  # nosec B112
                continue

        diff = RecordDiff[PolicyRecord]()

        for incoming in incoming_records:
            if incoming is None:
                raise ValueError("PolicyRecord cannot be None.")

            clean_scope, clean_target, clean_rule = self._validate_policy_key(
                incoming.policy_scope,
                incoming.target_ref_pattern,
                incoming.normalized_rule_type,
            )

            key = (clean_scope.lower(), clean_target.lower(), clean_rule.lower())

            if key in exact_index:
                existing_rec, row_idx = exact_index[key]

                changed = (
                    existing_rec.rule_parameters != incoming.rule_parameters
                    or existing_rec.bypass_team_slugs != incoming.bypass_team_slugs
                    or existing_rec.origin_raw_policy != incoming.origin_raw_policy
                )

                if changed:
                    updated_record = PolicyRecord(
                        policy_scope=clean_scope,
                        target_ref_pattern=clean_target,
                        normalized_rule_type=clean_rule,
                        rule_parameters=incoming.rule_parameters,
                        bypass_team_slugs=incoming.bypass_team_slugs,
                        origin_raw_policy=incoming.origin_raw_policy,
                        migration_status=existing_rec.migration_status,
                        project=incoming.project,
                        repository=incoming.repository,
                    )
                    diff.to_update.append(
                        RecordUpdate(record=updated_record, row_index=row_idx)
                    )
                else:
                    diff.unchanged.append(existing_rec)
            else:
                diff.to_add.append(incoming)

        return diff
