#!/usr/bin/env python3
"""Generate the spoken-Arabic name for every LEAP company.

The voice pack stores exhibitor names in Latin script only — there is no Arabic
name field on any of the 1087 rows. Without one the language model has to invent
a pronunciation from the spelling on every call, and it gets Arabic-origin
brands wrong in the worst way: HAMS AI (همس) came out as "هامز", a nonsense word
that happens to sound like an English reading of the romanization.

So the pronunciation is precomputed here and shipped beside the pack as
``01-companies/name_ar.json`` (a company id → spoken Arabic map). The RAG merges
it over the SQLite rows at load time, which keeps it out of the immutable
database and lets it survive a pack rebuild. A name that is wrong can be fixed
by editing that one file and restarting the agent — no regeneration needed.

Usage:
    .venv/bin/python scripts/build_company_name_ar.py [--limit N] [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.leap_rag import LeapRag, LeapRagConfig, NAME_AR_FILENAME

# gemini-2.5-flash is 404 for keys that did not already have it, and
# gemini-flash-latest is the alias Google points at the current flash model.
DEFAULT_MODEL = "gemini-flash-latest"
# Large enough that the whole pack costs ~30 requests, small enough that one bad
# response only has to be retried for 40 names.
BATCH_SIZE = 40

INSTRUCTION = """You produce the SPOKEN ARABIC form of exhibitor brand names for a Saudi Arabic text-to-speech voice.
For each input, output the Arabic script exactly as a Saudi presenter would SAY the name aloud.

Rules, in priority order:
1. If the brand is Arabic in origin (an Arab/Gulf company whose Latin name is a romanization of an Arabic word), restore the ORIGINAL Arabic spelling. Never transliterate it letter by letter.
   HAMS -> همس   Thiqah -> ثقة   Elm -> علم   Tuwaiq -> طويق   Mozn -> مزن   Nafath -> نفاذ   Jahez -> جاهز
2. An initialism said letter by letter is spelled with Arabic letter names.
   STC -> إس تي سي   IBM -> آي بي إم   AI -> إي آي. But an acronym said as a word stays a word: SDAIA -> سدايا.
3. Any other foreign brand: write it phonetically in Arabic script the way Arabic speakers pronounce it.
   Microsoft -> مايكروسوفت   Salesforce -> سيلز فورس   Huawei -> هواوي
4. Never translate the meaning. Never add or drop words beyond unspoken legal suffixes (Ltd, LLC, Co.).
5. Text already in Arabic stays exactly as it is.

Return STRICT JSON only: an object mapping each input id to its Arabic string. No prose, no code fence."""


# The model likes to append the official long name or an English gloss in
# parentheses. Spoken aloud that is a mouthful, and the leading form is always
# the one a caller would recognise, so the parenthetical is dropped.
_PARENTHETICAL = re.compile(r"\s*[（(][^)）]*[)）]\s*")


def normalize_spoken(name: str) -> str:
    """Trim one generated name down to what should actually be said."""
    return _PARENTHETICAL.sub(" ", name).strip(" -–—،,")


def _generate(
    client: httpx.Client, *, api_key: str, model: str, batch: dict[str, str]
) -> dict[str, str]:
    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{model}:generateContent"
    )
    body = {
        "contents": [
            {
                "parts": [
                    {
                        "text": INSTRUCTION
                        + "\n\nInput:\n"
                        + json.dumps(batch, ensure_ascii=False)
                    }
                ]
            }
        ],
        # Deterministic: the same pack must produce the same pronunciations, so a
        # rerun is a no-op diff rather than a fresh set of guesses.
        "generationConfig": {"temperature": 0, "responseMimeType": "application/json"},
    }
    response = client.post(url, params={"key": api_key}, json=body, timeout=180.0)
    response.raise_for_status()
    payload = response.json()
    text = payload["candidates"][0]["content"]["parts"][0]["text"]
    parsed = json.loads(text)
    if not isinstance(parsed, dict):
        raise ValueError("model did not return a JSON object")
    return {
        str(k): normalize_spoken(str(v))
        for k, v in parsed.items()
        if normalize_spoken(str(v))
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0, help="only the first N rows")
    parser.add_argument("--dry-run", action="store_true", help="print, do not write")
    parser.add_argument("--model", default=os.getenv("GEMINI_TEXT_MODEL", DEFAULT_MODEL))
    args = parser.parse_args()

    load_dotenv(ROOT / ".env.local")
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key:
        print("GEMINI_API_KEY is not set", file=sys.stderr)
        return 2

    config = LeapRagConfig.from_env()
    rag = LeapRag(config)
    rows = [
        {"id": str(row["id"]), "name": str(row.get("name") or "").strip()}
        for row in rag.all_companies()
    ]
    rows = [row for row in rows if row["name"]]
    if args.limit:
        rows = rows[: args.limit]
    print(f"companies to name: {len(rows)}")

    out_path = config.data_dir / "01-companies" / NAME_AR_FILENAME
    # A rerun only fills gaps, so a hand-corrected name is never overwritten and
    # an interrupted run resumes instead of paying for the whole pack again.
    existing: dict[str, str] = {}
    if out_path.is_file():
        existing = json.loads(out_path.read_text(encoding="utf-8"))
        print(f"existing names kept: {len(existing)}")
    todo = [row for row in rows if row["id"] not in existing]
    print(f"to generate: {len(todo)}")

    resolved = dict(existing)
    failures = 0
    with httpx.Client() as client:
        for start in range(0, len(todo), BATCH_SIZE):
            batch_rows = todo[start : start + BATCH_SIZE]
            batch = {row["id"]: row["name"] for row in batch_rows}
            try:
                got = _generate(
                    client, api_key=api_key, model=args.model, batch=batch
                )
            except Exception as exc:  # noqa: BLE001 — one bad batch is not fatal
                failures += 1
                print(f"  batch at {start}: FAILED {type(exc).__name__}: {exc}")
                got = {}
            for row in batch_rows:
                # Falling back to the Latin name leaves that one company exactly
                # as it behaves today rather than dropping it from the answer.
                resolved[row["id"]] = got.get(row["id"]) or row["name"]
            done = min(start + BATCH_SIZE, len(todo))
            print(f"  {done}/{len(todo)} named")
            time.sleep(0.4)

    missing = [row["id"] for row in rows if row["id"] not in resolved]
    print(f"resolved={len(resolved)} missing={len(missing)} failed_batches={failures}")

    if args.dry_run:
        for row in rows[:20]:
            print(f"  {row['name'][:40]:42s} -> {resolved.get(row['id'])}")
        return 0

    out_path.parent.mkdir(parents=True, exist_ok=True)
    # Atomic replace: the agent reads this file at startup and a half-written
    # one would take the worker down on its next restart.
    with tempfile.NamedTemporaryFile(
        "w", dir=out_path.parent, delete=False, encoding="utf-8", suffix=".tmp"
    ) as handle:
        json.dump(resolved, handle, ensure_ascii=False, indent=1, sort_keys=True)
        temp_path = Path(handle.name)
    temp_path.replace(out_path)
    print(f"wrote {out_path} ({len(resolved)} names)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
