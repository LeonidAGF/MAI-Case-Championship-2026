"""Knowledge retrieval for the chat: pgvector when embeddings exist, else Postgres FTS (russian)."""

from __future__ import annotations

import logging
import re
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

log = logging.getLogger(__name__)
WORD = re.compile(r"[A-Za-zА-Яа-яЁё]{3,}")


class Embedder:
    def __init__(self, model_name: str) -> None:
        from sentence_transformers import SentenceTransformer  # optional dependency (extra "embeddings")

        self.model_name = model_name
        self._model = SentenceTransformer(model_name)

    def embed(self, texts: list[str]) -> list[list[float]]:
        return self._model.encode(texts, normalize_embeddings=True).tolist()


def make_embedder(model_name: str) -> Embedder | None:
    if not model_name:
        return None
    try:
        return Embedder(model_name)
    except Exception as exc:  # noqa: BLE001 - optional feature, degrade to FTS
        log.warning("Embeddings disabled (%s): %s", model_name, exc)
        return None


async def retrieve(session: AsyncSession, query: str, *, top_k: int, embedder: Embedder | None = None) -> list[dict[str, Any]]:
    if embedder is not None:
        vec = "[" + ",".join(f"{x:.6f}" for x in embedder.embed([query])[0]) + "]"
        rows = await session.execute(
            text(
                """
                SELECT id, title, text, source, source_url FROM knowledge_chunk
                WHERE embedding IS NOT NULL AND embedding_model = :m
                ORDER BY embedding <=> CAST(:v AS vector) LIMIT :k
                """
            ),
            {"m": embedder.model_name, "v": vec, "k": top_k},
        )
        hits = [dict(r._mapping) for r in rows]
        if hits:
            return hits
    words = list(dict.fromkeys(w.lower() for w in WORD.findall(query)))[:12]
    if not words:
        return []
    tsquery = " | ".join(words)
    rows = await session.execute(
        text(
            """
            SELECT id, title, text, source, source_url, ts_rank(tsv, q) AS rank
            FROM knowledge_chunk, to_tsquery('russian', :q) AS q
            WHERE tsv @@ q ORDER BY rank DESC LIMIT :k
            """
        ),
        {"q": tsquery, "k": top_k},
    )
    return [dict(r._mapping) for r in rows]
