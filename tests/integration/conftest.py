"""Shared pytest fixtures and utilities for integration tests."""

from __future__ import annotations

import shutil
import subprocess
import time
import urllib.error
import urllib.request
from collections.abc import Generator
from typing import Any

import pytest

from gh_migration_inventory_manager import BasicAuthenticator


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

        # 8. Seed authz configuration for team topology discovery
        authz_content = (
            "[groups]\n"
            "core-devs = alice, bob, charlie\n"
            "ops = dave, eve\n"
            "leads = alice, dave\n"
            "admins = frank\n\n"
            "[/]\n"
            "@admins = rw\n"
            "* = r\n\n"
            "[/standard-app/trunk]\n"
            "@core-devs = rw\n"
            "@leads = rw\n\n"
            "[/standard-app/tags]\n"
            "@leads = rw\n"
            "* = r\n\n"
            "[/multi-app/auth-service/trunk]\n"
            "@core-devs = rw\n\n"
            "[/multi-app/billing-service]\n"
            "@ops = rw\n"
            "* =\n"
        )
        seed_authz_cmd = (
            f"cat << 'EOF' > /tmp/authz\n{authz_content}\nEOF\n"
            "svn import /tmp/authz file:///home/svn/standard-app/trunk/authz "
            "-m 'Add authz rules' && "
            "cp /tmp/authz /etc/subversion/subversion-access.file && "
            "rm -f /tmp/authz"
        )
        subprocess.run(  # nosec B603
            ["docker", "exec", container_id, "sh", "-c", seed_authz_cmd],
            check=True,
            capture_output=True,
        )

        import tempfile

        host_authz = tempfile.NamedTemporaryFile("w+", delete=False, suffix=".conf")
        host_authz.write(authz_content)
        host_authz.flush()
        host_authz.close()

        yield {
            "url": base_url,
            "username": username,
            "password": password,
            "container_id": container_id,
            "authz_url": f"{base_url}/standard-app/trunk/authz",
            "authz_path": host_authz.name,
            "authz_content": authz_content,
        }

    finally:
        if container_id is not None:
            subprocess.run(  # nosec B603
                ["docker", "rm", "-f", container_id],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        try:
            import os

            if "host_authz" in locals() and os.path.exists(host_authz.name):
                os.remove(host_authz.name)
        except Exception:
            pass


@pytest.fixture(scope="module")
def svn_auth(svn_server: dict[str, Any]) -> BasicAuthenticator:
    """Fixture providing BasicAuthenticator configured for test SVN server."""
    return BasicAuthenticator(svn_server["username"], svn_server["password"])


@pytest.fixture(scope="module")
def jenkins_server() -> Generator[dict[str, Any], None, None]:
    """Pytest module-scoped fixture spinning up a Jenkins Docker container."""
    if not _is_docker_available():
        pytest.skip("Docker is not available; skipping live Jenkins integration tests.")

    # Image available locally
    image = "test-jenkins:latest"
    run_cmd = [
        "docker",
        "run",
        "-d",
        "--rm",
        "-p",
        "0:8080",
        "-e",
        "CASC_JENKINS_CONFIG=",
        "-e",
        "JAVA_OPTS=-Djenkins.install.runSetupWizard=false -Dhudson.security.csrf.GlobalCrumbIssuerConfiguration.DISABLE_CSRF_PROTECTION=true",
        image,
    ]
    container_id: str | None = None
    try:
        proc = subprocess.run(run_cmd, capture_output=True, text=True, check=True)  # nosec B603
        container_id = proc.stdout.strip()
    except subprocess.CalledProcessError as e:
        pytest.skip(f"Failed to start Jenkins container: {e.stderr}")

    if not container_id:
        pytest.skip("Failed to start Jenkins container: container ID is empty.")

    try:
        port_proc = subprocess.run(  # nosec B603
            ["docker", "port", container_id, "8080/tcp"],
            capture_output=True,
            text=True,
            check=True,
        )
        port_line = port_proc.stdout.strip().splitlines()[0]
        host_port = int(port_line.split(":")[-1])
        base_url = f"http://127.0.0.1:{host_port}"

        # Wait for Jenkins API to be ready
        ready = False
        for _ in range(60):
            try:
                req = urllib.request.Request(f"{base_url}/api/json")
                with urllib.request.urlopen(req, timeout=2) as resp:  # nosec B310
                    if resp.status == 200:
                        ready = True
                        break
            except (
                urllib.error.HTTPError,
                urllib.error.URLError,
                TimeoutError,
                OSError,
            ):
                time.sleep(1)

        if not ready:
            pytest.fail("Timed out waiting for Jenkins Docker container to start.")

        # Seed test jobs into Jenkins
        # 1. Freestyle Job
        freestyle_xml = """<?xml version='1.1' encoding='UTF-8'?>
<project>
  <description>Sample integration test freestyle job</description>
  <scm class="hudson.scm.NullSCM"/>
</project>"""
        req_f = urllib.request.Request(
            f"{base_url}/createItem?name=sample-freestyle-job",
            data=freestyle_xml.encode("utf-8"),
            headers={"Content-Type": "application/xml"},
        )
        try:
            with urllib.request.urlopen(req_f, timeout=5):  # nosec B310
                pass
        except Exception:
            pass

        # 2. Pipeline Job (declarative / workflow job)
        pipeline_xml = """<?xml version='1.1' encoding='UTF-8'?>
<flow-definition plugin="workflow-job">
  <description>Sample integration test pipeline job</description>
  <definition class="org.jenkinsci.plugins.workflow.cps.CpsFlowDefinition" plugin="workflow-cps">
    <script>pipeline { agent any; stages { stage('Build') { steps { echo 'Building...' } } } }</script>
    <sandbox>true</sandbox>
  </definition>
</flow-definition>"""
        req_p = urllib.request.Request(
            f"{base_url}/createItem?name=sample-pipeline-job",
            data=pipeline_xml.encode("utf-8"),
            headers={"Content-Type": "application/xml"},
        )
        try:
            with urllib.request.urlopen(req_p, timeout=5):  # nosec B310
                pass
        except Exception:
            pass

        # Trigger a build on sample-pipeline-job so lastBuild is populated
        req_b = urllib.request.Request(
            f"{base_url}/job/sample-pipeline-job/build",
            data=b"",
        )
        try:
            with urllib.request.urlopen(req_b, timeout=5):  # nosec B310
                pass
            time.sleep(3)
        except Exception:
            pass

        yield {
            "base_url": base_url,
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


_DEFAULT_SPREADSHEET_ID = "1Z0PAjv8KK6WBrS2356gtR3VE0ulD1-8LM4DPQiG-Nkk"


@pytest.fixture(scope="module")
def gcp_credentials_path() -> str:
    """Fixture providing path to valid GCP credentials JSON file or skipping."""
    import os

    for candidate in (
        "gcp-key.json",
        os.environ.get("GCP_KEY_PATH"),
        os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"),
        "gcp-api-keys.json",
    ):
        if candidate and os.path.exists(candidate):
            return candidate
    pytest.skip(
        "GCP credentials file (gcp-key.json) not found; skipping live Google Sheets integration tests."
    )


@pytest.fixture(scope="module")
def live_spreadsheet(gcp_credentials_path: str) -> Any:
    """Fixture providing a connected Spreadsheet client for live integration tests."""
    from gh_migration_inventory_manager.google import Credentials, Spreadsheet

    creds = Credentials(credentials_path=gcp_credentials_path)
    sheet = Spreadsheet(
        id=_DEFAULT_SPREADSHEET_ID,
        credentials=creds,
        sheet_name="Inventario",
    )
    return sheet


@pytest.fixture
def reset_topology_sheet(gcp_credentials_path: str) -> Any:
    """Fixture ensuring the Topology tab exists and is reset for isolated test execution."""
    from gh_migration_inventory_manager.google import Credentials, Spreadsheet

    creds = Credentials(credentials_path=gcp_credentials_path)
    sheet = Spreadsheet(
        id=_DEFAULT_SPREADSHEET_ID,
        credentials=creds,
        sheet_name="Topology",
    )
    sheet.ensure_sheet()
    sheet.clear_tab()
    yield sheet
    sheet.clear_tab()
