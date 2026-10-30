"""Subversion (SVN) repository scraper and WebDAV client implementation."""

from __future__ import annotations

import base64
import email.utils
import html.parser
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET  # nosec B405
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from gh_migration_inventory_manager.scrap import (
    Authenticator,
    BasicAuthenticator,
    NormalizedRole,
    Repository,
    RepositoryScraper,
    ScraperAuthenticationError,
    ScraperConnectionError,
    ScraperError,
    ScraperProvider,
    Stack,
    TeamPermission,
    TeamScraper,
    TokenAuthenticator,
    default_scraper_registry,
    slugify,
)


class SvnLayoutType(str, Enum):
    """Layout type for SVN repositories."""

    AUTO = "auto"
    STANDARD = "standard"
    MULTI_PROJECT = "multi_project"
    FLAT = "flat"
    COLLECTION = "collection"


@dataclass(frozen=True)
class SvnEntry:
    """Pure data container representing an entry in an SVN repository."""

    name: str
    path: str
    is_directory: bool
    last_modified: datetime | None = None
    revision: int | None = None
    author: str | None = None
    size: int | None = None


@runtime_checkable
class SvnClient(Protocol):
    """Protocol defining operations required to communicate with an SVN server."""

    def list_dir(self, url: str) -> list[SvnEntry]:
        """List entries (files and directories) in an SVN path."""
        raise NotImplementedError


class _SvnHtmlParser(html.parser.HTMLParser):
    """Parser for HTML directory listings served by SVN HTTP servers."""

    def __init__(self) -> None:
        super().__init__()
        self.entries: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "a":
            for name, value in attrs:
                if name.lower() == "href" and value:
                    clean = value.strip()
                    if (
                        clean
                        and not clean.startswith(
                            ("?", "http://", "https://", "//", "mailto:")
                        )
                        and clean not in ("../", "..", "/")
                    ):
                        self.entries.append(clean)


_PROPFIND_XML_BODY: bytes = (
    b'<?xml version="1.0" encoding="utf-8" ?>\n'
    b'<D:propfind xmlns:D="DAV:">\n'
    b"  <D:prop>\n"
    b"    <D:displayname/>\n"
    b"    <D:resourcetype/>\n"
    b"    <D:getlastmodified/>\n"
    b"    <D:version-name/>\n"
    b"    <D:creator-displayname/>\n"
    b"    <D:getcontentlength/>\n"
    b"  </D:prop>\n"
    b"</D:propfind>"
)
_PROPFIND_HEADERS: dict[str, str] = {
    "Depth": "1",
    "Content-Type": "text/xml; charset=utf-8",
}


class SvnWebDavClient:
    """WebDAV and HTTP client for Subversion servers."""

    def __init__(
        self,
        auth: Authenticator | None = None,
        timeout: float = 30.0,
    ) -> None:
        self.auth = auth
        self.timeout = timeout
        self._auth_header = self._build_auth_header()

    def _build_auth_header(self) -> str | None:
        if not self.auth:
            return None
        creds = self.auth.resolve_credentials()
        if creds.username and creds.password is not None:
            raw = f"{creds.username}:{creds.password}".encode()
            token = base64.b64encode(raw).decode("ascii")
            return f"Basic {token}"
        if creds.token:
            return f"Bearer {creds.token}"
        return None

    def _build_request(
        self,
        url: str,
        method: str = "GET",
        headers: dict[str, str] | None = None,
        data: bytes | None = None,
    ) -> urllib.request.Request:
        req = urllib.request.Request(url, data=data, method=method)
        if headers:
            for k, v in headers.items():
                req.add_header(k, v)

        if self._auth_header:
            req.add_header("Authorization", self._auth_header)

        return req

    @staticmethod
    def _parse_date(date_str: str | None) -> datetime | None:
        if not date_str or not date_str.strip():
            return None
        clean = date_str.strip()
        if "T" in clean:
            try:
                return datetime.fromisoformat(clean.replace("Z", "+00:00"))
            except (ValueError, TypeError):  # nosec B110
                pass
        try:
            return email.utils.parsedate_to_datetime(clean)
        except (ValueError, TypeError, IndexError):  # nosec B110
            pass
        return None

    def list_dir(self, url: str) -> list[SvnEntry]:
        """List entries in an SVN path using WebDAV PROPFIND or HTML fallback."""
        clean_url = url.rstrip("/") + "/"
        try:
            req = self._build_request(
                clean_url,
                method="PROPFIND",
                headers=_PROPFIND_HEADERS,
                data=_PROPFIND_XML_BODY,
            )
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:  # nosec B310
                body = resp.read()
                return self._parse_propfind_xml(body, clean_url)
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                raise ScraperAuthenticationError(
                    f"SVN authentication failed ({e.code}) for {url}: {e.reason}"
                ) from e
            if e.code == 404:
                raise ScraperError(f"SVN path not found (404): {url}") from e
            if e.code in (405, 500, 501):
                return self._fetch_html_listing(clean_url)
            raise ScraperError(
                f"SVN HTTP error ({e.code}) accessing {url}: {e.reason}"
            ) from e
        except (urllib.error.URLError, TimeoutError) as e:
            raise ScraperConnectionError(f"Cannot connect to SVN at {url}: {e}") from e

    def get_text(self, url: str) -> str:
        """Fetch raw text content from an SVN HTTP URL."""
        clean_url = (url or "").strip()
        if not clean_url:
            raise ValueError("URL cannot be empty.")
        parsed = urllib.parse.urlparse(clean_url)
        if parsed.scheme.lower() not in ("http", "https"):
            raise ValueError(
                f"Unsupported URL scheme '{parsed.scheme}'. "
                "SVN URL must use http:// or https://"
            )
        try:
            req = self._build_request(clean_url, method="GET")
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:  # nosec B310
                raw: bytes = resp.read()
                return raw.decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                raise ScraperAuthenticationError(
                    f"SVN authentication failed ({e.code}) for {url}: {e.reason}"
                ) from e
            if e.code == 404:
                raise ScraperError(f"SVN path not found (404): {url}") from e
            raise ScraperError(
                f"SVN HTTP error ({e.code}) accessing {url}: {e.reason}"
            ) from e
        except (urllib.error.URLError, TimeoutError) as e:
            raise ScraperConnectionError(f"Cannot connect to SVN at {url}: {e}") from e

    def _fetch_html_listing(self, url: str) -> list[SvnEntry]:
        """Fetch and parse HTML directory listing for SVN parent paths."""
        try:
            req = self._build_request(url, method="GET")
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:  # nosec B310
                html_text = resp.read().decode("utf-8", errors="replace")
            parser = _SvnHtmlParser()
            parser.feed(html_text)

            entries: list[SvnEntry] = []
            for item in parser.entries:
                is_dir = item.endswith("/")
                name = item.strip("/").split("/")[-1]
                entries.append(
                    SvnEntry(
                        name=name,
                        path=item.strip("/"),
                        is_directory=is_dir,
                    )
                )
            return entries
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                raise ScraperAuthenticationError(
                    f"SVN authentication failed ({e.code}) for {url}: {e.reason}"
                ) from e
            raise ScraperError(
                f"SVN HTTP error ({e.code}) accessing {url}: {e.reason}"
            ) from e
        except (urllib.error.URLError, TimeoutError) as e:
            raise ScraperConnectionError(f"Cannot connect to SVN at {url}: {e}") from e

    def _parse_propfind_xml(self, xml_content: bytes, base_url: str) -> list[SvnEntry]:
        try:
            root = ET.fromstring(xml_content)  # nosec B314
        except ET.ParseError as err:
            raise ScraperError(f"Failed to parse SVN XML response: {err}") from err

        base_parsed = urllib.parse.urlparse(base_url)
        base_path = base_parsed.path.rstrip("/") + "/"

        entries: list[SvnEntry] = []
        for resp in root.findall(".//{*}response"):
            href_elem = resp.findtext(".//{*}href")
            if not href_elem:
                continue

            raw_href = href_elem.strip()
            item_parsed = urllib.parse.urlparse(raw_href)
            item_path = item_parsed.path

            clean_item_path = item_path.rstrip("/") + "/"
            if clean_item_path == base_path or item_path.rstrip(
                "/"
            ) == base_path.rstrip("/"):
                continue

            rel_path = (
                item_path.removeprefix(base_path)
                if item_path.startswith(base_path)
                else item_path
            )
            name = urllib.parse.unquote(rel_path.strip("/").split("/")[-1])
            if not name:
                continue

            is_dir = resp.find(".//{*}collection") is not None
            last_mod_str = resp.findtext(".//{*}getlastmodified") or resp.findtext(
                ".//{*}creationdate"
            )
            last_mod = self._parse_date(last_mod_str)

            rev_str = resp.findtext(".//{*}version-name")
            rev = int(rev_str) if rev_str and rev_str.isdigit() else None
            author = resp.findtext(".//{*}creator-displayname")
            size_str = resp.findtext(".//{*}getcontentlength")
            size = int(size_str) if size_str and size_str.isdigit() else None

            entries.append(
                SvnEntry(
                    name=name,
                    path=urllib.parse.unquote(rel_path.strip("/")),
                    is_directory=is_dir,
                    last_modified=last_mod,
                    revision=rev,
                    author=author,
                    size=size,
                )
            )

        return entries


class SvnRepositoryScraper(RepositoryScraper):
    """Repository scraper discovering projects and stacks from Subversion (SVN)."""

    def __init__(
        self,
        url: str,
        auth: Authenticator | None = None,
        layout: SvnLayoutType = SvnLayoutType.AUTO,
        organization: str | None = None,
        project: str | None = None,
        client: SvnClient | None = None,
    ) -> None:
        clean_url = (url or "").strip()
        if not clean_url:
            raise ValueError("SVN URL cannot be empty.")
        if not (clean_url.startswith("http://") or clean_url.startswith("https://")):
            raise ValueError("SVN URL must start with http:// or https://.")

        self.url = clean_url.rstrip("/")
        self.auth = auth
        self.layout = layout
        self.organization = organization
        self.project = project
        self.client: SvnClient = (
            client if client is not None else SvnWebDavClient(auth=auth)
        )

    @staticmethod
    def _detect_stacks(entries: list[SvnEntry]) -> list[Stack]:
        """Detect technology stacks from file entries within a repository."""
        detected: set[Stack] = set()
        file_names = {entry.name.lower() for entry in entries if not entry.is_directory}

        for name in file_names:
            if name in (
                "pom.xml",
                "build.gradle",
                "build.gradle.kts",
                "settings.gradle",
            ) or name.endswith(".java"):
                detected.add(Stack.JAVA)
            if name in (
                "requirements.txt",
                "pyproject.toml",
                "setup.py",
                "pipfile",
            ) or name.endswith(".py"):
                detected.add(Stack.PYTHON)
            if name in ("package.json", "tsconfig.json") or name.endswith(
                (".ts", ".tsx")
            ):
                detected.add(Stack.TYPESCRIPT)
            if name.endswith((".csproj", ".sln", ".cs")):
                detected.add(Stack.C_SHARP)
                detected.add(Stack.DOTNET_CORE)
            if name.endswith((".sh", ".bash")):
                detected.add(Stack.BASH)
            if name.endswith((".tf", ".tfvars")):
                detected.add(Stack.TERRAFORM)

        return sorted(detected) if detected else [Stack.UNKNOWN]

    def _extract_repo_name(self) -> str:
        parts = self.url.rstrip("/").split("/")
        return urllib.parse.unquote(parts[-1]) if parts else "svn-repo"

    def _scrape_standard(
        self,
        repo_name: str,
        root_entries: list[SvnEntry],
    ) -> list[Repository]:
        trunk_entry = next((e for e in root_entries if e.name.lower() == "trunk"), None)
        trunk_url = f"{self.url}/trunk"
        trunk_files: list[SvnEntry] = []
        try:
            trunk_files = self.client.list_dir(trunk_url)
        except ScraperError:
            # Trunk directory is empty or inaccessible; fallback to empty entry list
            pass

        stacks = self._detect_stacks(trunk_files)
        last_commit = trunk_entry.last_modified if trunk_entry else None
        if not last_commit and trunk_files:
            last_commit = max(
                (f.last_modified for f in trunk_files if f.last_modified),
                default=None,
            )

        revision = trunk_entry.revision if trunk_entry else None

        return [
            Repository(
                name=repo_name,
                organization=self.organization,
                project=self.project or repo_name,
                path="trunk",
                url=trunk_url,
                default_branch="trunk",
                last_commit=last_commit,
                stacks=stacks,
                metadata={
                    "layout": SvnLayoutType.STANDARD.value,
                    "revision": revision,
                },
            )
        ]

    def _scrape_multi_project(
        self,
        repo_name: str,
        root_entries: list[SvnEntry],
    ) -> list[Repository]:
        sub_dirs = [
            e
            for e in root_entries
            if e.is_directory and e.name.lower() not in ("trunk", "branches", "tags")
        ]

        repositories: list[Repository] = []
        for sub in sub_dirs:
            sub_url = f"{self.url}/{sub.name}"
            sub_entries: list[SvnEntry] = []
            try:
                sub_entries = self.client.list_dir(sub_url)
            except ScraperError:
                continue

            has_trunk = any(e.name.lower() == "trunk" for e in sub_entries)
            if has_trunk:
                target_url = f"{sub_url}/trunk"
                target_path = f"{sub.name}/trunk"
                default_branch: str | None = "trunk"
                trunk_files: list[SvnEntry] = []
                try:
                    trunk_files = self.client.list_dir(target_url)
                except ScraperError:
                    # Inaccessible or missing trunk; fallback to empty entries
                    pass
                stacks = self._detect_stacks(trunk_files)
                last_commit = sub.last_modified
            else:
                target_url = sub_url
                target_path = sub.name
                default_branch = None
                stacks = self._detect_stacks(sub_entries)
                last_commit = sub.last_modified

            repositories.append(
                Repository(
                    name=sub.name,
                    organization=self.organization,
                    project=self.project or repo_name,
                    path=target_path,
                    url=target_url,
                    default_branch=default_branch,
                    last_commit=last_commit,
                    stacks=stacks,
                    metadata={
                        "layout": SvnLayoutType.MULTI_PROJECT.value,
                        "revision": sub.revision,
                    },
                )
            )

        return repositories

    def _scrape_flat(
        self,
        repo_name: str,
        root_entries: list[SvnEntry],
    ) -> list[Repository]:
        stacks = self._detect_stacks(root_entries)
        last_commit = max(
            (e.last_modified for e in root_entries if e.last_modified),
            default=None,
        )
        revision = max(
            (e.revision for e in root_entries if e.revision is not None),
            default=None,
        )

        return [
            Repository(
                name=repo_name,
                organization=self.organization,
                project=self.project or repo_name,
                path="",
                url=self.url,
                default_branch=None,
                last_commit=last_commit,
                stacks=stacks,
                metadata={"layout": SvnLayoutType.FLAT.value, "revision": revision},
            )
        ]

    def _scrape_collection(self, root_entries: list[SvnEntry]) -> list[Repository]:
        repositories: list[Repository] = []
        for entry in root_entries:
            if not entry.is_directory:
                continue
            child_url = f"{self.url}/{entry.name}"
            try:
                child_scraper = SvnRepositoryScraper(
                    url=child_url,
                    auth=self.auth,
                    layout=SvnLayoutType.AUTO,
                    organization=self.organization,
                    project=self.project or entry.name,
                    client=self.client,
                )
                repositories.extend(child_scraper.scrape())
            except ScraperError:
                continue
        return repositories

    def scrape(self) -> list[Repository]:
        """Discover and return repositories from the Subversion server."""
        root_entries = self.client.list_dir(self.url)
        repo_name = self._extract_repo_name()

        if self.layout == SvnLayoutType.COLLECTION or (
            self.layout == SvnLayoutType.AUTO
            and (
                repo_name.lower() in ("svn", "subversion")
                or any(
                    e.name.lower() in ("collection", "repositories")
                    for e in root_entries
                )
            )
        ):
            standard_names = {"trunk", "branches", "tags"}
            if root_entries and not any(
                e.name.lower() in standard_names for e in root_entries
            ):
                if self.layout == SvnLayoutType.COLLECTION or repo_name.lower() in (
                    "svn",
                    "subversion",
                ):
                    return self._scrape_collection(root_entries)

        if self.layout == SvnLayoutType.STANDARD:
            return self._scrape_standard(repo_name, root_entries)

        if self.layout == SvnLayoutType.MULTI_PROJECT:
            return self._scrape_multi_project(repo_name, root_entries)

        if self.layout == SvnLayoutType.FLAT:
            return self._scrape_flat(repo_name, root_entries)

        has_trunk = any(e.name.lower() == "trunk" for e in root_entries)
        if has_trunk:
            return self._scrape_standard(repo_name, root_entries)

        sub_dirs = [
            e
            for e in root_entries
            if e.is_directory and e.name.lower() not in ("branches", "tags")
        ]
        if sub_dirs:
            is_multi = False
            for sub in sub_dirs:
                try:
                    sub_contents = self.client.list_dir(f"{self.url}/{sub.name}")
                    if any(e.name.lower() == "trunk" for e in sub_contents):
                        is_multi = True
                        break
                except ScraperError:
                    continue
            if is_multi:
                return self._scrape_multi_project(repo_name, root_entries)

        return self._scrape_flat(repo_name, root_entries)


class SvnScraperProvider(ScraperProvider):
    """Provider exposing Subversion (SVN) configuration to the CLI."""

    @property
    def platform_name(self) -> str:
        return "svn"

    @property
    def description(self) -> str:
        return "Subversion (SVN) repository scraper"

    def configure_parser(self, parser: Any) -> None:
        """Add SVN-specific arguments to the subparser."""
        parser.add_argument(
            "--url",
            required=True,
            help="SVN server or repository URL (http:// or https://)",
        )
        parser.add_argument(
            "--username",
            default=None,
            help="SVN authentication username",
        )
        parser.add_argument(
            "--password",
            default=None,
            help="SVN authentication password",
        )
        parser.add_argument(
            "--token",
            default=None,
            help="SVN bearer/PAT token",
        )
        parser.add_argument(
            "--layout",
            choices=[layout.value for layout in SvnLayoutType],
            default=SvnLayoutType.AUTO.value,
            help="SVN repository layout structure (default: auto)",
        )
        parser.add_argument(
            "--organization",
            default=None,
            help="Organization name for discovered repositories",
        )
        parser.add_argument(
            "--project",
            default=None,
            help="Project name override for discovered repositories",
        )

    def create_scraper(self, args: Any) -> SvnRepositoryScraper:
        """Build SvnRepositoryScraper from parsed arguments."""
        auth: Authenticator | None = None
        username = getattr(args, "username", None)
        password = getattr(args, "password", None)
        token = getattr(args, "token", None)

        if username and password is not None:
            auth = BasicAuthenticator(username=username, password=password)
        elif token:
            auth = TokenAuthenticator(token=token)

        raw_layout = getattr(args, "layout", SvnLayoutType.AUTO.value)
        layout = SvnLayoutType(raw_layout) if raw_layout else SvnLayoutType.AUTO

        return SvnRepositoryScraper(
            url=getattr(args, "url", None) or "",
            auth=auth,
            layout=layout,
            organization=getattr(args, "organization", None),
            project=getattr(args, "project", None),
        )


default_scraper_registry.register(SvnScraperProvider())


class SvnAuthzParser:
    """Parser for Subversion authz configuration files."""

    def parse(self, content: str) -> list[TeamPermission]:
        """Parse authz file content into a list of TeamPermission domain objects.

        Args:
            content: Raw text content of an SVN authz configuration file.

        Returns:
            A list of discovered TeamPermission objects.
        """
        if not content or not content.strip():
            return []

        groups: dict[str, list[str]] = {}
        path_rules: list[tuple[str, str, str]] = []
        current_section = ""

        for raw_line in content.splitlines():
            line = raw_line.strip()
            if not line or line.startswith(("#", ";")):
                continue

            if line.startswith("[") and line.endswith("]"):
                current_section = line[1:-1].strip()
                continue

            if "=" not in line:
                continue

            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip()

            if current_section.lower() == "groups":
                if key:
                    members = [m.strip() for m in value.split(",") if m.strip()]
                    groups[key] = members
            elif current_section:
                path_rules.append((current_section, key, value))

        permissions: list[TeamPermission] = []
        for path, key, access_str in path_rules:
            access = access_str.strip().lower()
            if not access:
                continue

            if key.startswith("@"):
                team_name = key[1:].strip()
                if not team_name:
                    continue
                member_count = len(groups.get(team_name, []))
                team_slug = slugify(team_name)
            elif key == "*":
                team_name = "*"
                member_count = 0
                team_slug = "all-users"
            else:
                continue

            clean_path = path.strip()
            norm_path = (
                clean_path.split(":", 1)[-1].strip()
                if ":" in clean_path
                else clean_path
            )
            norm_path_lower = norm_path.lower()

            if access == "r":
                norm_role = NormalizedRole.READ
            elif "w" in access:
                if norm_path in ("/", "") or norm_path == "[/]":
                    norm_role = NormalizedRole.ADMIN
                elif "/tags" in norm_path_lower or norm_path_lower.endswith("tags"):
                    norm_role = NormalizedRole.MAINTAIN
                else:
                    norm_role = NormalizedRole.WRITE
            else:
                norm_role = NormalizedRole.READ

            perm = TeamPermission(
                team_name=team_name,
                member_count=member_count,
                target_repo_or_project=clean_path,
                normalized_role=norm_role,
                origin_role=access_str,
                team_slug=team_slug,
            )
            permissions.append(perm)

        return permissions


class SvnTeamScraper(TeamScraper):
    """Scraper discovering team permissions from Subversion authz files."""

    scraper_type: str = "team"
    target_type: str = "team"

    def __init__(
        self,
        authz_path: str | Path | None = None,
        authz_url: str | None = None,
        client: SvnWebDavClient | None = None,
        auth: Authenticator | None = None,
        parser: SvnAuthzParser | None = None,
    ) -> None:
        if not authz_path and not authz_url:
            raise ValueError(
                "Either authz_path (local file) or authz_url (remote URL) "
                "must be provided."
            )
        self.authz_path = Path(authz_path) if authz_path else None
        clean_url = str(authz_url).strip() if authz_url else None
        if clean_url:
            parsed = urllib.parse.urlparse(clean_url)
            if parsed.scheme.lower() not in ("http", "https"):
                raise ValueError(
                    f"Unsupported URL scheme '{parsed.scheme}'. "
                    "SVN URL must use http:// or https://"
                )
        self.authz_url = clean_url
        self.client = client or (SvnWebDavClient(auth=auth) if self.authz_url else None)
        self.parser = parser or SvnAuthzParser()

    def scrape(self) -> list[TeamPermission]:
        """Discover and return team permissions from SVN authz configuration."""
        if self.authz_path is not None:
            if not self.authz_path.exists():
                raise ScraperError(f"SVN authz file not found: '{self.authz_path}'.")
            try:
                content = self.authz_path.read_text(encoding="utf-8", errors="replace")
            except OSError as err:
                raise ScraperError(
                    f"Failed to read SVN authz file '{self.authz_path}': {err}"
                ) from err
            return self.parser.parse(content)

        if self.authz_url is not None:
            client = self.client or SvnWebDavClient()
            content = client.get_text(self.authz_url)
            return self.parser.parse(content)

        return []


class SvnTeamsScraperProvider(ScraperProvider):
    """Provider exposing SVN team topology and authz scraping to the CLI."""

    target_type: str = "team"
    default_sheet_name: str = "Topology"

    @property
    def platform_name(self) -> str:
        return "svn-teams"

    @property
    def description(self) -> str:
        return "Subversion (SVN) team topology and authz scraper"

    def configure_parser(self, parser: Any) -> None:
        """Add SVN authz and team discovery arguments to the subparser."""
        parser.add_argument(
            "--authz-path",
            default=None,
            help="Path to local SVN authz configuration file",
        )
        parser.add_argument(
            "--authz-url",
            "--url",
            dest="authz_url",
            default=None,
            help="Remote SVN authz configuration HTTP/WebDAV URL",
        )
        parser.add_argument(
            "--username",
            default=None,
            help="SVN authentication username",
        )
        parser.add_argument(
            "--password",
            default=None,
            help="SVN authentication password",
        )
        parser.add_argument(
            "--token",
            default=None,
            help="SVN bearer/PAT token",
        )

    def create_scraper(self, args: Any) -> SvnTeamScraper:
        """Build SvnTeamScraper from parsed CLI arguments."""
        auth: Authenticator | None = None
        username = getattr(args, "username", None)
        password = getattr(args, "password", None)
        token = getattr(args, "token", None)

        if username and password is not None:
            auth = BasicAuthenticator(username=username, password=password)
        elif token:
            auth = TokenAuthenticator(token=token)

        authz_path = getattr(args, "authz_path", None)
        authz_url = getattr(args, "authz_url", None)

        return SvnTeamScraper(
            authz_path=authz_path,
            authz_url=authz_url,
            auth=auth,
        )


default_scraper_registry.register(SvnTeamsScraperProvider())


__all__ = [
    "SvnAuthzParser",
    "SvnClient",
    "SvnEntry",
    "SvnLayoutType",
    "SvnRepositoryScraper",
    "SvnScraperProvider",
    "SvnTeamScraper",
    "SvnTeamsScraperProvider",
    "SvnWebDavClient",
]
