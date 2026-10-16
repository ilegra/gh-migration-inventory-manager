"""Unit tests for repository scraper abstraction, authenticators, and models."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from gh_migration_inventory_manager import (
    AuthCredentials,
    Authenticator,
    Repository,
    RepositoryScraper,
    ScraperAuthenticationError,
    ScraperConnectionError,
    ScraperError,
    Stack,
    TokenAuthenticator,
)


class TestStackEnum:
    """Tests for Stack enum."""

    def test_stack_members_and_values(self) -> None:
        assert Stack.JAVA.value == "java"
        assert Stack.PYTHON.value == "python"
        assert Stack.TYPESCRIPT.value == "typescript"
        assert Stack.C_SHARP.value == "csharp"
        assert Stack.BASH.value == "bash"
        assert Stack.DOTNET_CORE.value == "dotnet_core"
        assert Stack.TERRAFORM.value == "terraform"
        assert Stack.UNKNOWN.value == "unknown"

    def test_stack_invalid_value_raises(self) -> None:
        with pytest.raises(ValueError):
            Stack("non_existent_stack")


class TestRepositoryModel:
    """Tests for Repository pure data container."""

    def test_repository_initialization_with_defaults(self) -> None:
        repo = Repository(name="org/my-service")
        assert repo.name == "org/my-service"
        assert repo.last_commit is None
        assert repo.stacks == []

    def test_repository_initialization_with_all_fields(self) -> None:
        commit_time = datetime(2026, 10, 16, 12, 0, 0, tzinfo=timezone.utc)
        stacks = [Stack.PYTHON, Stack.TERRAFORM]
        repo = Repository(name="org/infra", last_commit=commit_time, stacks=stacks)

        assert repo.name == "org/infra"
        assert repo.last_commit == commit_time
        assert repo.stacks == [Stack.PYTHON, Stack.TERRAFORM]


class TestAuthCredentialsModel:
    """Tests for AuthCredentials pure data container."""

    def test_auth_credentials_defaults(self) -> None:
        creds = AuthCredentials()
        assert creds.token is None
        assert creds.username is None
        assert creds.password is None
        assert creds.client_cert is None
        assert creds.extra == {}

    def test_auth_credentials_full_fields(self) -> None:
        creds = AuthCredentials(
            token="secret-token",
            username="bot-user",
            password="bot-password",
            client_cert="/path/to/cert.pem",
            extra={"tenant_id": "azure-tenant-123"},
        )
        assert creds.token == "secret-token"
        assert creds.username == "bot-user"
        assert creds.password == "bot-password"
        assert creds.client_cert == "/path/to/cert.pem"
        assert creds.extra == {"tenant_id": "azure-tenant-123"}


class TestScraperExceptions:
    """Tests for scraper exception hierarchy."""

    def test_exception_inheritance(self) -> None:
        assert issubclass(ScraperAuthenticationError, ScraperError)
        assert issubclass(ScraperConnectionError, ScraperError)
        assert issubclass(ScraperError, Exception)

    def test_catching_base_scraper_error(self) -> None:
        with pytest.raises(ScraperError) as exc_info:
            raise ScraperAuthenticationError("Invalid PAT token")
        assert "Invalid PAT token" in str(exc_info.value)

        with pytest.raises(ScraperError) as exc_info:
            raise ScraperConnectionError("Connection timed out")
        assert "Connection timed out" in str(exc_info.value)


class TestTokenAuthenticator:
    """Tests for TokenAuthenticator and Authenticator protocol."""

    def test_token_authenticator_success(self) -> None:
        auth = TokenAuthenticator("pat-12345")
        assert isinstance(auth, Authenticator)
        creds = auth.resolve_credentials()
        assert creds.token == "pat-12345"

    def test_token_authenticator_empty_raises(self) -> None:
        with pytest.raises(ValueError, match="Token cannot be empty"):
            TokenAuthenticator("   ")

    def test_authenticator_protocol_conformance(self) -> None:
        class CustomTokenAuth:
            def resolve_credentials(self) -> AuthCredentials:
                return AuthCredentials(token="custom-token")

        class IncompleteAuth:
            pass

        assert isinstance(CustomTokenAuth(), Authenticator)
        assert not isinstance(IncompleteAuth(), Authenticator)


class TestRepositoryScraperProtocol:
    """Tests for RepositoryScraper protocol."""

    def test_protocol_runtime_conformance(self) -> None:
        class ValidScraper:
            def scrape(self) -> list[Repository]:
                return []

        class IncompleteScraper:
            pass

        assert isinstance(ValidScraper(), RepositoryScraper)
        assert not isinstance(IncompleteScraper(), RepositoryScraper)


class TestMultiPlatformScrapersWithAuth:
    """Simulate platform-specific scrapers using Authenticator."""

    def test_ado_scraper_with_token_authenticator(self) -> None:
        class AdoRepositoryScraper:
            def __init__(
                self,
                org_url: str,
                project: str,
                auth: Authenticator,
            ) -> None:
                self.org_url = org_url
                self.project = project
                self.auth = auth

            def scrape(self) -> list[Repository]:
                creds = self.auth.resolve_credentials()
                if not creds.token:
                    raise ScraperAuthenticationError("ADO requires a valid token.")
                return [Repository(name=f"{self.project}/core-api")]

        auth = TokenAuthenticator("mock-pat-token")
        scraper = AdoRepositoryScraper(
            "https://dev.azure.com/myorg", "ProjectX", auth=auth
        )
        assert isinstance(scraper, RepositoryScraper)

        repos = scraper.scrape()
        assert len(repos) == 1
        assert repos[0].name == "ProjectX/core-api"

    def test_platform_scraper_returns_empty_when_no_repo_found(self) -> None:
        class EmptyPlatformScraper:
            def __init__(self, auth: Authenticator) -> None:
                self.auth = auth

            def scrape(self) -> list[Repository]:
                self.auth.resolve_credentials()
                return []

        scraper = EmptyPlatformScraper(auth=TokenAuthenticator("any-token"))
        assert isinstance(scraper, RepositoryScraper)
        assert scraper.scrape() == []

    def test_scraper_raises_auth_error_when_token_fails(self) -> None:
        class FailingAuth(Authenticator):
            def resolve_credentials(self) -> AuthCredentials:
                raise ScraperAuthenticationError("Token expired or revoked.")

        class AdoRepositoryScraper:
            def __init__(self, auth: Authenticator) -> None:
                self.auth = auth

            def scrape(self) -> list[Repository]:
                self.auth.resolve_credentials()
                return []

        scraper = AdoRepositoryScraper(auth=FailingAuth())
        with pytest.raises(ScraperAuthenticationError) as exc_info:
            scraper.scrape()
        assert "Token expired or revoked." in str(exc_info.value)
