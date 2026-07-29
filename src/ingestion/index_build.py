"""Embed chunks and publish them as a new corpus version.

The build always targets a fresh versioned collection and only moves the read
alias once every chunk is in and the count checks out. That is what makes a
rebuild safe to run while the agent is serving: it keeps reading the old
collection until the instant the new one is complete.
"""

from __future__ import annotations

from loguru import logger

from src.rag.config import RAGSettings
from src.rag.embedder import GeminiEmbedder
from src.rag.models import GuidelineChunk
from src.rag.store import QdrantStore, VectorStoreError

# Chunks per upsert. Large enough to keep the round-trip count down, small
# enough that one failure does not lose minutes of embedding work.
_UPSERT_BATCH = 64


async def build_index(
    settings: RAGSettings,
    chunks: list[GuidelineChunk],
    *,
    promote: bool = True,
    resume: bool = False,
) -> str:
    """Embed every chunk and publish the result as a versioned collection.

    ``resume`` skips chunks already present, which matters because embedding
    the full corpus on a free-tier key is rate-limited to roughly twenty
    minutes — losing that to a transient failure and starting over is the
    difference between a retry and an afternoon.
    """
    if not chunks:
        raise VectorStoreError("No chunks to index — run the fetch stage first")

    embedder = GeminiEmbedder(settings)
    store = QdrantStore(settings)
    collection = settings.versioned_collection

    try:
        if resume and await store.collection_exists(collection):
            done = await store.existing_ids(collection)
            pending = [chunk for chunk in chunks if chunk.id not in done]
            logger.info(
                "Resuming build of {}: {} already indexed, {} to go",
                collection,
                len(done),
                len(pending),
            )
        else:
            await store.recreate_collection(collection)
            pending = list(chunks)

        for start in range(0, len(pending), _UPSERT_BATCH):
            batch = pending[start : start + _UPSERT_BATCH]
            vectors = await embedder.embed_documents(
                [chunk.embedding_text for chunk in batch]
            )
            await store.upsert_chunks(collection, batch, vectors)
            logger.info(
                "Indexed {}/{} chunks into {}",
                min(start + _UPSERT_BATCH, len(pending)),
                len(pending),
                collection,
            )

        indexed = await store.count(collection)
        if indexed != len(chunks):
            raise VectorStoreError(
                f"Indexed {indexed} points but expected {len(chunks)}; "
                "refusing to promote an incomplete collection"
            )

        if promote:
            await store.promote(collection)
        logger.info(
            "Corpus {} built points={} promoted={}", collection, indexed, promote
        )
        return collection
    finally:
        await store.close()
