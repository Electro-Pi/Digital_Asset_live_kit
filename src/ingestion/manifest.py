"""Provenance records for the corpus.

This is the artifact the PoC's clinical validation actually runs on. A reviewer
needs to answer "which version of which guideline is the assistant quoting, and
when did we take it?" without reading any code, so every fetched document
records its URL, content digest, fetch date and licence terms.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

MANIFEST_PATH_DEFAULT = Path("knowledge") / "manifest.json"


@dataclass
class DocumentRecord:
    doc_id: str
    publisher: str
    guideline_title: str
    url: str
    jurisdiction: str
    audience: str
    kind: str
    license_note: str
    fetched_at: str
    content_sha256: str
    chunk_count: int
    topic_tags: list[str]
    published_on: str = ""
    reviewed_on: str = ""
    extraction: str = ""
    error: str = ""


@dataclass
class Manifest:
    corpus_version: str
    generated_at: str
    documents: list[DocumentRecord]

    @classmethod
    def load(cls, path: Path) -> "Manifest":
        if not path.exists():
            return cls(
                corpus_version="",
                generated_at="",
                documents=[],
            )
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            corpus_version=data.get("corpus_version", ""),
            generated_at=data.get("generated_at", ""),
            documents=[DocumentRecord(**doc) for doc in data.get("documents", [])],
        )

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload: dict[str, Any] = {
            "corpus_version": self.corpus_version,
            "generated_at": self.generated_at,
            "documents": [asdict(doc) for doc in self.documents],
        }
        path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    def upsert(self, record: DocumentRecord) -> None:
        for index, existing in enumerate(self.documents):
            if existing.doc_id == record.doc_id:
                self.documents[index] = record
                return
        self.documents.append(record)

    def by_publisher(self) -> dict[str, list[DocumentRecord]]:
        grouped: dict[str, list[DocumentRecord]] = {}
        for doc in self.documents:
            grouped.setdefault(doc.publisher, []).append(doc)
        return grouped


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
