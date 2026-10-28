"""Unit tests for PipelineConverter."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from gh_migration_inventory_manager.converter import PipelineConverter
from gh_migration_inventory_manager.inventory import MigrationStatus, PipelineRecord
from gh_migration_inventory_manager.scrap import (
    Pipeline,
    PipelineTrigger,
    PipelineType,
    Repository,
)


def test_given_valid_pipeline_when_converting_to_record_then_populates_all_fields() -> (
    None
):
    """Test standard pipeline model maps into a PipelineRecord correctly."""
    converter = PipelineConverter()
    build_time = datetime(2026, 10, 27, 12, 0, 0, tzinfo=timezone.utc)
    pipeline = Pipeline(
        name="payment-service-build",
        platform="jenkins",
        full_path="services/payment/payment-service-build",
        url="http://jenkins/job/services/job/payment/job/payment-service-build",
        pipeline_type=PipelineType.DECLARATIVE,
        file_path="Jenkinsfile",
        branch="main",
        triggers=[PipelineTrigger.PUSH],
        last_build_timestamp=build_time,
        last_status="SUCCESS",
        disabled=False,
    )

    record = converter.to_pipeline_record(
        pipeline, initial_status=MigrationStatus.WAITTING
    )

    assert isinstance(record, PipelineRecord)
    assert record.workflow_id == "services/payment/payment-service-build"
    assert record.platform == "jenkins"
    assert record.pipeline_type == "declarative"
    assert record.file_path == "Jenkinsfile"
    assert record.branch == "main"
    assert record.last_build == build_time
    assert record.last_status == "SUCCESS"
    assert record.migration_status == MigrationStatus.WAITTING


def test_given_pipeline_with_repository_when_converting_then_links_scm_repository_data() -> (
    None
):
    """Test SCM repository linkage fields are correctly resolved on PipelineRecord."""
    converter = PipelineConverter()
    repo = Repository(
        name="billing-api",
        project="fintech",
        url="https://github.com/org/billing-api.git",
        default_branch="master",
    )
    pipeline = Pipeline(
        name="billing-ci",
        platform="jenkins",
        full_path="fintech/billing-ci",
        url="http://jenkins/job/fintech/job/billing-ci",
        repository=repo,
        file_path="ci/Jenkinsfile",
    )

    record = converter.to_pipeline_record(pipeline)

    assert record.repository_association == "fintech/billing-api"
    assert record.repository_url == "https://github.com/org/billing-api.git"
    assert record.branch == "master"


def test_given_pipeline_with_empty_branch_when_converting_then_falls_back_to_repository_default_branch() -> (
    None
):
    """Test missing branch on pipeline falls back to repository default_branch."""
    converter = PipelineConverter()
    repo = Repository(
        name="core-service",
        url="https://gitlab.com/core-service.git",
        default_branch="develop",
    )
    pipeline = Pipeline(
        name="core-ci",
        platform="jenkins",
        full_path="core-ci",
        url="http://jenkins/job/core-ci",
        branch="",
        repository=repo,
    )

    record = converter.to_pipeline_record(pipeline)
    assert record.branch == "develop"


def test_given_pipeline_without_repository_when_converting_then_leaves_repository_fields_empty() -> (
    None
):
    """Test pipeline without SCM linkage produces blank repository metadata."""
    converter = PipelineConverter()
    pipeline = Pipeline(
        name="standalone-task",
        platform="jenkins",
        full_path="standalone-task",
        url="http://jenkins/job/standalone-task",
    )

    record = converter.to_pipeline_record(pipeline)

    assert record.repository_association == ""
    assert record.repository_url == ""
    assert record.branch == ""


def test_given_pipeline_with_string_migration_status_when_converting_then_normalizes_to_enum() -> (
    None
):
    """Test string migration statuses are normalized to MigrationStatus enum values."""
    converter = PipelineConverter()
    pipeline = Pipeline(
        name="deploy-job",
        platform="jenkins",
        full_path="deploy-job",
        url="http://jenkins/job/deploy-job",
    )

    record = converter.to_pipeline_record(pipeline, initial_status="migrated")
    assert record.migration_status == MigrationStatus.MIGRATED

    record_wait = converter.to_pipeline_record(pipeline, initial_status="waiting")
    assert record_wait.migration_status == MigrationStatus.WAITTING


def test_given_none_pipeline_when_converting_then_raises_value_error() -> None:
    """Test passing None as pipeline raises ValueError."""
    converter = PipelineConverter()
    with pytest.raises(ValueError, match="Pipeline cannot be None"):
        converter.to_pipeline_record(None)


def test_given_pipeline_with_blank_id_when_converting_then_raises_value_error() -> None:
    """Test pipeline lacking name and qualified_name raises ValueError."""
    converter = PipelineConverter()
    pipeline = Pipeline(name="", platform="jenkins", full_path="", url="http://jenkins")
    with pytest.raises(ValueError, match="Pipeline workflow_id cannot be empty"):
        converter.to_pipeline_record(pipeline)


def test_given_invalid_migration_status_when_converting_then_raises_value_error() -> (
    None
):
    """Test unaccepted migration status values raise ValueError."""
    converter = PipelineConverter()
    pipeline = Pipeline(
        name="test-job",
        platform="jenkins",
        full_path="test-job",
        url="http://jenkins/job/test-job",
    )

    with pytest.raises(ValueError, match="Invalid migration status"):
        converter.to_pipeline_record(pipeline, initial_status="unknown_status")

    with pytest.raises(ValueError, match="Invalid migration status: None"):
        converter.to_pipeline_record(pipeline, initial_status=None)  # type: ignore[arg-type]


def test_given_pipeline_with_shared_components_and_execution_env_when_converting_then_formats_sorted_and_deduplicated() -> (
    None
):
    """Test shared components and execution environment lists are deduplicated and sorted."""
    converter = PipelineConverter()
    pipeline = Pipeline(
        name="complex-pipeline",
        platform="jenkins",
        full_path="services/complex-pipeline",
        url="http://jenkins/services/complex-pipeline",
        shared_components=["security-lib", "build-utils", "security-lib"],
        execution_environment=["linux", "docker", "linux", "gpu"],
    )

    record = converter.to_pipeline_record(pipeline)

    assert record.shared_components == "build-utils, security-lib"
    assert record.execution_environment == "docker, gpu, linux"


def test_given_pipeline_with_string_components_and_env_when_converting_then_preserves_strings() -> (
    None
):
    """Test string representations for shared components and execution environment are preserved."""
    converter = PipelineConverter()
    pipeline = Pipeline(
        name="string-pipeline",
        platform="jenkins",
        full_path="string-pipeline",
        url="http://jenkins/string-pipeline",
        shared_components="single-lib",  # type: ignore[arg-type]
        execution_environment="custom-runner",  # type: ignore[arg-type]
    )

    record = converter.to_pipeline_record(pipeline)

    assert record.shared_components == "single-lib"
    assert record.execution_environment == "custom-runner"


def test_given_collection_of_pipelines_when_converting_then_returns_list_of_records() -> (
    None
):
    """Test to_pipeline_records batch converts a list of pipelines."""
    converter = PipelineConverter()
    pipelines = [
        Pipeline(
            name="job-1",
            platform="jenkins",
            full_path="job-1",
            url="http://jenkins/job-1",
        ),
        Pipeline(
            name="job-2",
            platform="jenkins",
            full_path="job-2",
            url="http://jenkins/job-2",
        ),
    ]

    records = converter.to_pipeline_records(
        pipelines, initial_status=MigrationStatus.ACTIVE_IN_BOTH
    )

    assert len(records) == 2
    assert records[0].workflow_id == "job-1"
    assert records[0].migration_status == MigrationStatus.ACTIVE_IN_BOTH
    assert records[1].workflow_id == "job-2"
    assert records[1].migration_status == MigrationStatus.ACTIVE_IN_BOTH


def test_given_none_collection_when_converting_to_records_then_raises_value_error() -> (
    None
):
    """Test to_pipeline_records raises ValueError if collection is None."""
    converter = PipelineConverter()
    with pytest.raises(ValueError, match="Pipelines collection cannot be None"):
        converter.to_pipeline_records(None)  # type: ignore[arg-type]
