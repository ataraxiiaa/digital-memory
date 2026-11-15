"""Apply versioned local migrations using the configured project's database.

Run from the workspace root: python -m src.backend.supabase.migrate
The migration role is administrative; never expose this through an HTTP route.
"""

import hashlib
import os
from pathlib import Path
from urllib.parse import urlsplit

import psycopg
from dotenv import dotenv_values

from ..config import ENV_FILE, ConfigurationError, load_supabase_settings


MIGRATIONS = Path(__file__).resolve().parent / "migrations"


def transaction_body(sql: str) -> str:
    """Keep the existing SQL-editor wrapper inside our tracked transaction."""
    lines = sql.splitlines()
    statements = [i for i, line in enumerate(lines) if line.strip() and not line.lstrip().startswith("--")]
    if not statements or lines[statements[0]].strip().lower() != "begin;" or lines[statements[-1]].strip().lower() != "commit;":
        raise ValueError("Migration must have an outer BEGIN/COMMIT wrapper")
    lines[statements[0]] = ""
    lines[statements[-1]] = ""
    return "\n".join(lines)


def verify_documents(connection) -> None:
    columns = connection.execute("select column_name from information_schema.columns where table_schema = 'public' and table_name = 'documents'").fetchall()
    expected = {"id", "owner_id", "title", "content", "document_date", "collection", "synthetic", "created_at", "status", "storage_path", "original_filename", "file_size_bytes", "page_count", "processing_run_id", "processing_started_at", "processing_error"}
    if {row[0] for row in columns} != expected:
        raise ValueError("Document table columns do not match the migration")
    rls = connection.execute("select relrowsecurity from pg_class where oid = 'public.documents'::regclass").fetchone()
    policies = connection.execute("select policyname from pg_policies where schemaname = 'public' and tablename = 'documents'").fetchall()
    if not rls or not rls[0] or {row[0] for row in policies} != {"documents_read_own", "documents_insert_own"}:
        raise ValueError("Document RLS configuration did not pass verification")
    permissions = connection.execute("""
        select has_table_privilege('anon', 'public.documents', 'SELECT'),
               has_table_privilege('authenticated', 'public.documents', 'SELECT'),
               has_column_privilege('authenticated', 'public.documents', 'title', 'INSERT'),
               has_column_privilege('authenticated', 'public.documents', 'owner_id', 'INSERT'),
               has_table_privilege('authenticated', 'public.documents', 'UPDATE'),
               has_table_privilege('authenticated', 'public.documents', 'DELETE'),
               to_regclass('public.documents_owner_created_idx') is not null
    """).fetchone()
    if permissions != (False, True, True, False, False, False, True):
        raise ValueError("Document permissions or index did not pass verification")
    storage_policies = connection.execute("select policyname from pg_policies where schemaname = 'storage' and tablename = 'objects'").fetchall()
    required_policies = {"documents_files_insert_own", "documents_files_read_own", "documents_files_cleanup_own"}
    if not required_policies.issubset({row[0] for row in storage_policies}):
        raise ValueError("PDF Storage ownership policies are missing")
    bucket = connection.execute("select public from storage.buckets where id = 'documents'").fetchone()
    if bucket != (False,):
        raise ValueError("Documents bucket must exist and remain private")
    extraction = connection.execute("""
        select relrowsecurity from pg_class where oid = 'public.document_extractions'::regclass
    """).fetchone()
    permissions = connection.execute("""
        select has_table_privilege('authenticated', 'public.document_extractions', 'SELECT'),
               has_table_privilege('authenticated', 'public.document_extractions', 'INSERT'),
               has_table_privilege('authenticated', 'public.document_extractions', 'UPDATE'),
               has_table_privilege('anon', 'public.document_extractions', 'SELECT'),
               has_function_privilege('authenticated', 'public.start_document_extraction(uuid)', 'EXECUTE'),
               has_function_privilege('anon', 'public.start_document_extraction(uuid)', 'EXECUTE'),
               has_function_privilege('authenticated', 'public.finish_document_extraction(uuid,text,jsonb)', 'EXECUTE'),
               has_function_privilege('anon', 'public.finish_document_extraction(uuid,text,jsonb)', 'EXECUTE')
    """).fetchone()
    if extraction != (True,) or permissions != (True, False, False, False, True, False, True, False):
        raise ValueError("Extraction ownership or function permissions failed verification")


def apply_migrations(connection) -> list[str]:
    applied = []
    # Schema changes, history updates, and verification commit together.
    with connection.transaction():
        connection.execute("select pg_advisory_xact_lock(hashtext('digital_memory_migrations'))")
        connection.execute("create schema if not exists digital_memory_meta")
        connection.execute("revoke all on schema digital_memory_meta from public, anon, authenticated")
        connection.execute("""create table if not exists digital_memory_meta.schema_migrations (
            filename text primary key, sha256 text not null,
            applied_at timestamptz not null default now()
        )""")
        connection.execute("revoke all on digital_memory_meta.schema_migrations from public, anon, authenticated")
        for path in sorted(MIGRATIONS.glob("*.sql")):
            data = path.read_bytes()
            checksum = hashlib.sha256(data).hexdigest()
            existing = connection.execute("select sha256 from digital_memory_meta.schema_migrations where filename = %s", (path.name,)).fetchone()
            if existing:
                if existing[0] != checksum:
                    raise ValueError("An applied migration has changed; create a new migration instead")
                print(f"Already applied: {path.name}")
                continue
            if path.name == "202611130001_documents.sql" and connection.execute("select to_regclass('public.documents')").fetchone()[0] is not None:
                raise ValueError("Documents table already exists without this migration's history; review before proceeding")
            connection.execute(transaction_body(data.decode("utf-8-sig")), prepare=False)
            connection.execute("insert into digital_memory_meta.schema_migrations (filename, sha256) values (%s, %s)", (path.name, checksum))
            applied.append(path.name)
        verify_documents(connection)
        connection.execute("notify pgrst, 'reload schema'")
    return applied


def main() -> int:
    try:
        settings = load_supabase_settings()
        values = {**dotenv_values(ENV_FILE, interpolate=False, encoding="utf-8-sig"), **os.environ}
        password = values.get("SUPABASE_DATABASE_PASSWORD")
        if not password:
            raise ConfigurationError("Missing configuration: SUPABASE_DATABASE_PASSWORD")
        host = urlsplit(settings.url).hostname
        if not host or not host.endswith(".supabase.co"):
            raise ConfigurationError("Migration tooling requires the standard project.supabase.co URL")
        ref = host.removesuffix(".supabase.co")
        if "." in ref:
            raise ConfigurationError("Unexpected Supabase project hostname")
        with psycopg.connect(host=f"db.{ref}.supabase.co", port=5432,
                              dbname="postgres", user="postgres", password=password,
                              sslmode="require", connect_timeout=10, autocommit=True) as connection:
            applied = apply_migrations(connection)
        for name in applied:
            print(f"Applied: {name}")
        print("PASS: Document columns, index, RLS policies, permissions, and migration history verified.")
        return 0
    except (ConfigurationError, ValueError) as error:
        print(f"FAIL: {error}")
    except psycopg.Error as error:
        print(f"FAIL: Database operation failed (SQLSTATE {error.sqlstate or 'connection error'}). Credentials and server details omitted.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
