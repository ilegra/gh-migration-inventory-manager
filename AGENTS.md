# Agent Development & Architecture Guidelines

This document outlines key architectural principles and testing conventions to adhere to across the codebase. These guidelines apply universally to any module and file.

---

## Coding Guidelines

### 1. Project Structure & Platform Scraper Architecture

```
src/gh_migration_inventory_manager/
├── scrap.py                # Canonical domain models (Repository, Stack) & contracts
├── <platform>_scrap.py     # Platform discovery adapters & transport clients (e.g. svn_scrap.py)
├── inventory.py            # Migration lifecycle orchestration & progress coordination
└── google.py               # Inventory persistence backend (Google Sheets)
tests/
├── unit/                   # Fast, offline validation via client dependency injection
└── integration/            # End-to-end verification against external environments
```

- **Core Architectural Strategy**:
  - **Decoupled Source Discovery**: Scrapers act strictly as discovery adapters. They normalize external platform structures into standard repository instances without awareness of the spreadsheet or migration tracking logic.
  - **Two-Tier Platform Design**: Each platform module separates raw communication from domain parsing:
    - *Transport Client*: Handles network protocol operations, raw payload retrieval, and authentication with the remote platform.
    - *Platform Scraper*: Traverses platform hierarchies, resolves structural layouts (e.g., standard vs multi-project), detects technology stacks, and normalizes repositories.
  - **Hierarchy Flattening**: External platforms organize code under diverse grouping structures (e.g., SVN standard trunks vs multi-project folders, GitLab subgroups, Bitbucket projects). The platform scraper is responsible for traversing and flattening these hierarchies into discrete, trackable migration units.
  - **Injectable Transports**: Scrapers must receive their transport client through dependency injection to allow comprehensive offline unit testing without live network services.

### 2. Data Classes & Separation of Concerns

- **Pure Data Containers**: Data classes, models, and data transfer objects (DTOs) should represent state and hold data only. They must not contain business rules, validation workflows, or external serialization logic.
- **Service/Client Encapsulation**: Any formatting, parsing, padding, or protocol-specific serialization rules for external systems belong inside the responsible client/service (as private helper methods) or within dedicated converter components, never inside the data models.

### 3. Code Comments & Documentation

- **Minimal & Purposeful Comments**: Code comments should only be added in public methods or complex logic and is not reasonable only looking at the code.
- **Self-Documenting Code**: Avoid redundant comments that merely restate what readable code is doing.
- **Public API Documentation**: Use clear, concise docstrings on public classes, methods, and functions to describe parameters, return types, and exceptions.

### 4. Testing Principles

- **Unit tests Scope**: Unit tests should not access external dependencies like Google API either databases. These external dependencies should be mocked  and their behavior similated during test execution to allow code verification.
- **Integration test scope**: Integration tests must access external dependencies and validate the integration between external dependencies and current implemnation. Avoid paralel execution since it because the shared state.
- **Test Exclusively via Public Methods**: Tess must only exercise behaviors through public methods and public APIs. Never test, invoke, or inspect private methods (`_method`) in unit tests.

---
