"""Shared schema for guideline chunks.

Both the offline ingestion pipeline and the live agent depend on this shape, so
it lives outside either one. Validation is deliberately fail-closed: a chunk
that cannot prove where it came from is a chunk the assistant would cite to a
patient without a traceable source, which is exactly what the PoC's clinical
review is meant to catch.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field, asdict
from datetime import date
from typing import Any, Mapping

from src.rag.config import APPROVED_PUBLISHERS

_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

AUDIENCES = ("patient", "clinician")


class ChunkValidationError(ValueError):
    """Raised when a chunk fails the provenance contract."""


@dataclass(frozen=True)
class GuidelineChunk:
    """One retrievable unit of guideline text plus its provenance."""

    id: str
    publisher: str
    guideline_title: str
    section_path: str
    text: str
    source_url: str
    jurisdiction: str
    audience: str
    topic_tags: tuple[str, ...] = ()
    keywords: tuple[str, ...] = ()
    summary_ar: str = ""
    grade: str = ""
    published_on: str = ""
    reviewed_on: str = ""
    lang: str = "en"
    corpus_version: str = ""
    ingested_at: str = ""
    sha256: str = ""

    def __post_init__(self) -> None:
        if self.publisher not in APPROVED_PUBLISHERS:
            raise ChunkValidationError(
                f"publisher {self.publisher!r} is not one of the approved "
                f"guideline bodies {APPROVED_PUBLISHERS}"
            )
        if not self.guideline_title.strip():
            raise ChunkValidationError(f"chunk {self.id}: guideline_title is empty")
        if not self.text.strip():
            raise ChunkValidationError(f"chunk {self.id}: text is empty")
        # HTTPS only: the source_url is read back by clinical reviewers and is
        # the assistant's audit trail, so it must not be a downgradeable link.
        if not self.source_url.startswith("https://"):
            raise ChunkValidationError(
                f"chunk {self.id}: source_url must be https, got {self.source_url!r}"
            )
        if self.audience not in AUDIENCES:
            raise ChunkValidationError(
                f"chunk {self.id}: audience must be one of {AUDIENCES}"
            )
        for label, value in (
            ("published_on", self.published_on),
            ("reviewed_on", self.reviewed_on),
        ):
            if value and not _ISO_DATE.match(value):
                raise ChunkValidationError(
                    f"chunk {self.id}: {label} must be ISO YYYY-MM-DD, got {value!r}"
                )

    @property
    def embedding_text(self) -> str:
        """The text actually handed to the embedder.

        Prefixing the publisher, guideline and section gives the vector some
        topical anchoring that a bare paragraph often lacks — guideline prose
        frequently says "this recommendation" without naming the condition.
        """
        header = f"{self.publisher} — {self.guideline_title}"
        if self.section_path:
            header = f"{header} — {self.section_path}"
        return f"{header}\n{self.text}"

    def to_payload(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["topic_tags"] = list(self.topic_tags)
        payload["keywords"] = list(self.keywords)
        return payload

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "GuidelineChunk":
        known = {f for f in cls.__dataclass_fields__}
        kwargs = {key: value for key, value in data.items() if key in known}
        for key in ("topic_tags", "keywords"):
            if key in kwargs and kwargs[key] is not None:
                kwargs[key] = tuple(kwargs[key])
        return cls(**kwargs)  # type: ignore[arg-type]

    def with_digest(self, corpus_version: str, ingested_at: str) -> "GuidelineChunk":
        digest = hashlib.sha256(self.text.encode("utf-8")).hexdigest()
        return GuidelineChunk(
            **{
                **{k: v for k, v in asdict(self).items()},
                "topic_tags": self.topic_tags,
                "keywords": self.keywords,
                "sha256": digest,
                "corpus_version": corpus_version,
                "ingested_at": ingested_at,
            }
        )


@dataclass(frozen=True)
class RetrievedChunk:
    """A search hit: the chunk plus the score that surfaced it."""

    chunk: GuidelineChunk
    score: float

    @property
    def citation(self) -> str:
        """Short provenance line shown to the LLM (never read aloud verbatim)."""
        parts = [self.chunk.publisher, self.chunk.guideline_title]
        stamp = self.chunk.reviewed_on or self.chunk.published_on
        if stamp:
            parts.append(stamp)
        return " — ".join(part for part in parts if part)


def today_iso() -> str:
    return date.today().isoformat()
