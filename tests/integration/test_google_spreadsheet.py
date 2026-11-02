"""Integration tests verifying Google Sheets physical persistence, schema, and API cell values across all tabs."""

from __future__ import annotations

import uuid

import pytest

from gh_migration_inventory_manager.google import (
    INVENTORY_HEADERS,
    POLICIES_HEADERS,
    TOPOLOGY_HEADERS,
    WORKFLOW_HEADERS,
    Credentials,
    Spreadsheet,
)
from gh_migration_inventory_manager.inventory import (
    Inventory,
    MigrationStatus,
    PipelineInventory,
    PipelineRecord,
    PolicyInventory,
    PolicyRecord,
    TeamInventory,
    TeamRecord,
)

_SPREADSHEET_ID = "1Z0PAjv8KK6WBrS2356gtR3VE0ulD1-8LM4DPQiG-Nkk"


@pytest.fixture(scope="module")
def live_spreadsheet(gcp_credentials_path: str) -> Spreadsheet:
    """Fixture providing an authenticated Spreadsheet client ensuring all tabs exist."""
    credentials = Credentials(credentials_path=gcp_credentials_path)
    sheet = Spreadsheet(
        id=_SPREADSHEET_ID,
        credentials=credentials,
        sheet_name="Inventario",
    )
    # Ensure all tabs and columns are present in the spreadsheet template before tests run
    sheet.ensure_all_tabs()
    return sheet


def test_given_spreadsheet_template_when_ensuring_all_tabs_then_all_tabs_and_columns_are_present_in_google_sheets(
    live_spreadsheet: Spreadsheet,
) -> None:
    """Validate that all 4 tabs exist and possess all relational schema columns using Google API."""
    service = live_spreadsheet._service

    # 1. Tab: Inventario (8 columns, A:H)
    res_inv = live_spreadsheet._execute(
        service.spreadsheets()
        .values()
        .get(spreadsheetId=_SPREADSHEET_ID, range="Inventario!A1:H1")
    )
    inv_headers = res_inv.get("values", [[]])[0]
    assert len(inv_headers) >= 8
    assert inv_headers[0] == INVENTORY_HEADERS[0]  # Projeto
    assert inv_headers[1] == INVENTORY_HEADERS[1]  # Nome do Repositório

    # 2. Tab: Workflows (14 columns, A:N)
    res_wf = live_spreadsheet._execute(
        service.spreadsheets()
        .values()
        .get(spreadsheetId=_SPREADSHEET_ID, range="Workflows!A1:N1")
    )
    wf_headers = res_wf.get("values", [[]])[0]
    assert len(wf_headers) == 14
    assert wf_headers == WORKFLOW_HEADERS
    assert wf_headers[12] == "Projeto"
    assert wf_headers[13] == "Repositório"

    # 3. Tab: Topology (9 columns, A:I)
    res_topo = live_spreadsheet._execute(
        service.spreadsheets()
        .values()
        .get(spreadsheetId=_SPREADSHEET_ID, range="Topology!A1:I1")
    )
    topo_headers = res_topo.get("values", [[]])[0]
    assert len(topo_headers) == 9
    assert topo_headers == TOPOLOGY_HEADERS
    assert topo_headers[7] == "Projeto"
    assert topo_headers[8] == "Repositório"

    # 4. Tab: Policies (9 columns, A:I)
    res_pol = live_spreadsheet._execute(
        service.spreadsheets()
        .values()
        .get(spreadsheetId=_SPREADSHEET_ID, range="Policies!A1:I1")
    )
    pol_headers = res_pol.get("values", [[]])[0]
    assert len(pol_headers) == 9
    assert pol_headers == POLICIES_HEADERS
    assert pol_headers[7] == "Projeto"
    assert pol_headers[8] == "Repositório"


def test_given_inventario_tab_when_writing_record_then_google_api_verifies_cells_and_columns(
    live_spreadsheet: Spreadsheet,
) -> None:
    """Validate writing to 'Inventario' tab and assert cell contents directly using Google API."""
    service = live_spreadsheet._service
    inv_sheet = Spreadsheet(
        id=_SPREADSHEET_ID,
        credentials=live_spreadsheet.credentials,
        sheet_name="Inventario",
    )
    inventory = Inventory(spreadsheet=inv_sheet)

    canary_id = f"test-canary-inv-{uuid.uuid4().hex[:8]}"
    canary_proj = "test-project-inv"
    created_row_index: int | None = None

    try:
        record = inventory.update_progress(
            canary_id, MigrationStatus.ACTIVE_IN_BOTH, project=canary_proj
        )
        assert record.name == canary_id
        assert record.project == canary_proj

        found_rows = inv_sheet.find_by("name", canary_id)
        assert len(found_rows) == 1
        created_row_index = found_rows[0].row_index

        # Direct Google API assertion on physical cells
        res = live_spreadsheet._execute(
            service.spreadsheets()
            .values()
            .get(
                spreadsheetId=_SPREADSHEET_ID,
                range=f"Inventario!A{created_row_index}:H{created_row_index}",
            )
        )
        row_cells = res.get("values", [[]])[0]
        assert len(row_cells) >= 2
        assert row_cells[0] == canary_proj
        assert row_cells[1] == canary_id
    finally:
        if created_row_index is not None:
            inv_sheet.clear_row(created_row_index)


def test_given_workflows_tab_when_writing_pipeline_then_google_api_verifies_fourteen_columns(
    live_spreadsheet: Spreadsheet,
) -> None:
    """Validate writing to 'Workflows' tab and assert all 14 cells including project/repo directly using Google API."""
    service = live_spreadsheet._service
    wf_sheet = Spreadsheet(
        id=_SPREADSHEET_ID,
        credentials=live_spreadsheet.credentials,
        sheet_name="Workflows",
    )
    pipe_inv = PipelineInventory(spreadsheet=wf_sheet)

    canary_wf = f"deploy/test-canary-wf-{uuid.uuid4().hex[:8]}"
    canary_proj = "test-project-wf"
    canary_repo = "test-repo-wf"
    created_row_index: int | None = None

    try:
        record = PipelineRecord(
            workflow_id=canary_wf,
            platform="jenkins",
            pipeline_type="declarative",
            repository_association=f"{canary_proj}/{canary_repo}",
            repository_url=f"https://github.com/{canary_proj}/{canary_repo}.git",
            file_path="Jenkinsfile",
            shared_components="pipeline-lib",
            execution_environment="docker",
            branch="main",
            last_status="SUCCESS",
            migration_status=MigrationStatus.ACTIVE_IN_BOTH,
            project=canary_proj,
            repository=canary_repo,
        )
        saved = pipe_inv.register_record(record)
        assert saved.workflow_id == canary_wf

        found_rows = wf_sheet.find_by("workflow_id", canary_wf)
        assert len(found_rows) == 1
        created_row_index = found_rows[0].row_index

        # Direct Google API assertion on physical cells
        res = live_spreadsheet._execute(
            service.spreadsheets()
            .values()
            .get(
                spreadsheetId=_SPREADSHEET_ID,
                range=f"Workflows!A{created_row_index}:N{created_row_index}",
            )
        )
        row_cells = res.get("values", [[]])[0]
        assert len(row_cells) == 14
        assert row_cells[0] == canary_wf
        assert row_cells[1] == "jenkins"
        assert row_cells[2] == "declarative"
        assert row_cells[3] == f"{canary_proj}/{canary_repo}"
        assert row_cells[12] == canary_proj
        assert row_cells[13] == canary_repo

        # Verify query API by project and repository
        by_proj = pipe_inv.get_pipelines_for_project(canary_proj)
        assert any(p.workflow_id == canary_wf for p in by_proj)
        by_repo = pipe_inv.get_pipelines_for_repository(canary_proj, canary_repo)
        assert any(p.workflow_id == canary_wf for p in by_repo)
    finally:
        if created_row_index is not None:
            wf_sheet.clear_row(created_row_index)


def test_given_topology_tab_when_writing_team_then_google_api_verifies_nine_columns(
    live_spreadsheet: Spreadsheet,
) -> None:
    """Validate writing to 'Topology' tab and assert all 9 cells including project/repo directly using Google API."""
    service = live_spreadsheet._service
    topo_sheet = Spreadsheet(
        id=_SPREADSHEET_ID,
        credentials=live_spreadsheet.credentials,
        sheet_name="Topology",
    )
    team_inv = TeamInventory(spreadsheet=topo_sheet)

    canary_slug = f"test-team-{uuid.uuid4().hex[:8]}"
    canary_proj = "test-project-topo"
    canary_repo = "test-repo-topo"
    created_row_index: int | None = None

    try:
        record = TeamRecord(
            team_name="Canary Squad",
            team_slug=canary_slug,
            member_count=7,
            target_repo_or_project=f"repo:{canary_proj}/{canary_repo}",
            normalized_role="Write",
            origin_role="push",
            migration_status=MigrationStatus.WAITTING,
            project=canary_proj,
            repository=canary_repo,
        )
        saved = team_inv.register_record(record)
        assert saved.team_slug == canary_slug

        found_rows = topo_sheet.find_by("team_slug", canary_slug)
        assert len(found_rows) == 1
        created_row_index = found_rows[0].row_index

        # Direct Google API assertion on physical cells
        res = live_spreadsheet._execute(
            service.spreadsheets()
            .values()
            .get(
                spreadsheetId=_SPREADSHEET_ID,
                range=f"Topology!A{created_row_index}:I{created_row_index}",
            )
        )
        row_cells = res.get("values", [[]])[0]
        assert len(row_cells) == 9
        assert row_cells[0] == "Canary Squad"
        assert row_cells[1] == canary_slug
        assert row_cells[2] == "7"
        assert row_cells[3] == f"repo:{canary_proj}/{canary_repo}"
        assert row_cells[4] == "Write"
        assert row_cells[5] == "push"
        assert row_cells[6] == "Aguardando Migração"
        assert row_cells[7] == canary_proj
        assert row_cells[8] == canary_repo

        # Verify query API
        by_proj = team_inv.get_teams_for_project(canary_proj)
        assert any(t.team_slug == canary_slug for t in by_proj)
        by_repo = team_inv.get_teams_for_repository(canary_proj, canary_repo)
        assert any(t.team_slug == canary_slug for t in by_repo)
    finally:
        if created_row_index is not None:
            topo_sheet.clear_row(created_row_index)


def test_given_policies_tab_when_writing_policy_then_google_api_verifies_nine_columns(
    live_spreadsheet: Spreadsheet,
) -> None:
    """Validate writing to 'Policies' tab and assert all 9 cells including project/repo directly using Google API."""
    service = live_spreadsheet._service
    pol_sheet = Spreadsheet(
        id=_SPREADSHEET_ID,
        credentials=live_spreadsheet.credentials,
        sheet_name="Policies",
    )
    pol_inv = PolicyInventory(spreadsheet=pol_sheet)

    canary_id = uuid.uuid4().hex[:8]
    canary_proj = f"test-project-{canary_id}"
    canary_repo = f"test-repo-{canary_id}"
    canary_scope = f"repo:{canary_proj}/{canary_repo}"
    created_row_index: int | None = None

    try:
        record = PolicyRecord(
            policy_scope=canary_scope,
            target_ref_pattern="refs/heads/main",
            normalized_rule_type="RequirePullRequest",
            rule_parameters="min_approvals: 2",
            bypass_team_slugs="release-leads",
            origin_raw_policy='{"id": 999}',
            migration_status=MigrationStatus.WAITTING,
            project=canary_proj,
            repository=canary_repo,
        )
        saved = pol_inv.register_record(record)
        assert saved.policy_scope == canary_scope

        found_rows = pol_sheet.find_by("policy_scope", canary_scope)
        assert len(found_rows) == 1
        created_row_index = found_rows[0].row_index

        # Direct Google API assertion on physical cells
        res = live_spreadsheet._execute(
            service.spreadsheets()
            .values()
            .get(
                spreadsheetId=_SPREADSHEET_ID,
                range=f"Policies!A{created_row_index}:I{created_row_index}",
            )
        )
        row_cells = res.get("values", [[]])[0]
        assert len(row_cells) == 9
        assert row_cells[0] == canary_scope
        assert row_cells[1] == "refs/heads/main"
        assert row_cells[2] == "RequirePullRequest"
        assert row_cells[3] == "min_approvals: 2"
        assert row_cells[4] == "release-leads"
        assert row_cells[5] == '{"id": 999}'
        assert row_cells[6] == "Aguardando Migração"
        assert row_cells[7] == canary_proj
        assert row_cells[8] == canary_repo

        # Verify query API
        by_repo = pol_inv.get_policies_for_repository(canary_proj, canary_repo)
        assert any(p.policy_scope == canary_scope for p in by_repo)
    finally:
        if created_row_index is not None:
            pol_sheet.clear_row(created_row_index)


def test_given_all_tabs_when_querying_get_all_rows_then_data_matches_direct_google_api_reads(
    live_spreadsheet: Spreadsheet,
) -> None:
    """Validate Spreadsheet.get_all_rows() across all tabs against direct raw Google API calls."""
    service = live_spreadsheet._service

    for tab_name, col_end in [
        ("Inventario", "H"),
        ("Workflows", "N"),
        ("Topology", "I"),
        ("Policies", "I"),
    ]:
        tab_sheet = Spreadsheet(
            id=_SPREADSHEET_ID,
            credentials=live_spreadsheet.credentials,
            sheet_name=tab_name,
        )
        rows_from_client = tab_sheet.get_all_rows()

        res = live_spreadsheet._execute(
            service.spreadsheets()
            .values()
            .get(spreadsheetId=_SPREADSHEET_ID, range=f"{tab_name}!A2:{col_end}")
        )
        raw_rows = res.get("values", [])

        # Non-empty rows count must match
        non_empty_raw = [r for r in raw_rows if any(cell.strip() for cell in r if cell)]
        assert len(rows_from_client) == len(non_empty_raw)
