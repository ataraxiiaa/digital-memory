"""Checks for migration transaction preparation; live catalog checks run on apply."""

import pytest

from backend.supabase.migrate import MIGRATIONS, transaction_body


def test_removes_only_outer_transaction_wrapper():
    sql = "-- Keep this comment\nbegin;\nselect 'commit;';\ncommit;\n"
    assert transaction_body(sql) == "-- Keep this comment\n\nselect 'commit;';\n"


@pytest.mark.parametrize("sql", ["", "select 1;", "begin;\nselect 1;", "select 1;\ncommit;"])
def test_rejects_missing_wrappers(sql):
    with pytest.raises(ValueError, match="wrapper"):
        transaction_body(sql)


def test_entity_migration_is_wrapped_and_private_by_default():
    body = transaction_body((MIGRATIONS / "202611170001_entities.sql").read_text(encoding="utf-8"))
    assert body.count("enable row level security") == 2
    assert "grant select on public.entity_mentions to authenticated" in body
    assert "grant insert" not in body and "grant update" not in body
    assert body.count("security definer set search_path = ''") == 3


def test_identity_migration_is_wrapped_private_and_function_gated():
    body = transaction_body((MIGRATIONS / "202611180002_entity_identity.sql").read_text(encoding="utf-8"))
    assert body.count("enable row level security") == 4
    assert "grant insert" not in body and "grant update" not in body and "grant delete" not in body
    # Every function is security definer with a pinned search_path.
    assert body.count("create function") == body.count("set search_path = ''") == 11
    assert "check (entity_a < entity_b)" in body
    assert "revoke all on function public.latest_entity_run_id(uuid) from public, anon, authenticated;" in body


def test_existing_migration_can_be_wrapped_for_atomic_tracking():
    body = transaction_body((MIGRATIONS / "202611130001_documents.sql").read_text(encoding="utf-8"))
    assert "create table public.documents" in body
    assert "enable row level security" in body
