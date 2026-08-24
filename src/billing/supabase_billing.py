"""Thin async client for the Coda quota RPCs on Supabase.

Only three calls are needed from the agent:

* ``live_remaining(user_id)``            -> int seconds left this period
* ``consume_live_seconds(user_id, n)``   -> record n consumed live seconds
* (``refund`` is intentionally not used by the agent)

All calls go to ``{SUPABASE_URL}/rest/v1/rpc/<fn>`` authenticated with the
service-role key. The key is read from the environment and NEVER logged. If the
Supabase env is not configured the client degrades to a no-op so the agent still
runs in local development without a billing backend (it just won't enforce the
per-user budget — the LiveKit token TTL still caps the session).
"""

from __future__ import annotations

import os
from typing import Any, Optional

import httpx
from loguru import logger


class SupabaseBilling:
    """Minimal service-role RPC client. Safe to share across sessions."""

    def __init__(self, url: str, service_role_key: str, timeout: float = 5.0) -> None:
        self._base = url.rstrip("/")
        self._key = service_role_key
        self._timeout = timeout

    @property
    def enabled(self) -> bool:
        return bool(self._base and self._key)

    async def _rpc(self, fn: str, payload: dict[str, Any]) -> Optional[Any]:
        if not self.enabled:
            return None
        url = f"{self._base}/rest/v1/rpc/{fn}"
        headers = {
            "apikey": self._key,
            "Authorization": f"Bearer {self._key}",
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(url, headers=headers, json=payload)
            if resp.status_code >= 400:
                # Never surface the key; surface only status + body preview.
                raise RuntimeError(
                    f"supabase rpc {fn} failed status={resp.status_code} body={resp.text[:200]!r}"
                )
            if resp.text:
                return resp.json()
            return None

    async def live_remaining(self, user_id: str) -> Optional[int]:
        """Remaining live seconds for the user in the current period."""
        try:
            data = await self._rpc("live_remaining", {"p_user": user_id})
            return int(data) if data is not None else None
        except Exception as exc:  # noqa: BLE001 - accounting must never crash the call site
            logger.warning("live_remaining failed user={} err={}", user_id, exc)
            return None

    async def consume_live_seconds(
        self, user_id: str, seconds: int, api_key_id: Optional[str] = None
    ) -> Optional[int]:
        """Record consumed live seconds (additive) and return the seconds LEFT.

        ``consume_quota`` is all-or-nothing: asked for more than the budget has
        left it records NOTHING and returns ``allowed=false``. Billing in fixed
        15s chunks therefore stalls the counter as soon as fewer than 15 seconds
        remain — ``used`` freezes just below the limit, ``live_remaining`` never
        reaches 0, and the token route happily mints a fresh session forever.
        So when the full amount is refused we re-read the authoritative
        remainder and charge exactly that, draining the budget to 0.

        Returns the remaining seconds as reported by the server, or None when
        billing is disabled or the call failed (caller must fail open, since
        accounting must never take down a live call).

        ``api_key_id`` attributes the usage to the API key that opened the
        session, which is what makes per-key minutes reportable. Website
        sessions have no key and pass nothing; the row is then attributed to
        the user only, exactly as before.
        """
        if seconds <= 0:
            return await self.live_remaining(user_id)

        def _payload(amount: int) -> dict[str, Any]:
            body: dict[str, Any] = {
                "p_user": user_id,
                "p_kind": "live",
                "p_amount": int(amount),
            }
            # Sent only when there is a key. Omitting it keeps website
            # sessions on the exact three-parameter call they have always
            # made, so this change cannot alter their behaviour.
            if api_key_id:
                body["p_api_key"] = api_key_id
            return body

        try:
            result = await self._rpc(
                "consume_quota",
                _payload(seconds),
            )
            if isinstance(result, dict) and result.get("allowed") is False:
                left = int(result.get("remaining") or 0)
                if left <= 0:
                    logger.info(
                        "Live budget already spent user={} requested={}s", user_id, seconds
                    )
                    return 0
                # Charge the stub that actually fits, so `used` reaches the limit.
                result = await self._rpc(
                    "consume_quota",
                    _payload(left),
                )
                logger.info(
                    "Billed partial live seconds user={} requested={}s charged={}s",
                    user_id,
                    seconds,
                    left,
                )
            else:
                logger.info("Billed live seconds user={} seconds={}", user_id, seconds)
            if isinstance(result, dict) and result.get("remaining") is not None:
                return int(result["remaining"])
            return None
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "consume_live_seconds failed user={} seconds={} err={}",
                user_id,
                seconds,
                exc,
            )
            return None


def billing_from_env() -> SupabaseBilling:
    """Build a billing client from SUPABASE_URL + SUPABASE_SERVICE_ROLE_KEY.

    Accepts NEXT_PUBLIC_SUPABASE_URL as a fallback for the URL so the agent and
    the Next.js server can share one .env.local.
    """
    url = os.getenv("SUPABASE_URL") or os.getenv("NEXT_PUBLIC_SUPABASE_URL") or ""
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or ""
    if not (url and key):
        logger.warning(
            "Supabase billing not configured (SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY "
            "missing) — live-minute budget will not be enforced by the agent; only the "
            "LiveKit token TTL caps the session."
        )
    return SupabaseBilling(url, key)


def parse_participant_budget(
    identity: Optional[str], metadata: Optional[str]
) -> tuple[Optional[str], Optional[int]]:
    """Extract (user_id, live_seconds_remaining) from a joined participant.

    The Next.js /api/connection-details route mints the token with
    ``identity = "u_<userId>"`` and ``metadata = {"userId", "liveSecondsRemaining"}``.
    Both are read defensively — a participant that did not come through our token
    route (no user id) yields (None, None) and the caller falls back to the TTL.
    """
    import json

    user_id: Optional[str] = None
    remaining: Optional[int] = None

    # The user id is taken from the participant IDENTITY, which is baked into the
    # cryptographically-signed LiveKit JWT by our token route and cannot be
    # altered by the client. Metadata is only consulted for the (advisory)
    # remaining-seconds hint, so a tampered metadata value can never cause
    # billing to be attributed to a different user.
    if identity and identity.startswith("u_"):
        candidate = identity[2:]
        if candidate:
            user_id = candidate

    if metadata:
        try:
            meta = json.loads(metadata)
            if isinstance(meta, dict):
                if user_id is None and meta.get("userId"):
                    user_id = str(meta["userId"])
                if meta.get("liveSecondsRemaining") is not None:
                    remaining = int(meta["liveSecondsRemaining"])
        except (ValueError, TypeError):
            pass

    return user_id, remaining


def parse_participant_api_key(metadata: Optional[str]) -> Optional[str]:
    """Extract the issuing API key id from a joined participant's metadata.

    Only the realtime gateway stamps ``apiKeyId``; sessions started from the
    website carry no key and yield None, which keeps their usage attributed to
    the user alone.

    Unlike the user id there is no identity field to cross-check this against —
    it travels only in metadata. That is still inside the signed LiveKit JWT, so
    it is as trustworthy as whoever minted the token, but the value is validated
    as a UUID before it is ever sent to Postgres: a malformed one would make
    ``consume_quota`` raise, and a raising meter would take down a live call
    that should have been billed and allowed to continue.
    """
    import json
    import uuid

    if not metadata:
        return None
    try:
        meta = json.loads(metadata)
    except (ValueError, TypeError):
        return None
    if not isinstance(meta, dict):
        return None

    raw = meta.get("apiKeyId")
    if not raw:
        return None
    try:
        return str(uuid.UUID(str(raw)))
    except (ValueError, AttributeError, TypeError):
        return None
