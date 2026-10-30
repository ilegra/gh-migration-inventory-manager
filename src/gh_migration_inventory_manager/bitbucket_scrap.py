"""Bitbucket branch governance discovery adapters, transport client, and CLI."""

from __future__ import annotations

import base64
import json
from typing import Any, Protocol, cast, runtime_checkable

import urllib3
from urllib3.util import Retry

from gh_migration_inventory_manager.scrap import (
    Authenticator,
    BasicAuthenticator,
    BranchPolicy,
    NormalizedRuleType,
    PolicyScraper,
    ScraperAuthenticationError,
    ScraperConnectionError,
    ScraperError,
    ScraperProvider,
    TokenAuthenticator,
    default_scraper_registry,
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
        self.auth = auth
        self.timeout = timeout

        if pool_manager is not None:
            self._http = pool_manager
        else:
            retry_strategy = Retry(
                total=retries,
                backoff_factor=backoff_factor,
                status_forcelist=[429, 500, 502, 503, 504],
                allowed_methods=["GET"],
                raise_on_status=False,
                respect_retry_after_header=True,
            )
            self._http = urllib3.PoolManager(
                retries=retry_strategy,
                timeout=urllib3.Timeout(timeout),
            )

    def _build_auth_headers(self) -> dict[str, str]:
        """Resolve authentication credentials and return HTTP headers."""
        if hasattr(self, "_cached_auth_headers"):
            return self._cached_auth_headers
        if self.auth is None:
            self._cached_auth_headers: dict[str, str] = {}
            return self._cached_auth_headers
        creds = self.auth.resolve_credentials()
        headers: dict[str, str] = {}
        if creds.token and not creds.username:
            headers["Authorization"] = f"Bearer {creds.token}"
        elif creds.username:
            secret = creds.password or creds.token or ""
            raw_cred = f"{creds.username}:{secret}".encode()
            encoded = base64.b64encode(raw_cred).decode("ascii")
            headers["Authorization"] = f"Basic {encoded}"
        self._cached_auth_headers = headers
        return headers

    def _request_json(
        self, path: str, query_params: dict[str, Any] | None = None
    ) -> Any:
        """Execute a GET request against Bitbucket REST API and return parsed JSON."""
        clean_path = path.lstrip("/")
        url = f"{self.base_url}/{clean_path}"

        headers = self._build_auth_headers()
        headers["Accept"] = "application/json"

        try:
            resp = self._http.request(
                "GET",
                url,
                fields=query_params or {},
                headers=headers,
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

    def create_scraper(self, args: Any) -> BitbucketPolicyScraper:
        """Build BitbucketPolicyScraper from parsed CLI arguments."""
        auth: Authenticator | None = None
        username = getattr(args, "username", None)
        password = getattr(args, "password", None)
        token = getattr(args, "token", None)

        if username and password is not None:
            auth = BasicAuthenticator(username=username, password=password)
        elif token:
            auth = TokenAuthenticator(token=token)

        client = BitbucketClient(
            base_url=args.url,
            auth=auth,
        )

        return BitbucketPolicyScraper(
            project_key=args.project_key,
            repo_slug=getattr(args, "repo_slug", None),
            client=client,
            default_branch=getattr(args, "default_branch", "main"),
        )


default_scraper_registry.register(BitbucketPoliciesScraperProvider())

__all__ = [
    "BitbucketClient",
    "BitbucketPoliciesScraperProvider",
    "BitbucketPolicyScraper",
    "BitbucketTransport",
]
