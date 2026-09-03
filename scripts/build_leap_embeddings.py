#!/usr/bin/env python3
"""Build the local LEAP company vector index with Gemini embeddings."""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
import time
from pathlib import Path

import httpx
import numpy as np
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.leap_rag import LeapRag, LeapRagConfig


def _embed_batch(
    client: httpx.Client,
    *,
    api_key: str,
    model: str,
    dimensions: int,
    texts: list[str],
) -> list[list[float]]:
    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{model}:batchEmbedContents"
    )
    body = {
        "requests": [
            {
                "model": f"models/{model}",
                "content": {"parts": [{"text": text}]},
                "taskType": "RETRIEVAL_DOCUMENT",
                "outputDimensionality": dimensions,
            }
            for text in texts
        ]
    }
    last_error: Exception | None = None
    for attempt in range(5):
        try:
            response = client.post(
                url,
                headers={"x-goog-api-key": api_key},
                json=body,
            )
            response.raise_for_status()
            values = [item["values"] for item in response.json()["embeddings"]]
            if len(values) != len(texts):
                raise RuntimeError("Gemini returned the wrong number of embeddings")
            return values
        except Exception as exc:
            last_error = exc
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status not in {429, 500, 502, 503, 504} or attempt == 4:
                raise
            time.sleep(min(8.0, 0.75 * (2**attempt)))
    raise RuntimeError("Gemini embedding request failed") from last_error


def main() -> None:
    load_dotenv(".env.local")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch-size", type=int, default=50)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.batch_size < 1 or args.batch_size > 100:
        parser.error("--batch-size must be between 1 and 100")

    config = LeapRagConfig.from_env()
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key:
        parser.error("GEMINI_API_KEY is empty in .env.local")
    rag = LeapRag(config)
    documents = rag.all_embedding_documents()
    output = (args.output or config.embedding_index).expanduser()
    output.parent.mkdir(parents=True, exist_ok=True)

    vectors: list[list[float]] = []
    with httpx.Client(timeout=httpx.Timeout(30.0)) as client:
        for start in range(0, len(documents), args.batch_size):
            batch = documents[start : start + args.batch_size]
            vectors.extend(
                _embed_batch(
                    client,
                    api_key=api_key,
                    model=config.embedding_model,
                    dimensions=config.embedding_dimensions,
                    texts=[text for _, text in batch],
                )
            )
            print(f"embedded {min(start + len(batch), len(documents))}/{len(documents)}")

    matrix = np.asarray(vectors, dtype=np.float32)
    if matrix.shape != (len(documents), config.embedding_dimensions):
        raise RuntimeError(f"Unexpected embedding matrix shape: {matrix.shape}")
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    if np.any(norms <= 0):
        raise RuntimeError("Gemini returned an empty document vector")
    matrix /= norms

    with tempfile.NamedTemporaryFile(
        mode="w+b",
        suffix=".npz",
        prefix="leap-embeddings-",
        dir=output.parent,
        delete=False,
    ) as temp:
        temp_path = Path(temp.name)
        np.savez_compressed(
            temp,
            ids=np.asarray([company_id for company_id, _ in documents]),
            vectors=matrix,
            model=np.asarray(config.embedding_model),
            dimensions=np.asarray(config.embedding_dimensions),
            source_sha256=np.asarray(rag.source_sha256()),
        )
    os.replace(temp_path, output)
    os.chmod(output, 0o600)
    print(f"wrote {len(documents)} vectors to {output}")


if __name__ == "__main__":
    main()
