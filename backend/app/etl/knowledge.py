"""
Knowledge documents and embeddings for the chat assistant.

Input: .md/.txt (one document) or JSONL/CSV with columns text [, title, source_url,
external_id | territory (+ region)]. Texts are chunked by paragraphs (~900 chars).
Embeddings use the configured FastEmbed model. PostgreSQL full-text search also
indexes every chunk and remains available when embeddings are disabled.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.etl.io import find_column, read_table, read_text
from app.etl.names import normalize_search, region_code_from
from app.etl.runs import finish_run, start_run

CHUNK_CHARS = 900


def chunk_text(body: str, limit: int = CHUNK_CHARS) -> list[str]:
    paragraphs = [p.strip() for p in body.replace("\r\n", "\n").split("\n\n") if p.strip()]
    chunks: list[str] = []
    current = ""
    for p in paragraphs:
        while len(p) > limit:
            cut = p.rfind(". ", 0, limit)
            cut = cut + 1 if cut > limit // 2 else limit
            if current:
                chunks.append(current)
                current = ""
            chunks.append(p[:cut].strip())
            p = p[cut:].strip()
        if len(current) + len(p) + 2 <= limit:
            current = f"{current}\n\n{p}" if current else p
        else:
            if current:
                chunks.append(current)
            current = p
    if current:
        chunks.append(current)
    return chunks


async def load_knowledge(
    session: AsyncSession,
    path: Path,
    *,
    source: str | None = None,
    replace: bool = False,
    embedder: Any | None = None,
) -> dict[str, Any]:
    source = source or path.name
    if path.suffix.lower() in (".md", ".txt"):
        docs = [{"title": path.stem, "text": read_text(path)}]
    else:
        docs = read_table(path)
    return await load_documents(session, docs, source=source, replace=replace, embedder=embedder, file=str(path))


async def load_documents(
    session: AsyncSession,
    docs: list[dict[str, Any]],
    *,
    source: str,
    replace: bool,
    embedder: Any | None,
    file: str,
) -> dict[str, Any]:
    if not docs:
        raise ValueError(f"{file}: no documents")
    headers = list(docs[0])
    c_text = find_column(headers, ["text", "content", "текст"]) or "text"
    c_title = find_column(headers, ["title", "заголовок"])
    c_url = find_column(headers, ["source_url", "url", "ссылка"])
    c_ext = find_column(headers, ["external_id"])
    c_name = find_column(headers, ["territory", "территория", "name"])
    c_region = find_column(headers, ["region", "регион", "region_code"])

    ext_map = {r[0]: r[1] for r in await session.execute(text("SELECT external_id, id FROM territory"))}
    name_rows = await session.execute(text("SELECT id, name_normalized, region_code FROM territory"))
    by_name: dict[str, list[tuple[int, str | None]]] = {}
    for tid, norm, region in name_rows:
        by_name.setdefault(norm, []).append((tid, region))

    run_id = await start_run(session, "knowledge", source, bumps_revision=False)
    if replace:
        await session.execute(text("DELETE FROM knowledge_chunk WHERE source = :s"), {"s": source})
    records: list[dict[str, Any]] = []
    unresolved = 0
    for doc in docs:
        body = str(doc.get(c_text) or "").strip()
        if not body:
            continue
        tid = None
        if c_ext and doc.get(c_ext):
            tid = ext_map.get(str(doc[c_ext]).strip())
        elif c_name and doc.get(c_name):
            cands = by_name.get(normalize_search(str(doc[c_name])), [])
            region = region_code_from(str(doc.get(c_region) or "")) if c_region else None
            if region:
                cands = [c for c in cands if c[1] == region]
            tid = cands[0][0] if len(cands) == 1 else None
            unresolved += tid is None
        for i, chunk in enumerate(chunk_text(body)):
            records.append({
                "tid": tid, "source": source, "url": doc.get(c_url) if c_url else None,
                "title": (doc.get(c_title) if c_title else None) or None, "idx": i, "text": chunk,
            })
    embeddings: list[list[float]] | None = None
    if embedder is not None and records:
        embeddings = await asyncio.to_thread(embedder.embed, [r["text"] for r in records])
        if len(embeddings) != len(records):
            raise ValueError("embedding count does not match knowledge chunks")
    for i, r in enumerate(records):
        params = {**r, "emb": None, "model": None}
        if embeddings is not None:
            from app.chat.retriever import vector_literal

            params["emb"] = vector_literal(embeddings[i])
            params["model"] = embedder.model_name  # type: ignore[union-attr]
        await session.execute(
            text(
                """
                INSERT INTO knowledge_chunk (territory_id, source, source_url, title, chunk_index, text,
                    embedding, embedding_model)
                VALUES (:tid, :source, :url, :title, :idx, :text, CAST(:emb AS vector), :model)
                """
            ),
            params,
        )
    stats = {"file": file, "documents": len(docs), "chunks": len(records),
             "territory_unresolved": unresolved, "embedded": embeddings is not None}
    await finish_run(session, run_id, stats)
    return {"etl_run_id": run_id, **stats}
