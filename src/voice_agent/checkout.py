"""Checkout: a LiveKit ``AgentTask`` that collects details and places the order.

The shopping agent starts it with the ``checkout`` tool (or by accepting the
``checkout`` offer). While it runs, only checkout tools are available, under
their own short instructions, so the model cannot wander back into searching
without saying so (``back_to_shopping``). Two gates are enforced in code:

* The order summary is read to the caller **word for word** (``say_exact``)
  before ``place_order`` can run; any cart change afterwards unreads it.
* ``place_order`` needs ``summary_confirmed=true``: the model states that the
  caller said yes to that summary. Writes cannot be interrupted once started.

Nothing is invented: no placeholder name or address, and a phone number is
stored only when it is a valid Saudi mobile.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from livekit.agents import AgentTask, RunContext, function_tool
from livekit.agents.llm import ToolResult
from loguru import logger

from src import catalog, flow_say, ops_mock
from src.prompts.agent_prompt import checkout_instructions
from src.spoken_lexicon import digits_say_ar, is_saudi_mobile, resolve_checkout_phone, written_phone, written_text
from src.voice_agent.base import HarnessNodes, Shop
from src.voice_agent.cart_tools import CartTools

# After this many rejected numbers she offers to go on without one.
PHONE_ATTEMPTS_BEFORE_SKIP = 2


def phone_problem(raw: str, attempts: int) -> dict[str, Any]:
    """Why a number was rejected, in facts she can say back to him.

    Live 2026-09-24: «05123456789» (11 digits) was rejected six times with
    only "not_a_saudi_mobile", so all she could say was "the number is wrong".
    """
    digits = written_phone(raw)
    problem: dict[str, Any] = {
        "error": "not_a_saudi_mobile",
        "expected": "10 digits starting with 05",
        "heard_digits": digits,
        "heard_say_ar": digits_say_ar(digits),
        "digit_count": len(digits),
    }
    if digits and not digits.startswith("05"):
        problem["starts_with"] = digits[:2]
    if attempts >= PHONE_ATTEMPTS_BEFORE_SKIP:
        problem["attempts"] = attempts
        problem["can_continue_without_phone"] = True
    return problem


_MISSING_AR = {"name": "الاسم", "phone": "رقم الجوال", "address": "العنوان", "branch": "فرع الاستلام"}


@dataclass
class CheckoutResult:
    placed: bool
    order_id: str = ""
    total_say_ar: str = ""
    # Why checkout ended without an order: what the caller asked for instead.
    request: str = ""


async def order_summary_say_ar(tools: CartTools) -> str:
    snap = await tools._cart_snapshot()
    lines = [
        flow_say.summary_line_say_ar(
            name=item["name_say_ar"],
            qty_say=item["quantity_say_ar"],
            extras_say=" وَ".join(item.get("addons_say_ar") or []),
        )
        for item in snap.get("items") or []
    ]
    return flow_say.order_summary_say_ar(lines=lines, total_say=str(snap.get("subtotal_say_ar") or ""))


class CheckoutTools:
    """Mixed into the shopping agent: the way into checkout."""

    @function_tool()
    async def checkout(self, context: RunContext) -> Any:
        """Start checkout: collect name, phone and address, then place the order.

        Call it when the caller wants to finish («بس كذا»، «اعتمد»، «كم
        الحساب وخلصني»). If a ``checkout`` offer is open and he said yes,
        respond_to_offer does the same.
        """
        return await self._run_checkout(context)

    async def _run_checkout(self, context: Any) -> Any:
        state = self.state
        if state.order_placed:
            return {"ok": False, "error": "order_already_placed", "order": state.completed_order}
        cart = await self._cart_snapshot()
        if not cart.get("ok"):
            return {"ok": False, "error": "cart_unavailable"}
        if cart.get("is_empty"):
            return {"ok": False, "error": "cart_empty", "summary_ar": "السلة فاضية، وش تحب نطلب؟"}
        state.close()
        state.begin_checkout()
        outcome = self.screen(await self.ui.navigate("/checkout"))
        if outcome.ok:
            state.ui_path = "/checkout"
        details = state.checkout
        if details.name or details.phone or details.address:
            await self.ui.fill_checkout(name=details.name or None, phone=details.phone or None, address=details.address or None)
        logger.info("Checkout starting items={} total={}", cart.get("item_count"), cart.get("subtotal_sar"))
        history = self.chat_ctx.copy(exclude_instructions=True, exclude_function_call=True)
        result: CheckoutResult = await CheckoutTask(self.shop, chat_ctx=history)
        logger.info("Checkout ended placed={} request={!r}", result.placed, result.request)
        if result.placed:
            # The confirmation was already spoken, word for word.
            return ToolResult(
                {"ok": True, "order_placed": True, "order_id": result.order_id, "total_say_ar": result.total_say_ar},
                reply_required=False,
            )
        return {
            "ok": True,
            "checkout_paused": True,
            "caller_wants": result.request,
            "summary_ar": "أبشر، وش تبي نضيف؟",
        }


class CheckoutTask(HarnessNodes, CartTools, AgentTask[CheckoutResult]):
    """Collects the caller's details, reads the summary, places the order."""

    def __init__(self, shop: Shop, *, chat_ctx: Any = None) -> None:
        self.shop = shop
        super().__init__(instructions=checkout_instructions(), chat_ctx=chat_ctx)

    async def on_enter(self) -> None:
        details = self.state.checkout
        missing = details.missing()
        if not missing:
            await self._read_summary(None)
            return
        known = {k: v for k, v in (("name", details.name), ("phone", details.phone), ("address", details.address)) if v}
        asks = "، ".join(_MISSING_AR[m] for m in missing)
        await self.session.generate_reply(
            instructions=(
                f"صفحة الدفع مفتوحة. اطلبي من العميل بجملة وحدة: {asks}. "
                + (f"عندك من قبل: {known}. " if known else "")
                + "لا تذكرين المبلغ الحين."
            )
        )

    async def _read_summary(self, context: Any) -> str:
        summary = await order_summary_say_ar(self)
        self.state.summary_read = True
        self.say_exact(context, summary)
        return summary

    @function_tool()
    async def set_customer_details(
        self,
        context: RunContext,
        name: str = "",
        phone: str = "",
        address: str = "",
        order_type: str = "",
        branch: str = "",
        phone_declined: bool = False,
    ) -> Any:
        """Record what the caller said: name, phone, address, or pickup.

        Pass only what he said this turn, as he said it. When nothing is
        missing any more, the order summary is read to him and you wait for
        his yes before place_order.

        Args:
            name: His name.
            phone: His mobile number, digits or spoken («صفر خمسة…»).
            address: The delivery address, in his words.
            order_type: "delivery" or "pickup", only when he chose.
            branch: The pickup branch he chose.
            phone_declined: True when he does not want to give a number.
        """
        details = self.state.checkout
        problems: dict[str, Any] = {}
        if name.strip():
            details.name = written_text(name)
        if phone.strip():
            normalized, rejected = resolve_checkout_phone(phone, "")
            if normalized and is_saudi_mobile(normalized):
                details.phone = normalized
                details.phone_attempts_rejected = 0
            else:
                details.phone_attempts_rejected += 1
                problems["phone"] = phone_problem(phone, details.phone_attempts_rejected)
        if phone_declined:
            details.phone_declined = True
        kind = (order_type or "").strip().lower()
        if kind in ("delivery", "pickup"):
            details.order_type = kind
        if address.strip():
            details.address = written_text(address)
            details.delivery_zone_ok = ops_mock.address_in_zone(details.address)
        if branch.strip():
            chosen = ops_mock.match_branch(branch)
            if chosen:
                details.branch = chosen["name"]
            else:
                problems["branch"] = "unknown_branch"

        self.no_interruptions(context)
        await self.ui.fill_checkout(
            name=details.name or None,
            phone=details.phone or None,
            address=(details.address if details.order_type == "delivery" else details.branch) or None,
        )
        missing = details.missing()
        out_of_zone = details.order_type == "delivery" and details.delivery_zone_ok is False
        logger.info("Checkout details name={} phone={} address={!r} type={} missing={} out_of_zone={}",
                    bool(details.name), bool(details.phone), details.address, details.order_type, missing, out_of_zone)
        facts: dict[str, Any] = {"ok": not problems, "missing": [_MISSING_AR[m] for m in missing]}
        if problems:
            facts["problems"] = problems
        if out_of_zone:
            facts["out_of_delivery_zone"] = True
            facts["pickup_branches"] = ops_mock.branch_choice_names()
            return facts
        if "branch" in missing and details.order_type == "pickup":
            facts["pickup_branches"] = ops_mock.branch_choice_names()
        if not missing and not problems:
            await self._read_summary(context)
            return ToolResult({"ok": True, "summary_read": True, "waiting_for": "his yes to the summary"}, reply_required=False)
        return facts

    @function_tool()
    async def place_order(self, context: RunContext, summary_confirmed: bool) -> Any:
        """Place the order.

        Only after the summary was read to him and he said yes to it.

        Args:
            summary_confirmed: True only when he said yes to the summary you read.
        """
        state = self.state
        if state.order_placed:
            return {"ok": True, "already_placed": True, "order": state.completed_order}
        if not state.summary_read:
            await self._read_summary(context)
            return ToolResult({"ok": False, "error": "summary_read_now", "waiting_for": "his yes"}, reply_required=False)
        if not summary_confirmed:
            return {"ok": False, "error": "caller_has_not_confirmed"}
        missing = state.checkout.missing()
        if missing:
            return {"ok": False, "error": "details_missing", "missing": [_MISSING_AR[m] for m in missing]}
        cart = await self._cart_snapshot()
        if not cart.get("ok") or cart.get("is_empty"):
            return {"ok": False, "error": "cart_empty" if cart.get("ok") else "cart_unavailable"}

        self.no_interruptions(context)
        details = state.checkout
        outcome = self.screen(
            await self.ui.complete_order(
                name=details.name,
                phone=details.phone,
                address=details.address if details.order_type == "delivery" else details.branch,
                idempotency_key=state.order_key(),
            )
        )
        logger.info("Tool place_order ok={} reason={} data={}", outcome.ok, outcome.reason, outcome.data)
        if not outcome.ok:
            return {"ok": False, "error": "order_failed", "reason": outcome.reason}
        data = outcome.data or {}
        total = float(data.get("total") or cart.get("subtotal_sar") or 0)
        total_say = catalog.sar_words(total)
        order_id = str(data.get("orderId") or "")
        state.order_placed = True
        state.completed_order = {"order_id": order_id, "total_sar": total, "total_say_ar": total_say}

        name = details.name or self.shop.known_customer.get("name", "")
        eta = (
            flow_say.pickup_eta_say_ar(eta=ops_mock.pickup_eta_say_ar(), branch=details.branch)
            if details.order_type == "pickup"
            else flow_say.delivery_eta_say_ar(ops_mock.delivery_eta_say_ar())
        )
        line = " ".join(
            bit for bit in (
                flow_say.order_confirmed_say_ar(total_say=total_say, name=name, state=state),
                ops_mock.payment_link_say_ar(),
                eta,
            ) if bit
        )
        self.say_exact(context, line)
        ops_mock.remember_customer_order(
            phone=details.phone,
            name=details.name,
            last_order_summary=" و".join(i["name_say_ar"] for i in cart.get("items") or []),
        )
        self.complete(CheckoutResult(placed=True, order_id=order_id, total_say_ar=total_say))
        return ToolResult({"ok": True, "order_placed": True, "order_id": order_id}, reply_required=False)

    @function_tool()
    async def back_to_shopping(self, context: RunContext, request: str) -> None:
        """Leave checkout because the caller wants to change the order.

        Use it when he asks to add, remove or look for something, or to stop
        ordering. The shopping agent takes over with his request.

        Args:
            request: What he asked for, in his words.
        """
        logger.info("Checkout paused: {!r}", request)
        self.state.summary_read = False
        self.complete(CheckoutResult(placed=False, request=str(request or "")))
