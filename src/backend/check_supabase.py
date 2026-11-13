"""Read-only Supabase connectivity diagnostic: python -m src.backend.check_supabase."""

import asyncio
import getpass
import sys

import httpx

from .config import ConfigurationError, SupabaseSettings, load_supabase_settings


class ConnectionCheckError(Exception):
    """Sanitized diagnostic failure."""


async def check_connection(client: httpx.AsyncClient, settings: SupabaseSettings) -> str:
    try:
        response = await client.get(
            f"{settings.url}/auth/v1/health",
            headers={"apikey": settings.publishable_key},
            timeout=15, follow_redirects=False,
        )
    except httpx.RequestError:
        raise ConnectionCheckError("Could not reach Supabase; check the project URL, network, and project status") from None
    if response.status_code in (401, 403):
        raise ConnectionCheckError("Supabase rejected the publishable key; verify it belongs to this project")
    if not response.is_success:
        raise ConnectionCheckError(f"Supabase health request failed (HTTP {response.status_code})")
    try:
        data = response.json()
        if not isinstance(data, dict) or not data.get("name"):
            raise ValueError("Unexpected health response")
    except ValueError:
        raise ConnectionCheckError("Supabase returned an unexpected health response") from None
    return "PASS: Supabase Auth service is reachable with the configured publishable key."


async def check_documents(client: httpx.AsyncClient, settings: SupabaseSettings, token: str) -> str:
    if not token.strip() or any(char.isspace() for char in token):
        raise ConnectionCheckError("Enter a valid user access token")
    headers = {"apikey": settings.publishable_key, "Authorization": f"Bearer {token}"}
    try:
        # Validate the user before treating an empty RLS result as a successful read.
        user = await client.get(f"{settings.url}/auth/v1/user", headers=headers, timeout=15, follow_redirects=False)
        if user.status_code in (401, 403):
            raise ConnectionCheckError("User access token is invalid or expired")
        if not user.is_success:
            raise ConnectionCheckError(f"User validation failed (HTTP {user.status_code})")
        data = user.json()
        if not isinstance(data, dict) or not data.get("id"):
            raise ValueError("Unexpected user response")
        response = await client.get(
            f"{settings.url}/rest/v1/documents",
            headers=headers,
            params={"select": "id,title,document_date,collection,synthetic,created_at", "limit": 1},
            timeout=15, follow_redirects=False,
        )
        if response.status_code == 404:
            raise ConnectionCheckError("Documents table was not found in the Data API; apply the migration and check schema exposure")
        if not response.is_success:
            raise ConnectionCheckError(f"Documents read failed (HTTP {response.status_code}); check the migration, grants, and RLS policies")
        rows = response.json()
        if not isinstance(rows, list):
            raise ValueError("Unexpected documents response")
    except httpx.RequestError:
        raise ConnectionCheckError("Could not reach Supabase during the document check") from None
    except ValueError:
        raise ConnectionCheckError("Supabase returned an unexpected response") from None
    return "PASS: Authenticated documents metadata read succeeded. No data was changed."


def main() -> int:
    if any(arg != "--documents" for arg in sys.argv[1:]):
        print("Usage: python -m src.backend.check_supabase [--documents]")
        return 2
    try:
        settings = load_supabase_settings()
        token = getpass.getpass("User access token (hidden): ") if "--documents" in sys.argv else None

        async def run():
            async with httpx.AsyncClient(trust_env=False) as client:
                print(await check_connection(client, settings))
                if token is not None:
                    print(await check_documents(client, settings, token))

        asyncio.run(run())
    except (ConfigurationError, ConnectionCheckError) as error:
        print(f"FAIL: {error}")
        return 1
    except (EOFError, KeyboardInterrupt):
        print("Connection check cancelled.")
        return 1
    if token is None:
        print("Document schema, user authentication, and RLS isolation have not been verified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
