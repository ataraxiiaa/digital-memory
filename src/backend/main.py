"""Application entry point for the first backend checkpoint."""

from fastapi import FastAPI

from backend.schemas.health import HealthResponse


app = FastAPI(
    title="Digital Memory API",
    version="0.1.0",
    description="Local development API. Checkpoint 1: application health.",
)


@app.get("/health", response_model=HealthResponse, tags=["Health"])
def get_health() -> HealthResponse:
    """Confirm that the API can receive a request and return a response."""
    return HealthResponse(status="ok", service="digital-memory-api")
