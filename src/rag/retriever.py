"""Turn-level orchestration: route, retrieve, and hand back an evidence block.

The whole path from end-of-utterance to "ready for the LLM" lives here, under a
single deadline. Everything it can fail at degrades to something safe: a
classifier outage means retrieve anyway, a retrieval outage means tell the
model there is no evidence, and neither drops the patient's turn.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field

from loguru import logger

from src.rag.classifier import QueryClassifier, QueryRoute
from src.rag.config import RAGSettings
from src.rag.context import format_evidence, format_no_evidence
from src.rag.embedder import GeminiEmbedder
from src.rag.models import RetrievedChunk
from src.rag.store import QdrantStore

# Below this length an utterance is almost certainly a follow-up ("and how
# often?", "وكم مرة؟") that means nothing on its own, so the preceding turn is
# folded into the search query.
_FOLLOW_UP_CHARS = 30


@dataclass(frozen=True)
class TurnDecision:
    route: QueryRoute
    #: Evidence block to append to the turn context. None means "no injection".
    evidence: str | None = None
    hits: tuple[RetrievedChunk, ...] = field(default_factory=tuple)
    latency_ms: float = 0.0
    degraded: bool = False


def build_query(utterance: str, history: str | None) -> str:
    if history and len(utterance.strip()) < _FOLLOW_UP_CHARS:
        return f"{history.strip()} {utterance.strip()}".strip()
    return utterance.strip()


class MedicalRetriever:
    #: Re-touch the embedding endpoint once it has been idle this long. Sized
    #: under the ~20s it takes an idle TLS connection to this provider to go
    #: cold, and well under the greeting-to-first-question gap it protects.
    KEEPALIVE_IDLE_S = 12.0

    def __init__(
        self,
        settings: RAGSettings,
        embedder: GeminiEmbedder,
        store: QdrantStore,
        classifier: QueryClassifier,
    ) -> None:
        self._settings = settings
        self._embedder = embedder
        self._store = store
        self._classifier = classifier
        self._last_embed_at = time.monotonic()

    @classmethod
    async def create(cls, settings: RAGSettings) -> "MedicalRetriever":
        """Build a retriever, proving its dependencies work first.

        Raises rather than degrading: a worker that starts without a usable
        index would answer clinical questions ungrounded, which the PoC's
        knowledge-base constraint forbids outright.
        """
        embedder = GeminiEmbedder(settings)
        store = QdrantStore(settings)
        total = await store.health_check()

        classifier = QueryClassifier(settings)

        # Warm both network paths before any patient is waiting on them. Each
        # pays connection setup on its first call — measured at ~400ms for
        # Gemini and ~900ms for the classifier endpoint — which is enough on
        # its own to push the first question of a session past the retrieval
        # budget and strip it of evidence. Every later question was fine, which
        # is exactly what makes this the kind of bug that ships.
        warm_ms = 0.0
        try:
            started = time.monotonic()
            await asyncio.gather(
                embedder.embed_query("مرحبا"),
                classifier.classify("مرحبا"),
            )
            warm_ms = (time.monotonic() - started) * 1000
        except Exception as exc:  # noqa: BLE001 - warmup is best effort
            logger.warning("Retriever warmup failed ({}); first turn may be slower", exc)

        logger.info(
            "Medical retriever ready collection={} chunks={} model={} dim={} "
            "top_k={} budget_ms={} warmup_ms={:.0f}",
            settings.collection_alias,
            total,
            settings.embedding_model,
            settings.embedding_dim,
            settings.top_k,
            settings.timeout_ms,
            warm_ms,
        )
        return cls(settings, embedder, store, classifier)

    async def aclose(self) -> None:
        await self._store.close()

    async def _search(self, query: str) -> list[RetrievedChunk]:
        vector = await self._embedder.embed_query(query)
        self._last_embed_at = time.monotonic()
        return await self._store.search(
            vector,
            top_k=self._settings.top_k,
            score_threshold=self._settings.score_threshold,
        )

    async def run_keepalive(self) -> None:
        """Hold the embedding connection open for the life of a session.

        The agent greets the patient and then waits, so roughly twenty seconds
        of silence sit between startup warmup and the first real question. That
        is long enough for the TLS connection to this provider to go cold, and
        reconnecting costs ~300ms on top of the query — measured live as the
        first question of a call taking 701ms and losing its evidence while
        every subsequent question in the same call landed in 287-413ms.

        Cancelled with the session, so it costs one tiny embedding every twelve
        seconds only while somebody is actually on the line.
        """
        try:
            while True:
                await asyncio.sleep(self.KEEPALIVE_IDLE_S / 2)
                idle = time.monotonic() - self._last_embed_at
                if idle < self.KEEPALIVE_IDLE_S:
                    continue
                try:
                    await self._embedder.embed_query("مرحبا")
                    self._last_embed_at = time.monotonic()
                except Exception as exc:  # noqa: BLE001 - never fail a session
                    logger.debug("Embedding keepalive failed: {}", type(exc).__name__)
        except asyncio.CancelledError:
            pass

    async def decide(
        self, utterance: str, *, history: str | None = None
    ) -> TurnDecision:
        started = time.monotonic()

        query = build_query(utterance, history)
        timeout_s = self._settings.timeout_ms / 1000.0

        try:
            route, hits, degraded = await asyncio.wait_for(
                self._route_and_retrieve(utterance, query, history),
                timeout=timeout_s,
            )
        except asyncio.TimeoutError:
            logger.warning(
                "RAG budget of {}ms exceeded; proceeding without evidence",
                self._settings.timeout_ms,
            )
            return TurnDecision(
                route=QueryRoute.RETRIEVE,
                evidence=format_no_evidence(),
                latency_ms=(time.monotonic() - started) * 1000,
                degraded=True,
            )

        elapsed_ms = (time.monotonic() - started) * 1000

        if route is QueryRoute.DIRECT:
            logger.info(
                "Turn routed DIRECT — no retrieval performed latency_ms={:.0f}",
                elapsed_ms,
            )
            return TurnDecision(route=route, latency_ms=elapsed_ms)

        logger.info(
            "Turn routed RETRIEVE evidence_count={} degraded={} latency_ms={:.0f}",
            len(hits),
            degraded,
            elapsed_ms,
        )
        return TurnDecision(
            route=route,
            evidence=format_evidence(hits),
            hits=tuple(hits),
            latency_ms=elapsed_ms,
            degraded=degraded,
        )

    async def _route_and_retrieve(
        self, utterance: str, query: str, history: str | None
    ) -> tuple[QueryRoute, list[RetrievedChunk], bool]:
        classify = self._classifier.classify(utterance, history=history)

        if not self._settings.speculative:
            route = await classify
            if route is not QueryRoute.RETRIEVE:
                return route, [], False
            try:
                return route, await self._search(query), False
            except Exception as exc:  # noqa: BLE001
                logger.warning("Retrieval failed ({}); no evidence injected", exc)
                return route, [], True

        # Speculative: the search runs alongside the classifier and is thrown
        # away if the label turns out not to need it. Trades a few wasted
        # embedding calls for ~200ms off every clinical turn.
        search = asyncio.create_task(self._search(query))
        try:
            try:
                route = await classify
            except Exception:  # noqa: BLE001 - classify() already swallows its own
                route = QueryRoute.RETRIEVE

            if route is not QueryRoute.RETRIEVE:
                return route, [], False

            try:
                return route, await search, False
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.warning("Retrieval failed ({}); no evidence injected", exc)
                return route, [], True
        finally:
            # Covers both the discarded-speculation case and the turn timing
            # out around us. A task left running would surface later as an
            # unretrieved exception on an unrelated turn.
            if not search.done():
                search.cancel()
            else:
                search.exception()
