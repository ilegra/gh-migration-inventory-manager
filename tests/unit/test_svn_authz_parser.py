"""Unit tests for SvnAuthzParser Subversion authz rule parser."""

from __future__ import annotations

from gh_migration_inventory_manager.scrap import NormalizedRole
from gh_migration_inventory_manager.svn_scrap import SvnAuthzParser


def test_given_standard_authz_text_when_parsing_then_extracts_all_teams_and_roles() -> (
    None
):
    """Verify parsing of groups, path rules, and permission normalization."""
    content = """
    # Subversion authz sample
    [groups]
    admins = root, sysadmin
    core-devs = alice, bob, charlie
    leads = alice, dave
    ops = dave, eve

    [/]
    @admins = rw
    * = r

    [/standard-app/trunk]
    @core-devs = rw
    @leads = rw

    [/standard-app/tags]
    @leads = rw
    * = r

    [/multi-app/billing-service]
    @ops = rw
    * =
    """
    parser = SvnAuthzParser()
    perms = parser.parse(content)

    assert len(perms) == 7

    # 1. [/] @admins = rw -> Admin
    admin_perm = next(p for p in perms if p.team_name == "admins")
    assert admin_perm.member_count == 2
    assert admin_perm.normalized_role == NormalizedRole.ADMIN
    assert admin_perm.origin_role == "rw"
    assert (
        admin_perm.target_repo_or_project == "[/]"
        or admin_perm.target_repo_or_project == "/"
    )

    # 2. [/] * = r -> Read (all-users)
    root_wildcard = next(
        p
        for p in perms
        if p.team_name == "*"
        and "/" in p.target_repo_or_project
        and "tags" not in p.target_repo_or_project
        and "standard-app" not in p.target_repo_or_project
    )
    assert root_wildcard.member_count == 0
    assert root_wildcard.team_slug == "all-users"
    assert root_wildcard.normalized_role == NormalizedRole.READ

    # 3. [/standard-app/trunk] @core-devs = rw -> Write
    core_devs = next(p for p in perms if p.team_name == "core-devs")
    assert core_devs.member_count == 3
    assert core_devs.team_slug == "core-devs"
    assert core_devs.normalized_role == NormalizedRole.WRITE
    assert "/standard-app/trunk" in core_devs.target_repo_or_project

    # 4. [/standard-app/tags] @leads = rw -> Maintain
    tags_leads = next(
        p
        for p in perms
        if p.team_name == "leads" and "tags" in p.target_repo_or_project
    )
    assert tags_leads.member_count == 2
    assert tags_leads.normalized_role == NormalizedRole.MAINTAIN
    assert tags_leads.origin_role == "rw"


def test_given_repo_prefixed_path_when_parsing_then_preserves_target_and_normalizes_role() -> (
    None
):
    """Verify repository-prefixed path rules like [repo:/path]."""
    content = """
    [groups]
    devs = user1, user2

    [myrepo:/projects/service-a/trunk]
    @devs = rw
    """
    parser = SvnAuthzParser()
    perms = parser.parse(content)

    assert len(perms) == 1
    assert perms[0].team_name == "devs"
    assert perms[0].member_count == 2
    assert perms[0].normalized_role == NormalizedRole.WRITE
    assert perms[0].target_repo_or_project == "myrepo:/projects/service-a/trunk"


def test_given_individual_user_rules_when_parsing_then_ignores_individual_pii() -> None:
    """Verify individual user rules without @ are skipped to avoid PII sprawl."""
    content = """
    [groups]
    devs = alice, bob

    [/trunk]
    @devs = rw
    individual_user = rw
    charlie = r
    """
    parser = SvnAuthzParser()
    perms = parser.parse(content)

    assert len(perms) == 1
    assert perms[0].team_name == "devs"


def test_given_denied_access_rule_when_parsing_then_skips_denied_entries() -> None:
    """Verify explicitly denied permission (* = or @group =) is ignored."""
    content = """
    [groups]
    guests = visitor1

    [/secret]
    * =
    @guests =
    """
    parser = SvnAuthzParser()
    perms = parser.parse(content)

    assert len(perms) == 0


def test_given_comments_and_whitespace_when_parsing_then_handles_cleanly() -> None:
    """Verify parser ignores lines starting with # or ; and trims whitespace."""
    content = """
    # Comment line
    ; Another comment style
    [groups]
       devs   =   user1  ,   user2  ,  user3

    # Rule comment
    [/trunk]
       @devs   =   r
    """
    parser = SvnAuthzParser()
    perms = parser.parse(content)

    assert len(perms) == 1
    assert perms[0].team_name == "devs"
    assert perms[0].member_count == 3
    assert perms[0].normalized_role == NormalizedRole.READ
    assert perms[0].origin_role == "r"


def test_given_empty_or_whitespace_content_when_parsing_then_returns_empty_list() -> (
    None
):
    """Verify empty or whitespace-only content returns empty list."""
    parser = SvnAuthzParser()
    assert parser.parse("") == []
    assert parser.parse("   \n   \n") == []


def test_given_group_not_found_when_parsing_then_defaults_member_count_to_zero() -> (
    None
):
    """Verify missing group definition results in member_count=0."""
    content = """
    [/trunk]
    @nonexistent_group = rw
    """
    parser = SvnAuthzParser()
    perms = parser.parse(content)

    assert len(perms) == 1
    assert perms[0].team_name == "nonexistent_group"
    assert perms[0].member_count == 0


def test_given_malformed_lines_when_parsing_then_continues_gracefully() -> None:
    """Verify malformed lines without equals signs are skipped without raising errors."""
    content = """
    [groups]
    devs = user1
    just a random string without equals
    [valid/path]
    @devs = rw
    another broken line
    """
    parser = SvnAuthzParser()
    perms = parser.parse(content)

    assert len(perms) == 1
    assert perms[0].team_name == "devs"
