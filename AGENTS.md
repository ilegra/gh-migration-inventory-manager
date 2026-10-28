# Agent Development & Architecture Guidelines

This document outlines key architectural principles and testing conventions to adhere to across the codebase. These guidelines apply universally to any module and file.

---

## Coding Guidelines

### 1. Project Structure & Platform Scraper Architecture

```
src/gh_migration_inventory_manager/
├── scrap.py                # Canonical domain models (Repository, Pipeline, Stack) & protocols (Scraper, ScraperProvider)
├── converter.py            # Domain-to-inventory converters (RepositoryConverter, PipelineConverter)
├── <platform>_scrap.py     # Platform discovery adapters & transport clients (svn_scrap.py, jenkins_scrap.py)
├── inventory.py            # Migration lifecycle orchestration (Inventory, PipelineInventory)
├── google.py               # Inventory persistence backend (Google Sheets: Inventario & Workflows tabs)
└── cli.py                  # Unified CLI entrypoint and platform subcommands
tests/
├── unit/                   # Fast, offline validation via client dependency injection
└── integration/            # End-to-end verification against external environments (Docker SVN/Jenkins, Google Sheets)
```

- **Core Architectural Strategy**:
  - **Decoupled Source Discovery**: Scrapers act strictly as discovery adapters. They normalize external platform structures into standard domain instances (`Repository` for VCS, `Pipeline` for CI/CD) without awareness of the spreadsheet or migration tracking logic.
  - **Two-Tier Platform Design**: Each platform module separates raw communication from domain parsing:
    - *Transport Client*: Handles network protocol operations, raw payload retrieval, standard library retries/exponential backoff (`urllib3.util.Retry`), and authentication with the remote platform.
    - *Platform Scraper*: Traverses platform hierarchies (SVN repository trees or Jenkins folder hierarchies), resolves structural layouts or pipeline types (declarative, scripted, multibranch, freestyle), extracts SCM repository linkages, detects technology stacks, and normalizes items.
  - **Dual Discovery Abstractions**:
    - `RepositoryScraper(Scraper[Repository])`: Discovers source repositories from VCS platforms (SVN, GitLab, Bitbucket).
    - `PipelineScraper(Scraper[Pipeline])`: Discovers CI/CD workflows and pipelines from orchestration platforms (Jenkins, GitLab CI, GitHub Actions).
  - **Hierarchy Flattening**: External platforms organize code and jobs under diverse grouping structures (e.g., SVN standard trunks vs multi-project folders, Jenkins nested folders and organization folders, GitLab subgroups). The platform scraper is responsible for traversing and flattening these hierarchies into discrete, trackable migration units.
  - **Injectable Transports**: Scrapers must receive their transport client through dependency injection to allow comprehensive offline unit testing without live network services.

### 2. Data Classes & Separation of Concerns

- **Pure Data Containers**: Data classes, models, and data transfer objects (DTOs) should represent state and hold data only. They must not contain business rules, validation workflows, or external serialization logic.
- **Service/Client Encapsulation**: Any formatting, parsing, padding, or protocol-specific serialization rules for external systems belong inside the responsible client/service (as private helper methods) or within dedicated converter components, never inside the data models.

### 3. Code Comments & Documentation

- **Minimal & Purposeful Comments**: Code comments should only be added in public methods or complex logic and is not reasonable only looking at the code.
- **Self-Documenting Code**: Avoid redundant comments that merely restate what readable code is doing.
- **Public API Documentation**: Use clear, concise docstrings on public classes, methods, and functions to describe parameters, return types, and exceptions.

### 4. Testing Principles

- **Unit Test Scope**: Unit tests should not access external dependencies like Google APIs or databases. These external dependencies must be mocked and their behavior simulated during test execution to allow fast, deterministic validation.
- **Integration Test Scope**: Integration tests must access external dependencies and validate end-to-end integration between external systems and the current implementation. Avoid parallel execution when tests share state or external resources.
- **Test Exclusively via Public Methods**: Tests must only exercise behaviors through public methods and public APIs. Never test, invoke, or inspect private methods (`_method`) in unit tests.
- **Scenario Naming ("Given..When..Then" Pattern)**: Test function and method names must follow the `test_given_<precondition>_when_<action>_then_<outcome>` pattern to clearly document test intent and expected behavior.
  - *Examples*:
    - `test_given_unregistered_repository_when_updating_progress_then_creates_new_record`
    - `test_given_empty_repository_url_when_scraping_then_raises_value_error`
    - `test_given_network_timeout_when_listing_remote_directory_then_raises_scraper_connection_error`
    - `test_given_existing_repository_when_registering_then_preserves_status_and_migration_dates`
- **Do Not Unit Test Pure Dataclasses**: Dataclasses, models, and DTOs that serve as pure data containers without business logic must not be unit tested. Testing attribute assignment or default values tests Python's runtime rather than business logic. Only test data structures if they encapsulate behavior, computed properties (`@property` with logic), custom validation, or state transitions.
- **Core Test Scenario Categories**: A comprehensive test suite for any component should systematically cover three scenario types:
  - *Valid Inputs (Happy Path)*: Standard operations with typical valid arguments and boundary values, asserting on expected return values and state mutations.
  - *Invalid Inputs (Negative Scenarios)*: Missing, null, empty, or malformed parameters, verifying that proper domain exceptions (`ValueError`, `TypeError`) or error outcomes are raised.
  - *Dependency Failures (Failure Modes)*: Simulated collaborator failures (e.g., network disconnects, HTTP 401/403/500 statuses, socket timeouts), verifying proper error translation, logging, or exception propagation.

---
