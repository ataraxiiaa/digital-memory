"""FastAPI entry point with public health and authenticated user routes."""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from backend.auth.routes import router as auth_router
from backend.schemas.health import HealthResponse
from backend.routes.documents import router as documents_router
from backend.routes.entities import router as entities_router
from backend.routes.text_notes import router as text_notes_router
from backend.routes.entity_graph import router as entity_graph_router
from backend.routes.processing import router as processing_router
from backend.routes.timeline import router as timeline_router
from backend.routes.search import router as search_router
from backend.storage.documents import StorageUnavailable


app = FastAPI(
    title="Digital Memory API",
    version="0.1.0",
    description="Digital Memory API with Supabase-backed user authentication.",
)

app.include_router(auth_router)
app.include_router(documents_router)
app.include_router(processing_router)
app.include_router(entities_router)
app.include_router(text_notes_router)
app.include_router(entity_graph_router)
app.include_router(timeline_router)
app.include_router(search_router)


@app.middleware("http")
async def prevent_document_caching(request: Request, call_next):
    response = await call_next(request)
    if request.url.path == "/documents" or request.url.path.startswith("/documents/"):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.exception_handler(StorageUnavailable)
async def handle_storage_unavailable(request: Request, error: StorageUnavailable):
    return JSONResponse(status_code=503, content={"detail": "Document storage is unavailable"}, headers={"Cache-Control": "no-store"})


@app.get("/health", response_model=HealthResponse, tags=["Health"])
def get_health() -> HealthResponse:
    """Confirm that the API can receive a request and return a response."""
    return HealthResponse(status="ok", service="digital-memory-api")
