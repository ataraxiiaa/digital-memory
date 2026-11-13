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


def test_existing_migration_can_be_wrapped_for_atomic_tracking():
    body = transaction_body((MIGRATIONS / "202611130001_documents.sql").read_text(encoding="utf-8"))
    assert "create table public.documents" in body
    assert "enable row level security" in body
