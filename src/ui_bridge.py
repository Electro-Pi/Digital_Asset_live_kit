"""Drive the user's browser UI from the agent, over LiveKit RPC.

The agent cannot reach the browser with HTTP: the tab is not addressable and
there is no inbound route to it. What already exists is the authenticated
WebRTC session the voice call runs on, and LiveKit exposes request/response RPC
on top of it. That is the whole transport — no gRPC, no extra socket, no
polling. See FRONTEND_INTEGRATION.md for the handler side.

RPC rather than ``publish_data`` specifically because it **returns a value**. A
fire-and-forget data message leaves the agent unable to distinguish "the user is
now looking at the meal page" from "nothing happened", and لقمة is under
instruction never to claim an action it did not perform. Here a failure comes
back as a failure, and the tool tells the model to say so.

Every call degrades instead of raising: an un-registered method (frontend not
built yet), a disconnected user, or a timeout all return ``ok=False`` with a
reason. A voice call must not die because a UI handler is missing.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Optional

from livekit import rtc
from livekit.rtc import RpcError
from loguru import logger

# Method names are a contract with the frontend. Namespaced so they cannot
# collide with anything else the app registers on the same participant.
METHOD_NAVIGATE = "luqma.navigate"
METHOD_SHOW_MEAL = "luqma.showMeal"
METHOD_ADD_TO_CART = "luqma.addToCart"
METHOD_GET_CART = "luqma.getCart"

# Bump when a payload shape changes incompatibly; the frontend echoes it back so
# a version skew shows up in logs instead of as silently ignored fields.
PROTOCOL_VERSION = 1

# Short on purpose. This blocks a live conversation: لقمة is mid-sentence while
# it runs, so a slow answer is worse than a failed one.
DEFAULT_RPC_TIMEOUT = 5.0


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
    ) -> None:
        self._room = room
        self._identity = identity
        self._timeout = timeout
        # Latched after the first UNSUPPORTED_METHOD: if the frontend has not
        # shipped the handlers, every later call would burn a round trip to
        # rediscover that, mid-conversation.
        self._unsupported: set[str] = set()

    @property
    def available(self) -> bool:
        return bool(self._room and self._identity)

    async def call(self, method: str, payload: dict[str, Any]) -> UIResult:
        if not self.available:
            logger.warning(
                "UI call skipped, no participant to send to method={} payload={}",
                method,
                payload,
            )
            return UIResult(ok=False, reason="no_ui_participant")

        if method in self._unsupported:
            return UIResult(ok=False, reason="frontend_handler_missing")

        body = dict(payload)
        body["v"] = PROTOCOL_VERSION

        try:
            raw = await self._room.local_participant.perform_rpc(
                destination_identity=self._identity,
                method=method,
                payload=json.dumps(body, ensure_ascii=False),
                response_timeout=self._timeout,
            )
        except RpcError as e:
            if e.code == RpcError.ErrorCode.UNSUPPORTED_METHOD:
                self._unsupported.add(method)
                logger.error(
                    "UI method {} is not registered by the frontend. See "
                    "FRONTEND_INTEGRATION.md — the agent will keep working but "
                    "cannot drive the UI.",
                    method,
                )
                return UIResult(ok=False, reason="frontend_handler_missing")
            logger.warning(
                "UI call failed method={} code={} message={}", method, e.code, e.message
            )
            return UIResult(ok=False, reason=f"rpc_error_{e.code}")
        except Exception as e:  # noqa: BLE001 - a UI failure must not end the call
            logger.exception("UI call raised method={} error={}", method, e)
            return UIResult(ok=False, reason="rpc_exception")

        data = _parse_response(method, raw)
        if data.get("ok") is False:
            return UIResult(ok=False, reason=str(data.get("error") or "frontend_declined"), data=data)
        logger.info("UI call ok method={} response={}", method, raw[:300])
        return UIResult(ok=True, data=data)

    async def navigate(self, path: str) -> UIResult:
        return await self.call(METHOD_NAVIGATE, {"path": path})

    async def show_meal(self, meal_id: str, path: str, reason: str = "") -> UIResult:
        return await self.call(
            METHOD_SHOW_MEAL, {"mealId": meal_id, "path": path, "reason": reason}
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

    async def get_cart(self) -> UIResult:
        return await self.call(METHOD_GET_CART, {})


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
