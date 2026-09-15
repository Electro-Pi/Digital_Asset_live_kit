#!/usr/bin/env python3
"""
Stdio MCP server exposing live Salla Merchant API tools for the voice agent.

Uses the shared `sala_api.SallaClient` from the parent sala-webhook project.
Configure tokens via env (populated from the app.store.authorize webhook flow):

  SALLA_STORE_ID
  SALLA_CLIENT_ID
  SALLA_CLIENT_SECRET
  SALLA_ACCESS_TOKEN
  SALLA_REFRESH_TOKEN
  SALLA_EXPIRES_AT   # unix timestamp; optional if token is fresh
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

# Parent repo root (sala-webhook/) so `sala_api` imports cleanly.
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mcp.server.fastmcp import FastMCP  # noqa: E402

from sala_api import InMemoryTokenStore, SallaAuthError, SallaClient  # noqa: E402

mcp = FastMCP("Salla Merchant API")


def _client() -> SallaClient:
    store_id = os.getenv("SALLA_STORE_ID", "").strip()
    client_id = os.getenv("SALLA_CLIENT_ID", "").strip()
    client_secret = os.getenv("SALLA_CLIENT_SECRET", "").strip()
    access_token = os.getenv("SALLA_ACCESS_TOKEN", "").strip()
    refresh_token = os.getenv("SALLA_REFRESH_TOKEN", "").strip()
    expires_at = float(os.getenv("SALLA_EXPIRES_AT", "0") or 0)

    if not store_id:
        raise RuntimeError("Missing SALLA_STORE_ID (use merchant id from app.store.authorize)")
    if not access_token:
        raise RuntimeError(
            "Missing SALLA_ACCESS_TOKEN. Install the app and capture tokens "
            "from the app.store.authorize webhook first."
        )
    # client_id/secret are only required later for refresh_token exchange.
    # Live reads work with a valid access_token alone.

    token_store = InMemoryTokenStore()
    token_store.save(
        store_id,
        {
            "access_token": access_token,
            "refresh_token": refresh_token or access_token,
            "expires_at": expires_at or (time.time() + 14 * 24 * 3600),
        },
    )
    return SallaClient(
        store_id=store_id,
        client_id=client_id or "unused",
        client_secret=client_secret or "unused",
        token_store=token_store,
    )


def _err(exc: Exception) -> dict:
    if isinstance(exc, SallaAuthError):
        return {"ok": False, "error": "unauthenticated", "detail": str(exc)}
    return {"ok": False, "error": "request_failed", "detail": str(exc)}


@mcp.tool()
def search_products(
    keyword: str = "",
    category: str = "",
    status: str = "",
    per_page: int = 10,
    page: int = 1,
) -> dict:
    """Search the merchant's Salla products by keyword, category, or status.

    Args:
        keyword: Product name or SKU fragment.
        category: Optional category id.
        status: Optional product status (sale, hidden, out).
        per_page: Page size (default 10, max 30).
        page: Page number.
    """
    try:
        data = _client().get_products(
            keyword=keyword or None,
            category=category or None,
            status=status or None,
            per_page=min(max(per_page, 1), 30),
            page=max(page, 1),
        )
        return {"ok": True, "data": data}
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@mcp.tool()
def list_orders(
    customer_id: str = "",
    status: str = "",
    per_page: int = 10,
    page: int = 1,
) -> dict:
    """List the merchant's Salla orders, optionally filtered by customer or status.

    Args:
        customer_id: Optional customer id to scope orders.
        status: Optional order status filter.
        per_page: Page size (default 10, max 30).
        page: Page number.
    """
    try:
        data = _client().get_orders(
            customer_id=customer_id or None,
            status=status or None,
            per_page=min(max(per_page, 1), 30),
            page=max(page, 1),
        )
        return {"ok": True, "data": data}
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@mcp.tool()
def get_order_details(order_id: str) -> dict:
    """Fetch full details for one Salla order by id.

    Args:
        order_id: The Salla order id.
    """
    if not order_id or not str(order_id).strip():
        return {"ok": False, "error": "invalid_args", "detail": "order_id is required"}
    try:
        data = _client().get_order(str(order_id).strip())
        return {"ok": True, "data": data}
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


if __name__ == "__main__":
    mcp.run(transport="stdio")
