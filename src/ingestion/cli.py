"""Command line entry point for building the guideline corpus.

    uv run python -m src.ingestion.cli fetch   --source all
    uv run python -m src.ingestion.cli enrich  --source all
    uv run python -m src.ingestion.cli index
    uv run python -m src.ingestion.cli report
    uv run python -m src.ingestion.cli search "متى أحتاج فحص السكر؟"

``fetch`` is the only stage that touches the publishers' servers, and it caches
everything it downloads, so the later stages are free to re-run.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from dotenv import load_dotenv
from loguru import logger

load_dotenv(".env.local")

from dataclasses import replace  # noqa: E402

from src.ingestion import sources as source_registry  # noqa: E402
from src.ingestion.enrich import ChunkEnricher, merge_topics  # noqa: E402
from src.ingestion.index_build import build_index  # noqa: E402
from src.ingestion.manifest import MANIFEST_PATH_DEFAULT, Manifest, utc_now  # noqa: E402
from src.ingestion.pipeline import (  # noqa: E402
    build_source,
    read_all_chunks,
    read_chunks,
    write_chunks,
)
from src.ingestion.report import write_report  # noqa: E402
from src.rag.config import settings_from_env  # noqa: E402
from src.rag.embedder import GeminiEmbedder  # noqa: E402
from src.rag.store import QdrantStore  # noqa: E402

REPORT_PATH = Path("knowledge") / "CORPUS_REPORT.md"


def _resolve_sources(name: str) -> list:
    if name == "all":
        return source_registry.all_sources()
    source = source_registry.get(name)
    if source is None:
        raise SystemExit(
            f"Unknown source {name!r}. Available: "
            f"{', '.join(source_registry.names())}, all"
        )
    return [source]


async def cmd_fetch(args: argparse.Namespace) -> int:
    settings = settings_from_env()
    manifest = Manifest.load(MANIFEST_PATH_DEFAULT)
    manifest.corpus_version = settings.corpus_version
    manifest.generated_at = utc_now()

    total = 0
    for source in _resolve_sources(args.source):
        logger.info("Fetching source {} ({} documents)", source.name, len(source.documents))
        chunks, records = await build_source(
            source, corpus_version=settings.corpus_version, refresh=args.refresh
        )
        write_chunks(source.name, chunks)
        for record in records:
            manifest.upsert(record)
        total += len(chunks)
        logger.info("Source {} produced {} chunks", source.name, len(chunks))

    manifest.save(MANIFEST_PATH_DEFAULT)
    logger.info("Fetch complete chunks={} manifest={}", total, MANIFEST_PATH_DEFAULT)
    return 0


async def cmd_enrich(args: argparse.Namespace) -> int:
    settings = settings_from_env()
    enricher = ChunkEnricher(settings, concurrency=args.concurrency)

    for source in _resolve_sources(args.source):
        chunks = read_chunks(source.name)
        if not chunks:
            logger.warning("No chunks for {} — run fetch first", source.name)
            continue
        if args.limit:
            chunks = chunks[: args.limit]
        logger.info("Enriching {} chunks for {}", len(chunks), source.name)
        enriched = await enricher.enrich(chunks)
        write_chunks(source.name, enriched)
    return 0


async def cmd_retag(args: argparse.Namespace) -> int:
    """Re-apply the catalogs' per-document topic tags to enriched chunks.

    Separate from `enrich` because it needs neither the LLM nor the embedder:
    topic tags are payload only and do not affect a chunk's vector, so a live
    collection can be corrected in place rather than rebuilt.
    """
    settings = settings_from_env()
    store = QdrantStore(settings) if args.update_index else None
    try:
        for source in _resolve_sources(args.source):
            chunks = read_chunks(source.name)
            if not chunks:
                continue
            by_doc = {doc.doc_id: doc for doc in source.documents}
            updated = []
            for chunk in chunks:
                # Chunk ids are "<source>:<doc_id>:<index>"; doc ids contain no
                # colons, so a 3-way split recovers the document.
                parts = chunk.id.split(":")
                document = by_doc.get(parts[1]) if len(parts) >= 3 else None
                if document is None:
                    updated.append(chunk)
                    continue
                updated.append(
                    replace(
                        chunk,
                        topic_tags=merge_topics(document.topic_tags, chunk.topic_tags),
                    )
                )
            write_chunks(source.name, updated)
            if store is not None:
                await store.update_topic_tags(
                    settings.versioned_collection,
                    {chunk.id: chunk.topic_tags for chunk in updated},
                )
            logger.info("Retagged {} chunks for {}", len(updated), source.name)
    finally:
        if store is not None:
            await store.close()
    return 0


async def cmd_index(args: argparse.Namespace) -> int:
    settings = settings_from_env()
    chunks = read_all_chunks()
    logger.info("Indexing {} chunks into {}", len(chunks), settings.versioned_collection)
    await build_index(
        settings, chunks, promote=not args.no_promote, resume=args.resume
    )
    return 0


async def cmd_report(_: argparse.Namespace) -> int:
    manifest = Manifest.load(MANIFEST_PATH_DEFAULT)
    chunks = read_all_chunks()
    path = write_report(REPORT_PATH, manifest, chunks)
    logger.info("Wrote {} ({} chunks)", path, len(chunks))
    print(path.read_text(encoding="utf-8"))
    return 0


async def cmd_search(args: argparse.Namespace) -> int:
    settings = settings_from_env()
    embedder = GeminiEmbedder(settings)
    store = QdrantStore(settings)
    try:
        vector = await embedder.embed_query(args.query)
        hits = await store.search(
            vector, top_k=args.top_k, score_threshold=args.threshold
        )
        if not hits:
            print("no hits above threshold")
        for hit in hits:
            print(f"\n[{hit.score:.3f}] {hit.citation}")
            print(f"  section: {hit.chunk.section_path}")
            if hit.chunk.summary_ar:
                print(f"  عربي: {hit.chunk.summary_ar}")
            print(f"  {hit.chunk.text[:280]}...")
    finally:
        await store.close()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="src.ingestion.cli")
    sub = parser.add_subparsers(dest="command", required=True)

    fetch = sub.add_parser("fetch", help="download, extract and chunk source documents")
    fetch.add_argument("--source", default="all")
    fetch.add_argument(
        "--refresh", action="store_true", help="ignore the on-disk cache"
    )
    fetch.set_defaults(handler=cmd_fetch)

    enrich = sub.add_parser("enrich", help="add Arabic summaries and topic tags")
    enrich.add_argument("--source", default="all")
    enrich.add_argument("--concurrency", type=int, default=6)
    enrich.add_argument("--limit", type=int, default=0)
    enrich.set_defaults(handler=cmd_enrich)

    retag = sub.add_parser(
        "retag", help="re-apply catalog topic tags (no LLM, no re-embedding)"
    )
    retag.add_argument("--source", default="all")
    retag.add_argument(
        "--update-index",
        action="store_true",
        help="also patch payloads in the live collection",
    )
    retag.set_defaults(handler=cmd_retag)

    index = sub.add_parser("index", help="embed chunks and publish the collection")
    index.add_argument("--no-promote", action="store_true")
    index.add_argument(
        "--resume",
        action="store_true",
        help="keep chunks already indexed instead of rebuilding from scratch",
    )
    index.set_defaults(handler=cmd_index)

    report = sub.add_parser("report", help="write the provenance and coverage report")
    report.set_defaults(handler=cmd_report)

    search = sub.add_parser("search", help="query the live index")
    search.add_argument("query")
    search.add_argument("--top-k", type=int, default=5)
    search.add_argument("--threshold", type=float, default=0.0)
    search.set_defaults(handler=cmd_search)

    return parser


def main() -> int:
    args = build_parser().parse_args()
    return asyncio.run(args.handler(args))


if __name__ == "__main__":
    sys.exit(main())
