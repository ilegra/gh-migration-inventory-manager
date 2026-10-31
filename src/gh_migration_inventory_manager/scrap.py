"""Repository scraper abstractions, authenticators, and domain models."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Protocol, TypeVar, runtime_checkable

T = TypeVar("T")


class Stack(str, Enum):
    """Technology stack or language identified for a repository."""

    JAVA = "java"
    PYTHON = "python"
    TYPESCRIPT = "typescript"
    C_SHARP = "csharp"
    BASH = "bash"
    DOTNET_CORE = "dotnet_core"
    TERRAFORM = "terraform"
    UNKNOWN = "unknown"


@dataclass
class Repository:
    """Pure data container representing a discovered repository or project."""

    name: str
    organization: str | None = None
    project: str | None = None
    path: str | None = None
    url: str | None = None
    default_branch: str | None = None
    last_commit: datetime | None = None
    stacks: list[Stack] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def qualified_name(self) -> str:
        """Return an unambiguous, fully-qualified name for inventory tracking."""
        parts = [p for p in (self.organization, self.project, self.name) if p]
        return "/".join(parts) if parts else self.name


class PipelineType(str, Enum):
    """Execution model or configuration paradigm of a CI/CD pipeline."""

    DECLARATIVE = "declarative"
    SCRIPTED = "scripted"
    YAML = "yaml"
    FREESTYLE = "freestyle"
    MULTIBRANCH = "multibranch"
    MATRIX = "matrix"
    UNKNOWN = "unknown"


class PipelineTrigger(str, Enum):
    """Trigger mechanisms initiating pipeline execution."""

    PUSH = "push"
    PULL_REQUEST = "pull_request"
    SCHEDULE = "schedule"
    WEBHOOK = "webhook"
    MANUAL = "manual"
    UPSTREAM = "upstream"
    SCM_POLLING = "scm_polling"
    UNKNOWN = "unknown"


class NormalizedRole(str, Enum):
    """Normalized GitHub-targetable permission archetype."""

    ADMIN = "Admin"
    MAINTAIN = "Maintain"
    WRITE = "Write"
    TRIAGE = "Triage"
    READ = "Read"


_NORMALIZED_ROLE_LOOKUP: dict[str, NormalizedRole] = {
    "admin": NormalizedRole.ADMIN,
    "project_admin": NormalizedRole.ADMIN,
    "repo_admin": NormalizedRole.ADMIN,
    "maintain": NormalizedRole.MAINTAIN,
    "write": NormalizedRole.WRITE,
    "project_write": NormalizedRole.WRITE,
    "repo_write": NormalizedRole.WRITE,
    "triage": NormalizedRole.TRIAGE,
    "read": NormalizedRole.READ,
    "project_read": NormalizedRole.READ,
    "repo_read": NormalizedRole.READ,
}


def normalize_role(role: Any) -> NormalizedRole:
    """Normalize a role name or permission string into a NormalizedRole enum.

    Args:
        role: String or enum representation of role.

    Returns:
        The matching NormalizedRole enum.

    Raises:
        ValueError: If role cannot be mapped to a valid NormalizedRole.
    """
    if isinstance(role, NormalizedRole):
        return role
    if not role or not str(role).strip():
        raise ValueError("Role cannot be empty.")
    clean = str(role).strip().lower()
    if clean in _NORMALIZED_ROLE_LOOKUP:
        return _NORMALIZED_ROLE_LOOKUP[clean]
    raise ValueError(
        f"Invalid normalized role: '{role}'. Expected one of: "
        f"{', '.join(r.value for r in NormalizedRole)}."
    )


class NormalizedRuleType(str, Enum):
    """Canonical GitHub Ruleset rule archetype."""

    REQUIRE_PULL_REQUEST = "RequirePullRequest"
    BLOCK_FORCE_PUSH = "BlockForcePush"
    PREVENT_BRANCH_DELETION = "PreventBranchDeletion"
    REQUIRE_STATUS_CHECKS = "RequireStatusChecks"
    REQUIRE_LINEAR_HISTORY = "RequireLinearHistory"
    REQUIRE_SIGNED_COMMITS = "RequireSignedCommits"


_NORMALIZED_RULE_TYPE_LOOKUP: dict[str, NormalizedRuleType] = {
    "requirepullrequest": NormalizedRuleType.REQUIRE_PULL_REQUEST,
    "require_pull_request": NormalizedRuleType.REQUIRE_PULL_REQUEST,
    "require-pull-request": NormalizedRuleType.REQUIRE_PULL_REQUEST,
    "pull_request": NormalizedRuleType.REQUIRE_PULL_REQUEST,
    "pullrequest": NormalizedRuleType.REQUIRE_PULL_REQUEST,
    "pull_request_only": NormalizedRuleType.REQUIRE_PULL_REQUEST,
    "pull-request-only": NormalizedRuleType.REQUIRE_PULL_REQUEST,
    "blockforcepush": NormalizedRuleType.BLOCK_FORCE_PUSH,
    "block_force_push": NormalizedRuleType.BLOCK_FORCE_PUSH,
    "block-force-push": NormalizedRuleType.BLOCK_FORCE_PUSH,
    "force_push": NormalizedRuleType.BLOCK_FORCE_PUSH,
    "fast_forward_only": NormalizedRuleType.BLOCK_FORCE_PUSH,
    "fast-forward-only": NormalizedRuleType.BLOCK_FORCE_PUSH,
    "read_only": NormalizedRuleType.BLOCK_FORCE_PUSH,
    "read-only": NormalizedRuleType.BLOCK_FORCE_PUSH,
    "preventbranchdeletion": NormalizedRuleType.PREVENT_BRANCH_DELETION,
    "prevent_branch_deletion": NormalizedRuleType.PREVENT_BRANCH_DELETION,
    "prevent-branch-deletion": NormalizedRuleType.PREVENT_BRANCH_DELETION,
    "no_deletes": NormalizedRuleType.PREVENT_BRANCH_DELETION,
    "no-deletes": NormalizedRuleType.PREVENT_BRANCH_DELETION,
    "requirestatuschecks": NormalizedRuleType.REQUIRE_STATUS_CHECKS,
    "require_status_checks": NormalizedRuleType.REQUIRE_STATUS_CHECKS,
    "require-status-checks": NormalizedRuleType.REQUIRE_STATUS_CHECKS,
    "status_checks": NormalizedRuleType.REQUIRE_STATUS_CHECKS,
    "build_validation": NormalizedRuleType.REQUIRE_STATUS_CHECKS,
    "requirelinearhistory": NormalizedRuleType.REQUIRE_LINEAR_HISTORY,
    "require_linear_history": NormalizedRuleType.REQUIRE_LINEAR_HISTORY,
    "require-linear-history": NormalizedRuleType.REQUIRE_LINEAR_HISTORY,
    "linear_history": NormalizedRuleType.REQUIRE_LINEAR_HISTORY,
    "requiresignedcommits": NormalizedRuleType.REQUIRE_SIGNED_COMMITS,
    "require_signed_commits": NormalizedRuleType.REQUIRE_SIGNED_COMMITS,
    "require-signed-commits": NormalizedRuleType.REQUIRE_SIGNED_COMMITS,
    "signed_commits": NormalizedRuleType.REQUIRE_SIGNED_COMMITS,
}


def normalize_rule_type(rule: Any) -> NormalizedRuleType:
    """Normalize a rule type representation into a NormalizedRuleType enum.

    Args:
        rule: String or enum representation of rule type.

    Returns:
        The matching NormalizedRuleType enum.

    Raises:
        ValueError: If rule cannot be mapped to a valid NormalizedRuleType.
    """
    if isinstance(rule, NormalizedRuleType):
        return rule
    if not rule or not str(rule).strip():
        raise ValueError("Rule type cannot be empty.")
    clean = str(rule).strip().lower()
    if clean in _NORMALIZED_RULE_TYPE_LOOKUP:
        return _NORMALIZED_RULE_TYPE_LOOKUP[clean]
    raise ValueError(
        f"Invalid normalized rule type: '{rule}'. Expected one of: "
        f"{', '.join(r.value for r in NormalizedRuleType)}."
    )


def format_rule_parameters(params: dict[str, Any] | str | None) -> str:
    """Format rule parameters into a canonical sorted semicolon-delimited string."""
    if params is None:
        return ""
    if isinstance(params, str):
        return params.strip()
    if not isinstance(params, dict):
        return str(params).strip()
    items: list[str] = []
    for k, v in sorted(params.items()):
        clean_k = str(k).strip()
        if isinstance(v, bool):
            val_str = "true" if v else "false"
        elif v is None:
            val_str = ""
        else:
            val_str = str(v).strip()
        items.append(f"{clean_k}: {val_str}")
    return "; ".join(items)


_SLUGIFY_NON_WORD = re.compile(r"[^\w\s-]")
_SLUGIFY_HYPHENS = re.compile(r"[-\s]+")


def slugify(name: str) -> str:
    """Convert a name into a URL and GitHub-compatible lowercase hyphenated slug."""
    if not name or not name.strip():
        return ""
    cleaned = _SLUGIFY_NON_WORD.sub("", name.strip().lower())
    return _SLUGIFY_HYPHENS.sub("-", cleaned).strip("-")


@dataclass
class BranchPolicy:
    """Pure data container representing a discovered branch governance policy."""

    policy_scope: str
    target_ref_pattern: str
    normalized_rule_type: NormalizedRuleType
    rule_parameters: dict[str, Any] | str = field(default_factory=dict)
    bypass_team_slugs: list[str] = field(default_factory=list)
    origin_raw_policy: str = ""
    platform: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class TeamPermission:
    """Pure data container representing team permissions on an origin platform."""

    team_name: str
    member_count: int
    target_repo_or_project: str
    normalized_role: NormalizedRole
    origin_role: str
    team_slug: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class Pipeline:
    """Pure data container representing a discovered CI/CD pipeline or workflow."""

    name: str
    platform: str
    full_path: str
    url: str
    pipeline_type: PipelineType = PipelineType.UNKNOWN
    repository: Repository | None = None
    file_path: str | None = None
    branch: str | None = None
    triggers: list[PipelineTrigger] = field(default_factory=list)
    shared_components: list[str] = field(default_factory=list)
    execution_environment: list[str] = field(default_factory=list)
    last_build_timestamp: datetime | None = None
    last_status: str | None = None
    disabled: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def qualified_name(self) -> str:
        """Return an unambiguous fully-qualified pipeline identifier."""
        return self.full_path if self.full_path else self.name


@dataclass(frozen=True)
class AuthCredentials:
    """Pure data container for resolved authentication artifacts."""

    token: str | None = None
    username: str | None = None
    password: str | None = None
    client_cert: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


class ScraperError(Exception):
    """Base exception for repository scraper errors."""


class ScraperAuthenticationError(ScraperError):
    """Raised when authentication with the downstream platform fails."""


class ScraperConnectionError(ScraperError):
    """Raised when network or connection issues occur while contacting the platform."""


@runtime_checkable
class Authenticator(Protocol):
    """Protocol for resolving authentication credentials across VCS platforms."""

    def resolve_credentials(self) -> AuthCredentials:
        """Resolve and return credentials from config, environment, or keystore.

        Returns:
            AuthCredentials containing resolved authentication artifacts.

        Raises:
            ScraperAuthenticationError: If credentials cannot be resolved.
        """
        raise NotImplementedError


class TokenAuthenticator:
    """Authenticator providing a static token (PAT, bearer, or API key)."""

    def __init__(self, token: str) -> None:
        if not token or not token.strip():
            raise ValueError("Token cannot be empty.")
        self.token = token.strip()
        self._credentials = AuthCredentials(token=self.token)

    def resolve_credentials(self) -> AuthCredentials:
        """Return credentials with the configured token."""
        return self._credentials


class BasicAuthenticator:
    """Authenticator providing username and password credentials."""

    def __init__(self, username: str, password: str) -> None:
        if not username or not username.strip():
            raise ValueError("Username cannot be empty.")
        self.username = username.strip()
        self.password = password
        self._credentials = AuthCredentials(
            username=self.username, password=self.password
        )

    def resolve_credentials(self) -> AuthCredentials:
        """Return credentials with the configured username and password."""
        return self._credentials


@runtime_checkable
class Scraper(Protocol[T]):
    """High-level abstraction for all platform discovery scrapers."""

    def scrape(self) -> list[T]:
        """Discover and return domain entities from the downstream platform.

        Returns:
            A list of discovered domain entities, or an empty list if none found.

        Raises:
            ScraperError: If an error occurs communicating with downstream platform.
        """
        raise NotImplementedError


@runtime_checkable
class RepositoryScraper(Scraper[Repository], Protocol):
    """Protocol defining the repository scraper contract for VCS platforms."""

    def scrape(self) -> list[Repository]:
        """Discover and return repositories from the downstream platform.

        Returns:
            A list of discovered Repository instances, or an empty list if none found.

        Raises:
            ScraperError: If an error occurs communicating with downstream API.
        """
        raise NotImplementedError


@runtime_checkable
class PipelineScraper(Scraper[Pipeline], Protocol):
    """Protocol defining the pipeline scraper contract for CI/CD platforms."""

    scraper_type: str = "pipeline"
    target_type: str = "pipeline"

    def scrape(self) -> list[Pipeline]:
        """Discover and return CI/CD pipelines from the downstream platform.

        Returns:
            A list of discovered Pipeline instances, or an empty list if none found.

        Raises:
            ScraperError: If an error occurs communicating with downstream API.
        """
        raise NotImplementedError


@runtime_checkable
class TeamScraper(Scraper[TeamPermission], Protocol):
    """Protocol defining the team topology discovery scraper contract."""

    scraper_type: str = "team"
    target_type: str = "team"

    def scrape(self) -> list[TeamPermission]:
        """Discover and return team permission mappings from the downstream platform.

        Returns:
            A list of discovered TeamPermission instances, or empty list if none found.

        Raises:
            ScraperError: If an error occurs communicating with downstream API.
        """
        raise NotImplementedError


@runtime_checkable
class PolicyScraper(Scraper[BranchPolicy], Protocol):
    """Protocol defining the branch governance policy discovery contract."""

    scraper_type: str = "policy"
    target_type: str = "policy"

    def scrape(self) -> list[BranchPolicy]:
        """Discover and return branch policies from the downstream platform.

        Returns:
            A list of discovered BranchPolicy instances, or empty list if none found.

        Raises:
            ScraperError: If an error occurs communicating with downstream API.
        """
        raise NotImplementedError


_STACK_DISPLAY_NAMES: dict[Stack, str] = {
    Stack.JAVA: "Java",
    Stack.PYTHON: "Python",
    Stack.TYPESCRIPT: "TypeScript",
    Stack.C_SHARP: "C#",
    Stack.BASH: "Bash",
    Stack.DOTNET_CORE: ".NET Core",
    Stack.TERRAFORM: "Terraform",
}


def format_stacks(stacks: list[Any]) -> str:
    """Format a list of Stack enums or stack objects into a display string."""
    formatted = [
        _STACK_DISPLAY_NAMES.get(s, getattr(s, "value", str(s)).title())
        for s in stacks
        if getattr(s, "value", str(s)).lower() != "unknown"
    ]
    return ", ".join(formatted)


@runtime_checkable
class ScraperProvider(Protocol):
    """Contract for platform scraper registration and CLI argument binding."""

    @property
    def platform_name(self) -> str:
        """Unique identifier for the platform (e.g. 'svn', 'bitbucket')."""
        raise NotImplementedError

    @property
    def description(self) -> str:
        """Brief human-readable description for CLI help text."""
        raise NotImplementedError

    def configure_parser(self, parser: Any) -> None:
        """Configure platform-specific CLI arguments on the provided subparser."""
        raise NotImplementedError

    def create_scraper(self, args: Any) -> Scraper[Any]:
        """Instantiate a Scraper from parsed CLI arguments."""
        raise NotImplementedError


class ScraperRegistry:
    """Registry maintaining available VCS and CI/CD platform scraper providers."""

    def __init__(self) -> None:
        self._providers: dict[str, ScraperProvider] = {}

    def register(self, provider: ScraperProvider) -> None:
        """Register a platform scraper provider.

        Raises:
            ValueError: If provider or provider.platform_name is invalid.
        """
        if not provider or not getattr(provider, "platform_name", None):
            raise ValueError("Provider must define a non-empty 'platform_name'.")
        key = provider.platform_name.strip().lower()
        if not key:
            raise ValueError("Provider platform_name cannot be empty.")
        self._providers[key] = provider

    def get(self, platform_name: str) -> ScraperProvider:
        """Retrieve a registered provider by platform name.

        Raises:
            KeyError: If platform_name is not registered.
        """
        clean = (platform_name or "").strip().lower()
        if clean not in self._providers:
            available = ", ".join(sorted(self._providers.keys())) or "none"
            raise KeyError(
                f"Unsupported platform: '{platform_name}'. Available: {available}."
            )
        return self._providers[clean]

    def list_platforms(self) -> list[str]:
        """Return sorted list of registered platform names."""
        return sorted(self._providers.keys())


default_scraper_registry = ScraperRegistry()


__all__ = [
    "AuthCredentials",
    "Authenticator",
    "BasicAuthenticator",
    "BranchPolicy",
    "NormalizedRole",
    "NormalizedRuleType",
    "Pipeline",
    "PipelineScraper",
    "PipelineTrigger",
    "PipelineType",
    "PolicyScraper",
    "Repository",
    "RepositoryScraper",
    "Scraper",
    "ScraperAuthenticationError",
    "ScraperConnectionError",
    "ScraperError",
    "ScraperProvider",
    "ScraperRegistry",
    "Stack",
    "TeamPermission",
    "TeamScraper",
    "TokenAuthenticator",
    "default_scraper_registry",
    "format_rule_parameters",
    "format_stacks",
    "normalize_role",
    "normalize_rule_type",
    "slugify",
]
