"""Load reuse-pack env. Role-specific keys fall back to CODA_API_KEY."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

_HERE = Path(__file__).resolve().parent
_PACK = _HERE.parent

load_dotenv(_PACK / ".env")
load_dotenv(_PACK / ".env.example")


def get(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


def require(name: str, default: str = "") -> str:
    value = get(name, default)
    if not value:
        raise SystemExit(f"missing {name} — copy .env.example to .env and fill it in")
    return value


def api_key(*names: str) -> str:
    for name in names:
        value = (os.getenv(name) or "").strip()
        if value:
            return value
    return require("CODA_API_KEY")


def strip_v1(url: str) -> str:
    return url.rstrip("/").removesuffix("/v1")
