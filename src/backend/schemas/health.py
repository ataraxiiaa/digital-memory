"""Health endpoint response schema."""

from typing import Literal

from pydantic import BaseModel


class HealthResponse(BaseModel):
    """Liveness only; storage and external services are not checked."""

    status: Literal["ok"]
    service: Literal["digital-memory-api"]
