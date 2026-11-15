"""Opt-in live database checks. Every synthetic row is rolled back."""

import os
from urllib.parse import urlsplit
from uuid import uuid4

import pytest


@pytest.mark.skipif(os.environ.get("DIGITAL_MEMORY_RUN_DB_TESTS") != "1",
    reason="Set DIGITAL_MEMORY_RUN_DB_TESTS=1 for rollback-only live processing/RLS checks")
def test_processing_rpc_ownership_transitions_and_stale_attempts():
    import psycopg
    from psycopg.types.json import Jsonb
    from dotenv import dotenv_values
    from backend.config import ENV_FILE, load_supabase_settings

    settings = load_supabase_settings()
    values = {**dotenv_values(ENV_FILE, interpolate=False, encoding="utf-8-sig"), **os.environ}
    ref = urlsplit(settings.url).hostname.removesuffix(".supabase.co")
    with psycopg.connect(host=f"db.{ref}.supabase.co", dbname="postgres", user="postgres",
        password=values["SUPABASE_DATABASE_PASSWORD"], sslmode="require", connect_timeout=10,
        autocommit=True) as connection:
        with connection.transaction(force_rollback=True):
            owner = connection.execute("select id from auth.users order by created_at limit 1").fetchone()
            if owner is None:
                pytest.skip("A development user is required; this test never creates accounts")
            owner = owner[0]
            other = uuid4()
            doc = uuid4()
            connection.execute("""insert into public.documents
                (id,owner_id,title,content,status,storage_path,original_filename,file_size_bytes,page_count)
                values (%s,%s,'Rollback-only processing test','','uploaded',%s,'synthetic.pdf',100,1)""",
                (doc, owner, f"{owner}/{doc}/original.pdf"))

            def act_as(user):
                connection.execute("set local role authenticated")
                connection.execute("select set_config('request.jwt.claim.sub',%s,true)", (str(user),))

            act_as(owner)
            first = connection.execute("select public.start_document_extraction(%s)", (doc,)).fetchone()[0]
            assert connection.execute("select status from public.documents where id=%s", (doc,)).fetchone() == ("processing",)
            with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
                with connection.transaction():
                    connection.execute("select public.start_document_extraction(%s)", (doc,))
            report = {"native_extraction": {"document_id": str(doc)}}
            assert connection.execute("select public.finish_document_extraction(%s,'ready',%s)", (first, Jsonb(report))).fetchone() == (True,)
            second = connection.execute("select public.start_document_extraction(%s)", (doc,)).fetchone()[0]
            assert connection.execute("select public.finish_document_extraction(%s,'failed',null)", (first,)).fetchone() == (False,)

            connection.execute("reset role")
            connection.execute("update public.documents set processing_started_at=now()-interval '21 minutes' where id=%s", (doc,))
            act_as(owner)
            third = connection.execute("select public.start_document_extraction(%s)", (doc,)).fetchone()[0]
            assert connection.execute("select status from public.document_extractions where id=%s", (second,)).fetchone() == ("failed",)
            assert connection.execute("select public.finish_document_extraction(%s,'needs_review',%s)", (third, Jsonb(report))).fetchone() == (True,)
            assert connection.execute("select status from public.documents where id=%s", (doc,)).fetchone() == ("needs_review",)
            act_as(other)
            assert connection.execute("select id from public.documents where id=%s", (doc,)).fetchall() == []
            assert connection.execute("select id from public.document_extractions where document_id=%s", (doc,)).fetchall() == []
            with pytest.raises(psycopg.errors.NoDataFound):
                with connection.transaction():
                    connection.execute("select public.start_document_extraction(%s)", (doc,))
            with pytest.raises(psycopg.errors.NoDataFound):
                with connection.transaction():
                    connection.execute("select public.finish_document_extraction(%s,'failed',null)", (third,))
            connection.execute("set local role anon")
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                with connection.transaction():
                    connection.execute("select public.start_document_extraction(%s)", (doc,))
