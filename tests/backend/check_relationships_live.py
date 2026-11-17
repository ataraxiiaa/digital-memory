r"""Exercise the relationships migration and functions against the real database, then ROLL BACK.

    .\.venv\Scripts\python.exe tests/backend/check_relationships_live.py

Runs the migration SQL (so it is safe before or after the migration is applied),
creates synthetic users, documents and accepted mentions, drives the real candidate
builder through replace / review / re-run / stale handling, and checks that a second
user and anon can read or change nothing. Nothing is committed.
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
from backend.schemas.extraction import CitationReference
from backend.services.relations import AcceptedMention, build_relation_candidates
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


class OneRelation:
    """A stand-in model that finds the one relation in the test sentence."""

    def __init__(self, sentence):
        def part(value):
            start = sentence.index(value)
            return {"text": value, "start": start, "end": start + len(value), "confidence": 0.95}
        self.output = {"relation_extraction": {"works_at": [{"head": part("Omar"), "tail": part("Northwind")}]}}

    def extract_relations(self, text, relation_types, **options):
        return self.output


try:
    c.execute(transaction_body((MIGRATIONS / "202611180005_relationships.sql").read_text(encoding="utf-8")), prepare=False)
    u, other, doc, other_doc, extraction, run = (uuid4() for _ in range(6))
    for x in (u, other):
        c.execute("insert into auth.users(id,aud,role,email) values (%s,'authenticated','authenticated',%s)", (x, f"{x}@example.invalid"))
    for owner, d in ((u, doc), (other, other_doc)):
        c.execute("""insert into public.documents(id,owner_id,title,content,status,storage_path,original_filename,file_size_bytes,page_count)
            values (%s,%s,'T','','ready',%s,'t.pdf',10,1)""", (d, owner, f"{owner}/{d}/original.pdf"))
    c.execute("insert into public.document_extractions(id,document_id,owner_id,status,finished_at,report) values (%s,%s,%s,'ready',now(),'{}'::jsonb)", (extraction, doc, u))
    c.execute("insert into public.entity_runs(id,document_id,owner_id,extraction_id,status,finished_at) values (%s,%s,%s,%s,'completed',now())", (run, doc, u, extraction))

    text = "Omar works at Northwind."
    report = report_for([text])
    native = report.native_extraction.model_copy(update={"document_id": doc})
    report = report.model_copy(update={"native_extraction": native})
    region = native.pages[0].regions[0]
    mentions = []
    for label, span in (("person", "Omar"), ("organization", "Northwind")):
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
        (unaccepted, run, doc, u, uuid4(), region.id, "a" * 64, mentions[0].citation.model_dump_json()))

    build = build_relation_candidates(report, mentions, OneRelation(text))
    check("builder produced one works_at candidate", len(build.candidates) == 1 and build.candidates[0].type == "works_at")
    draft = build.candidates[0]
    payload = json.dumps([d.model_dump(mode="json") for d in build.candidates])

    c.execute("set local role authenticated")
    as_user(u)
    result = c.execute("select public.replace_relationships(%s, %s::jsonb)", (doc, payload)).fetchone()[0]
    check("replace writes the candidate", result == {"written": 1, "removed": 0, "stale": 0}, str(result))
    row = c.execute("select type, review_status, score > .9, subject ->> 'text', object ->> 'text' from public.relationships where id=%s", (draft.id,)).fetchone()
    check("stored candidate is a suggestion with both ends cited", row == ("works_at", "suggested", True, "Omar", "Northwind"), str(row))

    reviewed = c.execute("select review_status, corrected_type, swapped from public.review_relationship(%s,'confirmed','member_of',false)", (draft.id,)).fetchone()
    check("confirm stores a corrected type", reviewed == ("confirmed", "member_of", False), str(reviewed))
    original = c.execute("select type from public.relationships where id=%s", (draft.id,)).fetchone()[0]
    check("the computed type is never overwritten", original == "works_at", original)
    swapped = c.execute("select swapped, corrected_type from public.review_relationship(%s,'confirmed',null,true)", (draft.id,)).fetchone()
    check("confirm can swap direction and clears the type correction", swapped == (True, None), str(swapped))

    c.execute("select public.replace_relationships(%s, %s::jsonb)", (doc, payload))
    kept = c.execute("select review_status, swapped, stale from public.relationships where id=%s", (draft.id,)).fetchone()
    check("re-running keeps the reviewer's decision", kept == ("confirmed", True, False), str(kept))

    expect_error("a rejected relationship cannot carry a correction", "select * from public.review_relationship(%s,'rejected','member_of',false)", (draft.id,), "22023")
    expect_error("an invalid type is refused", "select * from public.review_relationship(%s,'confirmed','friends_with',false)", (draft.id,), "23514")
    item = json.loads(payload)[0]
    expect_error("an unaccepted mention cannot support a relationship", "select public.replace_relationships(%s, %s::jsonb)",
        (doc, json.dumps([{**item, "id": str(uuid4()), "object": {**item["object"], "mention_id": str(unaccepted)}}])), "22023")
    expect_error("a relationship for another document is refused", "select public.replace_relationships(%s, %s::jsonb)",
        (doc, json.dumps([{**item, "document_id": str(other_doc)}])), "22023")

    # The supporting mention is no longer accepted: the confirmed row turns stale, not deleted.
    c.execute("reset role")
    c.execute("update public.entity_mentions set review_status='rejected' where id=%s", (mentions[0].mention_id,))
    c.execute("set local role authenticated")
    as_user(u)
    result = c.execute("select public.replace_relationships(%s, '[]'::jsonb)", (doc,)).fetchone()[0]
    stale = c.execute("select review_status, stale from public.relationships where id=%s", (draft.id,)).fetchone()
    check("an unsupported confirmed relationship is marked stale, not deleted", stale == ("confirmed", True) and result["stale"] == 1, f"{stale} {result}")
    c.execute("select * from public.review_relationship(%s,'suggested')", (draft.id,))
    result = c.execute("select public.replace_relationships(%s, '[]'::jsonb)", (doc,)).fetchone()[0]
    check("an unsupported suggestion is removed", result["removed"] == 1, str(result))

    c.execute("reset role")
    c.execute("update public.entity_mentions set review_status='accepted' where id=%s", (mentions[0].mention_id,))
    c.execute("set local role authenticated")
    as_user(u)
    c.execute("select public.replace_relationships(%s, %s::jsonb)", (doc, payload))
    as_user(other)
    check("second user sees no relationships", c.execute("select count(*) from public.relationships").fetchone()[0] == 0)
    expect_error("second user cannot review the relationship", "select * from public.review_relationship(%s,'rejected')", (draft.id,), "P0002")
    expect_error("second user cannot replace another user's document", "select public.replace_relationships(%s,'[]'::jsonb)", (doc,), "P0002")
    expect_error("authenticated users cannot write the table directly",
        "insert into public.relationships(id,owner_id,document_id,type,subject_run_id,subject_mention_id,object_run_id,object_mention_id,subject,object,page_number,sentence_text,sentence_start,sentence_end,method,score) values (%s,%s,%s,'works_at',%s,%s,%s,%s,'{}','{}',1,'x',0,1,'m',.5)",
        (uuid4(), other, other_doc, run, mentions[0].mention_id, run, mentions[1].mention_id), "42501")
    c.execute("reset role")
    c.execute("set local role anon")
    expect_error("anon cannot read the table", "select count(*) from public.relationships", (), "42501")
    expect_error("anon cannot call the functions", "select public.replace_relationships(%s,'[]'::jsonb)", (doc,), "42501")
finally:
    c.rollback()
    c.close()
    print("rolled back;", sum(checks), "of", len(checks), "checks passed")
    sys.exit(0 if all(checks) else 1)
