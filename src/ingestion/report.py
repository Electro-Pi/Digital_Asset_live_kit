"""Provenance and coverage reporting.

Two audiences. The provenance table is for the vendor's clinical experts, who
must confirm the assistant is reading current, correctly attributed guidelines.
The coverage table is for us: it says which parts of the PoC's clinical scope
the corpus is thin on, while there is still time to fix it.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from src.ingestion.manifest import Manifest
from src.ingestion.taxonomy import TOPICS
from src.rag.config import APPROVED_PUBLISHERS
from src.rag.models import GuidelineChunk

# Below this, a scope area is effectively unanswerable and needs more sources.
THIN_COVERAGE = 8


def render_report(manifest: Manifest, chunks: list[GuidelineChunk]) -> str:
    lines: list[str] = []
    lines.append("# Medical guideline corpus — provenance and coverage")
    lines.append("")
    lines.append(f"Corpus version: **{manifest.corpus_version or 'unbuilt'}**")
    lines.append(f"Generated: {manifest.generated_at or 'n/a'}")
    lines.append(f"Total chunks: **{len(chunks)}**")
    lines.append("")

    # ─── Publisher summary ───────────────────────────────────────────────────
    by_publisher = Counter(chunk.publisher for chunk in chunks)
    lines.append("## Sources")
    lines.append("")
    lines.append("| Publisher | Documents | Chunks | Status |")
    lines.append("|---|---:|---:|---|")
    grouped = manifest.by_publisher()
    for publisher in APPROVED_PUBLISHERS:
        docs = grouped.get(publisher, [])
        failed = [doc for doc in docs if doc.error]
        status = "ok" if docs and not failed else ("missing" if not docs else f"{len(failed)} failed")
        lines.append(
            f"| {publisher} | {len(docs)} | {by_publisher.get(publisher, 0)} | {status} |"
        )
    lines.append("")

    # ─── Scope coverage ──────────────────────────────────────────────────────
    tag_counts: Counter[str] = Counter()
    for chunk in chunks:
        tag_counts.update(chunk.topic_tags)

    lines.append("## Clinical scope coverage")
    lines.append("")
    lines.append("| Topic | Chunks | |")
    lines.append("|---|---:|---|")
    for tag, label in TOPICS.items():
        count = tag_counts.get(tag, 0)
        flag = "" if count >= THIN_COVERAGE else ("**GAP**" if count == 0 else "thin")
        lines.append(f"| {label} | {count} | {flag} |")
    lines.append("")

    # ─── Document detail ─────────────────────────────────────────────────────
    lines.append("## Documents")
    lines.append("")
    for publisher in APPROVED_PUBLISHERS:
        docs = grouped.get(publisher, [])
        if not docs:
            continue
        lines.append(f"### {publisher}")
        lines.append("")
        note = docs[0].license_note
        if note:
            lines.append(f"_Licence / attribution: {note}_")
            lines.append("")
        lines.append("| Guideline | Chunks | Fetched | URL | Notes |")
        lines.append("|---|---:|---|---|---|")
        for doc in sorted(docs, key=lambda d: d.guideline_title):
            note = doc.error or ""
            lines.append(
                f"| {doc.guideline_title} | {doc.chunk_count} | "
                f"{doc.fetched_at[:10]} | {doc.url} | {note} |"
            )
        lines.append("")

    return "\n".join(lines)


def write_report(path: Path, manifest: Manifest, chunks: list[GuidelineChunk]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_report(manifest, chunks), encoding="utf-8")
    return path
