"""Authentication API; sign-in and refresh remain Supabase responsibilities."""

from typing import Annotated

from fastapi import APIRouter, Depends, Response

from ..schemas.auth import AuthUser
from .dependencies import AuthenticatedSession, get_authenticated_session


router = APIRouter(prefix="/auth", tags=["Authentication"])


@router.get("/me", response_model=AuthUser)
async def get_me(
    response: Response,
    session: Annotated[AuthenticatedSession, Depends(get_authenticated_session)],
) -> AuthUser:
    """Return the user verified by Supabase for the supplied access token."""
    response.headers["Cache-Control"] = "no-store"
    return session.user
