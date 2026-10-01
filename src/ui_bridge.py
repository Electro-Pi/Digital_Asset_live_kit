"""Drive the user's browser UI from the agent, over LiveKit RPC.

The agent cannot reach the browser with HTTP: the tab is not addressable and
there is no inbound route to it. What already exists is the authenticated
WebRTC session the voice call runs on, and LiveKit exposes request/response RPC
on top of it. That is the whole transport — no gRPC, no extra socket, no
polling. See FRONTEND_INTEGRATION.md for the handler side.

RPC rather than ``publish_data`` specifically because it **returns a value**. A
fire-and-forget data message leaves the agent unable to distinguish "the user is
now looking at the meal page" from "nothing happened", and سلمى is under
instruction never to claim an action it did not perform. Here a failure comes
back as a failure, and the tool tells the model to say so.

Every call degrades instead of raising: an un-registered method (frontend not
built yet), a disconnected user, or a timeout all return ``ok=False`` with a
reason. A voice call must not die because a UI handler is missing.
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

# Method names are a contract with the frontend.
METHOD_NAVIGATE = "alawaly.navigate"
METHOD_SHOW_PROJECT = "alawaly.showProject"
METHOD_SHOW_MEAL = METHOD_SHOW_PROJECT
METHOD_FILL_CONTACT = "alawaly.fillContact"
METHOD_GET_UI_STATE = "alawaly.getUiState"

# Fallback method names
LEGACY_NAVIGATE = "luqma.navigate"
LEGACY_SHOW_MEAL = "luqma.showMeal"

PROTOCOL_VERSION = 1

DEFAULT_RPC_TIMEOUT = CONFIG.ui.rpc_timeout
RETRY_RPC_TIMEOUT = CONFIG.ui.rpc_retry_timeout

_TRANSIENT_RPC_CODES = frozenset(
    {
        RpcError.ErrorCode.CONNECTION_TIMEOUT,
        RpcError.ErrorCode.RESPONSE_TIMEOUT,
        RpcError.ErrorCode.RECIPIENT_DISCONNECTED,
        RpcError.ErrorCode.RECIPIENT_NOT_FOUND,
    }
)

_EFFECTS: dict[str, tuple[str, str]] = {
    METHOD_NAVIGATE: (turn_ledger.NAVIGATE, "path"),
    LEGACY_NAVIGATE: (turn_ledger.NAVIGATE, "path"),
    METHOD_SHOW_PROJECT: (turn_ledger.MEAL_PAGE, "projectId"),
    LEGACY_SHOW_MEAL: (turn_ledger.MEAL_PAGE, "mealId"),
    METHOD_FILL_CONTACT: ("contact_fill", ""),
}


@dataclass
class UIResult:
    """Outcome of one UI call, in the shape a function tool can return."""

    ok: bool
    reason: Optional[str] = None
    data: Optional[dict[str, Any]] = None

    def as_tool_result(self, *, on_success: str, on_failure: str) -> dict[str, Any]:
        """Render for the LLM, including what to tell the user.

        The Arabic line matters: without it the model narrates a failure as if
        it succeeded, or invents an explanation for why it could not.
        """
        result: dict[str, Any] = {"ok": self.ok}
        if self.ok:
            # Empty success text is not a script. Facts on the tool result
            # are what she speaks from (meal includes, price, addons, …).
            if on_success:
                result["say_to_user_ar"] = on_success
            if self.data:
                result.update(self.data)
        else:
            result["failure_reason"] = self.reason
            result["say_to_user_ar"] = on_failure
        return result


class UIBridge:
    """Sends UI commands to one participant's browser."""

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
        # Set by the agent to its TurnLedger.record. Every mutation reports
        # itself here, so no guard has to infer one from the transcript.
        self.on_effect = on_effect
        # Do NOT permanently latch UNSUPPORTED_METHOD. A production frontend can
        # be rebuilt mid-day while the voice session stays up; latching made
        # getUiState/fillCheckout dead for the whole call after one miss.

    @property
    def available(self) -> bool:
        return bool(self._room and self._resolve_identity())

    def _resolve_identity(self) -> Optional[str]:
        """Pick the browser participant to call, right now.

        The identity captured at session start goes stale the moment the user
        reloads the page: the tab rejoins the room under a new identity, and
        every RPC to the old one dies with CONNECTION_TIMEOUT (observed live —
        showMeal/navigate failing mid-session while the user was right there).
        So the stored identity is only a preference; whoever is actually in
        the room wins.
        """
        if self._room is None:
            return None
        remotes = list(self._room.remote_participants.values())
        for participant in remotes:
            if participant.identity == self._identity:
                return self._identity
        # Stored identity is gone. Prefer a participant that pushes luqma_
        # attributes (that is the Luqmah tab by definition), else any remote.
        fallback: Optional[str] = None
        for participant in remotes:
            attrs = getattr(participant, "attributes", None) or {}
            if any(str(k).startswith("luqma_") for k in dict(attrs)):
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
                return {str(k): str(v) for k, v in dict(attrs).items() if str(k).startswith("luqma_")}
        return {}

    async def call(self, method: str, payload: dict[str, Any]) -> UIResult:
        """Perform one UI call and record what it did to the customer.

        The recording lives here, wrapping every exit path of
        :meth:`_invoke`, because this is the only place a browser mutation can
        pass through. Recording in the tools instead would miss the seven
        places a tool calls another tool internally, which is exactly how the
        2026-09-21 silent add slipped past the speech guard.
        """
        outcome = await self._invoke(method, payload)
        effect = _EFFECTS.get(method)
        if effect is not None and self.on_effect is not None:
            kind, target_key = effect
            try:
                self.on_effect(
                    kind,
                    target=str(payload.get(target_key) or "") if target_key else "",
                    ok=outcome.ok,
                )
            except Exception as e:  # noqa: BLE001 - bookkeeping must not end a call
                logger.exception("Effect recording failed method={} error={}", method, e)
        return outcome

    async def _invoke(self, method: str, payload: dict[str, Any]) -> UIResult:
        identity = self._resolve_identity()
        if not identity or self._room is None:
            logger.warning(
                "UI call skipped, no participant to send to method={} payload={}",
                method,
                payload,
            )
            return UIResult(ok=False, reason="no_ui_participant")
        if identity != self._identity:
            logger.info(
                "UI call retargeted from stale identity {} to {}",
                self._identity, identity,
            )
            self._identity = identity

        body = dict(payload)
        body["v"] = PROTOCOL_VERSION
        encoded = json.dumps(body, ensure_ascii=False)

        last_error: Optional[RpcError] = None
        for attempt, timeout in enumerate((self._timeout, RETRY_RPC_TIMEOUT)):
            if attempt:
                # The target may have changed while the first attempt hung
                # (e.g. the tab finished reloading under a new identity).
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
                        "UI method {} is not registered by the frontend (user may "
                        "need a hard refresh after deploy). See FRONTEND_INTEGRATION.md.",
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
            except Exception as e:  # noqa: BLE001 - a UI failure must not end the call
                logger.exception("UI call raised method={} error={}", method, e)
                return UIResult(ok=False, reason="rpc_exception")

            data = _parse_response(method, raw)
            if data.get("ok") is False:
                return UIResult(ok=False, reason=str(data.get("error") or "frontend_declined"), data=data)
            logger.info("UI call ok method={} response={}", method, raw[:300])
            return UIResult(ok=True, data=data)

        code = last_error.code if last_error else "unknown"
        return UIResult(ok=False, reason=f"rpc_error_{code}")

    async def navigate(self, path: str, title: str = "") -> UIResult:
        res = await self.call(METHOD_NAVIGATE, {"path": path, "title": title})
        if not res.ok and res.reason == "frontend_handler_missing":
            res = await self.call(LEGACY_NAVIGATE, {"path": path})
        return res

    async def show_project(self, project_id: str, path: str = "", title: str = "") -> UIResult:
        res = await self.call(METHOD_SHOW_PROJECT, {"projectId": project_id, "path": path, "title": title})
        if not res.ok and res.reason == "frontend_handler_missing":
            res = await self.call(LEGACY_SHOW_MEAL, {"mealId": project_id, "path": path})
        return res

    async def show_meal(self, meal_id: str, path: str, reason: str = "") -> UIResult:
        return await self.show_project(project_id=meal_id, path=path)

    async def fill_contact(self, **kwargs: Any) -> UIResult:
        return await self.call(METHOD_FILL_CONTACT, kwargs)

    async def set_meal_options(
        self,
        *,
        meal_id: str,
        quantity: int = 1,
        spicy: bool = False,
        addon_ids: Optional[list[str]] = None,
    ) -> UIResult:
        """Tick the addon/spicy/quantity controls on the open meal page.

        Deliberately separate from ``add_to_cart``: this only moves the UI, so
        the user watches the additions appear while سلمى is still asking about
        them. Nothing is committed to the cart here.
        """
        return await self.call(
            METHOD_SET_MEAL_OPTIONS,
            {
                "mealId": meal_id,
                "quantity": quantity,
                "spicy": spicy,
                "addonIds": addon_ids or [],
            },
        )

    async def add_to_cart(
        self,
        *,
        meal_id: str,
        quantity: int = 1,
        spicy: bool = False,
        addon_ids: Optional[list[str]] = None,
    ) -> UIResult:
        return await self.call(
            METHOD_ADD_TO_CART,
            {
                "mealId": meal_id,
                "quantity": quantity,
                "spicy": spicy,
                "addonIds": addon_ids or [],
            },
        )

    async def set_cart_quantity(
        self,
        *,
        meal_id: str,
        quantity: int,
        spicy: bool = False,
        addon_ids: Optional[list[str]] = None,
        line_id: str = "",
    ) -> UIResult:
        """Set absolute quantity on a cart line. 0 removes the line.

        Distinct from ``add_to_cart``, which *adds* to any existing quantity.
        Live failure: "اعمل اثنين" called add_to_cart(qty=2) on a line that
        already had 1 → cart became 3 (231 SAR) while she said 154.
        """
        payload: dict[str, Any] = {
            "mealId": meal_id,
            "quantity": quantity,
            "spicy": spicy,
            "addonIds": addon_ids or [],
        }
        if line_id:
            payload["lineId"] = line_id
        return await self.call(METHOD_SET_CART_QUANTITY, payload)

    async def set_saved(self, *, meal_id: str, saved: bool = True) -> UIResult:
        """Set a meal's saved state — deliberately absolute, never a toggle.

        The agent cannot see which hearts are already filled, so "toggle" would
        mean asking twice unsaves the dish, and a retry after a timeout undoes
        the save it just made. Sending the desired end state makes the call
        idempotent: "save this" is safe to repeat.
        """
        return await self.call(METHOD_SET_SAVED, {"mealId": meal_id, "saved": saved})

    async def get_cart(self) -> UIResult:
        return await self.call(METHOD_GET_CART, {})

    async def get_saved(self) -> UIResult:
        """List meal ids currently in the browser bookmarks.

        Distinct from ``set_saved``: without a read, سلمى invents "empty"
        after opening /saved (live Bug-004).
        """
        return await self.call(METHOD_GET_SAVED, {})

    async def clear_saved(self) -> UIResult:
        """Remove every saved meal. 0 items is success."""
        return await self.call(METHOD_CLEAR_SAVED, {})

    async def set_ui_filter(
        self,
        *,
        page: str = "",
        offers_kind: str = "",
        query: str = "",
        restaurants_filter: str = "",
        cuisine: str = "",
        cashback: bool = False,
        menu_tab: str = "",
        path: str = "",
    ) -> UIResult:
        """Flip on-screen chips/tabs the agent cannot reach via navigate alone."""
        payload: dict[str, Any] = {}
        if page:
            payload["page"] = page
        if offers_kind:
            payload["offersKind"] = offers_kind
        if query:
            payload["query"] = query
        if restaurants_filter:
            payload["restaurantsFilter"] = restaurants_filter
        if cuisine:
            payload["cuisine"] = cuisine
        if cashback:
            payload["cashback"] = True
        if menu_tab:
            payload["menuTab"] = menu_tab
        if path:
            payload["path"] = path
        return await self.call(METHOD_SET_UI_FILTER, payload)

    async def get_ui_state(self) -> UIResult:
        """Ask the browser which page the user is looking at right now."""
        return await self.call(METHOD_GET_UI_STATE, {})

    async def fill_checkout(
        self,
        *,
        name: Optional[str] = None,
        phone: Optional[str] = None,
        address: Optional[str] = None,
    ) -> UIResult:
        """Fill fields on the open checkout form (page must be mounted)."""
        payload: dict[str, Any] = {}
        if name is not None:
            payload["name"] = name
        if phone is not None:
            payload["phone"] = phone
        if address is not None:
            payload["address"] = address
        return await self.call(METHOD_FILL_CHECKOUT, payload)

    async def complete_order(
        self,
        *,
        name: str,
        phone: str = "",
        address: str = "",
        idempotency_key: str = "",
    ) -> UIResult:
        """Place the order from the live cart and open the success page."""
        payload: dict[str, Any] = {
            "name": name,
            "phone": phone,
            "address": address,
        }
        if idempotency_key:
            payload["idempotencyKey"] = idempotency_key
        return await self.call(METHOD_COMPLETE_ORDER, payload)


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
