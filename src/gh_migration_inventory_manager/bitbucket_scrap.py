"""Bitbucket discovery adapters, transport client, policy scraper, and CLI."""

from __future__ import annotations

import argparse
import base64
import json
import posixpath
import urllib.parse
from datetime import datetime, timezone
from typing import Any, Protocol, cast, runtime_checkable

import urllib3
from urllib3.util import Retry

from gh_migration_inventory_manager.scrap import (
    Authenticator,
    BasicAuthenticator,
    BranchPolicy,
    NormalizedRole,
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


@runtime_checkable
class BitbucketTransport(Protocol):
    """Protocol defining network transport contract for Bitbucket REST APIs."""

    def get_branch_restrictions(
        self, project_key: str, repo_slug: str | None = None
    ) -> list[dict[str, Any]]:
        """Fetch all branch restrictions for a project or repository."""
        raise NotImplementedError

    def get_pull_request_settings(
        self, project_key: str, repo_slug: str | None = None
    ) -> dict[str, Any]:
        """Fetch pull request merge checks configuration for a project or repository."""
        raise NotImplementedError

    def get_projects(self, limit: int = 100) -> list[dict[str, Any]]:
        """Fetch all projects accessible to the caller using windowed pagination."""
        raise NotImplementedError

    def get_repositories(
        self, project_key: str, limit: int = 100
    ) -> list[dict[str, Any]]:
        """Fetch all repositories within a project using windowed pagination."""
        raise NotImplementedError

    def get_default_branch(self, project_key: str, repo_slug: str) -> str | None:
        """Fetch primary default branch name for a repository."""
        raise NotImplementedError

    def get_last_commit(
        self, project_key: str, repo_slug: str
    ) -> dict[str, Any] | None:
        """Fetch the most recent commit on the repository."""
        raise NotImplementedError

    def get_repository_files(
        self, project_key: str, repo_slug: str, limit: int = 100
    ) -> list[str]:
        """Fetch repository file paths for manifest and stack detection."""
        raise NotImplementedError

    def get_project_group_permissions(
        self, project_key: str, limit: int = 100
    ) -> list[dict[str, Any]]:
        """Fetch group permissions configured at project level."""
        raise NotImplementedError

    def get_repository_group_permissions(
        self, project_key: str, repo_slug: str, limit: int = 100
    ) -> list[dict[str, Any]]:
        """Fetch group permissions configured at repository level."""
        raise NotImplementedError

    def get_group_member_count(self, group_name: str) -> int:
        """Fetch aggregate member count for a group without extracting PII."""
        raise NotImplementedError


class BitbucketClient(BitbucketTransport):
    """Transport client communicating with Bitbucket Server, DC, and Cloud REST APIs."""

    def __init__(
        self,
        base_url: str,
        auth: Authenticator | None = None,
        timeout: float = 10.0,
        retries: int = 3,
        backoff_factor: float = 0.5,
        pool_manager: urllib3.PoolManager | None = None,
    ) -> None:
        """Initialize a new BitbucketClient with connection pooling and retries."""
        if not base_url or not base_url.strip():
            raise ValueError("Bitbucket base URL cannot be empty.")

        clean_url = base_url.strip().rstrip("/")
        if not (clean_url.startswith("http://") or clean_url.startswith("https://")):
            clean_url = f"https://{clean_url}"

        self.base_url = clean_url
        parsed_host = (urllib.parse.urlparse(clean_url).hostname or "").lower()
        self.is_cloud = parsed_host == "bitbucket.org" or parsed_host.endswith(
            ".bitbucket.org"
        )
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

    def _build_auth_headers(self) -> dict[str, str]:
        """Resolve authentication credentials and return HTTP headers."""
        return dict(self._default_headers)

    def _request_json(
        self, path: str, query_params: dict[str, Any] | None = None
    ) -> Any:
        """Execute a GET request against Bitbucket REST API and return parsed JSON."""
        if path.startswith("http://") or path.startswith("https://"):
            url = path
        else:
            clean_path = path.lstrip("/")
            url = f"{self.base_url}/{clean_path}"

        try:
            resp = self._http.request(
                "GET",
                url,
                fields=query_params or {},
                headers=self._default_headers,
                timeout=urllib3.Timeout(self.timeout),
                retries=self.retry_strategy,
            )
        except urllib3.exceptions.MaxRetryError as err:
            raise ScraperConnectionError(
                f"Failed to connect to Bitbucket at '{url}' after retries: {err}"
            ) from err
        except Exception as err:
            raise ScraperConnectionError(
                f"Failed to connect to Bitbucket at '{url}': {err}"
            ) from err

        if resp.status in (401, 403):
            raise ScraperAuthenticationError(
                f"Bitbucket authentication failed (HTTP {resp.status}) for '{url}'."
            )
        if resp.status == 404:
            return None
        if resp.status >= 400:
            raise ScraperConnectionError(
                f"Failed to communicate with Bitbucket: HTTP {resp.status} for '{url}'."
            )

        try:
            return json.loads(resp.data.decode("utf-8"))
        except Exception as err:
            msg = f"Failed to parse JSON response from '{url}': {err}"
            raise ScraperError(msg) from err

    def get_branch_restrictions(
        self, project_key: str, repo_slug: str | None = None
    ) -> list[dict[str, Any]]:
        """Fetch all branch restrictions using pagination."""
        if not project_key or not project_key.strip():
            raise ValueError("Project key cannot be empty.")

        clean_project = project_key.strip()
        clean_repo = repo_slug.strip() if repo_slug else None

        if clean_repo:
            paths_to_try = [
                f"rest/branch-permissions/2.0/projects/{clean_project}/repos/{clean_repo}/restrictions",
                f"rest/branch-permissions/latest/projects/{clean_project}/repos/{clean_repo}/restrictions",
                f"rest/api/1.0/projects/{clean_project}/repos/{clean_repo}/restrictions",
            ]
        else:
            paths_to_try = [
                f"rest/branch-permissions/2.0/projects/{clean_project}/restrictions",
                f"rest/branch-permissions/latest/projects/{clean_project}/restrictions",
            ]

        all_values: list[dict[str, Any]] = []
        start = 0
        limit = 50

        # Attempt first working endpoint
        selected_path: str | None = None
        for endpoint in paths_to_try:
            first_page = self._request_json(endpoint, {"start": start, "limit": limit})
            if first_page is not None:
                selected_path = endpoint
                values = first_page.get("values", [])
                all_values.extend(values)
                if first_page.get("isLastPage", True):
                    return all_values
                start = first_page.get("nextPageStart", start + len(values))
                break

        if selected_path is None:
            return []

        # Paginate remaining pages
        while True:
            page = self._request_json(selected_path, {"start": start, "limit": limit})
            if not page:
                break
            values = page.get("values", [])
            all_values.extend(values)
            if page.get("isLastPage", True) or not values:
                break
            start = page.get("nextPageStart", start + len(values))

        return all_values

    def get_pull_request_settings(
        self, project_key: str, repo_slug: str | None = None
    ) -> dict[str, Any]:
        """Fetch pull request settings for a repository or project."""
        if not project_key or not project_key.strip():
            raise ValueError("Project key cannot be empty.")

        clean_project = project_key.strip()
        clean_repo = repo_slug.strip() if repo_slug else None

        if clean_repo:
            paths = [
                f"rest/api/1.0/projects/{clean_project}/repos/{clean_repo}/settings/pull-requests",
                f"rest/api/latest/projects/{clean_project}/repos/{clean_repo}/settings/pull-requests",
            ]
        else:
            paths = [
                f"rest/api/1.0/projects/{clean_project}/settings/pull-requests",
                f"rest/api/latest/projects/{clean_project}/settings/pull-requests",
            ]

        for p in paths:
            data = self._request_json(p)
            if data is not None and isinstance(data, dict):
                return cast(dict[str, Any], data)

        return {}

    def _paginate(self, path: str, limit: int) -> list[dict[str, Any]]:
        """Paginate Bitbucket Cloud or Server collection endpoints."""
        results: list[dict[str, Any]] = []

        if self.is_cloud:
            sep = "&" if "?" in path else "?"
            next_url: str | None = f"{path}{sep}pagelen={limit}"
            while next_url:
                resp = self._request_json(next_url)
                if not resp or not isinstance(resp, dict):
                    break
                values = resp.get("values", [])
                results.extend(values)
                next_url = resp.get("next")
            return results

        start = 0
        while True:
            resp = self._request_json(path, {"start": start, "limit": limit})
            if not resp or not isinstance(resp, dict):
                break
            values = resp.get("values", [])
            results.extend(values)
            if resp.get("isLastPage", True) or not values:
                break
            start = resp.get("nextPageStart", start + len(values))

        return results

    def get_projects(self, limit: int = 100) -> list[dict[str, Any]]:
        """Fetch all projects accessible to the caller using windowed pagination."""
        eff_limit = limit if limit > 0 else 100
        path = "2.0/workspaces" if self.is_cloud else "rest/api/1.0/projects"
        return self._paginate(path, eff_limit)

    def get_repositories(
        self, project_key: str, limit: int = 100
    ) -> list[dict[str, Any]]:
        """Fetch all repositories within a project using windowed pagination."""
        if not project_key or not project_key.strip():
            raise ValueError("Project key cannot be empty.")
        eff_limit = limit if limit > 0 else 100
        clean_project = project_key.strip()
        path = (
            f"2.0/repositories/{clean_project}"
            if self.is_cloud
            else f"rest/api/1.0/projects/{clean_project}/repos"
        )
        return self._paginate(path, eff_limit)

    def get_default_branch(self, project_key: str, repo_slug: str) -> str | None:
        """Fetch primary default branch name for a repository."""
        if not project_key or not project_key.strip():
            raise ValueError("Project key cannot be empty.")
        if not repo_slug or not repo_slug.strip():
            raise ValueError("Repository slug cannot be empty.")

        clean_project = project_key.strip()
        clean_repo = repo_slug.strip()

        if self.is_cloud:
            data = self._request_json(f"2.0/repositories/{clean_project}/{clean_repo}")
            if data and isinstance(data, dict):
                mainbranch = data.get("mainbranch")
                if isinstance(mainbranch, dict) and mainbranch.get("name"):
                    return str(mainbranch["name"]).strip()
            return None

        data = self._request_json(
            f"rest/api/1.0/projects/{clean_project}/repos/{clean_repo}/branches/default"
        )
        if data and isinstance(data, dict):
            display_id = data.get("displayId")
            if display_id:
                return str(display_id).strip()
            branch_id = data.get("id")
            if branch_id:
                return str(branch_id).replace("refs/heads/", "").strip()
        return None

    def get_last_commit(
        self, project_key: str, repo_slug: str
    ) -> dict[str, Any] | None:
        """Fetch the most recent commit on the repository."""
        if not project_key or not project_key.strip():
            raise ValueError("Project key cannot be empty.")
        if not repo_slug or not repo_slug.strip():
            raise ValueError("Repository slug cannot be empty.")

        clean_project = project_key.strip()
        clean_repo = repo_slug.strip()

        endpoint = (
            f"2.0/repositories/{clean_project}/{clean_repo}/commits"
            if self.is_cloud
            else f"rest/api/1.0/projects/{clean_project}/repos/{clean_repo}/commits"
        )
        params = {"pagelen": 1} if self.is_cloud else {"limit": 1}
        data = self._request_json(endpoint, params)
        if data and isinstance(data, dict):
            values = data.get("values", [])
            if values and isinstance(values[0], dict):
                return cast(dict[str, Any], values[0])
        return None

    @staticmethod
    def _extract_file_paths(values: list[Any]) -> list[str]:
        """Extract relative file path strings from API response items."""
        paths: list[str] = []
        for item in values:
            if isinstance(item, dict) and item.get("path"):
                paths.append(str(item["path"]))
            elif isinstance(item, str):
                paths.append(item)
        return paths

    def get_repository_files(
        self, project_key: str, repo_slug: str, limit: int = 100
    ) -> list[str]:
        """Fetch repository file paths using windowed pagination."""
        if not project_key or not project_key.strip():
            raise ValueError("Project key cannot be empty.")
        if not repo_slug or not repo_slug.strip():
            raise ValueError("Repository slug cannot be empty.")
        if limit <= 0:
            limit = 100

        clean_project = project_key.strip()
        clean_repo = repo_slug.strip()
        file_paths: list[str] = []

        if self.is_cloud:
            page_len = min(limit, 100)
            next_url: str | None = (
                f"2.0/repositories/{clean_project}/{clean_repo}/src?pagelen={page_len}"
            )
            while next_url and len(file_paths) < limit:
                resp = self._request_json(next_url)
                if not resp or not isinstance(resp, dict):
                    break
                values = resp.get("values", [])
                file_paths.extend(self._extract_file_paths(values))
                next_url = resp.get("next")
            return file_paths[:limit]

        start = 0
        page_size = min(limit, 100)
        while len(file_paths) < limit:
            data = self._request_json(
                f"rest/api/1.0/projects/{clean_project}/repos/{clean_repo}/files",
                {"start": start, "limit": page_size},
            )
            if not data or not isinstance(data, dict):
                break
            values = data.get("values", [])
            file_paths.extend(self._extract_file_paths(values))
            if data.get("isLastPage", True) or not values:
                break
            start = data.get("nextPageStart", start + len(values))

        return file_paths[:limit]

    def get_project_group_permissions(
        self, project_key: str, limit: int = 100
    ) -> list[dict[str, Any]]:
        """Fetch all group permissions configured at project level."""
        if not project_key or not project_key.strip():
            raise ValueError("Project key cannot be empty.")
        eff_limit = limit if limit > 0 else 100
        clean_project = project_key.strip()
        path = (
            f"2.0/workspaces/{clean_project}/permissions/groups"
            if self.is_cloud
            else f"rest/api/1.0/projects/{clean_project}/permissions/groups"
        )
        return self._paginate(path, eff_limit)

    def get_repository_group_permissions(
        self, project_key: str, repo_slug: str, limit: int = 100
    ) -> list[dict[str, Any]]:
        """Fetch all group permissions configured at repository level."""
        if not project_key or not project_key.strip():
            raise ValueError("Project key cannot be empty.")
        if not repo_slug or not repo_slug.strip():
            raise ValueError("Repository slug cannot be empty.")
        eff_limit = limit if limit > 0 else 100
        clean_project = project_key.strip()
        clean_repo = repo_slug.strip()
        path = (
            f"2.0/repositories/{clean_project}/{clean_repo}/permissions-config/groups"
            if self.is_cloud
            else (
                f"rest/api/1.0/projects/{clean_project}/repos/"
                f"{clean_repo}/permissions/groups"
            )
        )
        return self._paginate(path, eff_limit)

    def get_group_member_count(self, group_name: str) -> int:
        """Fetch aggregate member count for a group without extracting PII."""
        if not group_name or not group_name.strip():
            raise ValueError("Group name cannot be empty.")
        clean_group = group_name.strip()
        if self.is_cloud:
            return 0

        path = "rest/api/1.0/admin/groups/more-members"
        resp = self._request_json(path, {"context": clean_group, "limit": 100})
        if not resp or not isinstance(resp, dict):
            return 0

        values = resp.get("values", [])
        if not isinstance(values, list):
            return 0

        count = len(values)
        if resp.get("isLastPage", True):
            return count

        start = resp.get("nextPageStart", count)
        max_pages = 10
        page_num = 0
        while not resp.get("isLastPage", True) and page_num < max_pages:
            resp = self._request_json(
                path, {"context": clean_group, "start": start, "limit": 100}
            )
            if not resp or not isinstance(resp, dict):
                break
            page_values = resp.get("values", [])
            if not isinstance(page_values, list) or not page_values:
                break
            count += len(page_values)
            if resp.get("isLastPage", True):
                break
            start = resp.get("nextPageStart", start + len(page_values))
            page_num += 1

        return count


class BitbucketPolicyScraper(PolicyScraper):
    """Platform scraper discovering branch governance policies from Bitbucket."""

    scraper_type: str = "policy"
    target_type: str = "policy"

    def __init__(
        self,
        project_key: str,
        repo_slug: str | None = None,
        client: BitbucketTransport | None = None,
        default_branch: str = "main",
    ) -> None:
        """Initialize a new BitbucketPolicyScraper instance."""
        if not project_key or not project_key.strip():
            raise ValueError("Project key cannot be empty.")

        self.project_key = project_key.strip()
        self.repo_slug = repo_slug.strip() if repo_slug and repo_slug.strip() else None
        self.client = client
        self.default_branch = default_branch.strip() if default_branch else "main"

    @staticmethod
    def _extract_enabled_count(setting: Any) -> int | None:
        """Extract positive numeric count from dict config or number."""
        if isinstance(setting, dict) and setting.get("enabled"):
            return int(setting.get("count", 1))
        if isinstance(setting, int | float) and setting > 0:
            return int(setting)
        return None

    def _canonicalize_ref(
        self, matcher_id: str, matcher_type_id: str | None = None
    ) -> str:
        """Standardize a ref pattern into a canonical Git ref format."""
        clean = (matcher_id or "").strip()
        if not clean or clean == "*":
            return "*"
        if clean.startswith("refs/"):
            return clean
        if matcher_type_id and matcher_type_id.upper() == "TAG":
            return f"refs/tags/{clean}"
        return f"refs/heads/{clean}"

    def _extract_group_slugs(self, raw_groups: Any) -> list[str]:
        """Extract and slugify group names to match Team Topology team_slugs."""
        if not raw_groups:
            return []
        slugs: list[str] = []
        for g in raw_groups:
            if isinstance(g, dict):
                name = g.get("name") or g.get("id") or ""
            else:
                name = str(g)
            slug = slugify(name)
            if slug:
                slugs.append(slug)
        return sorted(set(slugs))

    def scrape(self) -> list[BranchPolicy]:
        """Discover and return canonical branch policies from Bitbucket."""
        if self.client is None:
            raise ScraperError("Bitbucket client not configured.")

        scope = (
            f"repo:{self.repo_slug}"
            if self.repo_slug
            else f"project:{self.project_key}"
        )
        policies: list[BranchPolicy] = []

        # 1. Scrape branch restrictions
        restrictions = self.client.get_branch_restrictions(
            self.project_key, self.repo_slug
        )

        for item in restrictions:
            rule_type_raw = str(item.get("type", "")).strip().lower()
            matcher = item.get("matcher", {}) or {}
            matcher_id = matcher.get("id") or matcher.get("displayId") or "*"
            raw_type = matcher.get("type")
            matcher_type_id = raw_type.get("id") if isinstance(raw_type, dict) else None
            target_ref = self._canonicalize_ref(str(matcher_id), matcher_type_id)

            bypass_slugs = self._extract_group_slugs(
                item.get("groups") or item.get("groupNames")
            )
            raw_origin = json.dumps(item, sort_keys=True)

            if rule_type_raw == "pull-request-only":
                policies.append(
                    BranchPolicy(
                        policy_scope=scope,
                        target_ref_pattern=target_ref,
                        normalized_rule_type=NormalizedRuleType.REQUIRE_PULL_REQUEST,
                        rule_parameters={"enforce_pr": True},
                        bypass_team_slugs=bypass_slugs,
                        origin_raw_policy=raw_origin,
                        platform="bitbucket",
                    )
                )
            elif rule_type_raw in ("fast-forward-only", "read-only"):
                params = (
                    {"linear_history": True}
                    if rule_type_raw == "fast-forward-only"
                    else {"read_only": True}
                )
                policies.append(
                    BranchPolicy(
                        policy_scope=scope,
                        target_ref_pattern=target_ref,
                        normalized_rule_type=NormalizedRuleType.BLOCK_FORCE_PUSH,
                        rule_parameters=params,
                        bypass_team_slugs=bypass_slugs,
                        origin_raw_policy=raw_origin,
                        platform="bitbucket",
                    )
                )
            elif rule_type_raw == "no-deletes":
                policies.append(
                    BranchPolicy(
                        policy_scope=scope,
                        target_ref_pattern=target_ref,
                        normalized_rule_type=NormalizedRuleType.PREVENT_BRANCH_DELETION,
                        rule_parameters={"prevent_deletion": True},
                        bypass_team_slugs=bypass_slugs,
                        origin_raw_policy=raw_origin,
                        platform="bitbucket",
                    )
                )

        # 2. Scrape pull request merge checks
        pr_settings = self.client.get_pull_request_settings(
            self.project_key, self.repo_slug
        )

        if pr_settings:
            raw_pr_origin = json.dumps(pr_settings, sort_keys=True)
            default_ref = self._canonicalize_ref(self.default_branch)

            min_count = self._extract_enabled_count(
                pr_settings.get("requiredApprovers")
            )
            if min_count is not None:
                all_tasks = bool(pr_settings.get("requiredAllTasksComplete", False))
                all_approvers = bool(pr_settings.get("requiredAllApprovers", False))
                policies.append(
                    BranchPolicy(
                        policy_scope=scope,
                        target_ref_pattern=default_ref,
                        normalized_rule_type=NormalizedRuleType.REQUIRE_PULL_REQUEST,
                        rule_parameters={
                            "min_approvals": min_count,
                            "require_all_tasks_complete": all_tasks,
                            "require_all_approvers": all_approvers,
                        },
                        origin_raw_policy=raw_pr_origin,
                        platform="bitbucket",
                    )
                )

            build_count = self._extract_enabled_count(
                pr_settings.get("requiredSuccessfulBuilds")
            )
            if build_count is not None:
                policies.append(
                    BranchPolicy(
                        policy_scope=scope,
                        target_ref_pattern=default_ref,
                        normalized_rule_type=NormalizedRuleType.REQUIRE_STATUS_CHECKS,
                        rule_parameters={
                            "min_successful_builds": build_count,
                            "strict": True,
                        },
                        origin_raw_policy=raw_pr_origin,
                        platform="bitbucket",
                    )
                )

        return policies


def _resolve_auth(args: Any) -> Authenticator | None:
    """Resolve Authenticator from CLI arguments."""
    username = getattr(args, "username", None)
    password = getattr(args, "password", None)
    token = getattr(args, "token", None)

    if username and password is not None:
        return BasicAuthenticator(username=username, password=password)
    if token:
        return TokenAuthenticator(token=token)
    return None


class BitbucketPoliciesScraperProvider(ScraperProvider):
    """Platform scraper provider for Bitbucket branch governance discovery."""

    target_type: str = "policy"
    default_sheet_name: str = "Policies"

    @property
    def platform_name(self) -> str:
        return "bitbucket-policies"

    @property
    def description(self) -> str:
        return "Bitbucket Server / Data Center / Cloud branch governance policy scraper"

    def configure_parser(self, parser: Any) -> None:
        """Configure platform CLI arguments for Bitbucket policy scraping."""
        parser.add_argument(
            "--url",
            required=True,
            help="Bitbucket server or API base URL (e.g. https://bitbucket.corp.local)",
        )
        parser.add_argument(
            "--project",
            "--project-key",
            dest="project_key",
            required=True,
            help="Bitbucket project key (e.g. CORE)",
        )
        parser.add_argument(
            "--repo",
            "--repo-slug",
            dest="repo_slug",
            default=None,
            help=(
                "Bitbucket repository slug "
                "(optional, scrapes project policies if omitted)"
            ),
        )
        parser.add_argument(
            "--token",
            default=None,
            help="Bitbucket Personal Access Token (PAT) or Bearer token",
        )
        parser.add_argument(
            "--username",
            default=None,
            help="Bitbucket HTTP Basic authentication username",
        )
        parser.add_argument(
            "--password",
            default=None,
            help="Bitbucket HTTP Basic authentication password",
        )
        parser.add_argument(
            "--default-branch",
            default="main",
            help="Default branch name for pull request settings (default: main)",
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

    def create_scraper(self, args: Any) -> BitbucketPolicyScraper:
        """Build BitbucketPolicyScraper from parsed CLI arguments."""
        auth = _resolve_auth(args)
        timeout = float(getattr(args, "timeout", 15.0) or 15.0)
        retries = int(getattr(args, "retries", 3) or 3)
        backoff_factor = float(getattr(args, "backoff_factor", 0.5) or 0.5)

        client = BitbucketClient(
            base_url=args.url,
            auth=auth,
            timeout=timeout,
            retries=retries,
            backoff_factor=backoff_factor,
        )

        return BitbucketPolicyScraper(
            project_key=args.project_key,
            repo_slug=getattr(args, "repo_slug", None),
            client=client,
            default_branch=getattr(args, "default_branch", "main"),
        )


default_scraper_registry.register(BitbucketPoliciesScraperProvider())


class BitbucketRepositoryScraper(RepositoryScraper):
    """Platform scraper discovering repositories and stacks from Bitbucket."""

    scraper_type: str = "repository"
    target_type: str = "repository"

    def __init__(
        self,
        base_url: str,
        project_key: str | None = None,
        client: BitbucketTransport | None = None,
        page_size: int = 100,
        include_archived: bool = False,
        organization: str | None = None,
    ) -> None:
        """Initialize a new BitbucketRepositoryScraper instance."""
        if not base_url or not base_url.strip():
            raise ValueError("Bitbucket base URL cannot be empty.")

        self.base_url = base_url.strip().rstrip("/")
        self.project_key = (
            project_key.strip() if project_key and project_key.strip() else None
        )
        self.client = client
        self.page_size = page_size if page_size > 0 else 100
        self.include_archived = include_archived
        self.organization = (
            organization.strip() if organization and organization.strip() else None
        )
        self._default_org = urllib.parse.urlparse(self.base_url).netloc

    @staticmethod
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

    @staticmethod
    def _parse_commit_timestamp(commit: dict[str, Any] | None) -> datetime | None:
        """Parse author or committer timestamp into timezone-aware UTC datetime."""
        if not commit or not isinstance(commit, dict):
            return None
        ts = commit.get("committerTimestamp") or commit.get("authorTimestamp")
        if isinstance(ts, int | float) and ts > 0:
            secs = ts / 1000.0 if ts > 1e11 else float(ts)
            return datetime.fromtimestamp(secs, tz=timezone.utc)
        date_str = commit.get("date") or commit.get("timestamp")
        if date_str and isinstance(date_str, str):
            clean_date = date_str.strip().replace("Z", "+00:00")
            try:
                return datetime.fromisoformat(clean_date)
            except (ValueError, TypeError):
                pass
        return None

    def _resolve_clone_url(
        self, repo: dict[str, Any], project_key: str, slug: str
    ) -> str:
        """Resolve canonical clone URL prioritizing HTTP/HTTPS over SSH."""
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

        return f"{self.base_url}/scm/{project_key.lower()}/{slug}.git"

    def _resolve_organization(self, project_key: str) -> str:
        """Determine organization name from override or base URL hostname."""
        if self.organization:
            return self.organization
        return self._default_org if self._default_org else project_key

    def _resolve_default_branch(
        self, repo: dict[str, Any], proj_key: str, slug: str
    ) -> str | None:
        """Resolve default branch from repository payload or remote client."""
        mainbranch = repo.get("mainbranch")
        if isinstance(mainbranch, dict) and mainbranch.get("name"):
            return str(mainbranch["name"]).strip()
        if repo.get("defaultBranch"):
            return str(repo["defaultBranch"]).strip()
        if self.client is None:
            return None
        try:
            return self.client.get_default_branch(proj_key, slug)
        except ScraperError:
            return None

    def scrape(self) -> list[Repository]:
        """Discover and return canonical repositories and stacks from Bitbucket."""
        if self.client is None:
            raise ScraperError("Bitbucket client not configured.")

        if self.project_key:
            projects = [{"key": self.project_key, "name": self.project_key}]
        else:
            projects = self.client.get_projects(limit=self.page_size)

        repositories: list[Repository] = []

        for proj in projects:
            proj_key = proj.get("key") or proj.get("slug") or proj.get("name") or ""
            if not proj_key:
                continue

            repos = self.client.get_repositories(proj_key, limit=self.page_size)

            for repo in repos:
                is_archived = bool(repo.get("archived", False))
                if is_archived and not self.include_archived:
                    continue

                slug = repo.get("slug") or repo.get("name") or ""
                if not slug:
                    continue

                repo_name = repo.get("name") or slug
                clone_url = self._resolve_clone_url(repo, proj_key, slug)
                org = self._resolve_organization(proj_key)
                default_branch = self._resolve_default_branch(repo, proj_key, slug)

                last_commit_dt: datetime | None = None
                try:
                    commit = self.client.get_last_commit(proj_key, slug)
                    last_commit_dt = self._parse_commit_timestamp(commit)
                except ScraperError:
                    last_commit_dt = None

                files: list[str] = []
                try:
                    files = self.client.get_repository_files(proj_key, slug, limit=100)
                except ScraperError:
                    files = []
                stacks = self._detect_stacks(files)

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
                            "id": repo.get("id"),
                            "slug": slug,
                            "name": repo_name,
                            "description": repo.get("description", ""),
                            "public": repo.get("public", False),
                            "archived": is_archived,
                            "fork": repo.get("fork", False),
                            "scm_id": repo.get("scmId", "git"),
                            "project_key": proj_key,
                            "project_name": proj.get("name", proj_key),
                            "raw": repo,
                        },
                    )
                )

        return repositories


class BitbucketScraperProvider(ScraperProvider):
    """Platform scraper provider for Bitbucket repository discovery."""

    target_type: str = "repository"
    default_sheet_name: str = "Inventario"

    @property
    def platform_name(self) -> str:
        return "bitbucket"

    @property
    def description(self) -> str:
        return "Bitbucket Server / Data Center / Cloud repository scraper"

    def configure_parser(self, parser: Any) -> None:
        """Configure platform CLI arguments for Bitbucket repository scraping."""
        parser.add_argument(
            "--base-url",
            "--url",
            dest="base_url",
            required=True,
            help="Bitbucket Server/DC base URL or Cloud API base URL",
        )
        parser.add_argument(
            "--project",
            "--project-key",
            dest="project_key",
            default=None,
            help=(
                "Optional Bitbucket project key filter "
                "(scrapes all projects if omitted)"
            ),
        )
        parser.add_argument(
            "--token",
            default=None,
            help="Bitbucket Personal Access Token (PAT) or Bearer token",
        )
        parser.add_argument(
            "--username",
            default=None,
            help="Bitbucket HTTP Basic authentication username",
        )
        parser.add_argument(
            "--password",
            default=None,
            help="Bitbucket HTTP Basic authentication password",
        )
        parser.add_argument(
            "--page-size",
            type=int,
            default=100,
            help="Pagination window size for API requests (default: 100)",
        )
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

    def create_scraper(self, args: Any) -> BitbucketRepositoryScraper:
        """Build BitbucketRepositoryScraper from parsed CLI arguments."""
        auth = _resolve_auth(args)

        raw_url = getattr(args, "base_url", None) or getattr(args, "url", None) or ""
        base_url = str(raw_url).strip()
        timeout = float(getattr(args, "timeout", 15.0) or 15.0)
        retries = int(getattr(args, "retries", 3) or 3)
        backoff_factor = float(getattr(args, "backoff_factor", 0.5) or 0.5)

        client = BitbucketClient(
            base_url=base_url,
            auth=auth,
            timeout=timeout,
            retries=retries,
            backoff_factor=backoff_factor,
        )

        return BitbucketRepositoryScraper(
            base_url=base_url,
            project_key=getattr(args, "project_key", None),
            client=client,
            page_size=getattr(args, "page_size", 100),
            include_archived=bool(getattr(args, "include_archived", False)),
            organization=getattr(args, "organization", None),
        )


default_scraper_registry.register(BitbucketScraperProvider())


_ROLE_PRECEDENCE: dict[NormalizedRole, int] = {
    NormalizedRole.ADMIN: 5,
    NormalizedRole.MAINTAIN: 4,
    NormalizedRole.WRITE: 3,
    NormalizedRole.TRIAGE: 2,
    NormalizedRole.READ: 1,
}


def _normalize_bitbucket_role(role_raw: Any) -> tuple[NormalizedRole, str]:
    """Normalize a Bitbucket role string into a NormalizedRole enum."""
    if not role_raw or not str(role_raw).strip():
        raise ValueError("Bitbucket permission role cannot be empty.")
    role_str = str(role_raw).strip()
    try:
        norm = normalize_role(role_str)
        return norm, role_str
    except ValueError as err:
        raise ValueError(
            f"Unrecognized Bitbucket permission role: '{role_str}'."
        ) from err


class BitbucketTeamScraper(TeamScraper):
    """Platform scraper discovering team permissions from Bitbucket."""

    scraper_type: str = "team"
    target_type: str = "team"

    def __init__(
        self,
        base_url: str | None = None,
        project_key: str | None = None,
        repo_slug: str | None = None,
        client: BitbucketTransport | None = None,
        include_repo_level: bool = True,
        page_size: int = 100,
    ) -> None:
        self.base_url = base_url.strip() if base_url and base_url.strip() else None
        self.project_key = (
            project_key.strip() if project_key and project_key.strip() else None
        )
        self.repo_slug = repo_slug.strip() if repo_slug and repo_slug.strip() else None
        self.client = client
        self.include_repo_level = include_repo_level
        self.page_size = page_size if page_size > 0 else 100
        self._member_count_cache: dict[str, int] = {}

    @staticmethod
    def _extract_group_entry(entry: Any) -> tuple[str, str]:
        """Extract group name and raw permission from Bitbucket API response item."""
        if not isinstance(entry, dict):
            raise ValueError(
                f"Invalid group permission entry: '{entry}'. Expected dict."
            )
        group_raw = entry.get("group")
        group_name = ""
        if isinstance(group_raw, dict):
            group_name = str(
                group_raw.get("name") or group_raw.get("slug") or ""
            ).strip()
        elif isinstance(group_raw, str):
            group_name = group_raw.strip()

        if not group_name:
            raise ValueError(f"Group permission entry lacks valid group name: {entry}")

        permission_raw = entry.get("permission")
        if not permission_raw or not str(permission_raw).strip():
            raise ValueError(f"Group permission entry lacks valid permission: {entry}")

        return group_name, str(permission_raw).strip()

    def _resolve_member_count(self, group_name: str) -> int:
        """Resolve member count using cache and graceful fallback on auth/API errors."""
        if group_name in self._member_count_cache:
            return self._member_count_cache[group_name]

        count = 0
        if self.client is not None:
            try:
                count = self.client.get_group_member_count(group_name)
            except (ScraperAuthenticationError, ScraperError):
                count = 0

        self._member_count_cache[group_name] = count
        return count

    def _record_permission(
        self,
        perm_map: dict[tuple[str, str], TeamPermission],
        key: tuple[str, str],
        new_perm: TeamPermission,
    ) -> None:
        """Record permission with composite key deduplication by role precedence."""
        if key not in perm_map:
            perm_map[key] = new_perm
            return
        existing = perm_map[key]
        existing_rank = _ROLE_PRECEDENCE.get(existing.normalized_role, 0)
        new_rank = _ROLE_PRECEDENCE.get(new_perm.normalized_role, 0)
        if new_rank > existing_rank:
            perm_map[key] = new_perm

    def _build_permission(
        self,
        entry: Any,
        target: str,
        proj_key: str,
        repo_slug: str | None = None,
    ) -> TeamPermission:
        """Construct a normalized TeamPermission instance from an API group entry."""
        group_name, origin_role = self._extract_group_entry(entry)
        norm_role, origin_role_str = _normalize_bitbucket_role(origin_role)
        team_slug = slugify(group_name)
        member_count = self._resolve_member_count(group_name)

        metadata: dict[str, Any] = {
            "platform": "bitbucket",
            "project_key": proj_key,
            "scope": target,
            "origin_permission": origin_role_str,
        }
        if repo_slug:
            metadata["repo_slug"] = repo_slug

        return TeamPermission(
            team_name=group_name,
            team_slug=team_slug,
            member_count=member_count,
            target_repo_or_project=target,
            normalized_role=norm_role,
            origin_role=origin_role_str,
            metadata=metadata,
        )

    def scrape(self) -> list[TeamPermission]:
        """Discover and return canonical team permissions from Bitbucket."""
        if self.client is None:
            raise ScraperError("Bitbucket client not configured.")

        if self.project_key:
            projects = [{"key": self.project_key}]
        else:
            projects = self.client.get_projects(limit=self.page_size)

        permissions_by_key: dict[tuple[str, str], TeamPermission] = {}

        for proj in projects:
            proj_key = proj.get("key") or proj.get("slug") or proj.get("name") or ""
            if not proj_key:
                continue

            # 1. Project-level group permissions
            proj_perms = self.client.get_project_group_permissions(
                proj_key, limit=self.page_size
            )
            target = f"project:{proj_key}"
            for entry in proj_perms:
                perm = self._build_permission(entry, target, proj_key)
                composite_key = (perm.team_slug or slugify(perm.team_name), target)
                self._record_permission(permissions_by_key, composite_key, perm)

            # 2. Repository-level group permissions
            if not self.include_repo_level:
                continue

            if self.repo_slug:
                repos = [{"slug": self.repo_slug}]
            else:
                repos = self.client.get_repositories(proj_key, limit=self.page_size)

            for repo in repos:
                slug = repo.get("slug") or repo.get("name") or ""
                if not slug:
                    continue

                repo_perms = self.client.get_repository_group_permissions(
                    proj_key, slug, limit=self.page_size
                )
                repo_target = f"repo:{proj_key}/{slug}"
                for entry in repo_perms:
                    perm = self._build_permission(
                        entry, repo_target, proj_key, repo_slug=slug
                    )
                    composite_key = (
                        perm.team_slug or slugify(perm.team_name),
                        repo_target,
                    )
                    self._record_permission(permissions_by_key, composite_key, perm)

        return list(permissions_by_key.values())


class BitbucketTeamsScraperProvider(ScraperProvider):
    """Platform scraper provider for Bitbucket team topology discovery."""

    target_type: str = "team"
    default_sheet_name: str = "Topology"

    @property
    def platform_name(self) -> str:
        return "bitbucket-teams"

    @property
    def description(self) -> str:
        return "Bitbucket Server / Data Center / Cloud team topology scraper"

    def configure_parser(self, parser: Any) -> None:
        """Configure platform CLI arguments for Bitbucket team topology scraping."""
        parser.add_argument(
            "--base-url",
            "--url",
            dest="base_url",
            required=True,
            help="Bitbucket Server/DC base URL or Cloud API base URL",
        )
        parser.add_argument(
            "--project",
            "--project-key",
            dest="project_key",
            default=None,
            help=(
                "Optional Bitbucket project key filter "
                "(scrapes all projects if omitted)"
            ),
        )
        parser.add_argument(
            "--repo",
            "--repo-slug",
            dest="repo_slug",
            default=None,
            help="Optional Bitbucket repository slug filter",
        )
        parser.add_argument(
            "--token",
            default=None,
            help="Bitbucket Personal Access Token (PAT) or Bearer token",
        )
        parser.add_argument(
            "--username",
            default=None,
            help="Bitbucket HTTP Basic authentication username",
        )
        parser.add_argument(
            "--password",
            default=None,
            help="Bitbucket HTTP Basic authentication password",
        )
        parser.add_argument(
            "--page-size",
            type=int,
            default=100,
            help="Pagination window size for API requests (default: 100)",
        )
        parser.add_argument(
            "--include-repo-level",
            action=argparse.BooleanOptionalAction,
            default=True,
            help=(
                "Scrape repository-specific group permissions in addition "
                "to project-level permissions (default: true)"
            ),
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

    def create_scraper(self, args: Any) -> BitbucketTeamScraper:
        """Build BitbucketTeamScraper from parsed CLI arguments."""
        auth = _resolve_auth(args)

        raw_url = getattr(args, "base_url", None) or getattr(args, "url", None) or ""
        base_url = str(raw_url).strip()
        timeout = float(getattr(args, "timeout", 15.0) or 15.0)
        retries = int(getattr(args, "retries", 3) or 3)
        backoff_factor = float(getattr(args, "backoff_factor", 0.5) or 0.5)

        client = BitbucketClient(
            base_url=base_url,
            auth=auth,
            timeout=timeout,
            retries=retries,
            backoff_factor=backoff_factor,
        )

        include_repo = getattr(args, "include_repo_level", True)
        if include_repo is None:
            include_repo = True

        return BitbucketTeamScraper(
            base_url=base_url,
            project_key=getattr(args, "project_key", None),
            repo_slug=getattr(args, "repo_slug", None),
            client=client,
            include_repo_level=bool(include_repo),
            page_size=getattr(args, "page_size", 100),
        )


default_scraper_registry.register(BitbucketTeamsScraperProvider())

__all__ = [
    "BitbucketClient",
    "BitbucketPoliciesScraperProvider",
    "BitbucketPolicyScraper",
    "BitbucketRepositoryScraper",
    "BitbucketScraperProvider",
    "BitbucketTeamScraper",
    "BitbucketTeamsScraperProvider",
    "BitbucketTransport",
]
