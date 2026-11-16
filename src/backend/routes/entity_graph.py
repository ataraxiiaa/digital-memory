"""Cross-document canonical entities: resolve, browse, merge, split, rename."""

from typing import Annotated, Literal
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, Query, Response

from ..auth.dependencies import AuthenticatedSession, get_authenticated_session, get_auth_client, get_auth_settings
from ..config import SupabaseSettings
from ..schemas.entity_graph import (
    ConfirmSuggestionRequest, EntityDetail, EntityList, MentionTarget, MergeRequest, RenameRequest,
    ResolveResult, SuggestionList,
)
from ..storage.entity_graph import SupabaseEntityGraphStore


async def no_store(response: Response):
    response.headers["Cache-Control"] = "no-store"


router = APIRouter(prefix="/entities", tags=["Entity identity"], dependencies=[Depends(no_store)])


def get_graph_store(
    session: Annotated[AuthenticatedSession, Depends(get_authenticated_session)],
    settings: Annotated[SupabaseSettings, Depends(get_auth_settings)],
    client: Annotated[httpx.AsyncClient, Depends(get_auth_client)],
):
    return SupabaseEntityGraphStore(client, settings, session.access_token.get_secret_value())


Graph = Annotated[SupabaseEntityGraphStore, Depends(get_graph_store)]
EntityType = Literal["person", "organization", "project", "model", "method", "dataset", "component",
                     "date", "event", "location"]


@router.post("/resolve", response_model=ResolveResult)
async def resolve_entities(graph: Graph) -> ResolveResult:
    """Group accepted mentions into entities and refresh merge suggestions. Nothing merges."""
    return await graph.resolve()


@router.get("", response_model=EntityList)
async def list_entities(
    graph: Graph,
    type: Annotated[EntityType | None, Query()] = None,
    status: Annotated[Literal["suggested", "confirmed"] | None, Query()] = None,
    q: Annotated[str | None, Query(max_length=100)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> EntityList:
    return await graph.list_entities(type_=type, status=status, q=q, limit=limit, offset=offset)


@router.get("/suggestions", response_model=SuggestionList)
async def list_suggestions(
    graph: Graph,
    status: Annotated[Literal["pending", "accepted", "dismissed"], Query()] = "pending",
    min_score: Annotated[float | None, Query(ge=0, le=1)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> SuggestionList:
    return await graph.list_suggestions(status=status, min_score=min_score, limit=limit, offset=offset)


@router.post("/suggestions/{suggestion_id}/confirm", response_model=EntityDetail)
async def confirm_suggestion(suggestion_id: UUID, graph: Graph,
                             body: ConfirmSuggestionRequest | None = None) -> EntityDetail:
    """Merge the two suggested entities. The surviving entity keeps every mention."""
    kept = await graph.confirm_suggestion(suggestion_id, body.keep_id if body else None)
    return await graph.get_entity(kept)


@router.post("/suggestions/{suggestion_id}/dismiss", status_code=204)
async def dismiss_suggestion(suggestion_id: UUID, graph: Graph) -> Response:
    await graph.dismiss(suggestion_id)
    return Response(status_code=204, headers={"Cache-Control": "no-store"})


@router.post("/merge", response_model=EntityDetail)
async def merge_entities(body: MergeRequest, graph: Graph) -> EntityDetail:
    await graph.merge(body.keep_id, body.absorb_id)
    return await graph.get_entity(body.keep_id)


@router.post("/merges/{merge_id}/undo", status_code=204)
async def undo_merge(merge_id: UUID, graph: Graph) -> Response:
    await graph.undo_merge(merge_id)
    return Response(status_code=204, headers={"Cache-Control": "no-store"})


@router.post("/mentions/split", response_model=EntityDetail, status_code=201)
async def split_mention(body: MentionTarget, graph: Graph) -> EntityDetail:
    """Move one mention into a new entity of its own (also how a mention is unlinked)."""
    return await graph.get_entity(await graph.split(body.run_id, body.mention_id))


@router.get("/{entity_id}", response_model=EntityDetail)
async def get_entity(entity_id: UUID, graph: Graph) -> EntityDetail:
    return await graph.get_entity(entity_id)


@router.patch("/{entity_id}", response_model=EntityDetail)
async def rename_entity(entity_id: UUID, body: RenameRequest, graph: Graph) -> EntityDetail:
    await graph.rename(entity_id, body.name)
    return await graph.get_entity(entity_id)


@router.post("/{entity_id}/confirm", response_model=EntityDetail)
async def confirm_entity(entity_id: UUID, graph: Graph) -> EntityDetail:
    await graph.confirm_entity(entity_id)
    return await graph.get_entity(entity_id)


@router.post("/{entity_id}/link", response_model=EntityDetail)
async def link_mention(entity_id: UUID, body: MentionTarget, graph: Graph) -> EntityDetail:
    await graph.link(entity_id, body.run_id, body.mention_id)
    return await graph.get_entity(entity_id)
