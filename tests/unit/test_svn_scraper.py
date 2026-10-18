"""Unit tests for Subversion (SVN) repository scraper, authenticators, and models."""

from __future__ import annotations

import urllib.error
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from gh_migration_inventory_manager import (
    Authenticator,
    BasicAuthenticator,
    RepositoryScraper,
    ScraperAuthenticationError,
    ScraperConnectionError,
    Stack,
    SvnClient,
    SvnEntry,
    SvnLayoutType,
    SvnRepositoryScraper,
    SvnWebDavClient,
    TokenAuthenticator,
)


class MockSvnClient:
    """Mock SVN client providing predetermined directory listings."""

    def __init__(self, directory_tree: dict[str, list[SvnEntry]] | None = None) -> None:
        self.directory_tree: dict[str, list[SvnEntry]] = directory_tree or {}

    def list_dir(self, url: str) -> list[SvnEntry]:
        clean_url = url.rstrip("/")
        return self.directory_tree.get(clean_url, [])


class TestBasicAuthenticator:
    """Tests for BasicAuthenticator."""

    def test_basic_authenticator_success(self) -> None:
        auth = BasicAuthenticator("svnuser", "svnpass")
        assert isinstance(auth, Authenticator)
        creds = auth.resolve_credentials()
        assert creds.username == "svnuser"
        assert creds.password == "svnpass"
        assert creds.token is None

    def test_basic_authenticator_empty_username_raises(self) -> None:
        with pytest.raises(ValueError, match="Username cannot be empty"):
            BasicAuthenticator("   ", "password")

    def test_basic_authenticator_protocol_conformance(self) -> None:
        auth = BasicAuthenticator("user", "pass")
        assert isinstance(auth, Authenticator)


class TestSvnRepositoryScraperStandardLayout:
    """Tests for standard SVN layout (trunk, branches, tags)."""

    def test_standard_layout_success(self) -> None:
        base_url = "http://svn.example.com/svn/sample-repo"
        now = datetime(2026, 10, 16, 15, 30, 0, tzinfo=timezone.utc)

        root_entries = [
            SvnEntry(
                name="trunk",
                path="trunk",
                is_directory=True,
                last_modified=now,
                revision=42,
            ),
            SvnEntry(name="branches", path="branches", is_directory=True),
            SvnEntry(name="tags", path="tags", is_directory=True),
        ]
        trunk_entries = [
            SvnEntry(
                name="pom.xml",
                path="trunk/pom.xml",
                is_directory=False,
                last_modified=now,
            ),
            SvnEntry(name="src", path="trunk/src", is_directory=True),
        ]

        client = MockSvnClient(
            {
                base_url: root_entries,
                f"{base_url}/trunk": trunk_entries,
            }
        )

        scraper = SvnRepositoryScraper(
            url=base_url,
            organization="my-org",
            project="sample-project",
            layout=SvnLayoutType.STANDARD,
            client=client,
        )
        assert isinstance(scraper, RepositoryScraper)

        repos = scraper.scrape()
        assert len(repos) == 1
        repo = repos[0]

        assert repo.name == "sample-repo"
        assert repo.organization == "my-org"
        assert repo.project == "sample-project"
        assert repo.path == "trunk"
        assert repo.url == "http://svn.example.com/svn/sample-repo/trunk"
        assert repo.default_branch == "trunk"
        assert repo.last_commit == now
        assert repo.stacks == [Stack.JAVA]
        assert repo.metadata["layout"] == "standard"
        assert repo.metadata["revision"] == 42
        assert repo.qualified_name == "my-org/sample-project/sample-repo"


class TestSvnRepositoryScraperMultiProjectLayout:
    """Tests for multi-project SVN layout (multiple sub-projects under root)."""

    def test_multi_project_layout_success(self) -> None:
        base_url = "http://svn.example.com/svn/mono-repo"
        time_a = datetime(2026, 10, 10, 10, 0, 0, tzinfo=timezone.utc)
        time_b = datetime(2026, 10, 12, 12, 0, 0, tzinfo=timezone.utc)

        root_entries = [
            SvnEntry(
                name="auth-service",
                path="auth-service",
                is_directory=True,
                last_modified=time_a,
                revision=101,
            ),
            SvnEntry(
                name="web-ui",
                path="web-ui",
                is_directory=True,
                last_modified=time_b,
                revision=102,
            ),
        ]
        auth_entries = [
            SvnEntry(name="trunk", path="auth-service/trunk", is_directory=True),
        ]
        auth_trunk = [
            SvnEntry(
                name="requirements.txt",
                path="auth-service/trunk/requirements.txt",
                is_directory=False,
            ),
        ]
        web_entries = [
            SvnEntry(name="trunk", path="web-ui/trunk", is_directory=True),
        ]
        web_trunk = [
            SvnEntry(
                name="package.json",
                path="web-ui/trunk/package.json",
                is_directory=False,
            ),
            SvnEntry(
                name="tsconfig.json",
                path="web-ui/trunk/tsconfig.json",
                is_directory=False,
            ),
        ]

        client = MockSvnClient(
            {
                base_url: root_entries,
                f"{base_url}/auth-service": auth_entries,
                f"{base_url}/auth-service/trunk": auth_trunk,
                f"{base_url}/web-ui": web_entries,
                f"{base_url}/web-ui/trunk": web_trunk,
            }
        )

        scraper = SvnRepositoryScraper(
            url=base_url,
            organization="enterprise",
            project="core-platform",
            layout=SvnLayoutType.MULTI_PROJECT,
            client=client,
        )

        repos = scraper.scrape()
        assert len(repos) == 2

        auth_repo = next(r for r in repos if r.name == "auth-service")
        assert auth_repo.organization == "enterprise"
        assert auth_repo.project == "core-platform"
        assert auth_repo.path == "auth-service/trunk"
        assert (
            auth_repo.url == "http://svn.example.com/svn/mono-repo/auth-service/trunk"
        )
        assert auth_repo.default_branch == "trunk"
        assert auth_repo.last_commit == time_a
        assert auth_repo.stacks == [Stack.PYTHON]
        assert auth_repo.metadata["layout"] == "multi_project"
        assert auth_repo.metadata["revision"] == 101
        assert auth_repo.qualified_name == "enterprise/core-platform/auth-service"

        web_repo = next(r for r in repos if r.name == "web-ui")
        assert web_repo.organization == "enterprise"
        assert web_repo.project == "core-platform"
        assert web_repo.path == "web-ui/trunk"
        assert web_repo.url == "http://svn.example.com/svn/mono-repo/web-ui/trunk"
        assert web_repo.default_branch == "trunk"
        assert web_repo.last_commit == time_b
        assert web_repo.stacks == [Stack.TYPESCRIPT]
        assert web_repo.qualified_name == "enterprise/core-platform/web-ui"


class TestSvnRepositoryScraperFlatLayout:
    """Tests for flat SVN layout (source files directly in root)."""

    def test_flat_layout_success(self) -> None:
        base_url = "http://svn.example.com/svn/scripts-repo"
        now = datetime(2026, 10, 15, 8, 0, 0, tzinfo=timezone.utc)

        root_entries = [
            SvnEntry(
                name="backup.sh",
                path="backup.sh",
                is_directory=False,
                last_modified=now,
                revision=5,
            ),
            SvnEntry(
                name="main.tf",
                path="main.tf",
                is_directory=False,
                last_modified=now,
                revision=5,
            ),
        ]

        client = MockSvnClient({base_url: root_entries})
        scraper = SvnRepositoryScraper(
            url=base_url,
            layout=SvnLayoutType.FLAT,
            client=client,
        )

        repos = scraper.scrape()
        assert len(repos) == 1
        repo = repos[0]
        assert repo.name == "scripts-repo"
        assert repo.path == ""
        assert repo.url == base_url
        assert repo.default_branch is None
        assert repo.last_commit == now
        assert repo.stacks == [Stack.BASH, Stack.TERRAFORM]
        assert repo.metadata["layout"] == "flat"


class TestSvnRepositoryScraperCollectionLayout:
    """Tests for SVN parent collection layout hosting multiple repositories."""

    def test_collection_layout_success(self) -> None:
        collection_url = "http://svn.example.com/svn"
        now = datetime(2026, 10, 16, 10, 0, 0, tzinfo=timezone.utc)

        collection_entries = [
            SvnEntry(name="app-one", path="app-one", is_directory=True),
            SvnEntry(name="app-two", path="app-two", is_directory=True),
        ]
        app_one_entries = [
            SvnEntry(
                name="trunk",
                path="trunk",
                is_directory=True,
                last_modified=now,
                revision=10,
            ),
        ]
        app_one_trunk = [
            SvnEntry(name="pom.xml", path="trunk/pom.xml", is_directory=False),
        ]
        app_two_entries = [
            SvnEntry(
                name="trunk",
                path="trunk",
                is_directory=True,
                last_modified=now,
                revision=20,
            ),
        ]
        app_two_trunk = [
            SvnEntry(name="setup.py", path="trunk/setup.py", is_directory=False),
        ]

        client = MockSvnClient(
            {
                collection_url: collection_entries,
                f"{collection_url}/app-one": app_one_entries,
                f"{collection_url}/app-one/trunk": app_one_trunk,
                f"{collection_url}/app-two": app_two_entries,
                f"{collection_url}/app-two/trunk": app_two_trunk,
            }
        )

        scraper = SvnRepositoryScraper(
            url=collection_url,
            layout=SvnLayoutType.COLLECTION,
            client=client,
        )

        repos = scraper.scrape()
        assert len(repos) == 2
        names = {r.name for r in repos}
        assert names == {"app-one", "app-two"}
        assert any(r.stacks == [Stack.JAVA] for r in repos)
        assert any(r.stacks == [Stack.PYTHON] for r in repos)


class TestSvnLayoutAutoDetection:
    """Tests for automatic layout resolution in SvnRepositoryScraper."""

    def test_auto_detects_standard_layout(self) -> None:
        base_url = "http://svn.example.com/svn/repo-std"
        client = MockSvnClient(
            {
                base_url: [
                    SvnEntry(name="trunk", path="trunk", is_directory=True),
                    SvnEntry(name="branches", path="branches", is_directory=True),
                ],
                f"{base_url}/trunk": [
                    SvnEntry(name="app.py", path="trunk/app.py", is_directory=False),
                ],
            }
        )
        scraper = SvnRepositoryScraper(url=base_url, client=client)
        repos = scraper.scrape()
        assert len(repos) == 1
        assert repos[0].metadata["layout"] == "standard"
        assert repos[0].stacks == [Stack.PYTHON]

    def test_auto_detects_multi_project_layout(self) -> None:
        base_url = "http://svn.example.com/svn/repo-multi"
        client = MockSvnClient(
            {
                base_url: [
                    SvnEntry(name="project-x", path="project-x", is_directory=True),
                ],
                f"{base_url}/project-x": [
                    SvnEntry(name="trunk", path="project-x/trunk", is_directory=True),
                ],
                f"{base_url}/project-x/trunk": [
                    SvnEntry(
                        name="App.cs", path="project-x/trunk/App.cs", is_directory=False
                    ),
                ],
            }
        )
        scraper = SvnRepositoryScraper(url=base_url, client=client)
        repos = scraper.scrape()
        assert len(repos) == 1
        assert repos[0].name == "project-x"
        assert repos[0].metadata["layout"] == "multi_project"
        assert Stack.C_SHARP in repos[0].stacks

    def test_auto_detects_flat_layout(self) -> None:
        base_url = "http://svn.example.com/svn/repo-flat"
        client = MockSvnClient(
            {
                base_url: [
                    SvnEntry(name="script.sh", path="script.sh", is_directory=False),
                ],
            }
        )
        scraper = SvnRepositoryScraper(url=base_url, client=client)
        repos = scraper.scrape()
        assert len(repos) == 1
        assert repos[0].metadata["layout"] == "flat"
        assert repos[0].stacks == [Stack.BASH]

    def test_auto_detects_collection_when_url_is_svn_root(self) -> None:
        base_url = "http://svn.example.com/svn"
        client = MockSvnClient(
            {
                base_url: [
                    SvnEntry(name="service-one", path="service-one", is_directory=True),
                ],
                f"{base_url}/service-one": [
                    SvnEntry(name="trunk", path="trunk", is_directory=True),
                ],
                f"{base_url}/service-one/trunk": [
                    SvnEntry(name="pom.xml", path="trunk/pom.xml", is_directory=False),
                ],
            }
        )
        scraper = SvnRepositoryScraper(url=base_url, client=client)
        repos = scraper.scrape()
        assert len(repos) == 1
        assert repos[0].name == "service-one"


class TestTechnologyStackDetection:
    """Tests for technology stack detection from files."""

    @pytest.mark.parametrize(
        ("filenames", "expected_stacks"),
        [
            (["pom.xml", "App.java"], [Stack.JAVA]),
            (["build.gradle"], [Stack.JAVA]),
            (["requirements.txt", "main.py"], [Stack.PYTHON]),
            (["pyproject.toml"], [Stack.PYTHON]),
            (["package.json", "index.ts"], [Stack.TYPESCRIPT]),
            (["tsconfig.json"], [Stack.TYPESCRIPT]),
            (["App.csproj", "Program.cs"], [Stack.C_SHARP, Stack.DOTNET_CORE]),
            (["deploy.sh"], [Stack.BASH]),
            (["infra.tf", "vars.tfvars"], [Stack.TERRAFORM]),
            (["readme.md", "notes.txt"], [Stack.UNKNOWN]),
            (["pom.xml", "package.json"], [Stack.JAVA, Stack.TYPESCRIPT]),
        ],
    )
    def test_stack_detection_scenarios(
        self, filenames: list[str], expected_stacks: list[Stack]
    ) -> None:
        base_url = "http://svn.example.com/svn/tech-repo"
        entries = [
            SvnEntry(name=f, path=f"trunk/{f}", is_directory=False) for f in filenames
        ]
        client = MockSvnClient(
            {
                base_url: [SvnEntry(name="trunk", path="trunk", is_directory=True)],
                f"{base_url}/trunk": entries,
            }
        )

        scraper = SvnRepositoryScraper(url=base_url, client=client)
        repos = scraper.scrape()
        assert len(repos) == 1
        assert repos[0].stacks == expected_stacks


class TestSvnScraperCornerCasesAndErrors:
    """Tests for corner cases, validation errors, and communication failures."""

    def test_empty_url_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="SVN URL cannot be empty"):
            SvnRepositoryScraper(url="   ")

    def test_invalid_scheme_raises_value_error(self) -> None:
        with pytest.raises(
            ValueError, match="SVN URL must start with http:// or https://"
        ):
            SvnRepositoryScraper(url="ftp://svn.example.com/repo")

    def test_empty_repository_returns_single_repo_with_unknown_stack(self) -> None:
        base_url = "http://svn.example.com/svn/empty-repo"
        client = MockSvnClient({base_url: []})
        scraper = SvnRepositoryScraper(url=base_url, client=client)
        repos = scraper.scrape()
        assert len(repos) == 1
        assert repos[0].name == "empty-repo"
        assert repos[0].stacks == [Stack.UNKNOWN]

    def test_empty_trunk_returns_repo_with_unknown_stack(self) -> None:
        base_url = "http://svn.example.com/svn/empty-trunk-repo"
        client = MockSvnClient(
            {
                base_url: [SvnEntry(name="trunk", path="trunk", is_directory=True)],
                f"{base_url}/trunk": [],
            }
        )
        scraper = SvnRepositoryScraper(url=base_url, client=client)
        repos = scraper.scrape()
        assert len(repos) == 1
        assert repos[0].stacks == [Stack.UNKNOWN]

    def test_authentication_error_is_propagated(self) -> None:
        client = MagicMock(spec=SvnClient)
        client.list_dir.side_effect = ScraperAuthenticationError(
            "SVN authentication failed (401)"
        )
        scraper = SvnRepositoryScraper(
            url="http://svn.example.com/svn/repo",
            client=client,
        )
        with pytest.raises(ScraperAuthenticationError):
            scraper.scrape()

    def test_connection_error_is_propagated(self) -> None:
        client = MagicMock(spec=SvnClient)
        client.list_dir.side_effect = ScraperConnectionError("Cannot connect to SVN")
        scraper = SvnRepositoryScraper(
            url="http://svn.example.com/svn/repo",
            client=client,
        )
        with pytest.raises(ScraperConnectionError):
            scraper.scrape()


class TestSvnWebDavClientUnit:
    """Unit tests for SvnWebDavClient public methods and HTTP integrations."""

    def test_list_dir_propfind_parsing(self) -> None:
        xml_data = b"""<?xml version="1.0" encoding="utf-8"?>
<D:multistatus xmlns:D="DAV:" xmlns:lp1="DAV:" xmlns:lp3="http://subversion.tigris.org/xmlns/dav/">
<D:response>
<D:href>/svn/my-repo/</D:href>
<D:propstat><D:prop><lp1:resourcetype><D:collection/></lp1:resourcetype></D:prop></D:propstat>
</D:response>
<D:response>
<D:href>/svn/my-repo/trunk/</D:href>
<D:propstat>
<D:prop>
<lp1:resourcetype><D:collection/></lp1:resourcetype>
<lp1:getlastmodified>Fri, 16 Oct 2026 19:54:01 GMT</lp1:getlastmodified>
<lp1:version-name>12</lp1:version-name>
<lp1:creator-displayname>jackson</lp1:creator-displayname>
</D:prop>
<D:status>HTTP/1.1 200 OK</D:status>
</D:propstat>
</D:response>
<D:response>
<D:href>/svn/my-repo/trunk/pom.xml</D:href>
<D:propstat>
<D:prop>
<lp1:getlastmodified>2026-10-16T19:54:01.296022Z</lp1:getlastmodified>
<lp1:version-name>12</lp1:version-name>
<lp1:getcontentlength>512</lp1:getcontentlength>
</D:prop>
<D:status>HTTP/1.1 200 OK</D:status>
</D:propstat>
</D:response>
</D:multistatus>"""
        client = SvnWebDavClient()
        mock_resp = MagicMock()
        mock_resp.read.return_value = xml_data
        mock_resp.__enter__.return_value = mock_resp
        mock_resp.__exit__.return_value = None

        with patch("urllib.request.urlopen", return_value=mock_resp):
            entries = client.list_dir("http://svn.example.com/svn/my-repo/")

        assert len(entries) == 2

        trunk_entry = next(e for e in entries if e.name == "trunk")
        assert trunk_entry.is_directory
        assert trunk_entry.revision == 12
        assert trunk_entry.author == "jackson"
        assert trunk_entry.last_modified is not None
        assert trunk_entry.last_modified.year == 2026

        pom_entry = next(e for e in entries if e.name == "pom.xml")
        assert not pom_entry.is_directory
        assert pom_entry.size == 512
        assert pom_entry.last_modified is not None
        assert pom_entry.last_modified.minute == 54

    def test_list_dir_handles_invalid_date(self) -> None:
        xml_data = b"""<?xml version="1.0" encoding="utf-8"?>
<D:multistatus xmlns:D="DAV:" xmlns:lp1="DAV:">
<D:response>
<D:href>/svn/my-repo/test.txt</D:href>
<D:propstat><D:prop><lp1:getlastmodified>not-a-date</lp1:getlastmodified></D:prop></D:propstat>
</D:response>
</D:multistatus>"""
        client = SvnWebDavClient()
        mock_resp = MagicMock()
        mock_resp.read.return_value = xml_data
        mock_resp.__enter__.return_value = mock_resp
        mock_resp.__exit__.return_value = None

        with patch("urllib.request.urlopen", return_value=mock_resp):
            entries = client.list_dir("http://svn.example.com/svn/my-repo/")

        assert len(entries) == 1
        assert entries[0].last_modified is None

    def test_list_dir_sends_basic_auth_header(self) -> None:
        auth = BasicAuthenticator("myuser", "mypass")
        client = SvnWebDavClient(auth=auth)
        mock_resp = MagicMock()
        mock_resp.read.return_value = b'<D:multistatus xmlns:D="DAV:"></D:multistatus>'
        mock_resp.__enter__.return_value = mock_resp
        mock_resp.__exit__.return_value = None

        with patch("urllib.request.urlopen", return_value=mock_resp) as mock_urlopen:
            client.list_dir("http://svn.example.com/svn/my-repo")
            req = mock_urlopen.call_args[0][0]
            auth_header = req.get_header("Authorization")
            assert auth_header is not None and auth_header.startswith("Basic ")

    def test_list_dir_sends_token_bearer_auth_header(self) -> None:
        auth = TokenAuthenticator("secret-pat")
        client = SvnWebDavClient(auth=auth)
        mock_resp = MagicMock()
        mock_resp.read.return_value = b'<D:multistatus xmlns:D="DAV:"></D:multistatus>'
        mock_resp.__enter__.return_value = mock_resp
        mock_resp.__exit__.return_value = None

        with patch("urllib.request.urlopen", return_value=mock_resp) as mock_urlopen:
            client.list_dir("http://svn.example.com/svn/my-repo")
            req = mock_urlopen.call_args[0][0]
            assert req.get_header("Authorization") == "Bearer secret-pat"

    def test_list_dir_html_fallback_on_405(self) -> None:
        html_data = b"""
        <html><body>
        <h2>Collection of Repositories</h2>
        <ul>
          <li><a href="../">..</a></li>
          <li><a href="repo1/">repo1/</a></li>
          <li><a href="file2.txt">file2.txt</a></li>
        </ul>
        </body></html>
        """
        client = SvnWebDavClient()
        mock_err = urllib.error.HTTPError(
            url="http://svn.example.com/svn/",
            code=405,
            msg="Method Not Allowed",
            hdrs={},  # type: ignore[arg-type]
            fp=None,
        )
        mock_html_resp = MagicMock()
        mock_html_resp.read.return_value = html_data
        mock_html_resp.__enter__.return_value = mock_html_resp
        mock_html_resp.__exit__.return_value = None

        with patch("urllib.request.urlopen", side_effect=[mock_err, mock_html_resp]):
            entries = client.list_dir("http://svn.example.com/svn/")

        assert len(entries) == 2
        assert entries[0].name == "repo1"
        assert entries[0].is_directory
        assert entries[1].name == "file2.txt"
        assert not entries[1].is_directory
