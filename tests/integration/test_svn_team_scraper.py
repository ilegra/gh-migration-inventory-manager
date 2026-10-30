"""Integration tests verifying live SVN team topology discovery against Docker container."""

from __future__ import annotations

from typing import Any

import pytest

from gh_migration_inventory_manager import (
    BasicAuthenticator,
    NormalizedRole,
    ScraperAuthenticationError,
    SvnTeamScraper,
)


def test_given_live_svn_server_when_scraping_teams_via_remote_url_then_extracts_all_teams_and_roles(
    svn_server: dict[str, Any], svn_auth: BasicAuthenticator
) -> None:
    """Verify live scraping of SVN authz file served over HTTP."""
    scraper = SvnTeamScraper(
        authz_url=svn_server["authz_url"],
        auth=svn_auth,
    )

    permissions = scraper.scrape()
    assert len(permissions) >= 5

    # 1. Root admin rule
    admin_perm = next(p for p in permissions if p.team_name == "admins")
    assert admin_perm.member_count == 1
    assert admin_perm.normalized_role == NormalizedRole.ADMIN
    assert admin_perm.origin_role == "rw"

    # 2. Trunk write rule
    core_devs = next(
        p
        for p in permissions
        if p.team_name == "core-devs" and "standard-app" in p.target_repo_or_project
    )
    assert core_devs.member_count == 3
    assert core_devs.normalized_role == NormalizedRole.WRITE
    assert core_devs.team_slug == "core-devs"

    # 3. Tags maintain rule
    tags_leads = next(
        p
        for p in permissions
        if p.team_name == "leads" and "tags" in p.target_repo_or_project
    )
    assert tags_leads.member_count == 2
    assert tags_leads.normalized_role == NormalizedRole.MAINTAIN

    # 4. Global read wildcard
    wildcard = next(
        p
        for p in permissions
        if p.team_name == "*"
        and "/" in p.target_repo_or_project
        and "tags" not in p.target_repo_or_project
        and "standard-app" not in p.target_repo_or_project
    )
    assert wildcard.member_count == 0
    assert wildcard.team_slug == "all-users"
    assert wildcard.normalized_role == NormalizedRole.READ


def test_given_live_svn_server_when_scraping_teams_via_local_authz_path_then_extracts_all_teams_and_roles(
    svn_server: dict[str, Any],
) -> None:
    """Verify live scraping of SVN authz file from filesystem path."""
    scraper = SvnTeamScraper(authz_path=svn_server["authz_path"])

    permissions = scraper.scrape()
    assert len(permissions) >= 5

    ops_perm = next(p for p in permissions if p.team_name == "ops")
    assert ops_perm.member_count == 2
    assert ops_perm.normalized_role == NormalizedRole.WRITE


def test_given_invalid_credentials_when_scraping_live_svn_authz_url_then_raises_authentication_error(
    svn_server: dict[str, Any],
) -> None:
    """Verify live SVN HTTP authentication failure raises ScraperAuthenticationError."""
    bad_auth = BasicAuthenticator("invalid_user", "wrong_password")
    scraper = SvnTeamScraper(
        authz_url=svn_server["authz_url"],
        auth=bad_auth,
    )

    with pytest.raises(ScraperAuthenticationError):
        scraper.scrape()
