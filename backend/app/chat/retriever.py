"""Hybrid Russian full-text and multilingual vector retrieval."""

from __future__ import annotations

import asyncio
import logging
import os
import re
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

log = logging.getLogger(__name__)
WORD = re.compile(r"[A-Za-zА-Яа-яЁё][A-Za-zА-Яа-яЁё0-9]{1,}")


class Embedder:
    def __init__(self, model_name: str) -> None:
        from fastembed import TextEmbedding

        self.model_name = model_name
        self._model = TextEmbedding(
            model_name=model_name,
            cache_dir=os.environ.get("FASTEMBED_CACHE_PATH"),
            local_files_only=os.environ.get("EMBEDDINGS_OFFLINE", "").lower() == "true",
        )

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [vector.tolist() for vector in self._model.embed(texts)]


def make_embedder(model_name: str) -> Embedder | None:
    if not model_name:
        return None
    try:
        return Embedder(model_name)
    except Exception as exc:
        log.warning("Embeddings unavailable for %s: %s", model_name, exc)
        return None


def vector_literal(values: list[float]) -> str:
    return "[" + ",".join(f"{value:.8f}" for value in values) + "]"


async def retrieve(
    session: AsyncSession,
    query: str,
    *,
    top_k: int,
    embedder: Any | None = None,
) -> list[dict[str, Any]]:
    if top_k < 1 or not query.strip():
        return []
    limit = min(top_k * 3, 30)
    ranked: dict[int, dict[str, Any]] = {}

    def merge(rows: Any, method: str) -> None:
        for rank, row in enumerate(rows, 1):
            hit = dict(row._mapping)
            identifier = hit["id"]
            if identifier not in ranked:
                ranked[identifier] = {**hit, "score": 0.0, "retrieval_methods": []}
            ranked[identifier]["score"] += 1 / (60 + rank)
            ranked[identifier]["retrieval_methods"].append(method)

    words = list(dict.fromkeys(word.lower() for word in WORD.findall(query)))[:12]
    if words:
        rows = await session.execute(
            text(
                """SELECT id, title, text, source, source_url,
                          ts_rank(tsv, q) AS text_rank
                   FROM knowledge_chunk, to_tsquery('russian', :q) AS q
                   WHERE tsv @@ q ORDER BY text_rank DESC, id LIMIT :limit"""
            ),
            {"q": " | ".join(words), "limit": limit},
        )
        merge(rows, "full_text")

    if embedder is not None:
        try:
            vectors = await asyncio.to_thread(embedder.embed, [query])
            async with session.begin_nested():
                rows = await session.execute(
                    text(
                        """SELECT id, title, text, source, source_url,
                                  embedding <=> CAST(:vector AS vector) AS vector_distance
                           FROM knowledge_chunk
                           WHERE embedding IS NOT NULL AND embedding_model = :model
                           ORDER BY embedding <=> CAST(:vector AS vector), id LIMIT :limit"""
                    ),
                    {"vector": vector_literal(vectors[0]), "model": embedder.model_name, "limit": limit},
                )
                merge(rows, "vector")
        except Exception as exc:
            log.warning("Vector retrieval failed; full-text results remain available: %s", exc)

    return sorted(ranked.values(), key=lambda hit: (-hit["score"], hit["id"]))[:top_k]
