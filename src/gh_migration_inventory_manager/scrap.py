"""Repository scraper abstractions, authenticators, and domain models."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Protocol, runtime_checkable


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
class RepositoryScraper(Protocol):
    """Protocol defining the repository scraper contract for VCS platforms."""

    def scrape(self) -> list[Repository]:
        """Discover and return repositories from the downstream platform.

        Returns:
            A list of discovered Repository instances, or an empty list if none found.

        Raises:
            ScraperError: If an error occurs communicating with downstream API.
        """
        raise NotImplementedError


__all__ = [
    "AuthCredentials",
    "Authenticator",
    "BasicAuthenticator",
    "Repository",
    "RepositoryScraper",
    "ScraperAuthenticationError",
    "ScraperConnectionError",
    "ScraperError",
    "Stack",
    "TokenAuthenticator",
]
