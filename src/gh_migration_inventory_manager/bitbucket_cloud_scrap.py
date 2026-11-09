"""Platform discovery adapters and transport clients for Bitbucket Cloud REST 2.0."""

from __future__ import annotations

import argparse
import base64
import json
import logging
import posixpath
from datetime import datetime
from typing import Any, cast

import urllib3
from urllib3.util import Retry

from gh_migration_inventory_manager.scrap import (
    Authenticator,
    BasicAuthenticator,
    BranchPolicy,
    NormalizedRuleType,
    PolicyScraper,
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
    normalize_role,
    slugify,
)

logger = logging.getLogger(__name__)

DEFAULT_BITBUCKET_CLOUD_BASE_URL = "https://api.bitbucket.org/2.0"


class BitbucketCloudTransport:
    """Protocol-like base interface for Bitbucket Cloud operations."""

    def get_workspaces(self, limit: int = 100) -> list[dict[str, Any]]:
        raise NotImplementedError

    def get_repositories(
        self, workspace: str, project_key: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        raise NotImplementedError

    def get_default_branch(self, workspace: str, repo_slug: str) -> str | None:
        raise NotImplementedError

    def get_last_commit(self, workspace: str, repo_slug: str) -> dict[str, Any] | None:
        raise NotImplementedError

    def get_repository_files(
        self, workspace: str, repo_slug: str, limit: int = 100
    ) -> list[str]:
        raise NotImplementedError

    def get_branch_restrictions(
        self, workspace: str, repo_slug: str, limit: int = 100
    ) -> list[dict[str, Any]]:
        raise NotImplementedError

    def get_workspace_group_permissions(
        self, workspace: str, limit: int = 100
    ) -> list[dict[str, Any]]:
        raise NotImplementedError

    def get_repository_group_permissions(
        self, workspace: str, repo_slug: str, limit: int = 100
    ) -> list[dict[str, Any]]:
        raise NotImplementedError


class BitbucketCloudClient(BitbucketCloudTransport):
    """Transport client communicating with Bitbucket Cloud REST API 2.0."""

    def __init__(
        self,
        base_url: str = DEFAULT_BITBUCKET_CLOUD_BASE_URL,
        auth: Authenticator | None = None,
        timeout: float = 15.0,
        retries: int = 3,
        backoff_factor: float = 0.5,
        pool_manager: urllib3.PoolManager | None = None,
    ) -> None:
        """Initialize BitbucketCloudClient with connection pooling and retries."""
        if not base_url or not base_url.strip():
            raise ValueError("Bitbucket Cloud base URL cannot be empty.")

        clean_url = base_url.strip().rstrip("/")
        if not (clean_url.startswith("http://") or clean_url.startswith("https://")):
            clean_url = f"https://{clean_url}"

        self.base_url = clean_url
        self.auth = auth
        self.timeout = timeout

        self._default_headers: dict[str, str] = {"Accept": "application/json"}
        if self.auth is not None:
            creds = self.auth.resolve_credentials()
            if creds.token and not creds.username:
                self._default_headers["Authorization"] = f"Bearer {creds.token}"
            elif creds.username:
                secret = creds.password or creds.token or ""
                raw_cred = f"{creds.username}:{secret}".encode()
                encoded = base64.b64encode(raw_cred).decode("ascii")
                self._default_headers["Authorization"] = f"Basic {encoded}"

        self.retry_strategy = Retry(
            total=retries,
            backoff_factor=backoff_factor,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET"],
            raise_on_status=False,
            respect_retry_after_header=True,
        )

        if pool_manager is not None:
            self._http = pool_manager
        else:
            self._http = urllib3.PoolManager(
                retries=self.retry_strategy,
                timeout=urllib3.Timeout(timeout),
            )

    def _build_url(self, path: str) -> str:
        """Construct full URL from relative path or absolute next URL."""
        if path.startswith("http://") or path.startswith("https://"):
            return path
        clean_path = path.lstrip("/")
        return f"{self.base_url}/{clean_path}"

    def _request_json(
        self, path_or_url: str, query_params: dict[str, Any] | None = None
    ) -> Any:
        """Execute HTTP GET request and parse response JSON."""
        url = self._build_url(path_or_url)
        fields = (
            query_params
            if not (
                path_or_url.startswith("http://") or path_or_url.startswith("https://")
            )
            else None
        )

        try:
            resp = self._http.request(
                "GET",
                url,
                fields=fields,
                headers=dict(self._default_headers),
            )
        except urllib3.exceptions.MaxRetryError as err:
            msg = f"Connection failed after retries for Bitbucket Cloud '{url}': {err}"
            logger.error(msg)
            raise ScraperConnectionError(msg) from err
        except (
            urllib3.exceptions.TimeoutError,
            urllib3.exceptions.HTTPError,
            OSError,
        ) as err:
            msg = (
                f"Network or transport error contacting Bitbucket Cloud '{url}': {err}"
            )
            logger.error(msg)
            raise ScraperConnectionError(msg) from err

        if resp.status in (401, 403):
            msg = (
                f"Bitbucket Cloud authentication failed with HTTP {resp.status} "
                f"for '{url}'."
            )
            logger.error(msg)
            raise ScraperAuthenticationError(msg)

        if resp.status == 404:
            logger.debug("Bitbucket Cloud resource not found (404) at '%s'", url)
            return None

        if resp.status < 200 or resp.status >= 300:
            msg = (
                f"Bitbucket Cloud request failed with HTTP {resp.status} "
                f"for '{url}': {resp.data.decode('utf-8', errors='replace')}"
            )
            logger.error(msg)
            raise ScraperError(msg)

        try:
            return json.loads(resp.data.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as err:
            msg = f"Failed to parse JSON response from '{url}': {err}"
            logger.error(msg)
            raise ScraperError(msg) from err

    def _paginate(
        self,
        endpoint: str,
        query_params: dict[str, Any] | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Traverse Bitbucket Cloud REST 2.0 cursor pagination via 'next' links."""
        results: list[dict[str, Any]] = []
        params = dict(query_params or {})
        pagelen = min(max(limit, 10), 100) if limit > 0 else 100
        params["pagelen"] = pagelen

        next_url: str | None = endpoint
        first_call = True

        while next_url:
            resp = self._request_json(
                next_url, query_params=params if first_call else None
            )
            first_call = False

            if not resp or not isinstance(resp, dict):
                break

            values = resp.get("values", [])
            if isinstance(values, list):
                results.extend(values)

            next_url = resp.get("next")
            if not next_url:
                break

        return results

    def get_workspaces(self, limit: int = 100) -> list[dict[str, Any]]:
        """Discover all workspaces accessible to the authenticated identity."""
        return self._paginate("workspaces", limit=limit)

    def get_workspace_projects(
        self, workspace: str, limit: int = 100
    ) -> list[dict[str, Any]]:
        """Fetch all projects configured under a specific workspace."""
        if not workspace or not workspace.strip():
            raise ValueError("Workspace slug cannot be empty.")
        clean_ws = workspace.strip()
        return self._paginate(f"workspaces/{clean_ws}/projects", limit=limit)

    def get_repositories(
        self, workspace: str, project_key: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        """Fetch all repositories in a workspace, optionally filtered by project key."""
        if not workspace or not workspace.strip():
            raise ValueError("Workspace slug cannot be empty.")
        clean_ws = workspace.strip()
        params: dict[str, Any] = {}
        if project_key and project_key.strip():
            params["q"] = f'project.key="{project_key.strip()}"'

        return self._paginate(
            f"repositories/{clean_ws}", query_params=params, limit=limit
        )

    def get_default_branch(self, workspace: str, repo_slug: str) -> str | None:
        """Fetch default primary branch name for a repository."""
        if not workspace or not workspace.strip():
            raise ValueError("Workspace slug cannot be empty.")
        if not repo_slug or not repo_slug.strip():
            raise ValueError("Repository slug cannot be empty.")

        clean_ws = workspace.strip()
        clean_repo = repo_slug.strip()
        data = self._request_json(f"repositories/{clean_ws}/{clean_repo}")
        if data and isinstance(data, dict):
            mainbranch = data.get("mainbranch")
            if isinstance(mainbranch, dict) and mainbranch.get("name"):
                return str(mainbranch["name"]).strip()
        return None

    def get_last_commit(self, workspace: str, repo_slug: str) -> dict[str, Any] | None:
        """Fetch the most recent commit on the repository."""
        if not workspace or not workspace.strip():
            raise ValueError("Workspace slug cannot be empty.")
        if not repo_slug or not repo_slug.strip():
            raise ValueError("Repository slug cannot be empty.")

        clean_ws = workspace.strip()
        clean_repo = repo_slug.strip()
        data = self._request_json(
            f"repositories/{clean_ws}/{clean_repo}/commits",
            {"pagelen": 1},
        )
        if data and isinstance(data, dict):
            values = data.get("values", [])
            if values and isinstance(values[0], dict):
                return cast(dict[str, Any], values[0])
        return None

    def get_repository_files(
        self, workspace: str, repo_slug: str, limit: int = 100
    ) -> list[str]:
        """Fetch root repository file paths using cursor pagination."""
        if not workspace or not workspace.strip():
            raise ValueError("Workspace slug cannot be empty.")
        if not repo_slug or not repo_slug.strip():
            raise ValueError("Repository slug cannot be empty.")

        clean_ws = workspace.strip()
        clean_repo = repo_slug.strip()
        eff_limit = limit if limit > 0 else 100

        file_paths: list[str] = []
        next_url: str | None = f"repositories/{clean_ws}/{clean_repo}/src"
        params: dict[str, Any] = {"pagelen": min(eff_limit, 100)}
        first = True

        while next_url and len(file_paths) < eff_limit:
            resp = self._request_json(next_url, query_params=params if first else None)
            first = False
            if not resp or not isinstance(resp, dict):
                break
            values = resp.get("values", [])
            if isinstance(values, list):
                for item in values:
                    if isinstance(item, dict) and item.get("path"):
                        file_paths.append(str(item["path"]))
                    elif isinstance(item, str):
                        file_paths.append(item)
            next_url = resp.get("next")

        return file_paths[:eff_limit]

    def get_branch_restrictions(
        self, workspace: str, repo_slug: str, limit: int = 100
    ) -> list[dict[str, Any]]:
        """Fetch all branch restrictions configured on a repository."""
        if not workspace or not workspace.strip():
            raise ValueError("Workspace slug cannot be empty.")
        if not repo_slug or not repo_slug.strip():
            raise ValueError("Repository slug cannot be empty.")

        clean_ws = workspace.strip()
        clean_repo = repo_slug.strip()
        return self._paginate(
            f"repositories/{clean_ws}/{clean_repo}/branch-restrictions",
            limit=limit,
        )

    def get_workspace_group_permissions(
        self, workspace: str, limit: int = 100
    ) -> list[dict[str, Any]]:
        """Fetch workspace-level group permissions."""
        if not workspace or not workspace.strip():
            raise ValueError("Workspace slug cannot be empty.")
        clean_ws = workspace.strip()
        return self._paginate(
            f"workspaces/{clean_ws}/permissions-config/groups",
            limit=limit,
        )

    def get_repository_group_permissions(
        self, workspace: str, repo_slug: str, limit: int = 100
    ) -> list[dict[str, Any]]:
        """Fetch repository-level group permissions."""
        if not workspace or not workspace.strip():
            raise ValueError("Workspace slug cannot be empty.")
        if not repo_slug or not repo_slug.strip():
            raise ValueError("Repository slug cannot be empty.")

        clean_ws = workspace.strip()
        clean_repo = repo_slug.strip()
        return self._paginate(
            f"repositories/{clean_ws}/{clean_repo}/permissions-config/groups",
            limit=limit,
        )


def _detect_stacks(file_paths: list[str]) -> list[Stack]:
    """Detect technology stacks from repository manifest file signatures."""
    detected: set[Stack] = set()
    for raw_path in file_paths:
        name = posixpath.basename(raw_path).lower().strip()
        if name in (
            "pom.xml",
            "build.gradle",
            "build.gradle.kts",
            "gradlew",
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
            (".ts", ".tsx", ".js")
        ):
            detected.add(Stack.TYPESCRIPT)
        if name.endswith((".csproj", ".sln", ".cs")):
            detected.add(Stack.C_SHARP)
            detected.add(Stack.DOTNET_CORE)
        if name.endswith((".tf", ".tfvars")) or name == "terragrunt.hcl":
            detected.add(Stack.TERRAFORM)
        if name in ("dockerfile", "makefile") or name.endswith((".sh", ".bash")):
            detected.add(Stack.BASH)
    return sorted(detected) if detected else [Stack.UNKNOWN]


_LANGUAGE_STACK_MAP: dict[str, list[Stack]] = {
    "java": [Stack.JAVA],
    "python": [Stack.PYTHON],
    "typescript": [Stack.TYPESCRIPT],
    "javascript": [Stack.TYPESCRIPT],
    "ts": [Stack.TYPESCRIPT],
    "js": [Stack.TYPESCRIPT],
    "c#": [Stack.C_SHARP, Stack.DOTNET_CORE],
    "csharp": [Stack.C_SHARP, Stack.DOTNET_CORE],
    "c_sharp": [Stack.C_SHARP, Stack.DOTNET_CORE],
    "c sharp": [Stack.C_SHARP, Stack.DOTNET_CORE],
    "dotnet": [Stack.DOTNET_CORE, Stack.C_SHARP],
    ".net": [Stack.DOTNET_CORE, Stack.C_SHARP],
    "terraform": [Stack.TERRAFORM],
    "hcl": [Stack.TERRAFORM],
    "bash": [Stack.BASH],
    "shell": [Stack.BASH],
    "sh": [Stack.BASH],
}


def _detect_stacks_from_language(language: str | None) -> list[Stack]:
    """Map primary language reported by Bitbucket Cloud to Stack enum list."""
    if not language or not isinstance(language, str):
        return [Stack.UNKNOWN]
    clean_lang = language.lower().strip()
    return _LANGUAGE_STACK_MAP.get(clean_lang, [Stack.UNKNOWN])


def _parse_commit_timestamp(commit: dict[str, Any] | None) -> datetime | None:
    """Parse Bitbucket Cloud ISO commit timestamp into UTC datetime."""
    if not commit or not isinstance(commit, dict):
        return None
    date_str = commit.get("date") or commit.get("timestamp")
    if date_str and isinstance(date_str, str):
        clean_date = date_str.strip().replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(clean_date)
        except (ValueError, TypeError):
            pass
    return None


class BitbucketCloudRepositoryScraper(RepositoryScraper):
    """Platform scraper discovering repositories from Bitbucket Cloud."""

    scraper_type: str = "repository"
    target_type: str = "repository"

    def __init__(
        self,
        workspace: str | None = None,
        project_key: str | None = None,
        client: BitbucketCloudTransport | None = None,
        page_size: int = 100,
        include_archived: bool = False,
        organization: str | None = None,
    ) -> None:
        """Initialize BitbucketCloudRepositoryScraper."""
        self.workspace = workspace.strip() if workspace and workspace.strip() else None
        self.project_key = (
            project_key.strip() if project_key and project_key.strip() else None
        )
        self.client = client
        self.page_size = page_size if page_size > 0 else 100
        self.include_archived = include_archived
        self.organization = (
            organization.strip() if organization and organization.strip() else None
        )

    def _resolve_clone_url(
        self, repo: dict[str, Any], workspace: str, slug: str
    ) -> str:
        """Resolve clone URL prioritizing HTTPS over SSH."""
        links = repo.get("links")
        clone_links = links.get("clone") if isinstance(links, dict) else None
        if isinstance(clone_links, list):
            ssh_href: str | None = None
            for link in clone_links:
                if not isinstance(link, dict):
                    continue
                name = str(link.get("name", "")).lower()
                href = link.get("href")
                if not href:
                    continue
                clean_href = str(href).strip()
                if name in ("http", "https"):
                    return clean_href
                if name == "ssh" and not ssh_href:
                    ssh_href = clean_href
            if ssh_href:
                return ssh_href

        return f"https://bitbucket.org/{workspace}/{slug}.git"

    def _resolve_default_branch(
        self, repo: dict[str, Any], workspace: str, slug: str
    ) -> str | None:
        """Resolve default branch from repo payload or client."""
        mainbranch = repo.get("mainbranch")
        if isinstance(mainbranch, dict) and mainbranch.get("name"):
            return str(mainbranch["name"]).strip()
        if self.client is None:
            return None
        try:
            return self.client.get_default_branch(workspace, slug)
        except ScraperError:
            return None

    def scrape(self) -> list[Repository]:
        """Discover and return canonical repositories from Bitbucket Cloud."""
        if self.client is None:
            raise ScraperError("Bitbucket Cloud client not configured.")

        # Bypass get_workspaces() if workspace was provided via CLI
        workspaces: list[str] = []
        if self.workspace:
            workspaces = [self.workspace]
        else:
            logger.info("Discovering accessible Bitbucket Cloud workspaces...")
            ws_records = self.client.get_workspaces(limit=self.page_size)
            for w in ws_records:
                slug = w.get("slug")
                if slug:
                    workspaces.append(slug)

        repositories: list[Repository] = []

        for ws in workspaces:
            logger.info("Scraping Bitbucket Cloud workspace '%s'...", ws)
            repos = self.client.get_repositories(
                workspace=ws,
                project_key=self.project_key,
                limit=self.page_size,
            )

            for repo in repos:
                is_archived = bool(
                    repo.get("is_private") is False and repo.get("archived", False)
                ) or bool(repo.get("archived", False))
                if is_archived and not self.include_archived:
                    continue

                slug = repo.get("slug") or repo.get("name") or ""
                if not slug:
                    continue

                repo_name = repo.get("name") or slug
                proj_obj = repo.get("project")
                proj_key = (
                    proj_obj.get("key")
                    if isinstance(proj_obj, dict) and proj_obj.get("key")
                    else ""
                )

                clone_url = self._resolve_clone_url(repo, ws, slug)
                org = self.organization or ws
                default_branch = self._resolve_default_branch(repo, ws, slug)

                updated_str = repo.get("updated_on")
                last_commit_dt: datetime | None = None
                if updated_str and isinstance(updated_str, str):
                    try:
                        last_commit_dt = datetime.fromisoformat(
                            updated_str.strip().replace("Z", "+00:00")
                        )
                    except (ValueError, TypeError):
                        last_commit_dt = None

                lang_raw = repo.get("language")
                lang = (
                    str(lang_raw).lower().strip()
                    if lang_raw and isinstance(lang_raw, str)
                    else ""
                )
                stacks = _detect_stacks_from_language(lang)

                repositories.append(
                    Repository(
                        name=slug,
                        organization=org,
                        project=proj_key,
                        url=clone_url,
                        default_branch=default_branch,
                        last_commit=last_commit_dt,
                        stacks=stacks,
                        metadata={
                            "id": repo.get("uuid"),
                            "slug": slug,
                            "name": repo_name,
                            "workspace": ws,
                            "project_key": proj_key,
                            "project_name": proj_obj.get("name")
                            if isinstance(proj_obj, dict)
                            else "",
                            "archived": is_archived,
                            "raw": repo,
                        },
                    )
                )

        return repositories


class BitbucketCloudPolicyScraper(PolicyScraper):
    """Platform scraper discovering branch governance policies from Bitbucket Cloud."""

    scraper_type: str = "policy"
    target_type: str = "policy"

    def __init__(
        self,
        workspace: str | None = None,
        project_key: str | None = None,
        repo_slug: str | None = None,
        client: BitbucketCloudTransport | None = None,
        default_branch: str = "main",
        page_size: int = 100,
    ) -> None:
        """Initialize BitbucketCloudPolicyScraper."""
        self.workspace = workspace.strip() if workspace and workspace.strip() else None
        self.project_key = (
            project_key.strip() if project_key and project_key.strip() else None
        )
        self.repo_slug = repo_slug.strip() or None if repo_slug else None
        self.client = client
        self.default_branch = default_branch.strip() if default_branch else "main"
        self.page_size = page_size if page_size > 0 else 100

    def _canonicalize_ref(self, pattern: str | None) -> str:
        """Standardize a ref pattern into a canonical Git ref format."""
        clean = (pattern or "").strip()
        if not clean or clean == "*":
            return "*"
        if clean.startswith("refs/"):
            return clean
        return f"refs/heads/{clean}"

    def _extract_bypasses(self, item: dict[str, Any]) -> list[str]:
        """Extract and slugify group bypass names."""
        slugs: list[str] = []
        for g in item.get("groups", []) or []:
            name = ""
            if isinstance(g, dict):
                name = g.get("name") or g.get("slug") or ""
            elif isinstance(g, str):
                name = g
            slug = slugify(name)
            if slug:
                slugs.append(slug)
        return sorted(set(slugs))

    def _map_restriction_to_policy(
        self,
        item: dict[str, Any],
        workspace: str,
        project_key: str,
        repo_slug: str,
    ) -> BranchPolicy | None:
        """Map Bitbucket Cloud branch restriction to canonical BranchPolicy."""
        kind = str(item.get("kind", "")).lower().strip()
        pattern = self._canonicalize_ref(item.get("pattern"))
        scope = (
            f"repo:{project_key}/{repo_slug}" if project_key else f"repo:{repo_slug}"
        )
        bypass_slugs = self._extract_bypasses(item)
        raw_origin = json.dumps(item, sort_keys=True)

        if kind == "require_approvals_to_merge":
            min_approvals = int(item.get("value") or 1)
            return BranchPolicy(
                policy_scope=scope,
                target_ref_pattern=pattern,
                normalized_rule_type=NormalizedRuleType.REQUIRE_PULL_REQUEST,
                project=project_key,
                repository=repo_slug,
                rule_parameters={"min_approvals": min_approvals},
                bypass_team_slugs=bypass_slugs,
                origin_raw_policy=raw_origin,
                platform="bitbucket-cloud",
            )
        if kind == "require_passing_builds_to_merge":
            min_builds = int(item.get("value") or 1)
            return BranchPolicy(
                policy_scope=scope,
                target_ref_pattern=pattern,
                normalized_rule_type=NormalizedRuleType.REQUIRE_STATUS_CHECKS,
                project=project_key,
                repository=repo_slug,
                rule_parameters={"min_successful_builds": min_builds},
                bypass_team_slugs=bypass_slugs,
                origin_raw_policy=raw_origin,
                platform="bitbucket-cloud",
            )
        if kind in ("push", "restrict_merges"):
            return BranchPolicy(
                policy_scope=scope,
                target_ref_pattern=pattern,
                normalized_rule_type=NormalizedRuleType.REQUIRE_PULL_REQUEST,
                project=project_key,
                repository=repo_slug,
                bypass_team_slugs=bypass_slugs,
                origin_raw_policy=raw_origin,
                platform="bitbucket-cloud",
            )
        if kind == "force":
            return BranchPolicy(
                policy_scope=scope,
                target_ref_pattern=pattern,
                normalized_rule_type=NormalizedRuleType.BLOCK_FORCE_PUSH,
                project=project_key,
                repository=repo_slug,
                bypass_team_slugs=bypass_slugs,
                origin_raw_policy=raw_origin,
                platform="bitbucket-cloud",
            )
        if kind == "delete":
            return BranchPolicy(
                policy_scope=scope,
                target_ref_pattern=pattern,
                normalized_rule_type=NormalizedRuleType.PREVENT_BRANCH_DELETION,
                project=project_key,
                repository=repo_slug,
                bypass_team_slugs=bypass_slugs,
                origin_raw_policy=raw_origin,
                platform="bitbucket-cloud",
            )

        return None

    def scrape(self) -> list[BranchPolicy]:
        """Discover and return canonical branch policies from Bitbucket Cloud."""
        if self.client is None:
            raise ScraperError("Bitbucket Cloud client not configured.")

        workspaces: list[str] = [self.workspace] if self.workspace else []
        if not workspaces:
            ws_records = self.client.get_workspaces(limit=self.page_size)
            workspaces = [w["slug"] for w in ws_records if w.get("slug")]

        policies: list[BranchPolicy] = []

        for ws in workspaces:
            if self.repo_slug:
                repo_targets = [
                    {"slug": self.repo_slug, "project_key": self.project_key or ""}
                ]
            else:
                repos = self.client.get_repositories(
                    workspace=ws,
                    project_key=self.project_key,
                    limit=self.page_size,
                )
                repo_targets = []
                for r in repos:
                    slug = r.get("slug")
                    if not slug:
                        continue
                    p_obj = r.get("project")
                    p_key = p_obj.get("key", "") if isinstance(p_obj, dict) else ""
                    repo_targets.append({"slug": slug, "project_key": p_key})

            for target in repo_targets:
                slug = target["slug"]
                p_key = target["project_key"]
                try:
                    restrictions = self.client.get_branch_restrictions(
                        workspace=ws, repo_slug=slug, limit=self.page_size
                    )
                    for item in restrictions:
                        policy = self._map_restriction_to_policy(item, ws, p_key, slug)
                        if policy:
                            policies.append(policy)
                except ScraperAuthenticationError as auth_err:
                    logger.warning(
                        "Bitbucket Cloud credentials lack permission to read branch "
                        "restrictions: %s. Skipping.",
                        auth_err,
                    )
                    break
                except ScraperError as err:
                    if "403" in str(err) or "lack one or more" in str(err):
                        logger.warning(
                            "Bitbucket Cloud credentials lack permission: %s. "
                            "Skipping.",
                            err,
                        )
                        break

        return policies


class BitbucketCloudTeamScraper(TeamScraper):
    """Platform scraper discovering group permissions from Bitbucket Cloud."""

    scraper_type: str = "team"
    target_type: str = "team"

    def __init__(
        self,
        workspace: str | None = None,
        project_key: str | None = None,
        repo_slug: str | None = None,
        client: BitbucketCloudTransport | None = None,
        include_repo_level: bool = True,
        page_size: int = 100,
    ) -> None:
        """Initialize BitbucketCloudTeamScraper."""
        self.workspace = workspace.strip() if workspace and workspace.strip() else None
        self.project_key = (
            project_key.strip() if project_key and project_key.strip() else None
        )
        self.repo_slug = repo_slug.strip() or None if repo_slug else None
        self.client = client
        self.include_repo_level = include_repo_level
        self.page_size = page_size if page_size > 0 else 100

    def scrape(self) -> list[TeamPermission]:
        """Discover and return group permissions from Bitbucket Cloud."""
        if self.client is None:
            raise ScraperError("Bitbucket Cloud client not configured.")

        workspaces: list[str] = [self.workspace] if self.workspace else []
        if not workspaces:
            ws_records = self.client.get_workspaces(limit=self.page_size)
            workspaces = [w["slug"] for w in ws_records if w.get("slug")]

        permissions: list[TeamPermission] = []

        for ws in workspaces:
            # 1. Workspace-level groups
            try:
                ws_groups = self.client.get_workspace_group_permissions(
                    workspace=ws, limit=self.page_size
                )
                for entry in ws_groups:
                    group = entry.get("group", {})
                    group_name = group.get("name") or group.get("slug") or ""
                    perm_str = entry.get("permission", "")
                    if not group_name or not perm_str:
                        continue
                    norm_role = normalize_role(perm_str)
                    team_slug = slugify(group_name)
                    permissions.append(
                        TeamPermission(
                            team_name=group_name,
                            member_count=0,
                            target_repo_or_project=f"workspace:{ws}",
                            normalized_role=norm_role,
                            origin_role=perm_str,
                            project=self.project_key or "",
                            repository="",
                            team_slug=team_slug,
                            metadata={
                                "scope": "workspace",
                                "workspace": ws,
                                "raw": entry,
                            },
                        )
                    )
            except ScraperError:
                pass

            # 2. Repo-level groups
            if self.include_repo_level:
                if self.repo_slug:
                    repo_targets = [
                        {"slug": self.repo_slug, "project_key": self.project_key or ""}
                    ]
                else:
                    repos = self.client.get_repositories(
                        workspace=ws,
                        project_key=self.project_key,
                        limit=self.page_size,
                    )
                    repo_targets = []
                    for r in repos:
                        slug = r.get("slug")
                        if not slug:
                            continue
                        p_obj = r.get("project")
                        p_key = p_obj.get("key", "") if isinstance(p_obj, dict) else ""
                        repo_targets.append({"slug": slug, "project_key": p_key})

                seen_projects: set[str] = set()
                sampled_targets: list[dict[str, str]] = []
                for target in repo_targets:
                    p_key = target["project_key"] or "DEFAULT"
                    if (
                        p_key not in seen_projects
                        or len(sampled_targets) < len(seen_projects) * 2
                    ):
                        seen_projects.add(p_key)
                        sampled_targets.append(target)
                    if len(sampled_targets) >= 150:
                        break

                for target in sampled_targets:
                    slug = target["slug"]
                    p_key = target["project_key"]
                    try:
                        repo_groups = self.client.get_repository_group_permissions(
                            workspace=ws, repo_slug=slug, limit=self.page_size
                        )
                        for entry in repo_groups:
                            group = entry.get("group", {})
                            group_name = group.get("name") or group.get("slug") or ""
                            perm_str = entry.get("permission", "")
                            if not group_name or not perm_str:
                                continue
                            norm_role = normalize_role(perm_str)
                            team_slug = slugify(group_name)
                            target_id = (
                                f"repo:{p_key}/{slug}" if p_key else f"repo:{slug}"
                            )
                            permissions.append(
                                TeamPermission(
                                    team_name=group_name,
                                    member_count=0,
                                    target_repo_or_project=target_id,
                                    normalized_role=norm_role,
                                    origin_role=perm_str,
                                    project=p_key,
                                    repository=slug,
                                    team_slug=team_slug,
                                    metadata={
                                        "scope": "repository",
                                        "workspace": ws,
                                        "raw": entry,
                                    },
                                )
                            )
                    except ScraperAuthenticationError as auth_err:
                        logger.warning(
                            "Bitbucket Cloud credentials lack permission to read "
                            "repository groups: %s. Skipping.",
                            auth_err,
                        )
                        break
                    except ScraperError as err:
                        if "403" in str(err) or "lack one or more" in str(err):
                            logger.warning(
                                "Bitbucket Cloud credentials lack permission to "
                                "read repository groups: %s. Skipping.",
                                err,
                            )
                            break

        return permissions


def _build_bitbucket_cloud_client(args: Any) -> tuple[BitbucketCloudClient, str | None]:
    """Helper to construct BitbucketCloudClient from parsed CLI arguments."""
    base_url = getattr(args, "base_url", None) or DEFAULT_BITBUCKET_CLOUD_BASE_URL
    token = getattr(args, "token", None)
    username = getattr(args, "username", None)
    password = getattr(args, "app_password", None) or getattr(args, "password", None)

    auth: Authenticator | None = None
    if token:
        auth = TokenAuthenticator(token=token)
    elif username and password:
        auth = BasicAuthenticator(username=username, password=password)

    raw_timeout = getattr(args, "timeout", 15.0)
    try:
        timeout = float(raw_timeout) if raw_timeout is not None else 15.0
    except (TypeError, ValueError):
        timeout = 15.0

    raw_retries = getattr(args, "retries", 3)
    try:
        retries = int(raw_retries) if raw_retries is not None else 3
    except (TypeError, ValueError):
        retries = 3

    raw_backoff = getattr(args, "backoff_factor", 0.5)
    try:
        backoff_factor = float(raw_backoff) if raw_backoff is not None else 0.5
    except (TypeError, ValueError):
        backoff_factor = 0.5

    client = BitbucketCloudClient(
        base_url=base_url,
        auth=auth,
        timeout=timeout,
        retries=retries,
        backoff_factor=backoff_factor,
    )
    return client, base_url


def _add_common_cloud_arguments(parser: Any) -> None:
    """Register common CLI flags for Bitbucket Cloud subcommands."""
    parser.add_argument(
        "--workspace",
        "-w",
        default=None,
        help="Bitbucket Cloud workspace slug (bypasses workspace self-discovery)",
    )
    parser.add_argument(
        "--base-url",
        default=DEFAULT_BITBUCKET_CLOUD_BASE_URL,
        help="Bitbucket Cloud API base URL",
    )
    parser.add_argument(
        "--project",
        "--project-key",
        dest="project_key",
        default=None,
        help="Optional Bitbucket Cloud project key filter",
    )
    parser.add_argument(
        "--token",
        default=None,
        help="Bitbucket Bearer token, PAT, or Workspace Access Token (WAT)",
    )
    parser.add_argument(
        "--username",
        default=None,
        help="Atlassian Account email for HTTP Basic authentication",
    )
    parser.add_argument(
        "--app-password",
        "--password",
        dest="app_password",
        default=None,
        help="Bitbucket Cloud App Password for HTTP Basic authentication",
    )
    parser.add_argument(
        "--page-size",
        type=int,
        default=100,
        help="Pagination window size / pagelen for API requests (default: 100)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=15.0,
        help="HTTP request timeout in seconds (default: 15.0)",
    )
    parser.add_argument(
        "--retries",
        type=int,
        default=3,
        help="Maximum retry attempts on transient network errors (default: 3)",
    )
    parser.add_argument(
        "--backoff-factor",
        type=float,
        default=0.5,
        help="Exponential backoff factor for retries (default: 0.5)",
    )


class BitbucketCloudScraperProvider(ScraperProvider):
    """Platform scraper provider for Bitbucket Cloud repository discovery."""

    target_type: str = "repository"
    default_sheet_name: str = "Inventario"

    @property
    def platform_name(self) -> str:
        return "bitbucket-cloud"

    @property
    def description(self) -> str:
        return "Bitbucket Cloud repository discovery scraper (3-tier hierarchy)"

    def configure_parser(self, parser: Any) -> None:
        """Configure platform CLI arguments for Bitbucket Cloud repository scraping."""
        _add_common_cloud_arguments(parser)
        parser.add_argument(
            "--include-archived",
            action="store_true",
            default=False,
            help="Include archived repositories in discovery (default: excluded)",
        )
        parser.add_argument(
            "--organization",
            default=None,
            help="Target organization override for inventory records",
        )

    def create_scraper(self, args: Any) -> BitbucketCloudRepositoryScraper:
        """Build BitbucketCloudRepositoryScraper from parsed CLI arguments."""
        client, _ = _build_bitbucket_cloud_client(args)
        return BitbucketCloudRepositoryScraper(
            workspace=getattr(args, "workspace", None),
            project_key=getattr(args, "project_key", None),
            client=client,
            page_size=getattr(args, "page_size", 100),
            include_archived=bool(getattr(args, "include_archived", False)),
            organization=getattr(args, "organization", None),
        )


class BitbucketCloudPoliciesScraperProvider(ScraperProvider):
    """Platform scraper provider for Bitbucket Cloud branch governance discovery."""

    target_type: str = "policy"
    default_sheet_name: str = "Policies"

    @property
    def platform_name(self) -> str:
        return "bitbucket-cloud-policies"

    @property
    def description(self) -> str:
        return "Bitbucket Cloud branch restriction and governance policy scraper"

    def configure_parser(self, parser: Any) -> None:
        """Configure platform CLI arguments for Bitbucket Cloud policies scraping."""
        _add_common_cloud_arguments(parser)
        parser.add_argument(
            "--repo",
            "--repo-slug",
            dest="repo_slug",
            default=None,
            help="Optional Bitbucket repository slug filter",
        )
        parser.add_argument(
            "--default-branch",
            default="main",
            help="Default branch name fallback (default: main)",
        )

    def create_scraper(self, args: Any) -> BitbucketCloudPolicyScraper:
        """Build BitbucketCloudPolicyScraper from parsed CLI arguments."""
        client, _ = _build_bitbucket_cloud_client(args)
        return BitbucketCloudPolicyScraper(
            workspace=getattr(args, "workspace", None),
            project_key=getattr(args, "project_key", None),
            repo_slug=getattr(args, "repo_slug", None),
            client=client,
            default_branch=getattr(args, "default_branch", "main"),
            page_size=getattr(args, "page_size", 100),
        )


class BitbucketCloudTeamsScraperProvider(ScraperProvider):
    """Platform scraper provider for Bitbucket Cloud team access topology."""

    target_type: str = "team"
    default_sheet_name: str = "Topology"

    @property
    def platform_name(self) -> str:
        return "bitbucket-cloud-teams"

    @property
    def description(self) -> str:
        return "Bitbucket Cloud group permission and access topology scraper"

    def configure_parser(self, parser: Any) -> None:
        """Configure platform CLI arguments for Bitbucket Cloud team permissions."""
        _add_common_cloud_arguments(parser)
        parser.add_argument(
            "--repo",
            "--repo-slug",
            dest="repo_slug",
            default=None,
            help="Optional Bitbucket repository slug filter",
        )
        parser.add_argument(
            "--include-repo-level",
            action=argparse.BooleanOptionalAction,
            default=True,
            help="Scrape repository group permissions (default: true)",
        )

    def create_scraper(self, args: Any) -> BitbucketCloudTeamScraper:
        """Build BitbucketCloudTeamScraper from parsed CLI arguments."""
        client, _ = _build_bitbucket_cloud_client(args)
        return BitbucketCloudTeamScraper(
            workspace=getattr(args, "workspace", None),
            project_key=getattr(args, "project_key", None),
            repo_slug=getattr(args, "repo_slug", None),
            client=client,
            include_repo_level=bool(getattr(args, "include_repo_level", True)),
            page_size=getattr(args, "page_size", 100),
        )


default_scraper_registry.register(BitbucketCloudScraperProvider())
default_scraper_registry.register(BitbucketCloudPoliciesScraperProvider())
default_scraper_registry.register(BitbucketCloudTeamsScraperProvider())

__all__ = [
    "BitbucketCloudClient",
    "BitbucketCloudPoliciesScraperProvider",
    "BitbucketCloudPolicyScraper",
    "BitbucketCloudRepositoryScraper",
    "BitbucketCloudScraperProvider",
    "BitbucketCloudTeamScraper",
    "BitbucketCloudTeamsScraperProvider",
    "BitbucketCloudTransport",
]
