"""Validate user tokens with Supabase before trusting user identity."""

from dataclasses import dataclass
from typing import Annotated

import httpx
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import SecretStr

from ..config import ConfigurationError, SupabaseSettings, load_supabase_settings
from ..schemas.auth import AuthUser


bearer_scheme = HTTPBearer(auto_error=False, description="Supabase user access token, not an API key or refresh token")


@dataclass(frozen=True)
class AuthenticatedSession:
    user: AuthUser
    access_token: SecretStr


def unauthorized() -> HTTPException:
    return HTTPException(
        status_code=401, detail="A valid user access token is required",
        headers={"WWW-Authenticate": "Bearer", "Cache-Control": "no-store"},
    )


def get_auth_settings() -> SupabaseSettings:
    try:
        return load_supabase_settings()
    except ConfigurationError:
        raise HTTPException(503, "Authentication is not configured", headers={"Cache-Control": "no-store"}) from None


async def get_auth_client():
    async with httpx.AsyncClient(trust_env=False) as client:
        yield client


def get_access_token(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
) -> str:
    # Reject malformed credentials before configuration lookup or network access.
    if len(request.headers.getlist("authorization")) != 1 or credentials is None:
        raise unauthorized()
    token = credentials.credentials
    if not token or len(token) > 16384 or any(char.isspace() or ord(char) < 33 or ord(char) > 126 for char in token):
        raise unauthorized()
    return token


async def get_authenticated_session(
    token: Annotated[str, Depends(get_access_token)],
    settings: Annotated[SupabaseSettings, Depends(get_auth_settings)],
    client: Annotated[httpx.AsyncClient, Depends(get_auth_client)],
) -> AuthenticatedSession:
    return await validate_session(token, settings, client)


async def validate_session(token: str, settings: SupabaseSettings, client: httpx.AsyncClient) -> AuthenticatedSession:
    try:
        response = await client.get(
            f"{settings.url}/auth/v1/user",
            headers={"apikey": settings.publishable_key, "Authorization": f"Bearer {token}"},
            timeout=10, follow_redirects=False,
        )
    except httpx.RequestError:
        raise HTTPException(503, "Authentication service is unavailable", headers={"Cache-Control": "no-store"}) from None
    if response.status_code in (400, 401, 403):
        raise unauthorized()
    if not response.is_success:
        raise HTTPException(503, "Authentication service is unavailable", headers={"Cache-Control": "no-store"})
    try:
        user = AuthUser.model_validate(response.json())
    except ValueError:
        raise HTTPException(502, "Authentication service returned an invalid response", headers={"Cache-Control": "no-store"}) from None
    return AuthenticatedSession(user=user, access_token=SecretStr(token))
