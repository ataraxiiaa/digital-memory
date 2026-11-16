"""Live two-user isolation check: python -m src.backend.check_isolation DOCUMENT_ID.

User A must own a PDF document that has been extracted and has an entity run.
User B is a second, unrelated account. The check proves that A can see their data
(a positive control, so empty results cannot pass vacuously) and that B, and an
unauthenticated caller, can read none of it and cannot call the ownership-checked
functions against it. Tokens are read with hidden prompts and never printed.

Attempts made as B are expected to fail. If isolation were broken, the attempt to
start processing or an entity run could create a record on A's document, so run
this against test data only.
"""

import asyncio
from dataclasses import dataclass
import getpass
import sys
from uuid import UUID, uuid4

import httpx

from .config import ConfigurationError, SupabaseSettings, load_supabase_settings


@dataclass(frozen=True)
class Check:
    name: str
    passed: bool
    detail: str = ""


class Api:
    def __init__(self, client: httpx.AsyncClient, settings: SupabaseSettings, token: str | None):
        self.client = client
        self.base = settings.url.rstrip("/")
        self.headers = {"apikey": settings.publishable_key}
        if token:
            self.headers["Authorization"] = f"Bearer {token}"

    async def get(self, path: str, **params) -> httpx.Response:
        return await self.client.get(f"{self.base}/{path}", headers=self.headers, params=params, timeout=30, follow_redirects=False)

    async def rpc(self, name: str, body: dict) -> httpx.Response:
        return await self.client.post(f"{self.base}/rest/v1/rpc/{name}", headers=self.headers, json=body, timeout=30, follow_redirects=False)

    async def user_id(self) -> str | None:
        response = await self.get("auth/v1/user")
        return response.json().get("id") if response.is_success else None


def rows(response: httpx.Response) -> list | None:
    if not response.is_success:
        return None
    try:
        data = response.json()
    except ValueError:
        return None
    return data if isinstance(data, list) else None


async def run_checks(client: httpx.AsyncClient, settings: SupabaseSettings,
                     token_a: str, token_b: str, document_id: UUID) -> list[Check]:
    a, b, anon = Api(client, settings, token_a), Api(client, settings, token_b), Api(client, settings, None)
    checks: list[Check] = []

    def add(name: str, passed: bool, detail: str = ""):
        checks.append(Check(name, passed, detail))

    id_a, id_b = await a.user_id(), await b.user_id()
    add("Both tokens are valid and belong to different users", bool(id_a and id_b and id_a != id_b))
    if not (id_a and id_b and id_a != id_b):
        return checks

    # Positive control: A sees their own rows.
    doc = rows(await a.get("rest/v1/documents", select="id,storage_path,processing_run_id", id=f"eq.{document_id}"))
    add("A can read their document", bool(doc), "Document not found for user A" if not doc else "")
    if not doc:
        return checks
    storage_path, extraction_id = doc[0]["storage_path"], doc[0]["processing_run_id"]
    runs = rows(await a.get("rest/v1/entity_runs", select="id,status", document_id=f"eq.{document_id}", limit=1)) or []
    add("A has an entity run to protect", bool(runs), "Run entity extraction on this document first")
    mentions = []
    if runs:
        mentions = rows(await a.get("rest/v1/entity_mentions", select="id,review_status,reviewed_label",
            run_id=f"eq.{runs[0]['id']}", review_status="eq.suggested", limit=1)) or []
    if not runs or not extraction_id:
        return checks

    tables = {
        "documents": {"id": f"eq.{document_id}"},
        "document_extractions": {"document_id": f"eq.{document_id}"},
        "entity_runs": {"document_id": f"eq.{document_id}"},
        "entity_mentions": {"document_id": f"eq.{document_id}"},
    }
    for caller_name, caller in (("B", b), ("an unauthenticated caller", anon)):
        for table, filters in tables.items():
            response = await caller.get(f"rest/v1/{table}", select="id", limit=1, **filters)
            data = rows(response)
            # An error or an empty list both mean nothing leaked.
            add(f"{caller_name} cannot read {table}", not data, f"{len(data)} row(s) visible" if data else "")

    storage = await b.get(f"storage/v1/object/authenticated/documents/{storage_path}")
    add("B cannot download A's original PDF", not storage.is_success, f"HTTP {storage.status_code}")
    anon_storage = await anon.get(f"storage/v1/object/authenticated/documents/{storage_path}")
    add("An unauthenticated caller cannot download the PDF", not anon_storage.is_success)

    attempts = [
        ("start_document_extraction", {"p_document_id": str(document_id)}),
        ("start_entity_run", {"p_document_id": str(document_id), "p_run_id": str(uuid4())}),
        ("finish_entity_run", {"p_run_id": runs[0]["id"], "p_status": "failed"}),
        ("finish_document_extraction", {"p_run_id": extraction_id, "p_status": "failed"}),
    ]
    if mentions:
        attempts.append(("review_entity_mention", {"p_run_id": runs[0]["id"], "p_mention_id": mentions[0]["id"],
            "p_status": "suggested", "p_label": None}))
    for name, body in attempts:
        response = await b.rpc(name, body)
        add(f"B cannot call {name} on A's data", not response.is_success, f"HTTP {response.status_code}")
    if not mentions:
        add("B cannot call review_entity_mention on A's data", False,
            "Skipped: A needs at least one mention still awaiting review")

    # A's data must be unchanged after B's attempts.
    after = rows(await a.get("rest/v1/entity_runs", select="status", id=f"eq.{runs[0]['id']}")) or []
    add("A's entity run is unchanged after B's attempts", bool(after) and after[0]["status"] == runs[0]["status"])
    return checks


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: python -m src.backend.check_isolation DOCUMENT_ID")
        return 2
    try:
        document_id = UUID(sys.argv[1])
        settings = load_supabase_settings()
        token_a = getpass.getpass("User A access token (hidden): ").strip()
        token_b = getpass.getpass("User B access token (hidden): ").strip()
    except (ValueError, ConfigurationError) as error:
        print(f"FAIL: {error}")
        return 1
    except (EOFError, KeyboardInterrupt):
        print("Cancelled.")
        return 1

    async def go():
        async with httpx.AsyncClient(trust_env=False) as client:
            return await run_checks(client, settings, token_a, token_b, document_id)

    try:
        checks = asyncio.run(go())
    except httpx.RequestError:
        print("FAIL: Could not reach Supabase.")
        return 1
    for check in checks:
        print(f"{'PASS' if check.passed else 'FAIL'}: {check.name}" + (f" ({check.detail})" if check.detail and not check.passed else ""))
    failed = sum(not check.passed for check in checks)
    print(f"{len(checks) - failed} passed, {failed} failed.")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
