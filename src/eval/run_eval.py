"""Produce the clinical review pack.

The PoC requires vendor clinical experts to validate that the assistant reads
the right source material and says safe things about it. That review needs one
row per question showing what was asked, how it was routed, exactly which
guideline passages were retrieved with their URLs, and what the model then said.

    uv run python -m src.eval.run_eval --out knowledge/EVAL.md

Runs against the live stack, so Qdrant, Gemini and the vLLM endpoint must all be
up. Uses only the synthetic golden questions — no patient data is involved.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
from pathlib import Path

from dotenv import load_dotenv
from loguru import logger

load_dotenv(".env.local")

from openai import AsyncOpenAI  # noqa: E402

from src.prompts.medical_prompt import system_prompt  # noqa: E402
from src.rag.config import settings_from_env  # noqa: E402
from src.rag.retriever import MedicalRetriever  # noqa: E402

GOLDEN = Path("tests/fixtures/golden_questions_ar.jsonl")


async def _answer(client: AsyncOpenAI, settings, question: str, evidence: str | None) -> str:
    messages = [{"role": "system", "content": system_prompt}]
    if evidence:
        messages.append({"role": "system", "content": evidence})
    messages.append({"role": "user", "content": question})
    response = await client.chat.completions.create(
        model=settings.llm_model,
        messages=messages,  # type: ignore[arg-type]
        temperature=0.3,
        max_tokens=400,
    )
    return (response.choices[0].message.content or "").strip()


async def run(out_path: Path, csv_path: Path | None, limit: int) -> None:
    settings = settings_from_env()
    retriever = await MedicalRetriever.create(settings)
    client = AsyncOpenAI(
        base_url=settings.llm_base_url, api_key=settings.llm_api_key, timeout=90.0
    )

    cases = [
        json.loads(line)
        for line in GOLDEN.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if limit:
        cases = cases[:limit]

    rows: list[dict] = []
    try:
        for index, case in enumerate(cases, start=1):
            decision = await retriever.decide(case["question"])

            answer = await _answer(
                client, settings, case["question"], decision.evidence
            )

            rows.append(
                {
                    "id": case["id"],
                    "question": case["question"],
                    "expected_route": case["route"],
                    "actual_route": decision.route.value,
                    "expected_publishers": ", ".join(case.get("expect_publishers", [])),
                    "retrieved": [
                        {
                            "publisher": hit.chunk.publisher,
                            "guideline": hit.chunk.guideline_title,
                            "section": hit.chunk.section_path,
                            "url": hit.chunk.source_url,
                            "score": round(hit.score, 3),
                            "text": hit.chunk.text,
                        }
                        for hit in decision.hits
                    ],
                    "answer": answer,
                    "latency_ms": round(decision.latency_ms),
                }
            )
            logger.info("Evaluated {}/{} {}", index, len(cases), case["id"])
    finally:
        await retriever.aclose()

    _write_markdown(out_path, rows)
    if csv_path:
        _write_csv(csv_path, rows)


def _write_markdown(path: Path, rows: list[dict]) -> None:
    lines = [
        "# Clinical review pack",
        "",
        "One section per question. For each, the route the system chose, the "
        "guideline passages it retrieved (with source URLs), and the answer it "
        "produced. Reviewers should check that the retrieved passages support "
        "the answer and that nothing in the answer is absent from them.",
        "",
    ]
    routing_errors = [r for r in rows if r["expected_route"] != r["actual_route"]]
    lines.append(
        f"Questions: **{len(rows)}** · Routing mismatches: **{len(routing_errors)}**"
    )
    lines.append("")

    for row in rows:
        flag = "" if row["expected_route"] == row["actual_route"] else "  ⚠️ routing mismatch"
        lines.append(f"## {row['id']} — {row['question']}{flag}")
        lines.append("")
        lines.append(
            f"Route: `{row['actual_route']}` (expected `{row['expected_route']}`) · "
            f"{row['latency_ms']} ms"
        )
        lines.append("")
        if row["retrieved"]:
            lines.append("**Evidence retrieved**")
            lines.append("")
            for index, hit in enumerate(row["retrieved"], start=1):
                lines.append(
                    f"{index}. [{hit['score']}] **{hit['publisher']}** — "
                    f"{hit['guideline']} — _{hit['section']}_  \n"
                    f"   <{hit['url']}>  \n"
                    f"   > {hit['text'][:600]}"
                )
            lines.append("")
        else:
            lines.append("_No evidence retrieved._")
            lines.append("")
        lines.append("**Answer**")
        lines.append("")
        lines.append(f"> {row['answer']}")
        lines.append("")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
    logger.info("Wrote review pack to {}", path)


def _write_csv(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            ["id", "question", "expected_route", "actual_route",
             "expected_publishers", "retrieved_publishers", "sources", "answer"]
        )
        for row in rows:
            writer.writerow([
                row["id"], row["question"], row["expected_route"],
                row["actual_route"], row["expected_publishers"],
                ", ".join(hit["publisher"] for hit in row["retrieved"]),
                " | ".join(hit["url"] for hit in row["retrieved"]),
                row["answer"],
            ])
    logger.info("Wrote review CSV to {}", path)


def main() -> None:
    parser = argparse.ArgumentParser(prog="src.eval.run_eval")
    parser.add_argument("--out", type=Path, default=Path("knowledge/EVAL.md"))
    parser.add_argument("--csv", type=Path, default=Path("knowledge/EVAL.csv"))
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()
    asyncio.run(run(args.out, args.csv, args.limit))


if __name__ == "__main__":
    main()
