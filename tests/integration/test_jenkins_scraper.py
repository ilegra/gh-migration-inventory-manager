"""Integration tests verifying JenkinsPipelineScraper against a containerized Jenkins service."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from gh_migration_inventory_manager.inventory import (
    MigrationStatus,
    PipelineInventory,
)
from gh_migration_inventory_manager.jenkins_scrap import (
    JenkinsClient,
    JenkinsPipelineScraper,
)
from gh_migration_inventory_manager.scrap import (
    PipelineType,
    ScraperConnectionError,
)


def test_given_live_jenkins_server_when_scraping_then_discovers_seeded_pipeline_jobs(
    jenkins_server: dict[str, Any],
) -> None:
    """Verify JenkinsPipelineScraper discovers seeded jobs from containerized Jenkins."""
    base_url = jenkins_server["base_url"]
    client = JenkinsClient(base_url=base_url)
    scraper = JenkinsPipelineScraper(client=client, tree_depth=3)

    pipelines = scraper.scrape()

    assert len(pipelines) >= 2
    names = [p.name for p in pipelines]
    assert "sample-freestyle-job" in names
    assert "sample-pipeline-job" in names

    # Verify pipeline job properties
    pipe_job = next(p for p in pipelines if p.name == "sample-pipeline-job")
    assert pipe_job.pipeline_type in (
        PipelineType.DECLARATIVE,
        PipelineType.SCRIPTED,
    )
    assert pipe_job.full_path == "sample-pipeline-job"
    assert pipe_job.platform == "jenkins"
    assert pipe_job.url.startswith(base_url)

    # Verify freestyle job properties
    free_job = next(p for p in pipelines if p.name == "sample-freestyle-job")
    assert free_job.pipeline_type == PipelineType.FREESTYLE
    assert free_job.full_path == "sample-freestyle-job"


def test_given_unreachable_jenkins_host_when_scraping_then_raises_scraper_connection_error() -> (
    None
):
    """Verify connecting to an invalid port raises ScraperConnectionError after retries."""
    # Port 59999 is closed and non-routable
    unreachable_url = "http://127.0.0.1:59999"
    client = JenkinsClient(base_url=unreachable_url, retries=1, timeout=2.0)
    scraper = JenkinsPipelineScraper(client=client)

    with pytest.raises(ScraperConnectionError, match="Failed to connect to Jenkins"):
        scraper.scrape()


def test_given_live_jenkins_server_when_syncing_with_pipeline_inventory_then_persists_records(
    jenkins_server: dict[str, Any],
) -> None:
    """Verify discovered Jenkins pipelines integrate end-to-end with PipelineInventory."""
    from gh_migration_inventory_manager.converter import PipelineConverter
    from gh_migration_inventory_manager.google import Row

    base_url = jenkins_server["base_url"]
    client = JenkinsClient(base_url=base_url)
    scraper = JenkinsPipelineScraper(client=client)
    pipelines = scraper.scrape()

    converter = PipelineConverter()
    records = converter.to_pipeline_records(
        pipelines, initial_status=MigrationStatus.WAITTING
    )

    # Validate against an in-memory/mock spreadsheet client simulating live backend
    mock_sheet = MagicMock()
    mock_sheet.find_by.return_value = []
    mock_sheet.add.side_effect = lambda row: Row(
        row_index=10,
        data=row.data,
        values=list(row.data.values()),
    )

    inv = PipelineInventory(spreadsheet=mock_sheet, converter=converter)
    synced = inv.sync_records(records)

    assert len(synced) == len(records)
    assert any(r.workflow_id == "sample-pipeline-job" for r in synced)
    assert any(r.workflow_id == "sample-freestyle-job" for r in synced)
