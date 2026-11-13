"""Construct a user-scoped document store from backend configuration."""

from contextlib import asynccontextmanager

import httpx

from ..config import SupabaseSettings, load_supabase_settings
from .documents import SupabaseDocumentStore


@asynccontextmanager
async def open_document_store(access_token: str, settings: SupabaseSettings | None = None):
    settings = settings or load_supabase_settings()
    async with httpx.AsyncClient(trust_env=False) as client:
        yield SupabaseDocumentStore(
            client, url=settings.url,
            publishable_key=settings.publishable_key, access_token=access_token,
        )
