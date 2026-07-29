"""Offline enrichment pass over the extracted chunks.

Two things get added here, both to close the Arabic/English gap before any
patient is waiting on a reply:

* ``summary_ar`` — a short Arabic gist. This is what the agent paraphrases when
  it speaks, and it is reviewable by an Arabic-speaking clinician, which a
  runtime translation never would be.
* ``topic_tags`` — controlled-vocabulary labels that drive the coverage report
  and optional retrieval filters.

It runs against the same gemma endpoint the agent uses. Only public guideline
text is sent; no patient data exists at this stage.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import replace

from loguru import logger
from openai import AsyncOpenAI

from src.ingestion.taxonomy import TOPIC_TAGS, guess_topics, valid_topics
from src.rag.config import RAGSettings
from src.rag.models import GuidelineChunk

_PROMPT = """You label passages from clinical practice guidelines for a retrieval system.

Return ONLY a JSON object with exactly these keys:
  "topics": array of 1-4 tags chosen from this list: {tags}
  "summary_ar": one or two sentences of Modern Standard Arabic summarising the passage's clinical content
  "keywords": array of 4-8 short search keywords, mixing English clinical terms and their Arabic equivalents

Rules:
- Choose topics only from the provided list. If nothing fits well, return the single closest tag.
- The Arabic summary must state what the passage says, not describe it. Do not add advice that is not in the passage.
- No markdown, no code fences, no commentary outside the JSON object."""

_JSON_BLOCK = re.compile(r"\{.*\}", re.S)

# Cap so one chunk cannot dominate a topic filter.
_MAX_TOPICS = 6


def merge_topics(*groups: tuple[str, ...]) -> tuple[str, ...]:
    """Combine tag sets, preserving order and dropping duplicates."""
    merged: dict[str, None] = {}
    for group in groups:
        for tag in group:
            merged.setdefault(tag, None)
    return tuple(merged)[:_MAX_TOPICS]


_merge_topics = merge_topics


def _parse(raw: str) -> dict | None:
    match = _JSON_BLOCK.search(raw or "")
    if not match:
        return None
    try:
        parsed = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


class ChunkEnricher:
    def __init__(self, settings: RAGSettings, *, concurrency: int = 6) -> None:
        self._settings = settings
        self._client = AsyncOpenAI(
            base_url=settings.llm_base_url,
            api_key=settings.llm_api_key,
            timeout=60.0,
            max_retries=1,
        )
        self._semaphore = asyncio.Semaphore(concurrency)

    async def _enrich_one(self, chunk: GuidelineChunk) -> GuidelineChunk:
        async with self._semaphore:
            try:
                response = await self._client.chat.completions.create(
                    model=self._settings.llm_model,
                    messages=[
                        {
                            "role": "system",
                            "content": _PROMPT.format(tags=", ".join(TOPIC_TAGS)),
                        },
                        {
                            "role": "user",
                            "content": (
                                f"Publisher: {chunk.publisher}\n"
                                f"Guideline: {chunk.guideline_title}\n"
                                f"Section: {chunk.section_path}\n\n"
                                f"{chunk.text}"
                            ),
                        },
                    ],
                    temperature=0.0,
                    max_tokens=400,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("Enrichment failed for {}: {}", chunk.id, exc)
                return self._fallback(chunk)

        parsed = _parse(
            response.choices[0].message.content if response.choices else ""
        )
        if parsed is None:
            return self._fallback(chunk)

        topics = valid_topics(parsed.get("topics"))
        summary_ar = str(parsed.get("summary_ar") or "").strip()
        keywords = parsed.get("keywords")
        keyword_tuple = (
            tuple(str(k).strip() for k in keywords if str(k).strip())[:12]
            if isinstance(keywords, list)
            else ()
        )

        return replace(
            chunk,
            # Union, not replace. The catalog's per-document tag is the reliable
            # one — every chunk of NICE NG226 is about osteoarthritis whether or
            # not that word appears in the paragraph — while the model's tags add
            # per-chunk nuance. Letting the model overwrite made whole guidelines
            # vanish from the coverage report.
            topic_tags=_merge_topics(chunk.topic_tags, topics or guess_topics(chunk.text)),
            summary_ar=summary_ar,
            keywords=keyword_tuple,
        )

    def _fallback(self, chunk: GuidelineChunk) -> GuidelineChunk:
        """Keep the chunk, minus the enrichment.

        A chunk without an Arabic summary still retrieves — Gemini embeddings
        are cross-lingual on the English body alone — so dropping it would cost
        more coverage than it saves.
        """
        return replace(
            chunk,
            topic_tags=chunk.topic_tags or guess_topics(chunk.text),
        )

    async def enrich(self, chunks: list[GuidelineChunk]) -> list[GuidelineChunk]:
        results = await asyncio.gather(
            *(self._enrich_one(chunk) for chunk in chunks)
        )
        enriched = sum(1 for chunk in results if chunk.summary_ar)
        logger.info(
            "Enrichment complete chunks={} with_arabic_summary={}",
            len(results),
            enriched,
        )
        return list(results)
