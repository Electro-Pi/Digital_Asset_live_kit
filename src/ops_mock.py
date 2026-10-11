"""Delivery zones, pickup branches and returning customers.

No live zone API or branch list exists yet. The data lives in
``data/operations.json`` and returning customers in
``data/known_customers.json``; swap these functions for real lookups without
touching the tools that call them.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

from src.catalog_text import normalize_ar

_DATA = Path(__file__).resolve().parent.parent / "data"
_OPERATIONS_PATH = _DATA / "operations.json"
_CUSTOMERS_PATH = _DATA / "known_customers.json"


@lru_cache(maxsize=1)
def operations() -> dict[str, Any]:
    return json.loads(_OPERATIONS_PATH.read_text(encoding="utf-8"))


def pickup_branches() -> list[dict[str, str]]:
    return [dict(b) for b in operations().get("pickup_branches") or []]


def delivery_eta_say_ar() -> str:
    return str(operations().get("delivery_eta_say_ar") or "")


def pickup_eta_say_ar() -> str:
    return str(operations().get("pickup_eta_say_ar") or "")


def payment_link_say_ar() -> str:
    return str(operations().get("payment_link_say_ar") or "")


def address_in_zone(text: str) -> Optional[bool]:
    """True in zone, False out of zone, None when the address names no known place."""
    folded = normalize_ar(text)
    if not folded:
        return None
    if any(normalize_ar(tok) in folded for tok in operations().get("out_zone_tokens") or []):
        return False
    if any(normalize_ar(tok) in folded for tok in operations().get("in_zone_tokens") or []):
        return True
    return None


def match_branch(text: str) -> Optional[dict[str, str]]:
    folded = normalize_ar(text)
    if not folded:
        return None
    for branch in pickup_branches():
        name = normalize_ar(branch.get("name"))
        spoken = normalize_ar(branch.get("name_say_ar"))
        bare = name.replace("فرع", "").strip()
        if (name and name in folded) or (spoken and spoken in folded) or (bare and bare in folded):
            return branch
    return None


def branch_choice_names() -> list[str]:
    return [b["name_say_ar"] for b in pickup_branches()]


def _load_customers() -> dict[str, Any]:
    try:
        raw = json.loads(_CUSTOMERS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def lookup_customer(phone: str) -> Optional[dict[str, str]]:
    """A returning customer by phone, or None."""
    key = "".join(ch for ch in str(phone or "") if ch.isdigit())
    row = _load_customers().get(key) if key else None
    if not isinstance(row, dict):
        return None
    name = str(row.get("name") or "").strip()
    last_order = str(row.get("last_order_summary") or "").strip()
    if not name and not last_order:
        return None
    return {"phone": key, "name": name, "last_order_summary": last_order}


def remember_customer_order(*, phone: str = "", name: str = "", last_order_summary: str = "") -> None:
    key = "".join(ch for ch in str(phone or "") if ch.isdigit())
    if not key:
        return
    data = _load_customers()
    data[key] = {"name": str(name or "").strip(), "last_order_summary": str(last_order_summary or "").strip()}
    try:
        _CUSTOMERS_PATH.parent.mkdir(parents=True, exist_ok=True)
        _CUSTOMERS_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        return
