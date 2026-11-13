"""FastAPI entry point with public health and authenticated user routes."""

from fastapi import FastAPI

from backend.auth.routes import router as auth_router
from backend.schemas.health import HealthResponse


app = FastAPI(
    title="Digital Memory API",
    version="0.1.0",
    description="Digital Memory API with Supabase-backed user authentication.",
)

app.include_router(auth_router)


@app.get("/health", response_model=HealthResponse, tags=["Health"])
def get_health() -> HealthResponse:
    """Confirm that the API can receive a request and return a response."""
    return HealthResponse(status="ok", service="digital-memory-api")
