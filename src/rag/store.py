"""Qdrant-backed vector store for the guideline corpus.

Reads and writes are separated by an alias. Ingestion builds
``medical_guidelines_v2``, fills it, verifies it, and only then repoints the
``medical_guidelines`` alias — so a rebuild can never expose the live agent to a
half-populated index, and rolling back is one alias move.
"""

from __future__ import annotations

import uuid
from typing import Any, Iterable, Sequence

from loguru import logger
from qdrant_client import AsyncQdrantClient, QdrantClient, models as qmodels

from src.rag.config import RAGSettings
from src.rag.models import GuidelineChunk, RetrievedChunk

# Payload fields worth an index: everything the retriever may filter on.
# Unindexed filtering in Qdrant degrades to a full scan.
_INDEXED_FIELDS: tuple[tuple[str, Any], ...] = (
    ("publisher", qmodels.PayloadSchemaType.KEYWORD),
    ("topic_tags", qmodels.PayloadSchemaType.KEYWORD),
    ("audience", qmodels.PayloadSchemaType.KEYWORD),
    ("lang", qmodels.PayloadSchemaType.KEYWORD),
    ("corpus_version", qmodels.PayloadSchemaType.KEYWORD),
)


class VectorStoreError(RuntimeError):
    """Raised when the vector store is unusable."""


def _point_id(chunk_id: str) -> str:
    """Map a human-readable chunk id onto the UUID Qdrant wants for point ids.

    Deterministic, so re-ingesting the same chunk overwrites rather than
    duplicating it.
    """
    return str(uuid.uuid5(uuid.NAMESPACE_URL, chunk_id))


def verify_corpus_sync(settings: RAGSettings) -> int:
    """Prove the corpus is queryable, synchronously, before any job runs.

    Used by the worker's per-process setup hook, which is not async. Failing
    here takes the whole worker down, which is the intent: a medical assistant
    that cannot reach its guidelines must not accept calls.
    """
    client = QdrantClient(
        url=settings.qdrant_url, api_key=settings.qdrant_api_key, timeout=15
    )
    try:
        total = client.count(collection_name=settings.collection_alias, exact=True).count
    except Exception as exc:  # noqa: BLE001
        raise VectorStoreError(
            f"Qdrant at {settings.qdrant_url} is not serving collection "
            f"{settings.collection_alias!r}: {exc}"
        ) from exc
    finally:
        client.close()

    if total == 0:
        raise VectorStoreError(
            f"Qdrant collection {settings.collection_alias!r} is empty — run "
            "`uv run python -m src.ingestion.cli index` before starting the agent"
        )
    return total


class QdrantStore:
    def __init__(self, settings: RAGSettings) -> None:
        self._settings = settings
        self._client = AsyncQdrantClient(
            url=settings.qdrant_url,
            api_key=settings.qdrant_api_key,
            timeout=30,
        )
        self._alias = settings.collection_alias

    @property
    def client(self) -> AsyncQdrantClient:
        return self._client

    async def close(self) -> None:
        await self._client.close()

    # ─── Build-time ──────────────────────────────────────────────────────────

    async def recreate_collection(self, name: str) -> None:
        """Drop and rebuild a versioned collection, then index its payload."""
        if await self._client.collection_exists(name):
            await self._client.delete_collection(name)
        await self._client.create_collection(
            collection_name=name,
            vectors_config=qmodels.VectorParams(
                size=self._settings.embedding_dim,
                distance=qmodels.Distance.COSINE,
            ),
        )
        for field_name, schema in _INDEXED_FIELDS:
            await self._client.create_payload_index(
                collection_name=name,
                field_name=field_name,
                field_schema=schema,
            )
        logger.info(
            "Qdrant collection {} created dim={} distance=cosine",
            name,
            self._settings.embedding_dim,
        )

    async def upsert_chunks(
        self,
        name: str,
        chunks: Sequence[GuidelineChunk],
        vectors: Sequence[Sequence[float]],
    ) -> None:
        if len(chunks) != len(vectors):
            raise VectorStoreError(
                f"upsert mismatch: {len(chunks)} chunks vs {len(vectors)} vectors"
            )
        points = [
            qmodels.PointStruct(
                id=_point_id(chunk.id),
                vector=list(vector),
                payload=chunk.to_payload(),
            )
            for chunk, vector in zip(chunks, vectors)
        ]
        await self._client.upsert(collection_name=name, points=points, wait=True)

    async def collection_exists(self, name: str) -> bool:
        return await self._client.collection_exists(name)

    async def existing_ids(self, name: str) -> set[str]:
        """Chunk ids already indexed, for resuming an interrupted build."""
        found: set[str] = set()
        offset = None
        while True:
            points, offset = await self._client.scroll(
                collection_name=name,
                limit=1024,
                offset=offset,
                with_payload=["id"],
                with_vectors=False,
            )
            for point in points:
                chunk_id = (point.payload or {}).get("id")
                if chunk_id:
                    found.add(chunk_id)
            if offset is None:
                return found

    async def update_topic_tags(
        self, name: str, tags_by_chunk_id: dict[str, tuple[str, ...]]
    ) -> None:
        """Patch topic tags in place.

        Tags are payload only — they never enter a chunk's vector — so this
        avoids re-embedding a corpus just to correct its labels.
        """
        if not await self._client.collection_exists(name):
            return
        for chunk_id, tags in tags_by_chunk_id.items():
            await self._client.set_payload(
                collection_name=name,
                payload={"topic_tags": list(tags)},
                points=[_point_id(chunk_id)],
                wait=False,
            )

    async def promote(self, name: str) -> None:
        """Point the read alias at ``name``."""
        await self._client.update_collection_aliases(
            change_aliases_operations=[
                qmodels.CreateAliasOperation(
                    create_alias=qmodels.CreateAlias(
                        collection_name=name, alias_name=self._alias
                    )
                )
            ]
        )
        logger.info("Qdrant alias {} now points at {}", self._alias, name)

    async def count(self, name: str | None = None) -> int:
        target = name or self._alias
        result = await self._client.count(collection_name=target, exact=True)
        return result.count

    # ─── Query-time ──────────────────────────────────────────────────────────

    async def health_check(self) -> int:
        """Confirm the alias resolves to a non-empty collection.

        The agent calls this at startup and refuses to serve if it fails: an
        empty index would turn every clinical question into an ungrounded
        answer, which is the one failure mode this PoC cannot ship with.
        """
        try:
            total = await self.count()
        except Exception as exc:  # noqa: BLE001 - surface any client/transport error
            raise VectorStoreError(
                f"Qdrant at {self._settings.qdrant_url} is not serving collection "
                f"{self._alias!r}: {exc}"
            ) from exc
        if total == 0:
            raise VectorStoreError(
                f"Qdrant collection {self._alias!r} is empty — run "
                "`uv run python -m src.ingestion.cli index` before starting the agent"
            )
        return total

    async def search(
        self,
        vector: Sequence[float],
        *,
        top_k: int,
        score_threshold: float | None = None,
        publishers: Iterable[str] | None = None,
        topic_tags: Iterable[str] | None = None,
    ) -> list[RetrievedChunk]:
        conditions: list[qmodels.FieldCondition] = []
        if publishers:
            conditions.append(
                qmodels.FieldCondition(
                    key="publisher",
                    match=qmodels.MatchAny(any=list(publishers)),
                )
            )
        if topic_tags:
            conditions.append(
                qmodels.FieldCondition(
                    key="topic_tags",
                    match=qmodels.MatchAny(any=list(topic_tags)),
                )
            )

        response = await self._client.query_points(
            collection_name=self._alias,
            query=list(vector),
            limit=top_k,
            score_threshold=score_threshold,
            query_filter=qmodels.Filter(must=conditions) if conditions else None,
            with_payload=True,
        )

        hits: list[RetrievedChunk] = []
        for point in response.points:
            payload = point.payload or {}
            try:
                chunk = GuidelineChunk.from_mapping(payload)
            except Exception as exc:  # noqa: BLE001
                # A malformed payload is an ingestion bug, not a reason to fail
                # the patient's turn — skip it and keep the rest of the hits.
                logger.warning("Skipping malformed chunk payload: {}", exc)
                continue
            hits.append(RetrievedChunk(chunk=chunk, score=point.score))
        return hits
