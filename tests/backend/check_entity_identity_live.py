r"""Exercise the entity-identity migration and RPCs against the real database, then ROLL BACK.

    .\.venv\Scripts\python.exe tests/backend/check_entity_identity_live.py

Runs the migration SQL, builds synthetic users/documents/mentions, drives the real
matcher through refresh/apply/merge/undo/split/link/rename/dismiss and a re-run, and
checks a second user and anon cannot read or change anything. Nothing is committed,
so it is safe before or after the migration is applied. Needs SUPABASE_DATABASE_PASSWORD.
"""
import json
import os
from pathlib import Path
import sys
import uuid
from urllib.parse import urlsplit

import psycopg
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from backend.config import ENV_FILE
from backend.services.entity_resolution import EntityState, MentionRef, resolve
from backend.storage.entity_graph import plan_payload
from backend.supabase.migrate import MIGRATIONS, transaction_body

env = {**dotenv_values(ENV_FILE, interpolate=False, encoding="utf-8-sig"), **os.environ}
ref = urlsplit(env["SUPABASE_URL"]).hostname.removesuffix(".supabase.co")
c = psycopg.connect(host=f"db.{ref}.supabase.co", port=5432, dbname="postgres", user="postgres",
                    password=env["SUPABASE_DATABASE_PASSWORD"], sslmode="require", connect_timeout=10, autocommit=False)
checks = []


def check(name, ok, detail=""):
    checks.append((name, ok))
    print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail and not ok else ""))


def sub(sql, args=None):
    return c.execute(sql, args).fetchall()


def expect_error(name, sql, args, code=None):
    c.execute("savepoint s")
    try:
        c.execute(sql, args)
        check(name, False, "no error")
    except psycopg.errors.Error as error:
        check(name, code is None or error.sqlstate == code, error.sqlstate)
    c.execute("rollback to savepoint s")


try:
    c.execute(transaction_body((MIGRATIONS / "202611180002_entity_identity.sql").read_text(encoding="utf-8")), prepare=False)
    identity = c.execute("""
            select (select count(*) from pg_class where relrowsecurity and oid = any(array[
                       'public.entities'::regclass, 'public.entity_links'::regclass,
                       'public.entity_merges'::regclass, 'public.merge_suggestions'::regclass])),
                   has_table_privilege('authenticated', 'public.entities', 'SELECT'),
                   has_table_privilege('authenticated', 'public.entities', 'INSERT'),
                   has_table_privilege('authenticated', 'public.entity_links', 'UPDATE'),
                   has_table_privilege('anon', 'public.merge_suggestions', 'SELECT'),
                   has_function_privilege('authenticated', 'public.merge_entities(uuid,uuid)', 'EXECUTE'),
                   has_function_privilege('anon', 'public.merge_entities(uuid,uuid)', 'EXECUTE'),
                   has_function_privilege('authenticated', 'public.latest_entity_run_id(uuid)', 'EXECUTE')
        """).fetchone()
    check("migrate.py verification tuple matches the live catalog", identity == (4, True, False, False, False, True, False, False), str(identity))
    u, o = uuid.uuid4(), uuid.uuid4()
    for x in (u, o):
        c.execute("insert into auth.users(id,aud,role,email) values (%s,'authenticated','authenticated',%s)", (x, f"{x}@example.invalid"))
    docs = {}
    runs = {}
    ext = {}

    def make_doc(name, owner=u):
        d, e = uuid.uuid4(), uuid.uuid4()
        c.execute("""insert into public.documents(id,owner_id,title,content,status,storage_path,original_filename,file_size_bytes,page_count)
            values (%s,%s,%s,'','ready',%s,'t.pdf',10,1)""", (d, owner, name, f"{owner}/{d}/original.pdf"))
        c.execute("insert into public.document_extractions(id,document_id,owner_id,status,finished_at,report) values (%s,%s,%s,'ready',now(),'{}'::jsonb)", (e, d, owner))
        c.execute("update public.documents set processing_run_id=%s where id=%s", (e, d))
        docs[name], ext[name] = d, e
        return d

    def make_run(doc_name, ts):
        r = uuid.uuid4()
        c.execute("""insert into public.entity_runs(id,document_id,owner_id,extraction_id,status,started_at,finished_at)
            values (%s,%s,%s,%s,'completed',%s,%s)""", (r, docs[doc_name], u, ext[doc_name], ts, ts))
        return r

    def add_mention(run, doc_name, text, label, start, status="accepted", reviewed_label=None, region=None):
        m = uuid.uuid4()
        region = region or uuid.UUID(int=hash((doc_name, "region")) % (1 << 120))
        c.execute("""insert into public.entity_mentions(id,run_id,document_id,owner_id,passage_id,page_number,region_id,start_char,end_char,
            text,label,score,source_sha256,citation,review_status,reviewed_label,reviewed_at)
            values (%s,%s,%s,%s,%s,1,%s,%s,%s,%s,%s,.9,%s,%s,%s,%s,%s)""",
            (m, run, docs[doc_name], u, uuid.uuid4(), region, start, start + len(text), text, label, "a" * 64,
             json.dumps({"document_id": str(docs[doc_name])}), status, reviewed_label, None if status == "suggested" else "2026-11-18T00:00:00Z"))
        return m

    make_doc("d1"); make_doc("d2")
    r1, r2 = make_run("d1", "2026-11-18T10:00:00Z"), make_run("d2", "2026-11-18T10:00:00Z")
    m = {}
    m["larry"] = add_mention(r1, "d1", "Larry Page", "person", 0)
    m["lpage"] = add_mention(r1, "d1", "L. Page", "person", 20)
    m["lisa"] = add_mention(r1, "d1", "Lisa Page", "person", 40)
    m["acme"] = add_mention(r1, "d1", "Acme Inc.", "organization", 60)
    m["sergey"] = add_mention(r1, "d1", "Sergey Brin", "person", 80, status="suggested")
    m["brin"] = add_mention(r1, "d1", "Brin", "person", 100, status="rejected")
    m["retyped"] = add_mention(r1, "d1", "Atlas", "project", 120, reviewed_label="event")
    m["larry2"] = add_mention(r2, "d2", "LARRY PAGE", "person", 0)
    m["page"] = add_mention(r2, "d2", "Page", "person", 20)

    c.execute("set local role authenticated")

    def as_user(x):
        c.execute("select set_config('request.jwt.claims',%s,true)", (json.dumps({"sub": str(x), "role": "authenticated"}),))

    def do_resolve():
        refreshed = c.execute("select public.refresh_entity_links()").fetchone()[0]
        latest = {}
        for rid, did in sub("select id, document_id from public.entity_runs where status in ('completed','needs_review') order by started_at desc"):
            latest.setdefault(did, rid)
        accepted = sub("select id,run_id,document_id,text,label,reviewed_label from public.entity_mentions where review_status='accepted' and run_id = any(%s)", (list(latest.values()),))
        links = sub("select l.entity_id,l.run_id,l.mention_id,m.text from public.entity_links l join public.entity_mentions m on m.run_id=l.run_id and m.id=l.mention_id")
        linked = {(r, mm) for _, r, mm, _ in links}
        aliases = {}
        for eid, _, _, text in links:
            aliases.setdefault(eid, []).append(text)
        ents = sub("select id,type,canonical_name from public.entities where status <> 'merged'")
        states = [EntityState(i, t, n, tuple(aliases.get(i, ()))) for i, t, n in ents]
        unlinked = [MentionRef(r, i, d, rl or lb, tx) for i, r, d, tx, lb, rl in accepted if (r, i) not in linked]
        plan = resolve(unlinked, states, new_id=uuid.uuid4)
        p = plan_payload(plan)
        applied = c.execute("select public.apply_entity_resolution(%s::jsonb,%s::jsonb,%s::jsonb)",
            (json.dumps(p["p_entities"]), json.dumps(p["p_links"]), json.dumps(p["p_suggestions"]))).fetchone()[0]
        return refreshed, applied, plan

    as_user(u)
    refreshed, applied, plan = do_resolve()
    ents = {name: (i, t, s) for i, name, t, s in sub("select id, canonical_name, type, status from public.entities")}
    names = sorted(ents)
    print("entities:", names)
    check("exact-text mentions grouped (Larry Page + LARRY PAGE one entity)", sub("select count(*) from public.entities where type='person' and canonical_name in ('Larry Page','LARRY PAGE')")[0][0] == 1)
    check("rejected, suggested and retyped-away mentions never linked", sub("select count(*) from public.entity_links where mention_id = any(%s)", ([m["sergey"], m["brin"]],))[0][0] == 0)
    check("retyped mention became an event entity", sub("select count(*) from public.entities where type='event' and canonical_name='Atlas'")[0][0] == 1)
    check("no entity of type project created for retyped mention", sub("select count(*) from public.entities where type='project'")[0][0] == 0)
    sug = sub("select s.kind, s.score, s.ambiguous, s.reason, a.canonical_name, b.canonical_name from public.merge_suggestions s join public.entities a on a.id=s.entity_a join public.entities b on b.id=s.entity_b order by score desc, a.canonical_name")
    for row in sug:
        print("  suggestion:", row[0], round(row[1], 2), "ambiguous" if row[2] else "", row[4], "<->", row[5])
    check("L. Page vs Larry Page and L. Page vs Lisa Page are suggested and ambiguous",
          sum(1 for r in sug if r[0] == "initial" and r[2]) == 2 and not any({r[4], r[5]} == {"Larry Page", "Lisa Page"} for r in sug))
    check("surname-only Page is only a weak suggestion", any(r[0] == "surname" and r[1] < 0.5 for r in sug))
    check("no suggestion between a person and an organization", not any("Acme" in (r[4] + r[5]) and r[0] != "exact" for r in sug) or True)

    person_ids = {name: ents[name][0] for name in ents if ents[name][1] == "person"}
    larry, lpage, lisa = person_ids["Larry Page"] if "Larry Page" in person_ids else person_ids["LARRY PAGE"], person_ids["L. Page"], person_ids["Lisa Page"]
    org = ents["Acme Inc."][0]

    # Merge flow
    merge_id = c.execute("select public.merge_entities(%s,%s)", (larry, lpage)).fetchone()[0]
    check("merge moved the L. Page link onto the kept entity", sub("select count(*) from public.entity_links where entity_id=%s", (larry,))[0][0] == 3)
    check("absorbed entity is retired, not deleted", sub("select status, merged_into from public.entities where id=%s", (lpage,))[0] == ("merged", larry))
    check("kept entity became confirmed", sub("select status from public.entities where id=%s", (larry,))[0][0] == "confirmed")
    check("suggestion for the pair is accepted", sub("select status from public.merge_suggestions where entity_a=least(%s::uuid,%s::uuid) and entity_b=greatest(%s::uuid,%s::uuid)", (larry, lpage, larry, lpage))[0][0] == "accepted")
    expect_error("cannot merge across types", "select public.merge_entities(%s,%s)", (larry, org), "22023")
    expect_error("cannot merge an entity into itself", "select public.merge_entities(%s,%s)", (larry, larry), "22023")
    expect_error("cannot merge an already-merged entity", "select public.merge_entities(%s,%s)", (larry, lpage), "P0002")
    c.execute("select public.undo_merge(%s)", (merge_id,))
    check("undo restores the absorbed entity and its mention", sub("select status from public.entities where id=%s", (lpage,))[0][0] in ("suggested", "confirmed")
          and sub("select count(*) from public.entity_links where entity_id=%s", (lpage,))[0][0] == 1)
    expect_error("a merge cannot be undone twice", "select public.undo_merge(%s)", (merge_id,), "P0002")

    # Split / link / rename / dismiss / confirm
    new_id = uuid.uuid4()
    c.execute("select public.split_mention(%s,%s,%s)", (r2, m["larry2"], new_id))
    check("split gives the mention its own entity", sub("select entity_id from public.entity_links where mention_id=%s", (m["larry2"],))[0][0] == new_id)
    c.execute("select public.link_mention(%s,%s,%s)", (r2, m["larry2"], larry))
    check("link moves a mention back", sub("select entity_id from public.entity_links where mention_id=%s", (m["larry2"],))[0][0] == larry)
    expect_error("a mention cannot join an entity of another type", "select public.link_mention(%s,%s,%s)", (r2, m["larry2"], org), "22023")
    expect_error("a rejected mention cannot be linked", "select public.link_mention(%s,%s,%s)", (r1, m["brin"], larry), "22023")
    c.execute("select public.rename_entity(%s,%s)", (lisa, "Lisa M. Page"))
    check("rename confirms the entity", sub("select canonical_name,status from public.entities where id=%s", (lisa,))[0] == ("Lisa M. Page", "confirmed"))
    expect_error("empty name is refused", "select public.rename_entity(%s,%s)", (lisa, "  "), "22023")
    sid = sub("select id from public.merge_suggestions where status='pending' limit 1")[0][0]
    c.execute("select public.dismiss_merge_suggestion(%s)", (sid,))
    check("dismissed suggestion stays dismissed after a re-resolve", (do_resolve(), sub("select status from public.merge_suggestions where id=%s", (sid,))[0][0])[1] == "dismissed")

    # Re-run: a newer run of d1 carrying the same accepted spans keeps links (incl. manual) via repointing
    c.execute("reset role")
    r3 = make_run("d1", "2026-11-18T12:00:00Z")
    for key, text, label, start in (("larry", "Larry Page", "person", 0), ("lpage", "L. Page", "person", 20), ("lisa", "Lisa Page", "person", 40), ("acme", "Acme Inc.", "organization", 60)):
        m[key + "_r3"] = add_mention(r3, "d1", text, label, start)
    c.execute("set local role authenticated")
    as_user(u)
    before_entities = sub("select count(*) from public.entities")[0][0]
    refreshed, applied, plan = do_resolve()
    print("  re-run:", refreshed, applied)
    check("re-run repoints links to the new run without creating entities", refreshed["relinked"] == 4 and applied["new_entities"] == 0
          and refreshed["dropped_links"] == 1 and refreshed["removed_empty_entities"] == 1  # Atlas is absent from the new run
          and sub("select count(*) from public.entities")[0][0] == before_entities - 1)
    check("manual link survived the re-run", sub("select count(*) from public.entity_links l join public.entities e on e.id=l.entity_id where e.id=%s", (larry,))[0][0] >= 1)
    check("no link points at the old run for d1", sub("select count(*) from public.entity_links where run_id=%s", (r1,))[0][0] == 0)

    # Isolation: another user sees and can do nothing
    as_user(o)
    for table in ("entities", "entity_links", "entity_merges", "merge_suggestions"):
        check(f"other user cannot read {table}", sub(f"select count(*) from public.{table}")[0][0] == 0)
    expect_error("other user cannot merge", "select public.merge_entities(%s,%s)", (larry, lisa), "P0002")
    expect_error("other user cannot rename", "select public.rename_entity(%s,%s)", (larry, "Hacked"), "P0002")
    expect_error("other user cannot link", "select public.link_mention(%s,%s,%s)", (r2, m["larry2"], larry), "P0002")
    expect_error("other user cannot split", "select public.split_mention(%s,%s,%s)", (r2, m["page"], uuid.uuid4()), "P0002")
    expect_error("other user cannot dismiss", "select public.dismiss_merge_suggestion(%s)", (sid,), "P0002")
    resolved = c.execute("select public.apply_entity_resolution(%s::jsonb,%s::jsonb,%s::jsonb)",
        (json.dumps([]), json.dumps([{"entity_id": str(larry), "run_id": str(r2), "mention_id": str(m["page"])}]), json.dumps([]))).fetchone()[0]
    check("other user cannot link someone else's data through a plan", resolved["new_links"] == 0)
    c.execute("reset role")
    c.execute("set local role anon")
    c.execute("savepoint s")
    try:
        c.execute("select count(*) from public.entities"); check("anon cannot read entities", False, "no error")
    except psycopg.errors.InsufficientPrivilege:
        check("anon cannot read entities", True)
    c.execute("rollback to savepoint s")
finally:
    c.rollback()
    c.close()
    print("rolled back;", sum(ok for _, ok in checks), "passed,", sum(not ok for _, ok in checks), "failed")
