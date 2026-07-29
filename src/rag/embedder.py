"""Gemini text embeddings for the medical guideline corpus.

The corpus is English guideline text; patients speak Arabic. Rather than
translating the query at runtime (which would add a whole LLM round-trip to
every clinical turn) we lean on ``gemini-embedding-001`` being strongly
multilingual: an Arabic question and the English recommendation that answers it
land close together in the same vector space.

Documents and queries must be embedded with different ``task_type`` values —
the model produces asymmetric vectors for retrieval, and mixing the two
noticeably degrades ranking.
"""

from __future__ import annotations

import asyncio
import math
import re
import time
from typing import Sequence

from google import genai
from google.genai import types
from loguru import logger

from src.rag.config import RAGSettings

# Task types the retrieval pair requires. Gemini uses these to steer the
# embedding: queries and documents are encoded for asymmetric matching.
TASK_QUERY = "RETRIEVAL_QUERY"
TASK_DOCUMENT = "RETRIEVAL_DOCUMENT"

# Gemini reports how long to wait when it rate-limits; obeying that number
# beats guessing at a backoff curve.
_RETRY_DELAY = re.compile(r"'retryDelay':\s*'(\d+(?:\.\d+)?)s'")


def _retry_after(error: Exception) -> float | None:
    match = _RETRY_DELAY.search(str(error))
    return float(match.group(1)) if match else None


def _is_rate_limit(error: Exception) -> bool:
    return "429" in str(error) or "RESOURCE_EXHAUSTED" in str(error)


class _RequestPacer:
    """Keeps embedding calls under the account's per-minute quota.

    The quota is counted per *text*, not per HTTP call: batching 32 contents
    into one request still spends 32 units of a 100-per-minute free-tier
    allowance. Pacing is therefore proportional to batch size — pacing per
    request instead lets a corpus build blow the quota on its second batch and
    die partway through.
    """

    def __init__(self, seconds_per_item: float) -> None:
        self._seconds_per_item = seconds_per_item
        self._next_allowed = 0.0
        self._lock = asyncio.Lock()

    async def wait(self, cost: int = 1) -> None:
        async with self._lock:
            now = time.monotonic()
            if now < self._next_allowed:
                await asyncio.sleep(self._next_allowed - now)
            self._next_allowed = (
                max(now, self._next_allowed) + self._seconds_per_item * max(1, cost)
            )

    async def pause_for(self, seconds: float) -> None:
        """Hold every caller off after the server says we are over quota."""
        async with self._lock:
            self._next_allowed = max(
                self._next_allowed, time.monotonic() + seconds
            )


class EmbeddingError(RuntimeError):
    """Raised when the embedding API cannot produce usable vectors."""


def _l2_normalize(vector: Sequence[float]) -> list[float]:
    """Scale a vector to unit length.

    Only the native 3072-dim output arrives normalized. Truncating to 1536 via
    ``output_dimensionality`` breaks that invariant, and Qdrant's cosine metric
    assumes unit vectors for its scores to be comparable against a fixed
    threshold — so every vector is renormalized here regardless of dimension.
    """
    norm = math.sqrt(sum(component * component for component in vector))
    if norm == 0.0:
        raise EmbeddingError("Embedding API returned a zero vector")
    return [component / norm for component in vector]


class GeminiEmbedder:
    """Async wrapper around the Gemini embedding endpoint."""

    def __init__(self, settings: RAGSettings) -> None:
        if not settings.google_api_key:
            raise EmbeddingError(
                "GOOGLE_API_KEY is not set; the medical RAG index cannot be "
                "queried or built without it"
            )
        self._settings = settings
        self._client = genai.Client(api_key=settings.google_api_key)
        self._model = settings.embedding_model
        self._dim = settings.embedding_dim
        self._pacer = _RequestPacer(settings.embedding_seconds_per_item)

    @property
    def dim(self) -> int:
        return self._dim

    async def _embed(
        self,
        contents: list[str],
        task_type: str,
        *,
        attempts: int = 3,
        paced: bool = True,
    ) -> list[list[float]]:
        config = types.EmbedContentConfig(
            task_type=task_type,
            output_dimensionality=self._dim,
        )

        last_error: Exception | None = None
        for attempt in range(1, attempts + 1):
            if paced:
                await self._pacer.wait(len(contents))
            try:
                response = await self._client.aio.models.embed_content(
                    model=self._model,
                    contents=contents,  # type: ignore[arg-type]
                    config=config,
                )
            except Exception as exc:  # noqa: BLE001 - provider raises broadly
                last_error = exc
                if attempt == attempts:
                    break
                if _is_rate_limit(exc):
                    # Push the shared schedule out — the quota is per project,
                    # so every caller has to back off, not just this one. The
                    # next loop's pacer.wait() performs the actual delay;
                    # sleeping here as well would double it.
                    delay = (_retry_after(exc) or 30.0) + 1.0
                    await self._pacer.pause_for(delay)
                else:
                    delay = 0.5 * (2 ** (attempt - 1))
                    await asyncio.sleep(delay)
                logger.warning(
                    "Gemini embedding attempt {}/{} failed ({}); backing off {:.0f}s",
                    attempt,
                    attempts,
                    "rate limited" if _is_rate_limit(exc) else type(exc).__name__,
                    delay,
                )
                continue

            embeddings = response.embeddings or []
            if len(embeddings) != len(contents):
                raise EmbeddingError(
                    f"Gemini returned {len(embeddings)} embeddings for "
                    f"{len(contents)} inputs"
                )
            return [_l2_normalize(item.values or []) for item in embeddings]

        raise EmbeddingError(
            f"Gemini embedding failed after {attempts} attempts: {last_error}"
        ) from last_error

    async def embed_query(self, text: str) -> list[float]:
        """Embed a single patient utterance for search.

        Deliberately unpaced. The pacer exists to keep a corpus build under a
        per-minute quota, and applying it here would make every patient turn
        wait out the build's throttle — enough on its own to blow the retrieval
        budget and drop the evidence. One query per turn is nowhere near
        quota-relevant; if it does get rate-limited it fails fast and the turn
        proceeds ungrounded, which the prompt already handles.
        """
        cleaned = text.strip()
        if not cleaned:
            raise EmbeddingError("Cannot embed an empty query")
        vectors = await self._embed([cleaned], TASK_QUERY, paced=False)
        return vectors[0]

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        """Embed guideline chunks for indexing, in batches.

        Batching is what makes a full-corpus rebuild finish in minutes rather
        than hours; the batch size is capped by config because the API rejects
        oversized payloads rather than splitting them.
        """
        cleaned = [text.strip() for text in texts]
        if any(not text for text in cleaned):
            raise EmbeddingError("Cannot embed an empty document chunk")

        batch_size = max(1, self._settings.embedding_batch_size)
        vectors: list[list[float]] = []
        for start in range(0, len(cleaned), batch_size):
            batch = cleaned[start : start + batch_size]
            # More patience than a query gets: nothing is waiting on this, and
            # giving up mid-corpus wastes everything embedded so far.
            vectors.extend(await self._embed(batch, TASK_DOCUMENT, attempts=8))
        return vectors
