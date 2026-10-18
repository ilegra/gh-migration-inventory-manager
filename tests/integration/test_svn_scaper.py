"""Integration tests verifying live SVN repository scraping against Docker container."""

from __future__ import annotations

import shutil
import subprocess
import time
import urllib.error
import urllib.request
from collections.abc import Generator
from typing import Any
from unittest.mock import MagicMock

import pytest

from gh_migration_inventory_manager import (
    BasicAuthenticator,
    Inventory,
    MigrationStatus,
    Repository,
    Row,
    ScraperAuthenticationError,
    ScraperError,
    Stack,
    SvnLayoutType,
    SvnRepositoryScraper,
)


def _is_docker_available() -> bool:
    """Check if Docker command line and daemon are available."""
    if not shutil.which("docker"):
        return False
    try:
        res = subprocess.run(  # nosec B603 B607
            ["docker", "info"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
            check=False,
        )
        return res.returncode == 0
    except Exception:
        return False


@pytest.fixture(scope="module")
def svn_server() -> Generator[dict[str, Any], None, None]:
    """Pytest module-scoped fixture spinning up an SVN server Docker container."""
    if not _is_docker_available():
        pytest.skip("Docker is not available; skipping live SVN integration tests.")

    # 1. Run elleflorio/svn-server with ephemeral ports
    run_cmd = [
        "docker",
        "run",
        "-d",
        "--rm",
        "-p",
        "0:80",
        "-p",
        "0:3690",
        "elleflorio/svn-server:latest",
    ]
    container_id: str | None = None
    try:
        proc = subprocess.run(run_cmd, capture_output=True, text=True, check=True)  # nosec B603
        container_id = proc.stdout.strip()
    except subprocess.CalledProcessError as e:
        pytest.skip(f"Failed to start SVN container: {e.stderr}")

    if not container_id:
        pytest.skip("Failed to start SVN container: container ID is empty.")

    try:
        # 2. Resolve mapped HTTP port
        port_proc = subprocess.run(  # nosec B603
            ["docker", "port", container_id, "80/tcp"],
            capture_output=True,
            text=True,
            check=True,
        )
        # Output format: 0.0.0.0:49153 or :::49153
        port_line = port_proc.stdout.strip().splitlines()[0]
        host_port = int(port_line.split(":")[-1])
        base_url = f"http://127.0.0.1:{host_port}/svn"

        # 3. Wait for SVN server HTTP port to be ready
        ready = False
        for _ in range(30):
            try:
                req = urllib.request.Request(base_url)
                with urllib.request.urlopen(req, timeout=2) as resp:  # nosec B310
                    if resp.status in (200, 401):
                        ready = True
                        break
            except urllib.error.HTTPError as e:
                if e.code in (200, 401):
                    ready = True
                    break
                time.sleep(0.5)
            except (urllib.error.URLError, TimeoutError, OSError):
                time.sleep(0.5)

        if not ready:
            pytest.fail("Timed out waiting for SVN Docker container to start.")

        # 4. Configure Basic Auth credentials
        username = "svnuser"
        password = "svnpass"
        subprocess.run(  # nosec B603
            [
                "docker",
                "exec",
                container_id,
                "htpasswd",
                "-b",
                "/etc/subversion/passwd",
                username,
                password,
            ],
            check=True,
            capture_output=True,
        )

        # 5. Seed standard layout repository (Java project)
        subprocess.run(  # nosec B603
            [
                "docker",
                "exec",
                container_id,
                "svnadmin",
                "create",
                "/home/svn/standard-app",
            ],
            check=True,
            capture_output=True,
        )
        subprocess.run(  # nosec B603
            [
                "docker",
                "exec",
                container_id,
                "svn",
                "mkdir",
                "-m",
                "Init standard layout",
                "file:///home/svn/standard-app/trunk",
                "file:///home/svn/standard-app/branches",
                "file:///home/svn/standard-app/tags",
            ],
            check=True,
            capture_output=True,
        )
        # Populate files in standard-app trunk
        seed_standard_script = (
            "mkdir -p /tmp/std-src && "
            "echo '<project><modelVersion>4.0.0</modelVersion></project>' "
            "> /tmp/std-src/pom.xml && "
            "echo 'public class App {}' > /tmp/std-src/App.java && "
            "svn import /tmp/std-src file:///home/svn/standard-app/trunk "
            "-m 'Add Java source' && "
            "rm -rf /tmp/std-src"
        )
        subprocess.run(  # nosec B603
            ["docker", "exec", container_id, "sh", "-c", seed_standard_script],
            check=True,
            capture_output=True,
        )

        # 6. Seed multi-project repository (Python & TypeScript)
        subprocess.run(  # nosec B603
            [
                "docker",
                "exec",
                container_id,
                "svnadmin",
                "create",
                "/home/svn/multi-app",
            ],
            check=True,
            capture_output=True,
        )
        subprocess.run(  # nosec B603
            [
                "docker",
                "exec",
                container_id,
                "svn",
                "mkdir",
                "-m",
                "Init multi project layout",
                "file:///home/svn/multi-app/auth-service",
                "file:///home/svn/multi-app/auth-service/trunk",
                "file:///home/svn/multi-app/billing-service",
                "file:///home/svn/multi-app/billing-service/trunk",
            ],
            check=True,
            capture_output=True,
        )
        seed_multi_script = (
            "mkdir -p /tmp/auth-src /tmp/billing-src && "
            "echo 'flask>=3.0.0' > /tmp/auth-src/requirements.txt && "
            'echo \'{"name": "billing"}\' > /tmp/billing-src/package.json && '
            "echo '{\"compilerOptions\": {}}' > /tmp/billing-src/tsconfig.json && "
            "svn import /tmp/auth-src "
            "file:///home/svn/multi-app/auth-service/trunk -m 'Add auth files' && "
            "svn import /tmp/billing-src "
            "file:///home/svn/multi-app/billing-service/trunk "
            "-m 'Add billing files' && "
            "rm -rf /tmp/auth-src /tmp/billing-src"
        )
        subprocess.run(  # nosec B603
            ["docker", "exec", container_id, "sh", "-c", seed_multi_script],
            check=True,
            capture_output=True,
        )

        # 7. Seed flat repository (Bash & Terraform scripts)
        subprocess.run(  # nosec B603
            [
                "docker",
                "exec",
                container_id,
                "svnadmin",
                "create",
                "/home/svn/flat-app",
            ],
            check=True,
            capture_output=True,
        )
        seed_flat_script = (
            "mkdir -p /tmp/flat-src && "
            "echo '#!/bin/bash\necho deploy' > /tmp/flat-src/deploy.sh && "
            "echo 'terraform {}' > /tmp/flat-src/main.tf && "
            "svn import /tmp/flat-src file:///home/svn/flat-app "
            "-m 'Add flat scripts' && "
            "rm -rf /tmp/flat-src"
        )
        subprocess.run(  # nosec B603
            ["docker", "exec", container_id, "sh", "-c", seed_flat_script],
            check=True,
            capture_output=True,
        )

        yield {
            "url": base_url,
            "username": username,
            "password": password,
            "container_id": container_id,
        }

    finally:
        if container_id is not None:
            subprocess.run(  # nosec B603
                ["docker", "rm", "-f", container_id],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )


@pytest.fixture(scope="module")
def svn_auth(svn_server: dict[str, Any]) -> BasicAuthenticator:
    """Fixture providing BasicAuthenticator configured for test SVN server."""
    return BasicAuthenticator(svn_server["username"], svn_server["password"])


def test_given_live_svn_standard_repo_when_scraping_then_discovers_trunk_repository(
    svn_server: dict[str, Any], svn_auth: BasicAuthenticator
) -> None:
    """Verify live scraping of standard SVN layout (trunk, branches, tags)."""
    repo_url = f"{svn_server['url']}/standard-app"

    scraper = SvnRepositoryScraper(
        url=repo_url,
        auth=svn_auth,
        organization="org-corp",
        project="platform-proj",
        layout=SvnLayoutType.STANDARD,
    )

    repos = scraper.scrape()
    assert len(repos) == 1
    repo = repos[0]

    assert repo.name == "standard-app"
    assert repo.organization == "org-corp"
    assert repo.project == "platform-proj"
    assert repo.path == "trunk"
    assert repo.url == f"{repo_url}/trunk"
    assert repo.default_branch == "trunk"
    assert repo.last_commit is not None
    assert repo.stacks == [Stack.JAVA]
    assert repo.metadata["layout"] == "standard"
    assert repo.qualified_name == "org-corp/platform-proj/standard-app"


def test_given_live_svn_multi_project_repo_when_scraping_then_discovers_all_subproject_repositories(
    svn_server: dict[str, Any], svn_auth: BasicAuthenticator
) -> None:
    """Verify live scraping of multi-project SVN repository layout."""
    repo_url = f"{svn_server['url']}/multi-app"

    scraper = SvnRepositoryScraper(
        url=repo_url,
        auth=svn_auth,
        organization="org-corp",
        project="cloud-suite",
        layout=SvnLayoutType.MULTI_PROJECT,
    )

    repos = scraper.scrape()
    assert len(repos) == 2

    auth_service = next(r for r in repos if r.name == "auth-service")
    assert auth_service.organization == "org-corp"
    assert auth_service.project == "cloud-suite"
    assert auth_service.path == "auth-service/trunk"
    assert auth_service.default_branch == "trunk"
    assert auth_service.stacks == [Stack.PYTHON]
    assert auth_service.qualified_name == "org-corp/cloud-suite/auth-service"

    billing_service = next(r for r in repos if r.name == "billing-service")
    assert billing_service.organization == "org-corp"
    assert billing_service.project == "cloud-suite"
    assert billing_service.path == "billing-service/trunk"
    assert billing_service.default_branch == "trunk"
    assert billing_service.stacks == [Stack.TYPESCRIPT]
    assert billing_service.qualified_name == "org-corp/cloud-suite/billing-service"


def test_given_live_svn_flat_repo_when_scraping_then_discovers_flat_repository(
    svn_server: dict[str, Any], svn_auth: BasicAuthenticator
) -> None:
    """Verify live scraping of flat SVN repository layout."""
    repo_url = f"{svn_server['url']}/flat-app"

    scraper = SvnRepositoryScraper(
        url=repo_url,
        auth=svn_auth,
        organization="org-corp",
        layout=SvnLayoutType.FLAT,
    )

    repos = scraper.scrape()
    assert len(repos) == 1
    repo = repos[0]

    assert repo.name == "flat-app"
    assert repo.path == ""
    assert repo.default_branch is None
    assert repo.last_commit is not None
    assert Stack.BASH in repo.stacks
    assert Stack.TERRAFORM in repo.stacks
    assert repo.metadata["layout"] == "flat"


def test_given_live_svn_server_root_when_scraping_collection_then_discovers_all_nested_repositories(
    svn_server: dict[str, Any], svn_auth: BasicAuthenticator
) -> None:
    """Verify live scraping of SVN collection parent discovering repositories."""
    collection_url = svn_server["url"]

    scraper = SvnRepositoryScraper(
        url=collection_url,
        auth=svn_auth,
        layout=SvnLayoutType.COLLECTION,
    )

    repos = scraper.scrape()
    assert len(repos) >= 3
    discovered_names = {r.name for r in repos}
    assert "standard-app" in discovered_names
    assert "flat-app" in discovered_names


def test_given_invalid_credentials_when_scraping_live_svn_then_raises_scraper_authentication_error(
    svn_server: dict[str, Any],
) -> None:
    """Verify that invalid credentials raise ScraperAuthenticationError."""
    invalid_auth = BasicAuthenticator(svn_server["username"], "incorrect_password")
    repo_url = f"{svn_server['url']}/standard-app"

    scraper = SvnRepositoryScraper(url=repo_url, auth=invalid_auth)
    with pytest.raises(ScraperAuthenticationError):
        scraper.scrape()


def test_given_nonexistent_svn_path_when_scraping_live_svn_then_raises_scraper_error(
    svn_server: dict[str, Any], svn_auth: BasicAuthenticator
) -> None:
    """Verify scraping non-existent repository raises ScraperError."""
    invalid_repo_url = f"{svn_server['url']}/non-existent-repository-xyz"

    scraper = SvnRepositoryScraper(url=invalid_repo_url, auth=svn_auth)
    with pytest.raises(ScraperError):
        scraper.scrape()


def test_given_scraped_live_svn_repos_when_syncing_to_inventory_then_registers_and_tracks_progress(
    svn_server: dict[str, Any], svn_auth: BasicAuthenticator
) -> None:
    """Verify scraped SVN repositories bridge into Inventory tracking."""
    repo_url = f"{svn_server['url']}/standard-app"

    scraper = SvnRepositoryScraper(
        url=repo_url,
        auth=svn_auth,
        organization="corp",
        project="core-banking",
    )
    discovered_repos = scraper.scrape()
    assert len(discovered_repos) == 1
    scraped_repo: Repository = discovered_repos[0]

    # Mock spreadsheet to record inventory update
    mock_spreadsheet = MagicMock()
    mock_spreadsheet.find_by.return_value = []
    mock_spreadsheet.add.side_effect = lambda row: Row(
        row_index=5,
        data=dict(row.data),
    )

    inventory = Inventory(spreadsheet=mock_spreadsheet)
    record = inventory.update_progress(
        repository_name=scraped_repo.name,
        status=MigrationStatus.ACTIVE_IN_BOTH,
        project=scraped_repo.project,
    )

    assert record.name == "standard-app"
    assert record.project == "core-banking"
    assert record.migration_status == MigrationStatus.ACTIVE_IN_BOTH
    assert record.gh_migratation is not None
    assert mock_spreadsheet.add.called
