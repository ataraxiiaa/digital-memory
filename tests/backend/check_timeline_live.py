r"""Exercise the timeline migration and functions against the real database, then ROLL BACK.

    .\.venv\Scripts\python.exe tests/backend/check_timeline_live.py

Runs the migration SQL (so it is safe before or after the migration is applied),
creates synthetic users, a document and accepted mentions, drives the real timeline
builder through replace / review / re-run / stale handling, and checks that a second
user and anon cannot read or change anything. Nothing is committed.
Needs SUPABASE_DATABASE_PASSWORD in .env.
"""
from datetime import date
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
from backend.schemas.extraction import CitationReference
from backend.services.timeline import AcceptedMention, build_timeline_items
from backend.supabase.migrate import MIGRATIONS, transaction_body
from test_source_passages import report_for

env = {**dotenv_values(ENV_FILE, interpolate=False, encoding="utf-8-sig"), **os.environ}
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
    c.execute(transaction_body((MIGRATIONS / "202611180003_timeline.sql").read_text(encoding="utf-8")), prepare=False)
    u, other, doc, other_doc, extraction, run = (uuid4() for _ in range(6))
    for x in (u, other):
        c.execute("insert into auth.users(id,aud,role,email) values (%s,'authenticated','authenticated',%s)", (x, f"{x}@example.invalid"))
    for owner, d in ((u, doc), (other, other_doc)):
        c.execute("""insert into public.documents(id,owner_id,title,content,status,storage_path,original_filename,file_size_bytes,page_count)
            values (%s,%s,'T','','ready',%s,'t.pdf',10,1)""", (d, owner, f"{owner}/{d}/original.pdf"))
    c.execute("insert into public.document_extractions(id,document_id,owner_id,status,finished_at,report) values (%s,%s,%s,'ready',now(),'{}'::jsonb)", (extraction, doc, u))
    c.execute("insert into public.entity_runs(id,document_id,owner_id,extraction_id,status,finished_at) values (%s,%s,%s,%s,'completed',now())", (run, doc, u, extraction))

    text = "Omar's wedding is on 21 December in Murree."
    report = report_for([text])
    native = report.native_extraction.model_copy(update={"document_id": doc})
    report = report.model_copy(update={"native_extraction": native})
    region = native.pages[0].regions[0]
    mentions = []
    for label, span in (("person", "Omar"), ("event", "wedding"), ("date", "21 December"), ("location", "Murree")):
        start = text.index(span)
        citation = CitationReference(document_id=doc, extraction_id=native.id, page_number=1, region_id=region.id,
            start_char=start, end_char=start + len(span))
        mention = AcceptedMention(run_id=run, mention_id=uuid4(), label=label, text=span, citation=citation)
        mentions.append(mention)
        c.execute("""insert into public.entity_mentions(id,run_id,document_id,owner_id,passage_id,page_number,region_id,start_char,end_char,
            text,label,score,source_sha256,citation,review_status,reviewed_at)
            values (%s,%s,%s,%s,%s,1,%s,%s,%s,%s,%s,.9,%s,%s::jsonb,'accepted',now())""",
            (mention.mention_id, run, doc, u, uuid4(), region.id, citation.start_char, citation.end_char, span, label,
             "a" * 64, citation.model_dump_json()))
    unaccepted = uuid4()
    c.execute("""insert into public.entity_mentions(id,run_id,document_id,owner_id,passage_id,page_number,region_id,start_char,end_char,
        text,label,score,source_sha256,citation)
        values (%s,%s,%s,%s,%s,1,%s,0,4,'Omar','person',.9,%s,%s::jsonb)""",
        (unaccepted, run, doc, u, uuid4(), region.id, "a" * 64, mentions[0].citation.model_copy(update={"start_char": 0, "end_char": 4}).model_dump_json()))

    anchor = date(2026, 11, 18)
    build = build_timeline_items(report, mentions, anchor)
    payload = json.dumps([item.model_dump(mode="json") for item in build.items])
    check("builder produced one event item with a resolved date", len(build.items) == 1 and build.items[0].start_date == date(2026, 12, 21))
    item = build.items[0]

    c.execute("set local role authenticated")
    as_user(u)
    result = c.execute("select public.replace_timeline_items(%s, %s::jsonb)", (doc, payload)).fetchone()[0]
    check("replace writes the item", result == {"written": 1, "removed": 0, "stale": 0}, str(result))
    row = c.execute("select title,date_status,start_date,precision,review_status,flags from public.timeline_items where id=%s", (item.id,)).fetchone()
    check("stored item is a suggestion with the computed date", row == ("wedding", "resolved", date(2026, 12, 21), "day", "suggested", ["year_inferred"]), str(row))

    reviewed = c.execute("select review_status, corrected_start, corrected_title from public.review_timeline_item(%s,'confirmed','Omar''s wedding',%s,%s,'day')",
        (item.id, date(2027, 12, 21), date(2027, 12, 21))).fetchone()
    check("confirm stores a date and title correction", reviewed == ("confirmed", date(2027, 12, 21), "Omar's wedding"), str(reviewed))
    original = c.execute("select start_date,title from public.timeline_items where id=%s", (item.id,)).fetchone()
    check("computed values are never overwritten by a correction", original == (date(2026, 12, 21), "wedding"), str(original))

    result = c.execute("select public.replace_timeline_items(%s, %s::jsonb)", (doc, payload)).fetchone()[0]
    kept = c.execute("select review_status, corrected_start, stale from public.timeline_items where id=%s", (item.id,)).fetchone()
    check("re-running keeps the reviewer's decision and correction", kept == ("confirmed", date(2027, 12, 21), False), f"{kept} {result}")

    expect_error("a rejected item cannot carry a correction", "select * from public.review_timeline_item(%s,'rejected','x',null,null,null)", (item.id,), "22023")
    expect_error("an unaccepted mention cannot support an item", "select public.replace_timeline_items(%s, %s::jsonb)",
        (doc, json.dumps([{**json.loads(payload)[0], "id": str(uuid4()),
            "refs": [{**json.loads(payload)[0]["refs"][0], "mention_id": str(unaccepted)}]}])), "22023")
    expect_error("an item for another document is refused", "select public.replace_timeline_items(%s, %s::jsonb)",
        (doc, json.dumps([{**json.loads(payload)[0], "document_id": str(other_doc)}])), "22023")

    # The supporting mention is no longer accepted: the confirmed item turns stale, a suggestion would be deleted.
    c.execute("reset role")
    c.execute("update public.entity_mentions set review_status='rejected' where id=%s", (mentions[1].mention_id,))
    c.execute("set local role authenticated")
    as_user(u)
    result = c.execute("select public.replace_timeline_items(%s, '[]'::jsonb)", (doc,)).fetchone()[0]
    stale = c.execute("select review_status, stale from public.timeline_items where id=%s", (item.id,)).fetchone()
    check("an unsupported confirmed item is marked stale, not deleted", stale == ("confirmed", True) and result["stale"] == 1, f"{stale} {result}")
    c.execute("select * from public.review_timeline_item(%s,'suggested')", (item.id,))
    result = c.execute("select public.replace_timeline_items(%s, '[]'::jsonb)", (doc,)).fetchone()[0]
    check("an unsupported suggestion is removed", result["removed"] == 1, str(result))

    # A second user and anon can read or change nothing.
    c.execute("reset role")
    c.execute("select public.replace_timeline_items(%s, %s::jsonb)", (doc, payload)) if False else None
    c.execute("set local role authenticated")
    as_user(u)
    c.execute("update public.entity_mentions set review_status='accepted' where id=%s", (mentions[1].mention_id,)) if False else None
    c.execute("reset role")
    c.execute("update public.entity_mentions set review_status='accepted' where id=%s", (mentions[1].mention_id,))
    c.execute("set local role authenticated")
    as_user(u)
    c.execute("select public.replace_timeline_items(%s, %s::jsonb)", (doc, payload))
    as_user(other)
    check("second user sees no items", c.execute("select count(*) from public.timeline_items").fetchone()[0] == 0)
    expect_error("second user cannot review the item", "select * from public.review_timeline_item(%s,'rejected')", (item.id,), "P0002")
    expect_error("second user cannot replace another user's document", "select public.replace_timeline_items(%s,'[]'::jsonb)", (doc,), "P0002")
    expect_error("authenticated users cannot write the table directly",
        "insert into public.timeline_items(id,owner_id,document_id,title,page_number,sentence_text,sentence_start,sentence_end,date_status,refs) values (%s,%s,%s,'x',1,'x',0,1,'none','[{\"role\":\"event\"}]')",
        (uuid4(), other, other_doc), "42501")
    c.execute("reset role")
    c.execute("set local role anon")
    expect_error("anon cannot read the table", "select count(*) from public.timeline_items", (), "42501")
    expect_error("anon cannot call the functions", "select public.replace_timeline_items(%s,'[]'::jsonb)", (doc,), "42501")
finally:
    c.rollback()
    c.close()
    print("rolled back;", sum(checks), "of", len(checks), "checks passed")
    sys.exit(0 if all(checks) else 1)
