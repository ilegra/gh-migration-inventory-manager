# Agent Development & Architecture Guidelines

This document outlines key architectural principles and testing conventions to adhere to across the codebase. These guidelines apply universally to any module and file.

---

## Coding Guidelines
### 1. Data Classes & Separation of Concerns

- **Pure Data Containers**: Data classes, models, and data transfer objects (DTOs) should represent state and hold data only. They must not contain business rules, validation workflows, or external serialization logic.
- **Service/Client Encapsulation**: Any formatting, parsing, padding, or protocol-specific serialization rules for external systems belong inside the responsible client/service (as private helper methods) or within dedicated converter components, never inside the data models.

### 2. Code Comments & Documentation

- **Minimal & Purposeful Comments**: Code comments should only be added in public methods or complex logic and is not reasonable only looking at the code.
- **Self-Documenting Code**: Avoid redundant comments that merely restate what readable code is doing.
- **Public API Documentation**: Use clear, concise docstrings on public classes, methods, and functions to describe parameters, return types, and exceptions.

### 3. Testing Principles

- **Unit tests Scope**: Unit tests should not access external dependencies like Google API either databases. These external dependencies should be mocked  and their behavior similated during test execution to allow code verification.
- **Integration test scope**: Integration tests must access external dependencies and validate the integration between external dependencies and current implemnation. Avoid paralel execution since it because the shared state.
- **Test Exclusively via Public Methods**: Tess must only exercise behaviors through public methods and public APIs. Never test, invoke, or inspect private methods (`_method`) in unit tests.

### 4. Project Structure & Platform Scraper Extensibility

- **Suggested Directory Layout**:
  - `src/gh_migration_inventory_manager/scrap.py`: Core domain models (`Repository`, `Stack`), protocols (`RepositoryScraper`, `Authenticator`), and unified exception hierarchy (`ScraperError`, `ScraperAuthenticationError`, `ScraperConnectionError`).
  - `src/gh_migration_inventory_manager/<platform>_scrap.py`: Platform-specific scrapers and clients (e.g., `svn_scrap.py`, and future additions such as `gitlab_scrap.py`, `bitbucket_scrap.py`, `azure_devops_scrap.py`).
  - `src/gh_migration_inventory_manager/inventory.py` & `google.py`: Inventory coordination, domain status tracking, and spreadsheet backend integrations.
  - `src/gh_migration_inventory_manager/__init__.py`: Unified public package exports.
  - `tests/unit/test_<platform>_scraper.py` & `tests/integration/test_<platform>_scraper.py`: Dedicated unit and integration test suites mirroring each platform scraper.

- **Extending Platform Scrapers**:
  To add support for scraping repositories from a new platform (e.g., GitLab, Bitbucket, Azure DevOps):
  1. **Implement `RepositoryScraper` Protocol**: Define `<Platform>RepositoryScraper` implementing the `RepositoryScraper` protocol (`scrape() -> list[Repository]`).
  2. **Decouple Platform Client via Protocol**: Define a `@runtime_checkable` `<Platform>Client(Protocol)` with the required communication operations (e.g., listing projects, fetching file trees, retrieving commit timestamps). Implement a default client (e.g., `<Platform>RestClient`) and inject it into `<Platform>RepositoryScraper.__init__(..., client: <Platform>Client | None = None)`. This enables clean dependency injection and fast unit test mocking without monkeypatching.
  3. **Adhere to `Authenticator` Abstraction**: Reuse `TokenAuthenticator` or `BasicAuthenticator` (from `scrap.py`), or implement a new authenticator conforming to `Authenticator(Protocol)` if the platform requires custom credential resolution.
  4. **Pure Platform Data Containers**: Model raw platform entries or responses as frozen dataclasses (e.g., `<Platform>Entry`), strictly separated from business logic.
  5. **Map to Unified `Repository` & `Stack`**: Convert platform entries into standard `Repository` instances. Map file names/extensions to `Stack` enum items via private helper methods within the scraper (e.g., `_detect_stacks`).
  6. **Normalize Error Handling**: Trap low-level network/HTTP/SDK errors and wrap them into `ScraperError`, `ScraperAuthenticationError`, or `ScraperConnectionError`.
  7. **Public Package Export**: Export the new scraper, client protocol, default client, and relevant models in `__init__.py`.
  8. **Comprehensive Testing**: Provide unit tests in `tests/unit/test_<platform>_scraper.py` exercising scraper workflows using a mock client conforming to `<Platform>Client`, and add integration tests under `tests/integration/` where live platform verification is required.

---
