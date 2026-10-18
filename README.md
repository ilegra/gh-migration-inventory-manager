# GitHub Migration Inventory Manager

A Python library for managing, querying, and orchestrating inventories of GitHub repositories and organization resources during migrations.

## Features

- **Isolated Environment**: Poetry with in-project virtual environment (`.venv`).
- **Standard Public Library Interface**: Clean `gh_migration_inventory_manager` package export with PEP 561 typing support (`py.typed`).
- **Comprehensive Quality Gates**: Formatted with Ruff, typed with Mypy, and secured with Gitleaks & Bandit.
- **Git Pre-commit Hook**: Scans every commit for secret leaks with Gitleaks before commits are finalized.
- **Automated CI/CD Workflows**:
  - `ci.yaml`: Runs full quality + security gates on PRs and merges to `main`.
  - `cd.yaml`: Bumps version, creates Git tags, builds `.whl` and `.tar.gz` packages, and publishes GitHub releases with artifacts.

---

## Installation & Setup

### Prerequisites

- Python `>= 3.10`
- [Poetry](https://python-poetry.org/) `>= 2.0`

### 1. Install Dependencies & Virtual Environment

```bash
# Clone the repository
git clone git@github.com:ilegra/gh-migration-inventory-manager.git
cd gh-migration-inventory-manager

# Install dependencies into local .venv
poetry install --with dev
```

### 2. Set Up Pre-Commit Hooks (including Secret Detection)

```bash
# Install git hooks
poetry run pre-commit install

# Test hooks across all files
poetry run pre-commit run --all-files
```

---

## Running Quality Checks Locally

```bash
# Linting & Formatting Check
poetry run ruff check src tests
poetry run ruff format --check src tests

# Static Type Checking
poetry run mypy src tests

# Security Scanning
poetry run bandit -r src
```

---

## Running Tests

```bash
# Unit Tests
poetry run pytest tests/unit -v

# Integration Tests
poetry run pytest tests/integration -v
```

---

## Building the Library Package

```bash
poetry build
```
This produces ready-to-distribute wheels and source archives in `dist/`:
- `dist/gh_migration_inventory_manager-<version>-py3-none-any.whl`
- `dist/gh_migration_inventory_manager-<version>.tar.gz`

---

## Usage as a Python Library

```python
from gh_migration_inventory_manager import (
    Credentials,
    Inventory,
    MigrationStatus,
    Spreadsheet,
)

# 1. Initialize credentials and spreadsheet client
credentials = Credentials(credentials_path="gcp-key.json")
spreadsheet = Spreadsheet(
    id="1Z0PAjv8KK6WBrS2356gtR3VE0ulD1-8LM4DPQiG-Nkk",
    credentials=credentials,
    sheet_name="Inventario",
)

# 2. Instantiate inventory manager
inventory = Inventory(spreadsheet=spreadsheet)

# 3. Update or create repository progress
record = inventory.update_progress(
    repository_name="api-gateway",
    status=MigrationStatus.ACTIVE_IN_BOTH,
)

print(f"Repository: {record.name}")
print(f"Status: {record.migration_status}")
print(f"Migration Month: {record.migration_month}")
```

---

## License

This project is licensed under the Apache 2.0 License.
