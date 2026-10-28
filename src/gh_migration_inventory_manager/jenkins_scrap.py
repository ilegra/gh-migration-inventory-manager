"""Jenkins CI/CD pipeline scraper and REST Tree API transport client."""

from __future__ import annotations

import base64
import json
import urllib.parse
from datetime import datetime, timezone
from typing import Any, Protocol, runtime_checkable

import urllib3
from urllib3.util import Retry

from gh_migration_inventory_manager.scrap import (
    Authenticator,
    BasicAuthenticator,
    Pipeline,
    PipelineScraper,
    PipelineTrigger,
    PipelineType,
    Repository,
    ScraperAuthenticationError,
    ScraperConnectionError,
    ScraperError,
    ScraperProvider,
    TokenAuthenticator,
    default_scraper_registry,
)


@runtime_checkable
class JenkinsTransport(Protocol):
    """Protocol defining network operations required to communicate with Jenkins."""

    base_url: str

    def get_json(self, path: str, tree: str | None = None) -> dict[str, Any]:
        """Fetch JSON payload from Jenkins API."""
        raise NotImplementedError


class JenkinsClient:
    """Transport client communicating with Jenkins REST API with built-in retries."""

    def __init__(
        self,
        base_url: str,
        auth: Authenticator | None = None,
        retries: int = 3,
        backoff_factor: float = 0.5,
        timeout: float = 30.0,
        pool_manager: urllib3.PoolManager | None = None,
    ) -> None:
        clean_url = (base_url or "").strip().rstrip("/")
        if not clean_url:
            raise ValueError("Jenkins URL cannot be empty.")
        parsed = urllib.parse.urlparse(clean_url)
        if parsed.scheme.lower() not in ("http", "https"):
            raise ValueError(
                f"Unsupported URL scheme '{parsed.scheme}'. "
                "Jenkins URL must use http:// or https://"
            )

        self.base_url = clean_url
        self.auth = auth
        self.timeout = timeout
        self._crumb_cache: tuple[str, str] | None = None
        self._crumb_checked = False

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
        """Resolve authentication credentials and return appropriate HTTP headers."""
        if self.auth is None:
            return {}
        creds = self.auth.resolve_credentials()
        headers: dict[str, str] = {}
        if creds.token and not creds.username:
            headers["Authorization"] = f"Bearer {creds.token}"
        elif creds.username:
            secret = creds.password or creds.token or ""
            encoded = base64.b64encode(f"{creds.username}:{secret}".encode()).decode(
                "ascii"
            )
            headers["Authorization"] = f"Basic {encoded}"
        return headers

    def _get_crumb(self) -> tuple[str, str] | None:
        """Resolve Jenkins CSRF protection crumb if enabled."""
        if self._crumb_checked:
            return self._crumb_cache

        crumb_url = f"{self.base_url}/crumbIssuer/api/json"
        headers = self._build_auth_headers()

        try:
            resp = self._http.request("GET", crumb_url, headers=headers)
            if resp.status == 200:
                data = json.loads(resp.data.decode("utf-8"))
                field = data.get("crumbRequestField")
                crumb = data.get("crumb")
                if field and crumb:
                    self._crumb_cache = (str(field), str(crumb))
            self._crumb_checked = True
            return self._crumb_cache
        except Exception:
            self._crumb_checked = True
            return None

    def get_json(self, path: str, tree: str | None = None) -> dict[str, Any]:
        """Fetch and decode JSON from the specified Jenkins API endpoint.

        Raises:
            ScraperAuthenticationError: If HTTP 401 or 403 occurs.
            ScraperConnectionError: If network disconnect or timeout occurs.
            ScraperError: For other HTTP errors or malformed payloads.
        """
        clean_path = path.strip().strip("/")
        endpoint = (
            f"{self.base_url}/{clean_path}/api/json"
            if clean_path
            else f"{self.base_url}/api/json"
        )

        params: dict[str, str] = {}
        if tree:
            params["tree"] = tree
        if params:
            endpoint = f"{endpoint}?{urllib.parse.urlencode(params)}"

        headers = self._build_auth_headers()
        crumb = self._get_crumb()
        if crumb:
            headers[crumb[0]] = crumb[1]

        try:
            resp = self._http.request("GET", endpoint, headers=headers)
        except (
            urllib3.exceptions.MaxRetryError,
            urllib3.exceptions.HTTPError,
            TimeoutError,
            OSError,
        ) as err:
            raise ScraperConnectionError(
                f"Failed to connect to Jenkins controller at '{endpoint}': {err}"
            ) from err

        if resp.status in (401, 403):
            raise ScraperAuthenticationError(
                f"Authentication failed for Jenkins controller at '{endpoint}' "
                f"(HTTP {resp.status})."
            )
        if resp.status == 404:
            raise ScraperError(
                f"Jenkins endpoint not found at '{endpoint}' (HTTP 404)."
            )
        if resp.status >= 400:
            raise ScraperError(
                f"Jenkins API returned HTTP {resp.status} for endpoint '{endpoint}'."
            )

        try:
            data = json.loads(resp.data.decode("utf-8"))
            if not isinstance(data, dict):
                return {}
            return data
        except Exception as err:
            raise ScraperError(
                f"Failed to parse JSON response from Jenkins at '{endpoint}': {err}"
            ) from err


_TREE_QUERY = (
    "jobs[name,url,color,_class,disabled,"
    "lastBuild[timestamp,result,number],"
    "actions[causes[shortDescription,_class]],"
    "property[_class,branchConfigs[name]],"
    "definition[_class,script,scriptPath,scm[_class,userRemoteConfigs[url],branches[name],locations[remote]]],"
    "sources[source[_class,remote,credentialsId]]]"
)


class JenkinsPipelineScraper(PipelineScraper):
    """Platform discovery scraper for Jenkins CI/CD controllers."""

    scraper_type: str = "pipeline"

    def __init__(
        self,
        client: JenkinsClient | JenkinsTransport,
        tree_depth: int = 5,
        staleness_days: int = 180,
    ) -> None:
        if tree_depth < 1:
            raise ValueError("Tree depth must be at least 1.")
        if staleness_days < 0:
            raise ValueError("Staleness days cannot be negative.")

        self.client = client
        self.tree_depth = tree_depth
        self.staleness_days = staleness_days

    def _resolve_pipeline_type(
        self, job_class: str, definition: dict[str, Any]
    ) -> PipelineType:
        """Classify pipeline type based on Jenkins job class and definition."""
        cls_lower = (job_class or "").lower()
        if "workflowmultibranch" in cls_lower:
            return PipelineType.MULTIBRANCH
        if "workflowjob" in cls_lower:
            def_cls = str(definition.get("_class", "")).lower()
            if "cpsscmflowdefinition" in def_cls:
                return PipelineType.DECLARATIVE
            if "cpsflowdefinition" in def_cls:
                return PipelineType.SCRIPTED
            return PipelineType.DECLARATIVE
        if "freestyleproject" in cls_lower:
            return PipelineType.FREESTYLE
        if "matrixproject" in cls_lower:
            return PipelineType.MATRIX
        return PipelineType.UNKNOWN

    def _extract_repository(
        self, definition: dict[str, Any], sources: list[dict[str, Any]]
    ) -> tuple[Repository | None, str | None, str | None]:
        """Extract SCM repository linkage, file path, and target branch."""
        scm = definition.get("scm") or {}
        file_path = definition.get("scriptPath")

        # 1. Inspect Git SCM / standard userRemoteConfigs
        remote_configs = scm.get("userRemoteConfigs") or []
        for rc in remote_configs:
            url = rc.get("url")
            if url:
                clean_url = str(url).strip()
                branches = scm.get("branches") or []
                branch = None
                if branches and isinstance(branches, list):
                    branch_spec = branches[0].get("name", "")
                    branch = branch_spec.replace("*/", "").replace("refs/heads/", "")

                repo_name = clean_url.rstrip("/").split("/")[-1]
                if repo_name.endswith(".git"):
                    repo_name = repo_name[:-4]

                repo = Repository(
                    name=repo_name,
                    url=clean_url,
                    default_branch=branch,
                )
                return repo, file_path, branch

        # 2. Inspect SVN SCM / locations
        locations = scm.get("locations") or []
        for loc in locations:
            remote = loc.get("remote")
            if remote:
                clean_remote = str(remote).strip()
                repo_name = clean_remote.rstrip("/").split("/")[-1]
                repo = Repository(
                    name=repo_name,
                    url=clean_remote,
                    default_branch="trunk",
                )
                return repo, file_path, "trunk"

        # 3. Inspect MultiBranch sources
        for src_entry in sources:
            source = src_entry.get("source") or {}
            remote = source.get("remote")
            if remote:
                clean_remote = str(remote).strip()
                repo_name = clean_remote.rstrip("/").split("/")[-1]
                if repo_name.endswith(".git"):
                    repo_name = repo_name[:-4]
                repo = Repository(
                    name=repo_name,
                    url=clean_remote,
                )
                return repo, file_path, None

        return None, file_path, None

    def _extract_triggers(self, job_data: dict[str, Any]) -> list[PipelineTrigger]:
        """Detect execution triggers from job causes or properties."""
        triggers: list[PipelineTrigger] = []
        actions = job_data.get("actions") or []
        for act in actions:
            if not isinstance(act, dict):
                continue
            causes = act.get("causes") or []
            for c in causes:
                desc = str(c.get("shortDescription", "")).lower()
                cls_name = str(c.get("_class", "")).lower()
                if "timer" in desc or "timer" in cls_name:
                    if PipelineTrigger.SCHEDULE not in triggers:
                        triggers.append(PipelineTrigger.SCHEDULE)
                elif "push" in desc or "github" in desc or "webhook" in desc:
                    if PipelineTrigger.WEBHOOK not in triggers:
                        triggers.append(PipelineTrigger.WEBHOOK)
                elif "user" in desc or "user" in cls_name:
                    if PipelineTrigger.MANUAL not in triggers:
                        triggers.append(PipelineTrigger.MANUAL)
                elif (
                    "upstream" in desc
                    or "started by project" in desc
                    or "upstream" in cls_name
                ):
                    if PipelineTrigger.UPSTREAM not in triggers:
                        triggers.append(PipelineTrigger.UPSTREAM)
                elif "scm" in desc or "polling" in desc or "scm" in cls_name:
                    if PipelineTrigger.SCM_POLLING not in triggers:
                        triggers.append(PipelineTrigger.SCM_POLLING)
        return triggers

    def _traverse_folder(self, folder_path: str, current_depth: int) -> list[Pipeline]:
        """Recursively query Jenkins folders and discover pipeline entities."""
        if current_depth > self.tree_depth:
            return []

        payload = self.client.get_json(folder_path, tree=_TREE_QUERY)
        raw_jobs = payload.get("jobs", [])
        pipelines: list[Pipeline] = []

        now = datetime.now(timezone.utc)

        for job in raw_jobs:
            if not isinstance(job, dict):
                continue

            name = str(job.get("name", "")).strip()
            job_class = str(job.get("_class", ""))
            url = str(job.get("url", "")).strip()

            full_path = f"{folder_path}/{name}" if folder_path else name

            # 1. Folder traversal
            is_folder = (
                "folder" in job_class.lower()
                or "organizationfolder" in job_class.lower()
            )
            if is_folder:
                sub_pipelines = self._traverse_folder(full_path, current_depth + 1)
                pipelines.extend(sub_pipelines)
                continue

            # 2. Pipeline Job processing
            definition = job.get("definition") or {}
            sources = job.get("sources") or []
            pipeline_type = self._resolve_pipeline_type(job_class, definition)

            repository, file_path, branch = self._extract_repository(
                definition, sources
            )
            triggers = self._extract_triggers(job)

            # Build timestamp and status
            last_build = job.get("lastBuild") or {}
            ts_millis = last_build.get("timestamp")
            last_build_dt: datetime | None = None
            if ts_millis and isinstance(ts_millis, int | float):
                last_build_dt = datetime.fromtimestamp(
                    ts_millis / 1000.0, tz=timezone.utc
                )

            last_status = last_build.get("result")
            disabled = bool(job.get("disabled", False))

            # Staleness evaluation
            is_stale = False
            if self.staleness_days > 0 and last_build_dt:
                age_days = (now - last_build_dt).days
                if age_days > self.staleness_days:
                    is_stale = True
                    disabled = True

            pipe = Pipeline(
                name=name,
                platform="jenkins",
                full_path=full_path,
                url=url,
                pipeline_type=pipeline_type,
                repository=repository,
                file_path=file_path,
                branch=branch,
                triggers=triggers,
                last_build_timestamp=last_build_dt,
                last_status=str(last_status) if last_status else None,
                disabled=disabled,
                metadata={
                    "_class": job_class,
                    "color": job.get("color"),
                    "is_stale": is_stale,
                },
            )
            pipelines.append(pipe)

        return pipelines

    def scrape(self) -> list[Pipeline]:
        """Discover and return all CI/CD pipelines from the Jenkins controller."""
        return self._traverse_folder(folder_path="", current_depth=1)


class JenkinsScraperProvider(ScraperProvider):
    """Provider exposing Jenkins discovery and scraping configuration to the CLI."""

    default_sheet_name: str = "Workflows"
    target_type: str = "pipeline"

    @property
    def platform_name(self) -> str:
        return "jenkins"

    @property
    def description(self) -> str:
        return "Jenkins CI/CD controller workflow and pipeline scraper"

    def configure_parser(self, parser: Any) -> None:
        """Add Jenkins-specific CLI arguments to the subparser."""
        parser.add_argument(
            "--url",
            required=True,
            help="Jenkins controller base URL (http:// or https://)",
        )
        parser.add_argument(
            "--username",
            default=None,
            help="Jenkins authentication username",
        )
        parser.add_argument(
            "--password",
            default=None,
            help="Jenkins authentication password",
        )
        parser.add_argument(
            "--token",
            default=None,
            help="Jenkins API token or bearer token",
        )
        parser.add_argument(
            "--tree-depth",
            type=int,
            default=5,
            help="Maximum recursion depth for folder hierarchies (default: 5)",
        )
        parser.add_argument(
            "--staleness-days",
            type=int,
            default=180,
            help="Age threshold in days to flag inactive jobs (default: 180)",
        )

    def create_scraper(self, args: Any) -> JenkinsPipelineScraper:
        """Build JenkinsPipelineScraper from parsed CLI arguments."""
        auth: Authenticator | None = None
        username = getattr(args, "username", None)
        password = getattr(args, "password", None)
        token = getattr(args, "token", None)

        if username and (password is not None or token is not None):
            secret = password if password is not None else (token or "")
            auth = BasicAuthenticator(username=username, password=secret)
        elif token:
            auth = TokenAuthenticator(token=token)

        client = JenkinsClient(
            base_url=getattr(args, "url", "") or "",
            auth=auth,
        )

        tree_depth = getattr(args, "tree_depth", 5) or 5
        staleness_days = getattr(args, "staleness_days", 180) or 180

        return JenkinsPipelineScraper(
            client=client,
            tree_depth=int(tree_depth),
            staleness_days=int(staleness_days),
        )


default_scraper_registry.register(JenkinsScraperProvider())


__all__ = [
    "JenkinsClient",
    "JenkinsPipelineScraper",
    "JenkinsScraperProvider",
    "JenkinsTransport",
]
