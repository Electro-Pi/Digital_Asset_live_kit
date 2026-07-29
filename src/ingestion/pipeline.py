"""Fetch → extract → chunk, for one guideline source.

Each stage writes its output to disk so the pipeline is resumable and every
later stage can be re-run without touching the publishers again. Chunk JSONL
lives under ``knowledge/chunks/<source>.jsonl`` and is the hand-off point to
enrichment and indexing.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from loguru import logger

from src.ingestion.catalog import GuidelineSource, SourceDocument
from src.ingestion.chunker import chunk_blocks
from src.ingestion.extract import extract_html_blocks, extract_pdf_blocks
from src.ingestion.fetch import FetchError, PoliteFetcher
from src.ingestion.manifest import DocumentRecord, utc_now
from src.ingestion.taxonomy import guess_topics
from src.rag.models import GuidelineChunk, today_iso

KNOWLEDGE_ROOT = Path("knowledge")
RAW_ROOT = KNOWLEDGE_ROOT / "raw"
CHUNK_ROOT = KNOWLEDGE_ROOT / "chunks"
MANIFEST_PATH = KNOWLEDGE_ROOT / "manifest.json"


def _slugify(value: str) -> str:
    return "".join(ch if ch.isalnum() else "-" for ch in value.lower()).strip("-")


async def process_document(
    source: GuidelineSource,
    document: SourceDocument,
    fetcher: PoliteFetcher,
    *,
    corpus_version: str,
    refresh: bool = False,
) -> tuple[list[GuidelineChunk], DocumentRecord]:
    record = DocumentRecord(
        doc_id=document.doc_id,
        publisher=source.publisher,
        guideline_title=document.guideline_title,
        url=document.url,
        jurisdiction=source.jurisdiction,
        audience=document.audience,
        kind=document.kind,
        license_note=source.license_note,
        fetched_at=utc_now(),
        content_sha256="",
        chunk_count=0,
        topic_tags=list(document.topic_tags),
        published_on=document.published_on,
        reviewed_on=document.reviewed_on,
        extraction="trafilatura" if document.kind == "html" else "pymupdf",
    )

    try:
        payload = await fetcher.get(
            document.url,
            delay_s=source.crawl_delay_s,
            binary=document.kind == "pdf",
            refresh=refresh,
        )
    except FetchError as exc:
        logger.error("Fetch failed for {}: {}", document.doc_id, exc)
        record.error = str(exc)
        return [], record

    record.content_sha256 = hashlib.sha256(payload).hexdigest()

    if document.kind == "pdf":
        blocks = extract_pdf_blocks(payload)
    else:
        blocks = extract_html_blocks(
            payload.decode("utf-8", errors="replace"),
            drop_sections=source.drop_sections,
            stop_at_section=source.stop_at_section,
        )

    if not blocks:
        record.error = "extraction produced no text"
        logger.error("No text extracted from {}", document.url)
        return [], record

    pieces = chunk_blocks(blocks)
    chunks: list[GuidelineChunk] = []
    for index, piece in enumerate(pieces):
        section = piece.section_path or document.guideline_title
        chunk = GuidelineChunk(
            id=f"{source.name}:{document.doc_id}:{index:04d}",
            publisher=source.publisher,
            guideline_title=document.guideline_title,
            section_path=section,
            text=piece.text,
            source_url=document.url,
            jurisdiction=source.jurisdiction,
            audience=document.audience,
            topic_tags=document.topic_tags or guess_topics(piece.text),
            grade=document.grade,
            published_on=document.published_on,
            reviewed_on=document.reviewed_on,
            lang="en",
        ).with_digest(corpus_version, today_iso())
        chunks.append(chunk)

    record.chunk_count = len(chunks)
    logger.info(
        "Processed {} blocks={} chunks={}", document.doc_id, len(blocks), len(chunks)
    )
    return chunks, record


async def build_source(
    source: GuidelineSource,
    *,
    corpus_version: str,
    refresh: bool = False,
) -> tuple[list[GuidelineChunk], list[DocumentRecord]]:
    fetcher = PoliteFetcher(RAW_ROOT)
    all_chunks: list[GuidelineChunk] = []
    records: list[DocumentRecord] = []
    try:
        for document in source.documents:
            chunks, record = await process_document(
                source,
                document,
                fetcher,
                corpus_version=corpus_version,
                refresh=refresh,
            )
            all_chunks.extend(chunks)
            records.append(record)
    finally:
        await fetcher.aclose()
    return all_chunks, records


def write_chunks(source_name: str, chunks: list[GuidelineChunk]) -> Path:
    CHUNK_ROOT.mkdir(parents=True, exist_ok=True)
    path = CHUNK_ROOT / f"{source_name}.jsonl"
    with path.open("w", encoding="utf-8") as handle:
        for chunk in chunks:
            handle.write(json.dumps(chunk.to_payload(), ensure_ascii=False) + "\n")
    return path


def read_chunks(source_name: str) -> list[GuidelineChunk]:
    path = CHUNK_ROOT / f"{source_name}.jsonl"
    if not path.exists():
        return []
    chunks: list[GuidelineChunk] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                chunks.append(GuidelineChunk.from_mapping(json.loads(line)))
    return chunks


def read_all_chunks() -> list[GuidelineChunk]:
    if not CHUNK_ROOT.exists():
        return []
    chunks: list[GuidelineChunk] = []
    for path in sorted(CHUNK_ROOT.glob("*.jsonl")):
        chunks.extend(read_chunks(path.stem))
    return chunks
