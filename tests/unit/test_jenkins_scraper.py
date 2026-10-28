"""Unit tests for Jenkins transport client and JenkinsPipelineScraper."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock

import pytest
import urllib3
from urllib3.exceptions import TimeoutError

from gh_migration_inventory_manager.jenkins_scrap import (
    JenkinsClient,
    JenkinsPipelineScraper,
    JenkinsScraperProvider,
    JenkinsTransport,
)
from gh_migration_inventory_manager.scrap import (
    BasicAuthenticator,
    PipelineTrigger,
    PipelineType,
    ScraperAuthenticationError,
    ScraperConnectionError,
    ScraperError,
    TokenAuthenticator,
)


def _make_http_response(
    status: int, data: dict[str, Any] | str | bytes = b""
) -> MagicMock:
    """Helper to mock an HTTP response from urllib3."""
    resp = MagicMock()
    resp.status = status
    if isinstance(data, dict):
        resp.data = json.dumps(data).encode("utf-8")
    elif isinstance(data, str):
        resp.data = data.encode("utf-8")
    else:
        resp.data = data
    return resp


# --- JenkinsClient Tests ---


def test_given_valid_base_url_when_initializing_client_then_strips_trailing_slash() -> (
    None
):
    """Test URL normalization strips trailing slashes."""
    client = JenkinsClient("http://jenkins.example.com:8080/prefix/")
    assert client.base_url == "http://jenkins.example.com:8080/prefix"


def test_given_empty_base_url_when_initializing_client_then_raises_value_error() -> (
    None
):
    """Test initializing with empty base URL raises ValueError."""
    with pytest.raises(ValueError, match="Jenkins URL cannot be empty"):
        JenkinsClient("   ")


def test_given_unsupported_scheme_when_initializing_client_then_raises_value_error() -> (
    None
):
    """Test non-HTTP URL schemes raise ValueError."""
    with pytest.raises(ValueError, match="Unsupported URL scheme 'ftp'"):
        JenkinsClient("ftp://jenkins.example.com")


def test_given_basic_auth_when_requesting_then_sends_authorization_header() -> None:
    """Test JenkinsClient attaches Basic authentication header."""
    mock_pool = MagicMock(spec=urllib3.PoolManager)
    # 1st call: crumb (404), 2nd call: api/json (200)
    mock_pool.request.side_effect = [
        _make_http_response(404),
        _make_http_response(200, {"jobs": []}),
    ]

    auth = BasicAuthenticator(username="admin", password="password123")
    client = JenkinsClient("http://jenkins:8080", auth=auth, pool_manager=mock_pool)

    data = client.get_json("")

    assert data == {"jobs": []}
    headers = mock_pool.request.call_args[1]["headers"]
    assert "Authorization" in headers
    assert headers["Authorization"].startswith("Basic ")


def test_given_token_auth_when_requesting_then_sends_bearer_header() -> None:
    """Test JenkinsClient attaches Bearer token authorization header."""
    mock_pool = MagicMock(spec=urllib3.PoolManager)
    mock_pool.request.side_effect = [
        _make_http_response(404),
        _make_http_response(200, {"jobs": []}),
    ]

    auth = TokenAuthenticator(token="jenkins-api-token-xyz")
    client = JenkinsClient("http://jenkins:8080", auth=auth, pool_manager=mock_pool)

    client.get_json("")

    headers = mock_pool.request.call_args[1]["headers"]
    assert headers["Authorization"] == "Bearer jenkins-api-token-xyz"


def test_given_crumb_issuer_available_when_requesting_then_sends_crumb_headers() -> (
    None
):
    """Test Jenkins CSRF crumb is fetched and sent in subsequent requests."""
    mock_pool = MagicMock(spec=urllib3.PoolManager)
    mock_pool.request.side_effect = [
        _make_http_response(
            200,
            {"crumbRequestField": "Jenkins-Crumb", "crumb": "7b8a9c0d1e2f3a4b"},
        ),
        _make_http_response(200, {"jobs": []}),
    ]

    client = JenkinsClient("http://jenkins:8080", pool_manager=mock_pool)
    data = client.get_json("")

    assert data == {"jobs": []}
    second_call_headers = mock_pool.request.call_args_list[1][1]["headers"]
    assert second_call_headers["Jenkins-Crumb"] == "7b8a9c0d1e2f3a4b"


def test_given_http_401_or_403_when_fetching_json_then_raises_scraper_authentication_error() -> (
    None
):
    """Test HTTP 401 and 403 are translated to ScraperAuthenticationError."""
    mock_pool = MagicMock(spec=urllib3.PoolManager)
    mock_pool.request.return_value = _make_http_response(401, "Unauthorized")

    client = JenkinsClient("http://jenkins:8080", pool_manager=mock_pool)

    with pytest.raises(ScraperAuthenticationError, match="HTTP 401"):
        client.get_json("")

    mock_pool.request.return_value = _make_http_response(403, "Forbidden")
    with pytest.raises(ScraperAuthenticationError, match="HTTP 403"):
        client.get_json("")


def test_given_http_404_when_fetching_json_then_raises_scraper_error() -> None:
    """Test HTTP 404 raises ScraperError describing missing endpoint."""
    mock_pool = MagicMock(spec=urllib3.PoolManager)
    mock_pool.request.return_value = _make_http_response(404, "Not Found")

    client = JenkinsClient("http://jenkins:8080", pool_manager=mock_pool)

    with pytest.raises(ScraperError, match="HTTP 404"):
        client.get_json("job/missing-job")


def test_given_http_500_when_fetching_json_then_raises_scraper_error() -> None:
    """Test 5xx server errors raise ScraperError."""
    mock_pool = MagicMock(spec=urllib3.PoolManager)
    mock_pool.request.return_value = _make_http_response(500, "Internal Server Error")

    client = JenkinsClient("http://jenkins:8080", pool_manager=mock_pool)

    with pytest.raises(ScraperError, match="HTTP 500"):
        client.get_json("")


def test_given_network_error_when_fetching_json_then_raises_scraper_connection_error() -> (
    None
):
    """Test connection timeouts or MaxRetryErrors translate to ScraperConnectionError."""
    mock_pool = MagicMock(spec=urllib3.PoolManager)
    mock_pool.request.side_effect = TimeoutError("Connection timed out.")

    client = JenkinsClient("http://jenkins:8080", pool_manager=mock_pool)

    with pytest.raises(ScraperConnectionError, match="Failed to connect to Jenkins"):
        client.get_json("")


def test_given_malformed_json_response_when_fetching_json_then_raises_scraper_error() -> (
    None
):
    """Test invalid non-JSON payloads raise ScraperError."""
    mock_pool = MagicMock(spec=urllib3.PoolManager)
    mock_pool.request.side_effect = [
        _make_http_response(404),
        _make_http_response(200, "<html><body>Invalid HTML</body></html>"),
    ]

    client = JenkinsClient("http://jenkins:8080", pool_manager=mock_pool)

    with pytest.raises(ScraperError, match="Failed to parse JSON response"):
        client.get_json("")


# --- JenkinsPipelineScraper Tests ---


def test_given_declarative_pipeline_job_when_scraping_then_resolves_pipeline_with_scm_and_file_path() -> (
    None
):
    """Test scraping a declarative pipeline job extracts SCM repository and Jenkinsfile."""
    mock_transport = MagicMock(spec=JenkinsTransport)
    mock_transport.base_url = "http://jenkins:8080"
    now_ts = int(datetime.now(timezone.utc).timestamp() * 1000)
    mock_transport.get_json.return_value = {
        "jobs": [
            {
                "name": "checkout-service-ci",
                "url": "http://jenkins:8080/job/checkout-service-ci/",
                "color": "blue",
                "_class": "org.jenkinsci.plugins.workflow.job.WorkflowJob",
                "disabled": False,
                "lastBuild": {
                    "timestamp": now_ts,
                    "result": "SUCCESS",
                    "number": 42,
                },
                "actions": [
                    {
                        "causes": [
                            {"_class": "hudson.triggers.SCMTrigger$SCMTriggerCause"}
                        ]
                    }
                ],
                "definition": {
                    "_class": "org.jenkinsci.plugins.workflow.cps.CpsScmFlowDefinition",
                    "scriptPath": "Jenkinsfile",
                    "scm": {
                        "_class": "hudson.plugins.git.GitSCM",
                        "userRemoteConfigs": [
                            {"url": "https://github.com/company/checkout-service.git"}
                        ],
                        "branches": [{"name": "*/main"}],
                    },
                },
            }
        ]
    }

    scraper = JenkinsPipelineScraper(client=mock_transport)
    pipelines = scraper.scrape()

    assert len(pipelines) == 1
    p = pipelines[0]
    assert p.name == "checkout-service-ci"
    assert p.full_path == "checkout-service-ci"
    assert p.platform == "jenkins"
    assert p.pipeline_type == PipelineType.DECLARATIVE
    assert p.file_path == "Jenkinsfile"
    assert p.branch == "main"
    assert p.last_status == "SUCCESS"
    assert p.disabled is False
    assert PipelineTrigger.SCM_POLLING in p.triggers
    assert p.repository is not None
    assert p.repository.name == "checkout-service"
    assert p.repository.url == "https://github.com/company/checkout-service.git"


def test_given_multibranch_pipeline_job_when_scraping_then_resolves_multibranch_type_and_git_source() -> (
    None
):
    """Test multibranch pipeline resolves PipelineType.MULTIBRANCH and extracts sources."""
    mock_transport = MagicMock(spec=JenkinsTransport)
    mock_transport.base_url = "http://jenkins:8080"
    mock_transport.get_json.return_value = {
        "jobs": [
            {
                "name": "payment-api",
                "url": "http://jenkins:8080/job/payment-api/",
                "color": "blue",
                "_class": "org.jenkinsci.plugins.workflow.multibranch.WorkflowMultiBranchProject",
                "disabled": False,
                "sources": [
                    {
                        "source": {
                            "_class": "jenkins.plugins.git.GitSCMSource",
                            "remote": "git@github.com:org/payment-api.git",
                            "credentialsId": "git-deploy-key",
                        }
                    }
                ],
            }
        ]
    }

    scraper = JenkinsPipelineScraper(client=mock_transport)
    pipelines = scraper.scrape()

    assert len(pipelines) == 1
    p = pipelines[0]
    assert p.name == "payment-api"
    assert p.pipeline_type == PipelineType.MULTIBRANCH
    assert p.repository is not None
    assert p.repository.url == "git@github.com:org/payment-api.git"


def test_given_freestyle_project_job_when_scraping_then_resolves_freestyle_type() -> (
    None
):
    """Test freestyle project maps to PipelineType.FREESTYLE."""
    mock_transport = MagicMock(spec=JenkinsTransport)
    mock_transport.base_url = "http://jenkins:8080"
    mock_transport.get_json.return_value = {
        "jobs": [
            {
                "name": "legacy-ant-build",
                "url": "http://jenkins:8080/job/legacy-ant-build/",
                "color": "red",
                "_class": "hudson.model.FreeStyleProject",
                "disabled": False,
                "lastBuild": {
                    "timestamp": 1729800000000,
                    "result": "FAILURE",
                    "number": 10,
                },
            }
        ]
    }

    scraper = JenkinsPipelineScraper(client=mock_transport)
    pipelines = scraper.scrape()

    assert len(pipelines) == 1
    p = pipelines[0]
    assert p.name == "legacy-ant-build"
    assert p.pipeline_type == PipelineType.FREESTYLE
    assert p.last_status == "FAILURE"


def test_given_nested_folder_hierarchy_when_scraping_then_recursively_traverses_all_folders() -> (
    None
):
    """Test folder items trigger sub-level API calls up to tree_depth."""
    mock_transport = MagicMock(spec=JenkinsTransport)
    mock_transport.base_url = "http://jenkins:8080"

    root_payload = {
        "jobs": [
            {
                "name": "backend",
                "url": "http://jenkins:8080/job/backend/",
                "_class": "com.cloudbees.hudson.plugins.folder.Folder",
            }
        ]
    }
    folder_payload = {
        "jobs": [
            {
                "name": "user-service",
                "url": "http://jenkins:8080/job/backend/job/user-service/",
                "_class": "org.jenkinsci.plugins.workflow.job.WorkflowJob",
                "definition": {
                    "_class": "org.jenkinsci.plugins.workflow.cps.CpsScmFlowDefinition",
                    "scriptPath": "Jenkinsfile",
                },
            }
        ]
    }

    mock_transport.get_json.side_effect = [root_payload, folder_payload]

    scraper = JenkinsPipelineScraper(client=mock_transport, tree_depth=3)
    pipelines = scraper.scrape()

    assert len(pipelines) == 1
    p = pipelines[0]
    assert p.name == "user-service"
    assert p.full_path == "backend/user-service"
    assert p.pipeline_type == PipelineType.DECLARATIVE


def test_given_job_exceeding_max_tree_depth_when_scraping_then_stops_recursion_at_limit() -> (
    None
):
    """Test recursion terminates when current_depth reaches tree_depth."""
    mock_transport = MagicMock(spec=JenkinsTransport)
    mock_transport.base_url = "http://jenkins:8080"
    mock_transport.get_json.return_value = {
        "jobs": [
            {
                "name": "deep-folder",
                "url": "http://jenkins:8080/job/deep-folder/",
                "_class": "com.cloudbees.hudson.plugins.folder.Folder",
            }
        ]
    }

    # Set tree_depth=1 (cannot recurse into deep-folder)
    scraper = JenkinsPipelineScraper(client=mock_transport, tree_depth=1)
    pipelines = scraper.scrape()

    assert len(pipelines) == 0
    assert mock_transport.get_json.call_count == 1


def test_given_stale_job_exceeding_staleness_threshold_when_scraping_then_tags_staleness_in_metadata() -> (
    None
):
    """Test jobs older than staleness_days threshold have is_stale=True in metadata."""
    mock_transport = MagicMock(spec=JenkinsTransport)
    mock_transport.base_url = "http://jenkins:8080"
    # Build from 2020 (over 6 years old)
    old_timestamp = int(datetime(2020, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)

    mock_transport.get_json.return_value = {
        "jobs": [
            {
                "name": "archived-service",
                "url": "http://jenkins:8080/job/archived-service/",
                "_class": "org.jenkinsci.plugins.workflow.job.WorkflowJob",
                "lastBuild": {"timestamp": old_timestamp, "result": "SUCCESS"},
            }
        ]
    }

    scraper = JenkinsPipelineScraper(client=mock_transport, staleness_days=180)
    pipelines = scraper.scrape()

    assert len(pipelines) == 1
    assert pipelines[0].metadata["is_stale"] is True


def test_given_disabled_job_when_scraping_then_sets_disabled_true() -> None:
    """Test disabled job property is properly captured."""
    mock_transport = MagicMock(spec=JenkinsTransport)
    mock_transport.base_url = "http://jenkins:8080"
    mock_transport.get_json.return_value = {
        "jobs": [
            {
                "name": "deprecated-job",
                "url": "http://jenkins:8080/job/deprecated-job/",
                "color": "disabled",
                "disabled": True,
                "_class": "hudson.model.FreeStyleProject",
            }
        ]
    }

    scraper = JenkinsPipelineScraper(client=mock_transport)
    pipelines = scraper.scrape()

    assert len(pipelines) == 1
    assert pipelines[0].disabled is True


def test_given_invalid_tree_depth_when_initializing_scraper_then_raises_value_error() -> (
    None
):
    """Test tree_depth less than 1 raises ValueError."""
    mock_transport = MagicMock(spec=JenkinsTransport)
    with pytest.raises(ValueError, match="Tree depth must be at least 1"):
        JenkinsPipelineScraper(client=mock_transport, tree_depth=0)


def test_given_negative_staleness_days_when_initializing_scraper_then_raises_value_error() -> (
    None
):
    """Test negative staleness_days raises ValueError."""
    mock_transport = MagicMock(spec=JenkinsTransport)
    with pytest.raises(ValueError, match="Staleness days cannot be negative"):
        JenkinsPipelineScraper(client=mock_transport, staleness_days=-5)


# --- JenkinsScraperProvider Tests ---


def test_given_cli_args_when_creating_scraper_then_builds_configured_jenkins_pipeline_scraper() -> (
    None
):
    """Test JenkinsScraperProvider builds JenkinsPipelineScraper from CLI arguments."""
    provider = JenkinsScraperProvider()
    args = argparse.Namespace(
        url="http://jenkins:8080",
        username="admin",
        password="secretpassword",
        token=None,
        tree_depth=4,
        staleness_days=90,
    )

    scraper = provider.create_scraper(args)

    assert isinstance(scraper, JenkinsPipelineScraper)
    assert scraper.tree_depth == 4
    assert scraper.staleness_days == 90
    assert scraper.client.base_url == "http://jenkins:8080"


def test_given_token_only_when_creating_scraper_then_configures_token_authenticator() -> (
    None
):
    """Test providing only token configures TokenAuthenticator on client."""
    provider = JenkinsScraperProvider()
    args = argparse.Namespace(
        url="https://ci.example.com",
        username=None,
        password=None,
        token="jenkins-pat-xyz",
        tree_depth=5,
        staleness_days=180,
    )

    scraper = provider.create_scraper(args)
    assert isinstance(scraper.client, JenkinsClient)
    assert isinstance(scraper.client.auth, TokenAuthenticator)
