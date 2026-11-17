r"""Exercise the search migration against the real Postgres, measure relevance, then ROLL BACK.

    .\.venv\Scripts\python.exe tests/backend/check_search_live.py

Runs the migration SQL (and the entity-identity migration first if it is not applied),
indexes the synthetic documents in tests/fixtures/search_relevance.json through the real
passage builder, runs every authored query with the real english configuration, and checks
ownership, staleness, citations, and safe snippets. Nothing is committed.
Needs SUPABASE_DATABASE_PASSWORD in .env.
"""
import json
import os
from pathlib import Path
import sys
from urllib.parse import urlsplit
from uuid import uuid4

import psycopg
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from backend.config import ENV_FILE
from backend.services.search import passage_rows, snippet_segments
from backend.supabase.migrate import MIGRATIONS, transaction_body
from test_source_passages import report_for

FIXTURE = json.loads((ROOT / "tests/fixtures/search_relevance.json").read_text(encoding="utf-8"))
# A git worktree has no .env of its own; fall back to the nearest one in a parent folder.
env_file = next((path for path in (ENV_FILE, *(parent / ".env" for parent in ROOT.parents)) if path.is_file()), ENV_FILE)
env = {**dotenv_values(env_file, interpolate=False, encoding="utf-8-sig"), **os.environ}
ref = urlsplit(env["SUPABASE_URL"]).hostname.removesuffix(".supabase.co")
c = psycopg.connect(host=f"db.{ref}.supabase.co", port=5432, dbname="postgres", user="postgres",
                    password=env["SUPABASE_DATABASE_PASSWORD"], sslmode="require", connect_timeout=10, autocommit=False)
checks = []


def check(name, ok, detail=""):
    checks.append(ok)
    print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail and not ok else ""))


def expect_error(name, sql, args, code=None):
    c.execute("savepoint s")
    try:
        c.execute(sql, args)
        check(name, False, "no error")
    except psycopg.errors.Error as error:
        check(name, code is None or error.sqlstate == code, error.sqlstate)
    c.execute("rollback to savepoint s")


def as_user(user):
    c.execute("select set_config('request.jwt.claims', %s, true)", (json.dumps({"sub": str(user), "role": "authenticated"}),))


try:
    if c.execute("select to_regclass('public.entities')").fetchone()[0] is None:
        c.execute(transaction_body((MIGRATIONS / "202611180002_entity_identity.sql").read_text(encoding="utf-8")), prepare=False)
        print("note: entity-identity migration applied inside this rolled-back transaction")
    c.execute(transaction_body((MIGRATIONS / "202611180004_search.sql").read_text(encoding="utf-8")), prepare=False)

    u, other = uuid4(), uuid4()
    for x in (u, other):
        c.execute("insert into auth.users(id,aud,role,email) values (%s,'authenticated','authenticated',%s)", (x, f"{x}@example.invalid"))

    docs, rows_by_doc, extraction_by_doc = {}, {}, {}
    for entry in FIXTURE["documents"]:
        doc, extraction = uuid4(), uuid4()
        docs[entry["key"]], extraction_by_doc[entry["key"]] = doc, extraction
        c.execute("""insert into public.documents(id,owner_id,title,content,status,storage_path,original_filename,file_size_bytes,page_count)
            values (%s,%s,%s,'','ready',%s,'t.pdf',10,%s)""", (doc, u, entry["title"], f"{u}/{doc}/original.pdf", len(entry["pages"])))
        c.execute("insert into public.document_extractions(id,document_id,owner_id,status,finished_at,report) values (%s,%s,%s,'ready',now(),'{}'::jsonb)", (extraction, doc, u))
        c.execute("update public.documents set processing_run_id=%s where id=%s", (extraction, doc))
        report = report_for(entry["pages"])
        report = report.model_copy(update={"native_extraction": report.native_extraction.model_copy(update={"document_id": doc})})
        rows_by_doc[entry["key"]], _ = passage_rows(report)
    other_doc, other_extraction = uuid4(), uuid4()
    c.execute("""insert into public.documents(id,owner_id,title,content,status,storage_path,original_filename,file_size_bytes,page_count)
        values (%s,%s,'Other user note','','ready',%s,'t.pdf',10,1)""", (other_doc, other, f"{other}/{other_doc}/original.pdf"))
    c.execute("insert into public.document_extractions(id,document_id,owner_id,status,finished_at,report) values (%s,%s,%s,'ready',now(),'{}'::jsonb)", (other_extraction, other_doc, other))
    c.execute("update public.documents set processing_run_id=%s where id=%s", (other_extraction, other_doc))
    other_report = report_for(["Marigold budget secrets belong to the other user."])
    other_report = other_report.model_copy(update={"native_extraction": other_report.native_extraction.model_copy(update={"document_id": other_doc})})
    other_rows, _ = passage_rows(other_report)

    c.execute("set local role authenticated")
    as_user(u)
    for key, rows in rows_by_doc.items():
        written = c.execute("select public.replace_search_passages(%s,%s,%s::jsonb)", (docs[key], extraction_by_doc[key], json.dumps(rows))).fetchone()[0]
        check(f"indexed {key}", written == {"passages": len(rows)}, str(written))
    as_user(other)
    c.execute("select public.replace_search_passages(%s,%s,%s::jsonb)", (other_doc, other_extraction, json.dumps(other_rows)))
    as_user(u)

    where = {(passage["id"]): (key, page) for key, rows in rows_by_doc.items() for page, passage in enumerate(rows, 1)}
    hits_at_3, reciprocal, positives = 0, 0.0, 0
    for case in FIXTURE["queries"]:
        found = c.execute("select id, snippet from public.search_passages_query(%s, null, 10, 0)", (case["q"],)).fetchall()
        ranked = [where[str(row[0])] for row in found if str(row[0]) in where]
        if case.get("none"):
            check(f"no results for {case['q']!r}", not found, str(ranked))
            continue
        positives += 1
        expected = [tuple(item) for item in case["expect"]]
        top3 = ranked[:3]
        hit = any(item in top3 for item in expected)
        hits_at_3 += hit
        reciprocal += next((1 / (rank + 1) for rank, item in enumerate(ranked) if item in expected), 0.0)
        check(f"top 3 for {case['q']!r}", hit, f"got {ranked[:5]}")
        for item in case.get("absent", []):
            check(f"{case['q']!r} excludes {item}", tuple(item) not in ranked, str(ranked))
    recall = hits_at_3 / positives
    print(f"RELEVANCE recall@3 = {hits_at_3}/{positives} = {recall:.3f}; MRR = {reciprocal / positives:.3f}")

    # Snippets are marker-wrapped text that becomes safe segments.
    snippet = c.execute("select snippet from public.search_passages_query('Marigold', null, 5, 0)").fetchone()[0]
    segments = snippet_segments(snippet)
    check("snippet marks the matched word", any(s.match and s.text.lower().startswith("marigold") for s in segments), snippet)
    check("snippet has no stray markers or markup", all(chr(0xE000) not in s.text and chr(0xE001) not in s.text for s in segments))
    count = c.execute("select total from public.search_passages_query('attention', null, 1, 0)").fetchone()
    check("total counts all matches while paging", count is not None and count[0] >= 2, str(count))
    check("paging offset skips ranked rows", len(c.execute("select id from public.search_passages_query('attention', null, 5, 1000)").fetchall()) == 0)
    one_doc = c.execute("select count(distinct document_id) from public.search_passages_query('attention', %s, 20, 0)", (docs["paper"],)).fetchone()[0]
    check("document filter limits passage results", one_doc == 1)
    citation = c.execute("select citation from public.search_passages_query('castle', null, 1, 0)").fetchone()[0]
    check("results carry the exact citation", citation["document_id"] == str(docs["trip"]) and citation["page_number"] == 2, str(citation))
    expect_error("empty query is refused", "select * from public.search_passages_query('   ', null, 10, 0)", (), "22023")
    expect_error("oversized query is refused", "select * from public.search_passages_query(%s, null, 10, 0)", ("x " * 300,), "22023")
    expect_error("invalid paging is refused", "select * from public.search_passages_query('a', null, 1000, 0)", (), "22023")

    # Another user's data is invisible both ways.
    as_user(other)
    check("second user finds nothing of user one's", not c.execute("select 1 from public.search_passages_query('budget Marigold', null, 10, 0) where document_id <> %s", (other_doc,)).fetchall())
    check("second user finds their own", len(c.execute("select 1 from public.search_passages_query('Marigold', null, 10, 0)").fetchall()) == 1)
    check("second user sees no index rows", c.execute("select count(*) from public.search_passages where document_id <> %s", (other_doc,)).fetchone()[0] == 0)
    expect_error("second user cannot reindex user one's document", "select public.replace_search_passages(%s,%s,%s::jsonb)", (docs["budget"], extraction_by_doc["budget"], json.dumps(rows_by_doc["budget"])), "P0002")
    expect_error("direct writes are denied", "insert into public.search_passages(id,owner_id,document_id,page_number,region_id,start_char,end_char,method,text,source_sha256,citation) values (%s,%s,%s,1,%s,0,1,'native','x',%s,'{}'::jsonb)", (uuid4(), other, other_doc, uuid4(), "a" * 64), "42501")

    as_user(u)
    good = rows_by_doc["budget"]
    expect_error("only the current extraction can be indexed", "select public.replace_search_passages(%s,%s,%s::jsonb)", (docs["budget"], uuid4(), json.dumps(good)), "22023")
    tampered = [{**good[0], "text": good[0]["text"] + " extra"}]
    expect_error("a passage whose text disagrees with its citation is refused", "select public.replace_search_passages(%s,%s,%s::jsonb)", (docs["budget"], extraction_by_doc["budget"], json.dumps(tampered)), "22023")
    foreign = [{**good[0], "citation": {**good[0]["citation"], "document_id": str(other_doc)}}]
    expect_error("a citation to another document is refused", "select public.replace_search_passages(%s,%s,%s::jsonb)", (docs["budget"], extraction_by_doc["budget"], json.dumps(foreign)), "22023")
    check("a failed replace left the index intact", len(c.execute("select 1 from public.search_passages where document_id = %s", (docs["budget"],)).fetchall()) == len(good))

    # Freshness: a new extraction makes the index stale until it is rebuilt.
    c.execute("reset role")
    new_extraction = uuid4()
    c.execute("insert into public.document_extractions(id,document_id,owner_id,status,finished_at,report) values (%s,%s,%s,'ready',now(),'{}'::jsonb)", (new_extraction, docs["budget"], u))
    c.execute("update public.documents set processing_run_id=%s where id=%s", (new_extraction, docs["budget"]))
    c.execute("set local role authenticated")
    as_user(u)
    state = c.execute("select s.extraction_id = d.processing_run_id from public.search_index_state s join public.documents d on d.id = s.document_id where d.id = %s", (docs["budget"],)).fetchone()[0]
    check("index state shows the document as stale after re-extraction", state is False)
    c.execute("select public.replace_search_passages(%s,%s,%s::jsonb)", (docs["budget"], new_extraction, json.dumps(good)))
    state = c.execute("select s.extraction_id = d.processing_run_id from public.search_index_state s join public.documents d on d.id = s.document_id where d.id = %s", (docs["budget"],)).fetchone()[0]
    check("rebuilding makes the index current again", state is True)

    # Entities: an unlinked accepted mention is found as a mention; linking it makes the entity the hit.
    c.execute("reset role")
    run = uuid4()
    c.execute("insert into public.entity_runs(id,document_id,owner_id,extraction_id,status,finished_at) values (%s,%s,%s,%s,'completed',now())", (run, docs["budget"], u, new_extraction))
    mention_ids = []
    for label, text, start in (("project", "Project Marigold", 49), ("person", "L. Page", 0)):
        mention = uuid4()
        mention_ids.append(mention)
        c.execute("""insert into public.entity_mentions(id,run_id,document_id,owner_id,passage_id,page_number,region_id,start_char,end_char,
            text,label,score,source_sha256,citation,review_status,reviewed_at)
            values (%s,%s,%s,%s,%s,1,%s,%s,%s,%s,%s,.9,%s,%s::jsonb,'accepted',now())""",
            (mention, run, docs["budget"], u, uuid4(), uuid4(), start, start + len(text), text, label, "a" * 64,
             json.dumps({**good[0]["citation"], "start_char": start, "end_char": start + len(text)})))
    c.execute("set local role authenticated")
    as_user(u)
    found = c.execute("select kind, name from public.search_entities_query('marigold', 10, 0)").fetchall()
    check("an unlinked accepted mention is searchable", found == [("mention", "Project Marigold")], str(found))
    c.execute("reset role")
    entity = uuid4()
    c.execute("insert into public.entities(id,owner_id,type,canonical_name,status) values (%s,%s,'person','Larry Page','confirmed')", (entity, u))
    c.execute("insert into public.entity_links(run_id,mention_id,document_id,owner_id,entity_id,method) values (%s,%s,%s,%s,%s,'manual')", (run, mention_ids[1], docs["budget"], u, entity))
    c.execute("set local role authenticated")
    as_user(u)
    found = c.execute("select kind, name, matched, mention_count from public.search_entities_query('L. Page', 10, 0)").fetchall()
    check("an entity is found through the text of a linked alias", found == [("entity", "Larry Page", "L. Page", 1)], str(found))
    found = c.execute("select kind from public.search_entities_query('page larry', 10, 0)").fetchall()
    check("every query word must match, in any order", found == [("entity",)], str(found))
    check("a linked mention is not listed twice", [r[0] for r in c.execute("select kind from public.search_entities_query('page', 10, 0)").fetchall()] == ["entity"])
    check("wildcard characters are plain text", c.execute("select count(*) from public.search_entities_query('%', 10, 0)").fetchone()[0] == 0)
    as_user(other)
    check("second user cannot find user one's entities", c.execute("select count(*) from public.search_entities_query('page', 10, 0)").fetchone()[0] == 0)
    as_user(u)
    check("timeline search is safe when the timeline table is absent or empty", c.execute("select count(*) from public.search_timeline_query('anything', 10, 0)").fetchone()[0] >= 0)

    c.execute("reset role")
    c.execute("set local role anon")
    expect_error("anon cannot query passages", "select * from public.search_passages_query('budget', null, 10, 0)", (), "42501")
    expect_error("anon cannot read the index", "select count(*) from public.search_passages", (), "42501")
    expect_error("anon cannot reindex", "select public.replace_search_passages(%s,%s,'[]'::jsonb)", (docs["budget"], new_extraction), "42501")
finally:
    c.rollback()
    c.close()
    print("rolled back;", sum(checks), "of", len(checks), "checks passed")
    sys.exit(0 if all(checks) else 1)
