import pytest
from sqlalchemy import text

from app.chat.retriever import retrieve
from app.etl.knowledge import load_documents

pytestmark = pytest.mark.integration


class FakeEmbedder:
    model_name = "test-rag-3d"

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[1.0, 0.0, 0.0] if "больниц" in value.lower() else [0.0, 1.0, 0.0] for value in texts]


async def test_knowledge_reindex_and_hybrid_search(container, client) -> None:
    docs = [{"title": "Больница", "text": "Больница и поликлиника относятся к медицинской доступности."}]
    embedder = FakeEmbedder()
    async with container.sessionmaker() as session:
        for _ in range(2):
            stats = await load_documents(
                session, docs, source="test-rag", replace=True,
                embedder=embedder, file="test documents",
            )
            await session.commit()
            assert stats["chunks"] == 1 and stats["embedded"]
        count = (
            await session.execute(text("SELECT count(*) FROM knowledge_chunk WHERE source = 'test-rag'"))
        ).scalar_one()
        assert count == 1
        hits = await retrieve(session, "больница", top_k=5, embedder=embedder)
        target = next(hit for hit in hits if hit["source"] == "test-rag")
        assert set(target["retrieval_methods"]) == {"full_text", "vector"}

    status = await client.get("/knowledge/status")
    assert status.status_code == 200 and status.json()["embedded"] >= 1
    chunk = await client.get(f"/knowledge/chunks/{target['id']}")
    assert chunk.status_code == 200 and chunk.json()["text"] == docs[0]["text"]
