"""Drive the user's browser UI from the agent, over LiveKit RPC.

The agent cannot reach the browser with HTTP: the tab is not addressable and
there is no inbound route to it. What already exists is the authenticated
WebRTC session the voice call runs on, and LiveKit exposes request/response RPC
on top of it. That is the whole transport — no gRPC, no extra socket, no
polling. See FRONTEND_INTEGRATION.md for the handler side.

RPC rather than ``publish_data`` specifically because it **returns a value**. A
fire-and-forget data message leaves the agent unable to distinguish "the user is
now looking at the page" from "nothing happened". Here a failure comes back as
a failure, and the tool tells the model to say so.

Every call degrades instead of raising: an un-registered method (frontend not
built yet), a disconnected user, or a timeout all return ``ok=False`` with a
reason. A voice call must not die because a UI handler is missing.

──────────────────────────────────────────────────────────────────────────────
RPC CONTRACT
──────────────────────────────────────────────────────────────────────────────

Every payload is a JSON object. Every response is a JSON string.

1. alawaly.navigate
   Receives: { "url": "https://www.alawaly.sa/ar/projects" }
   Returns:  { "ok": true, "url": "https://www.alawaly.sa/ar/projects" }

2. alawaly.showProject
   Receives: { "projectId": "akam-homes",
                "url": "https://www.alawaly.sa/ar/project/akam-homes",
                "name": "آكام هومز" }
   Returns:  { "ok": true, "projectId": "akam-homes", "url": "..." }

3. alawaly.fillContact
   Receives: only the fields the caller actually said. Any key may be omitted.
              { "name": "أحمد محمد",
                "phone": "0551234567",
                "identityType": "هوية وطنية",
                "purchasePlan": "تمويل عقاري",
                "employer": "أرامكو",
                "email": "ahmad@example.com",
                "sakaniEligible": true,
                "monthlyIncome": "15000",
                "projectId": "akam-homes",
                "spaceType": "مكتب",
                "companySize": "10-50",
                "purpose": "شراء" }
   Returns:  { "ok": true }

4. alawaly.getUiState
   Receives: {}
   Returns:  { "ok": true,
                "url": "https://www.alawaly.sa/ar/project/akam-homes",
                "projectId": "akam-homes" }
──────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable, Optional

from livekit import rtc
from livekit.rtc import RpcError
from loguru import logger

from src import turn_ledger
from src.config import CONFIG

METHOD_NAVIGATE = "alawaly.navigate"
METHOD_SHOW_PROJECT = "alawaly.showProject"
METHOD_FILL_CONTACT = "alawaly.fillContact"
METHOD_GET_UI_STATE = "alawaly.getUiState"
SITE_ORIGIN = "https://www.alawaly.sa"

_CONTACT_FIELDS = (
    "name",
    "phone",
    "identityType",
    "purchasePlan",
    "employer",
    "email",
    "sakaniEligible",
    "monthlyIncome",
    "projectId",
    "spaceType",
    "companySize",
    "purpose",
)

DEFAULT_RPC_TIMEOUT = CONFIG.ui.rpc_timeout
RETRY_RPC_TIMEOUT   = CONFIG.ui.rpc_retry_timeout

_TRANSIENT_RPC_CODES = frozenset(
    {
        RpcError.ErrorCode.CONNECTION_TIMEOUT,
        RpcError.ErrorCode.RESPONSE_TIMEOUT,
        RpcError.ErrorCode.RECIPIENT_DISCONNECTED,
        RpcError.ErrorCode.RECIPIENT_NOT_FOUND,
    }
)

_EFFECTS: dict[str, tuple[str, str]] = {
    METHOD_NAVIGATE: (turn_ledger.NAVIGATE, "url"),
    METHOD_SHOW_PROJECT: (turn_ledger.NAVIGATE, "url"),
}


@dataclass
class UIResult:
    """Outcome of one UI call, in the shape a function tool can return."""

    ok: bool
    reason: Optional[str] = None
    data: Optional[dict[str, Any]] = None

    def as_tool_result(self, *, on_success: str, on_failure: str) -> dict[str, Any]:
        """Render for the LLM, including what to tell the user."""
        result: dict[str, Any] = {"ok": self.ok}
        if self.ok:
            if on_success:
                result["say_to_user_ar"] = on_success
            if self.data:
                result.update(self.data)
        else:
            result["failure_reason"] = self.reason
            result["say_to_user_ar"] = on_failure
        return result


class UIBridge:
    """Sends UI commands to one participant's browser over LiveKit RPC."""

    def __init__(
        self,
        room: Optional[rtc.Room],
        identity: Optional[str],
        *,
        timeout: float = DEFAULT_RPC_TIMEOUT,
        on_effect: Optional[Callable[..., None]] = None,
    ) -> None:
        self._room = room
        self._identity = identity
        self._timeout = timeout
        self.on_effect = on_effect

    @property
    def available(self) -> bool:
        return bool(self._room and self._resolve_identity())

    def _resolve_identity(self) -> Optional[str]:
        """Pick the browser participant to call right now.

        The identity captured at session start goes stale the moment the user
        reloads the page: the tab rejoins under a new identity, and every RPC
        to the old one dies with CONNECTION_TIMEOUT. So the stored identity is
        only a preference; whoever is actually in the room wins.
        """
        if self._room is None:
            return None
        remotes = list(self._room.remote_participants.values())
        for participant in remotes:
            if participant.identity == self._identity:
                return self._identity
        fallback: Optional[str] = None
        for participant in remotes:
            attrs = getattr(participant, "attributes", None) or {}
            if any(str(k).startswith("alawaly_") or str(k).startswith("luqma_") for k in dict(attrs)):
                return participant.identity
            if fallback is None:
                fallback = participant.identity
        return fallback

    def page_attributes(self) -> dict[str, str]:
        """Page snapshot the browser pushes onto the participant attributes."""
        identity = self._resolve_identity()
        if not identity or self._room is None:
            return {}
        for participant in self._room.remote_participants.values():
            if participant.identity == identity:
                attrs = getattr(participant, "attributes", None) or {}
                return {str(k): str(v) for k, v in dict(attrs).items()}
        return {}

    async def call(self, method: str, payload: dict[str, Any] | str) -> UIResult:
        """Perform one UI call and record what it did."""
        outcome = await self._invoke(method, payload)
        effect = _EFFECTS.get(method)
        if effect is not None and self.on_effect is not None:
            kind, target_key = effect
            if isinstance(payload, str):
                target = payload
            else:
                target = str(payload.get(target_key) or "") if target_key else ""
            try:
                self.on_effect(kind, target=target, ok=outcome.ok)
            except Exception as e:  # noqa: BLE001
                logger.exception("Effect recording failed method={} error={}", method, e)
        return outcome

    async def _invoke(self, method: str, payload: dict[str, Any] | str) -> UIResult:
        identity = self._resolve_identity()
        if not identity or self._room is None:
            logger.warning(
                "UI call skipped, no participant to send to method={} payload={}",
                method, payload,
            )
            return UIResult(ok=False, reason="no_ui_participant")
        if identity != self._identity:
            logger.info(
                "UI call retargeted from stale identity {} to {}",
                self._identity, identity,
            )
            self._identity = identity

        encoded = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)

        last_error: Optional[RpcError] = None
        for attempt, timeout in enumerate((self._timeout, RETRY_RPC_TIMEOUT)):
            if attempt:
                identity = self._resolve_identity()
                if not identity:
                    break
                self._identity = identity
                logger.info(
                    "UI call retrying method={} identity={} timeout={}s",
                    method, identity, timeout,
                )
            try:
                raw = await self._room.local_participant.perform_rpc(
                    destination_identity=identity,
                    method=method,
                    payload=encoded,
                    response_timeout=timeout,
                )
            except RpcError as e:
                if e.code == RpcError.ErrorCode.UNSUPPORTED_METHOD:
                    logger.error(
                        "UI method {} is not registered by the frontend. "
                        "See FRONTEND_INTEGRATION.md.",
                        method,
                    )
                    return UIResult(ok=False, reason="frontend_handler_missing")
                logger.warning(
                    "UI call failed method={} code={} message={} attempt={}",
                    method, e.code, e.message, attempt + 1,
                )
                last_error = e
                if e.code in _TRANSIENT_RPC_CODES:
                    continue
                return UIResult(ok=False, reason=f"rpc_error_{e.code}")
            except Exception as e:  # noqa: BLE001
                logger.exception("UI call raised method={} error={}", method, e)
                return UIResult(ok=False, reason="rpc_exception")

            data = _parse_response(method, raw)
            if data.get("ok") is False:
                return UIResult(ok=False, reason=str(data.get("error") or "frontend_declined"), data=data)
            logger.info("UI call ok method={} response={}", method, raw[:300])
            return UIResult(ok=True, data=data)

        code = last_error.code if last_error else "unknown"
        return UIResult(ok=False, reason=f"rpc_error_{code}")

    # ── High-level helpers (called by tools) ───────────────────────────────

    async def navigate(self, path: str, title: str = "") -> UIResult:
        """``alawaly.navigate`` receives ``{"url": "<absolute url>"}``."""
        del title
        return await self.call(METHOD_NAVIGATE, {"url": absolute_url(path)})

    async def show_project(self, project_id: str, path: str, name: str = "") -> UIResult:
        """``alawaly.showProject`` receives project id, page URL, and Arabic name."""
        return await self.call(
            METHOD_SHOW_PROJECT,
            {"projectId": project_id, "url": absolute_url(path), "name": name},
        )

    async def fill_contact(self, **fields: Any) -> UIResult:
        """``alawaly.fillContact`` receives only the interest-form fields that were given."""
        payload = {key: value for key, value in fields.items() if key in _CONTACT_FIELDS and value not in (None, "")}
        return await self.call(METHOD_FILL_CONTACT, payload)

    async def get_ui_state(self) -> UIResult:
        """``alawaly.getUiState`` receives an empty object and returns the open page."""
        return await self.call(METHOD_GET_UI_STATE, {})


def absolute_url(path: str) -> str:
    """Turn a site path into the URL the frontend navigates to."""
    raw = (path or "").strip() or "/ar"
    if raw.startswith(("http://", "https://")):
        return raw
    if raw.startswith("#"):
        return f"{SITE_ORIGIN}/ar{raw}"
    if not raw.startswith("/"):
        raw = f"/{raw}"
    return f"{SITE_ORIGIN}{raw}"


def _parse_response(method: str, raw: str) -> dict[str, Any]:
    """Tolerate a handler that returns nothing, or plain text instead of JSON."""
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        logger.warning("UI response for {} was not JSON: {!r}", method, raw[:200])
        return {"raw": raw}
    if not isinstance(parsed, dict):
        return {"raw": parsed}
    return parsed
