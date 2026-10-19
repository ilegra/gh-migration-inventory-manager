# GitHub Migration Inventory Manager

A Python library for managing, querying, and orchestrating inventories of GitHub repositories and organization resources during migrations.

## Features

- **Isolated Environment**: Poetry with in-project virtual environment (`.venv`).
- **Standard Public Library Interface**: Clean `gh_migration_inventory_manager` package export with PEP 561 typing support (`py.typed`).
- **Pluggable Platform Scrapers**: Modular discovery adapters (`ScraperRegistry` & `ScraperProvider`) for platforms like Subversion (SVN) with automatic layout detection and technology stack analysis.
- **Unified CLI (`gh-migration-inventory`)**: Discover remote repositories across VCS platforms, preview with table or JSON output, and synchronize directly with Google Sheets.
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

## Supported Platform Scrapers

The library uses a decoupled provider architecture (`ScraperProvider` & `ScraperRegistry`) where scrapers act as discovery adapters. They normalize external platform structures into standard repository representations (`Repository`) and detect technology stacks, independent of the inventory tracking backend.

| Platform | Scraper Identifier | Status | Layout Detection | Tech Stack Detection |
| :--- | :--- | :--- | :--- | :--- |
| **Subversion (SVN)** | `svn` | Built-in | `auto`, `standard`, `multi_project`, `flat`, `collection` | Java, Python, TypeScript, C#/.NET Core, Bash, Terraform |
| **Git Platforms** *(GitLab, Bitbucket, Azure DevOps)* | Pluggable | Architecture-ready | Native branches & subgroups | Repository file tree analysis |

---

## Command-Line Interface (CLI)

The package provides the `gh-migration-inventory` command-line executable (or via `poetry run gh-migration-inventory`).

### Global Synopsis

```bash
gh-migration-inventory scrape <platform> [OPTIONS]
```

### Scraping Subversion (SVN) Repositories

Scrape repositories from a remote SVN server via WebDAV/HTTP protocol, identify repository structure and technology stacks, and optionally persist records directly into Google Sheets.

```bash
gh-migration-inventory scrape svn --url <SVN_URL> [OPTIONS]
```

#### SVN Scraper Options

| Option | Environment Variable | Default | Description |
| :--- | :--- | :--- | :--- |
| `--url <URL>` | — | *(Required)* | SVN repository or server URL (`http://` or `https://`) |
| `--layout <TYPE>` | — | `auto` | SVN repository layout (`auto`, `standard`, `multi_project`, `flat`, `collection`) |
| `--username <USER>` | `SVN_USERNAME` | — | HTTP Basic authentication username |
| `--password <PASS>` | `SVN_PASSWORD` | — | HTTP Basic authentication password |
| `--token <TOKEN>` | `SVN_TOKEN` | — | HTTP Bearer/PAT token |
| `--organization <ORG>` | — | — | Organization name tag for discovered repositories |
| `--project <PROJECT>` | — | — | Project name override for discovered repositories |

#### Google Sheets Synchronization Options

| Option | Environment Variable | Default | Description |
| :--- | :--- | :--- | :--- |
| `--spreadsheet-id <ID>` | `SPREADSHEET_ID`, `GOOGLE_SPREADSHEET_ID` | — | Target Google Spreadsheet ID (mandatory for sync) |
| `--credentials-path <PATH>` | `GCP_KEY_PATH`, `GOOGLE_APPLICATION_CREDENTIALS` | — | Path to GCP Service Account JSON key |
| `--sheet-name <NAME>` | `INVENTORY_SHEET_NAME` | `Inventario` | Google Sheets worksheet/tab name |
| `--initial-status <STATUS>` | — | `waitting` | Initial status for newly discovered repositories (`waitting`, `waiting`, `active_in_both`, `migrated`, `skipped`) |

#### SVN Repository Layouts

- **`auto`** *(default)*: Dynamically inspects the repository root. If `trunk/`, `branches/`, or `tags/` are present, it processes it as `standard`. If subdirectories contain `trunk/`, it processes it as `multi_project`. Otherwise, it falls back to `flat`.
- **`standard`**: Classic single-project SVN structure containing `/trunk`, `/branches`, and `/tags`.
- **`multi_project`**: Repository holding multiple projects, where each subfolder has its own `/trunk`, `/branches`, and `/tags`.
- **`flat`**: Repository without branch folders; the directory tree represents source code directly.
- **`collection`**: Top-level server directory hosting multiple distinct SVN repositories.

#### CLI Usage Examples

##### 1. Synchronize Directly to Google Sheets

```bash
poetry run gh-migration-inventory scrape svn \
  --url https://svn.internal.company.com/svn/core-apps \
  --username "$SVN_USERNAME" \
  --password "$SVN_PASSWORD" \
  --organization "Engineering" \
  --spreadsheet-id "1Z0PAjv8KK6WBrS2356gtR3VE0ulD1-8LM4DPQiG-Nkk" \
  --credentials-path "./gcp-service-account.json" \
  --sheet-name "Inventario"
```

Output:
```text
https://docs.google.com/spreadsheets/d/1Z0PAjv8KK6WBrS2356gtR3VE0ulD1-8LM4DPQiG-Nkk
```

##### 2. Headless Execution Using Environment Variables

You can supply credentials and IDs via environment variables, ideal for CI/CD pipelines and automated cron jobs:

```bash
export SVN_USERNAME="service-account"
export SVN_PASSWORD="service-password"
export SPREADSHEET_ID="1Z0PAjv8KK6WBrS2356gtR3VE0ulD1-8LM4DPQiG-Nkk"
export GCP_KEY_PATH="/secrets/gcp-sa.json"

poetry run gh-migration-inventory scrape svn \
  --url https://svn.internal.company.com/svn/core-apps \
  --organization "Engineering"
```

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
