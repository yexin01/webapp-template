"""No migration, and no copy of its role block, names a role as GRANT or REVOKE grantee with a keyword.

``GRANT <role> TO CURRENT_USER`` crashes the database server (signal 11) on Supabase images older than
17.6.1.152, whose supautils is older than 3.2.3, when it runs as the non-superuser ``postgres``. The grantee is
named instead: ``EXECUTE format('GRANT ... TO %I', current_user)``. This guard needs no Supabase image, so it
protects adopters on those images and every later migration.
"""

from __future__ import annotations

import re

import pytest

from app.kernel.infrastructure import migrations

KEYWORD = r"(?:current_user|session_user|current_role)"
# A grantee list that has one of the keywords in it: ``TO a, CURRENT_USER`` as well as ``TO CURRENT_USER``.
KEYWORD_GRANTEE = re.compile(
    rf"\b(?:grant|revoke)\b[^;]*?\b(?:to|from)\s+(?:[\w.\"]+\s*,\s*)*{KEYWORD}\b", re.IGNORECASE | re.DOTALL
)
ROLES_SQL = re.compile(r"const ROLES_SQL = `(?P<sql>.*?)`;", re.DOTALL)


def code(sql: str) -> str:
    """SQL without its ``--`` comments, which may explain the rule by quoting it."""
    return re.sub(r"--[^\n]*", "", sql)


def grants_to_a_keyword(sql: str) -> list[str]:
    return [match.group(0) for match in KEYWORD_GRANTEE.finditer(code(sql))]


@pytest.mark.parametrize(
    "sql",
    [
        "GRANT app_api TO CURRENT_USER;",
        "grant app_api, app_worker to current_user",
        "GRANT app_api TO app_worker, SESSION_USER;",
        "REVOKE app_api\n  FROM current_role;",
        "GRANT USAGE ON SCHEMA app TO CURRENT_USER;",
    ],
)
def test_the_scan_finds_a_keyword_grantee(sql: str) -> None:
    assert grants_to_a_keyword(sql)


@pytest.mark.parametrize(
    "sql",
    [
        "GRANT app_api TO postgres;",
        "DO $$ BEGIN EXECUTE format('GRANT app_api, app_worker TO %I', current_user); END $$;",
        "-- GRANT app_api TO CURRENT_USER crashes supautils\nSELECT 1;",
        "GRANT SELECT ON app.users TO app_api;\nSELECT current_user;",
        "CREATE POLICY p ON app.t FOR ALL TO app_api USING (owner = current_user);",
    ],
)
def test_the_scan_accepts_named_grantees(sql: str) -> None:
    assert grants_to_a_keyword(sql) == []


def test_no_migration_grants_or_revokes_with_a_keyword_grantee() -> None:
    found = {m.path.name: grants_to_a_keyword(m.path.read_text(encoding="utf-8")) for m in migrations.discover()}
    assert {name: hits for name, hits in found.items() if hits} == {}


def test_the_module_template_migration_names_its_grantees() -> None:
    template = migrations.repository_file("scripts", "templates", "module", "migration.sql.tmpl")
    assert grants_to_a_keyword(template.read_text(encoding="utf-8")) == []


def test_the_restore_drill_role_block_names_its_grantees() -> None:
    script = migrations.repository_file("scripts", "restore-drill.mjs").read_text(encoding="utf-8")
    block = ROLES_SQL.search(script)
    assert block is not None, "ROLES_SQL moved: update this guard"
    assert "GRANT" in block["sql"]
    assert grants_to_a_keyword(block["sql"]) == []
