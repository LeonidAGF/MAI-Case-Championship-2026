"""Inspectable knowledge retrieval endpoints for the chat assistant."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_container, get_session, rate_limit
from app.chat.retriever import retrieve
from app.core.errors import NotFoundError

router = APIRouter(prefix="/knowledge", tags=["knowledge"])


@router.get("/status", dependencies=[rate_limit("knowledge", "rate_limit_per_minute")])
async def status(request: Request, session: AsyncSession = Depends(get_session)) -> dict:
    configured_model = get_container(request).settings.embeddings_model
    rows = await session.execute(
        text(
            """SELECT source, embedding_model, count(*) AS chunks,
                      count(embedding) AS embedded
               FROM knowledge_chunk GROUP BY source, embedding_model
               ORDER BY source, embedding_model"""
        )
    )
    groups = [dict(row._mapping) for row in rows]
    return {
        "configured_model": configured_model or None,
        "chunks": sum(group["chunks"] for group in groups),
        "embedded": sum(group["embedded"] for group in groups),
        "groups": groups,
    }


@router.get("/search", dependencies=[rate_limit("knowledge", "rate_limit_per_minute")])
async def search(
    request: Request,
    q: str = Query(min_length=2, max_length=500),
    limit: int = Query(default=5, ge=1, le=20),
    session: AsyncSession = Depends(get_session),
) -> dict:
    c = get_container(request)
    hits = await retrieve(session, q, top_k=limit, embedder=c.chat.embedder() if c.chat else None)
    for hit in hits:
        hit["source_url"] = hit["source_url"] or f"/knowledge/chunks/{hit['id']}"
    return {"query": q, "hits": hits}


@router.get("/chunks/{chunk_id}", dependencies=[rate_limit("knowledge", "rate_limit_per_minute")])
async def chunk(chunk_id: int, session: AsyncSession = Depends(get_session)) -> dict:
    row = (
        await session.execute(
            text("SELECT id, source, source_url, title, chunk_index, text, embedding_model FROM knowledge_chunk WHERE id = :id"),
            {"id": chunk_id},
        )
    ).first()
    if row is None:
        raise NotFoundError("Фрагмент базы знаний не найден")
    return dict(row._mapping)
