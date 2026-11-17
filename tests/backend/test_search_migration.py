"""Static checks of the search migration; live behaviour is covered by check_search_live.py."""

from backend.supabase.migrate import MIGRATIONS, transaction_body

SQL = transaction_body((MIGRATIONS / "202611180004_search.sql").read_text(encoding="utf-8"))


def test_tables_are_private_by_default_and_read_only_for_users():
    assert SQL.count("enable row level security") == 2
    assert "grant select on public.search_passages to authenticated" in SQL
    assert "grant select on public.search_index_state to authenticated" in SQL
    assert "grant insert" not in SQL and "grant update" not in SQL and "grant all" not in SQL


def test_every_function_pins_search_path_and_revokes_public_access():
    functions = SQL.count("create function public.")
    assert functions == 4
    assert SQL.count("set search_path = ''") == functions
    assert SQL.count("from public, anon;") == functions
    assert SQL.count(" to authenticated;") >= functions


def test_queries_use_the_english_tsvector_and_never_concatenate_user_text_into_sql():
    assert "to_tsvector('english', text)" in SQL and "using gin (tsv)" in SQL
    assert "websearch_to_tsquery('english', p_query)" in SQL
    # The only dynamic SQL receives values through USING parameters.
    assert SQL.count("execute $q$") == 1 and "using words, p_limit, p_offset" in SQL
    assert "format(" not in SQL and "|| p_query" not in SQL and "p_query ||" not in SQL


def test_passages_must_match_their_citations_and_the_current_extraction():
    assert "Passage citation does not match its text" in SQL
    assert "d.processing_run_id is distinct from p_extraction_id" in SQL
    assert "check (end_char - start_char = char_length(text))" in SQL
