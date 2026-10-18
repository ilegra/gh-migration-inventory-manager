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
from typing import Protocol, runtime_checkable

from gh_migration_inventory_manager.scrap import (
    Authenticator,
    Repository,
    ScraperAuthenticationError,
    ScraperConnectionError,
    ScraperError,
    Stack,
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
        ...

    def get_file_content(self, url: str) -> bytes | None:
        """Fetch raw content of an SVN file."""
        ...


class _SvnHtmlParser(html.parser.HTMLParser):
    """Parser for HTML directory listings served by SVN HTTP servers."""

    def __init__(self) -> None:
        super().__init__()
        self.entries: list[str] = []
        self.is_collection_listing: bool = False

    def handle_data(self, data: str) -> None:
        if "Collection of Repositories" in data:
            self.is_collection_listing = True

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


class SvnWebDavClient:
    """WebDAV and HTTP client for Subversion servers."""

    def __init__(
        self,
        auth: Authenticator | None = None,
        timeout: float = 30.0,
    ) -> None:
        self.auth = auth
        self.timeout = timeout

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

        if self.auth:
            creds = self.auth.resolve_credentials()
            if creds.username and creds.password is not None:
                raw = f"{creds.username}:{creds.password}".encode()
                token = base64.b64encode(raw).decode("ascii")
                req.add_header("Authorization", f"Basic {token}")
            elif creds.token:
                req.add_header("Authorization", f"Bearer {creds.token}")

        return req

    @staticmethod
    def _parse_date(date_str: str | None) -> datetime | None:
        if not date_str or not date_str.strip():
            return None
        clean = date_str.strip()
        try:
            return email.utils.parsedate_to_datetime(clean)
        except (ValueError, TypeError, IndexError):  # nosec B110
            pass
        try:
            return datetime.fromisoformat(clean.replace("Z", "+00:00"))
        except (ValueError, TypeError):  # nosec B110
            pass
        return None

    def list_dir(self, url: str) -> list[SvnEntry]:
        """List entries in an SVN path using WebDAV PROPFIND or HTML fallback."""
        clean_url = url.rstrip("/") + "/"
        xml_body = (
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

        headers = {
            "Depth": "1",
            "Content-Type": "text/xml; charset=utf-8",
        }

        try:
            req = self._build_request(
                clean_url, method="PROPFIND", headers=headers, data=xml_body
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
        for resp in root:
            if resp.tag.split("}")[-1] != "response":
                continue

            href_elem = None
            prop_elems: dict[str, str] = {}
            is_dir = False

            for child in resp:
                tag = child.tag.split("}")[-1]
                if tag == "href":
                    href_elem = child
                elif tag == "propstat":
                    for propstat_child in child:
                        if propstat_child.tag.split("}")[-1] == "prop":
                            for prop in propstat_child:
                                ptag = prop.tag.split("}")[-1]
                                if ptag == "resourcetype":
                                    for rt in prop:
                                        if rt.tag.split("}")[-1] == "collection":
                                            is_dir = True
                                elif prop.text:
                                    prop_elems[ptag] = prop.text

            if href_elem is None or not href_elem.text:
                continue

            raw_href = href_elem.text.strip()
            item_parsed = urllib.parse.urlparse(raw_href)
            item_path = item_parsed.path

            # Skip the root container itself
            clean_item_path = item_path.rstrip("/") + "/"
            if clean_item_path == base_path or item_path.rstrip(
                "/"
            ) == base_path.rstrip("/"):
                continue

            rel_path = (
                item_path[len(base_path) :]
                if item_path.startswith(base_path)
                else item_path
            )
            name = urllib.parse.unquote(rel_path.strip("/").split("/")[-1])
            if not name:
                continue

            last_mod = self._parse_date(
                prop_elems.get("getlastmodified") or prop_elems.get("creationdate")
            )
            rev_str = prop_elems.get("version-name")
            rev = int(rev_str) if rev_str and rev_str.isdigit() else None
            author = prop_elems.get("creator-displayname")
            size_str = prop_elems.get("getcontentlength")
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

    def get_file_content(self, url: str) -> bytes | None:
        """Fetch raw content of an SVN file."""
        try:
            req = self._build_request(url, method="GET")
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:  # nosec B310
                return bytes(resp.read())
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                raise ScraperAuthenticationError(
                    f"SVN authentication failed ({e.code}) for {url}: {e.reason}"
                ) from e
            if e.code == 404:
                return None
            raise ScraperError(
                f"SVN HTTP error ({e.code}) fetching {url}: {e.reason}"
            ) from e
        except (urllib.error.URLError, TimeoutError) as e:
            raise ScraperConnectionError(f"Cannot connect to SVN at {url}: {e}") from e


class SvnRepositoryScraper:
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

        return sorted(detected, key=lambda s: s.value) if detected else [Stack.UNKNOWN]

    def _extract_repo_name(self) -> str:
        parts = self.url.rstrip("/").split("/")
        return urllib.parse.unquote(parts[-1]) if parts else "svn-repo"

    def _scrape_standard(
        self,
        url: str,
        repo_name: str,
        root_entries: list[SvnEntry],
    ) -> list[Repository]:
        trunk_entry = next((e for e in root_entries if e.name.lower() == "trunk"), None)
        trunk_url = f"{url}/trunk"
        trunk_files: list[SvnEntry] = []
        try:
            trunk_files = self.client.list_dir(trunk_url)
        except ScraperError:
            pass

        stacks = self._detect_stacks(trunk_files)
        last_commit = trunk_entry.last_modified if trunk_entry else None
        if not last_commit and trunk_files:
            dates = [f.last_modified for f in trunk_files if f.last_modified]
            if dates:
                last_commit = max(dates)

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
        url: str,
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
            sub_url = f"{url}/{sub.name}"
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
        url: str,
        repo_name: str,
        root_entries: list[SvnEntry],
    ) -> list[Repository]:
        stacks = self._detect_stacks(root_entries)
        dates = [e.last_modified for e in root_entries if e.last_modified]
        last_commit = max(dates) if dates else None
        revs = [e.revision for e in root_entries if e.revision is not None]
        revision = max(revs) if revs else None

        return [
            Repository(
                name=repo_name,
                organization=self.organization,
                project=self.project or repo_name,
                path="",
                url=url,
                default_branch=None,
                last_commit=last_commit,
                stacks=stacks,
                metadata={"layout": SvnLayoutType.FLAT.value, "revision": revision},
            )
        ]

    def _scrape_collection(
        self, url: str, root_entries: list[SvnEntry]
    ) -> list[Repository]:
        repositories: list[Repository] = []
        for entry in root_entries:
            if not entry.is_directory:
                continue
            child_url = f"{url}/{entry.name}"
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

        # Check if the root corresponds to an SVN collection of multiple repositories
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
                    return self._scrape_collection(self.url, root_entries)

        if self.layout == SvnLayoutType.STANDARD:
            return self._scrape_standard(self.url, repo_name, root_entries)

        if self.layout == SvnLayoutType.MULTI_PROJECT:
            return self._scrape_multi_project(self.url, repo_name, root_entries)

        if self.layout == SvnLayoutType.FLAT:
            return self._scrape_flat(self.url, repo_name, root_entries)

        # AUTO layout resolution
        has_trunk = any(e.name.lower() == "trunk" for e in root_entries)
        if has_trunk:
            return self._scrape_standard(self.url, repo_name, root_entries)

        sub_dirs = [
            e
            for e in root_entries
            if e.is_directory and e.name.lower() not in ("branches", "tags")
        ]
        if sub_dirs:
            # Check if any sub-directory contains a trunk (multi-project layout)
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
                return self._scrape_multi_project(self.url, repo_name, root_entries)

        return self._scrape_flat(self.url, repo_name, root_entries)
