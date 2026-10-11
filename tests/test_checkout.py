"""Checkout: the task's gates, and how the shopping agent enters and leaves it."""

from __future__ import annotations

from livekit.agents.llm import ToolResult

from fakes import FakeContext, make_shop
from src import catalog
from src.voice_agent import checkout as checkout_module
from src.voice_agent.assistant import Assistant
from src.voice_agent.checkout import CheckoutResult, CheckoutTask

SALAD = "protein-box-2"


def task_with_cart(**ui):
    shop = make_shop(**ui)
    shop.ui.cart.append({"mealId": SALAD, "quantity": 2, "addonIds": ["water"], "spicy": False})
    return CheckoutTask(shop), shop


def output(result):
    return result.output if isinstance(result, ToolResult) else result


async def test_missing_details_are_listed_not_invented():
    task, shop = task_with_cart()
    result = await task.set_customer_details(FakeContext(), name="محمد")
    assert set(result["missing"]) == {"العنوان", "رقم الجوال"}
    assert shop.ui.form == {"name": "محمد"}
    assert shop.state.summary_read is False


async def test_invalid_phone_is_reported():
    task, shop = task_with_cart()
    result = await task.set_customer_details(FakeContext(), phone="12345")
    problem = result["problems"]["phone"]
    assert problem["error"] == "not_a_saudi_mobile" and problem["digit_count"] == 5
    assert shop.state.checkout.phone == ""


async def test_spoken_phone_is_normalised():
    task, shop = task_with_cart()
    await task.set_customer_details(FakeContext(), phone="0 5 5 1 2 3 4 5 6 7")
    assert shop.state.checkout.phone == "0551234567"


async def test_complete_details_read_the_summary_word_for_word():
    task, shop = task_with_cart()
    ctx = FakeContext()
    result = await task.set_customer_details(ctx, name="محمد", phone="0551234567", address="حي النرجس، الرياض")
    assert isinstance(result, ToolResult) and result.reply_required is False
    assert shop.state.summary_read is True
    summary = ctx.session.said[-1]
    meal = catalog.load_catalog().by_id(SALAD)
    assert catalog.speakable_name(meal.name) in summary
    water = next(float(a["price"]) for a in meal.addons if a["id"] == "water")
    assert catalog.sar_words((meal.effective_price + water) * 2) in summary


async def test_declining_the_phone_is_allowed():
    task, shop = task_with_cart()
    result = await task.set_customer_details(FakeContext(), name="محمد", address="حي العليا الرياض", phone_declined=True)
    assert output(result)["summary_read"] is True


async def test_out_of_zone_offers_pickup_branches():
    task, shop = task_with_cart()
    result = await task.set_customer_details(FakeContext(), name="محمد", phone="0551234567", address="حي الروضة، جدة")
    assert result["out_of_delivery_zone"] is True and result["pickup_branches"]
    assert shop.state.summary_read is False
    picked = await task.set_customer_details(FakeContext(), order_type="pickup", branch="النرجس")
    assert output(picked)["summary_read"] is True
    assert shop.state.checkout.branch == "فرع النرجس"


async def test_unknown_branch_is_reported():
    task, _ = task_with_cart()
    result = await task.set_customer_details(FakeContext(), order_type="pickup", branch="فرع القمر")
    assert result["problems"] == {"branch": "unknown_branch"}


async def _ready(task, ctx=None):
    return await task.set_customer_details(ctx or FakeContext(), name="محمد", phone="0551234567", address="حي النرجس الرياض")


async def test_place_order_before_the_summary_reads_it_first():
    task, shop = task_with_cart()
    shop.state.checkout.name, shop.state.checkout.address, shop.state.checkout.phone = "محمد", "حي النرجس الرياض", "0551234567"
    ctx = FakeContext()
    result = await task.place_order(ctx, summary_confirmed=True)
    assert output(result)["error"] == "summary_read_now"
    assert shop.ui.orders == [] and ctx.session.said  # summary spoken, nothing ordered


async def test_place_order_needs_his_yes():
    task, shop = task_with_cart()
    await _ready(task)
    result = await task.place_order(FakeContext(), summary_confirmed=False)
    assert result["error"] == "caller_has_not_confirmed" and shop.ui.orders == []


async def test_place_order_after_yes_places_speaks_and_completes():
    task, shop = task_with_cart()
    await _ready(task)
    ctx = FakeContext()
    result = await task.place_order(ctx, summary_confirmed=True)
    assert isinstance(result, ToolResult) and result.reply_required is False
    assert shop.ui.orders and shop.ui.orders[0]["idempotencyKey"] == shop.state.idempotency_key
    assert shop.ui.orders[0]["name"] == "محمد"
    assert shop.state.order_placed is True
    assert "ثَبَّت طَلَبَك" in ctx.session.said[-1]
    assert ctx.uninterruptible >= 1
    assert task.done()


async def test_placing_twice_does_not_order_twice():
    task, shop = task_with_cart()
    await _ready(task)
    await task.place_order(FakeContext(), summary_confirmed=True)
    again = await task.place_order(FakeContext(), summary_confirmed=True)
    assert output(again)["already_placed"] is True and len(shop.ui.orders) == 1


async def test_changing_the_cart_after_the_summary_requires_a_new_one():
    task, shop = task_with_cart()
    await _ready(task)
    await task.update_cart(FakeContext(), meal_id=SALAD, quantity=1)
    result = await task.place_order(FakeContext(), summary_confirmed=True)
    assert output(result)["error"] == "summary_read_now" and shop.ui.orders == []


async def test_order_failure_is_reported():
    task, shop = task_with_cart(fail={"luqma.completeOrder"})
    await _ready(task)
    result = await task.place_order(FakeContext(), summary_confirmed=True)
    assert result["ok"] is False and result["error"] == "order_failed"
    assert shop.state.order_placed is False


async def test_back_to_shopping_ends_the_task_with_the_request():
    task, _ = task_with_cart()
    await task.back_to_shopping(FakeContext(), request="أبي أزيد بيبسي")
    assert task.done()
    assert task._AgentTask__fut.result() == CheckoutResult(placed=False, request="أبي أزيد بيبسي")  # noqa: SLF001


# ─── Entering checkout from the shopping agent ───────────────────────────────

class _StubTask:
    """Stands in for CheckoutTask, which needs a running session to await."""

    result = CheckoutResult(placed=True, order_id="order-9", total_say_ar="عشرين ريال")

    def __init__(self, shop, *, chat_ctx=None):
        self.shop = shop

    def __await__(self):
        async def run():
            return _StubTask.result

        return run().__await__()


async def test_checkout_with_an_empty_cart_is_refused():
    a = Assistant(instructions="x", shop=make_shop())
    result = await a.checkout(FakeContext())
    assert result["ok"] is False and result["error"] == "cart_empty"


async def test_checkout_opens_the_page_and_hands_over(monkeypatch):
    monkeypatch.setattr(checkout_module, "CheckoutTask", _StubTask)
    shop = make_shop()
    shop.ui.cart.append({"mealId": SALAD, "quantity": 1, "addonIds": [], "spicy": False})
    a = Assistant(instructions="x", shop=shop)
    result = await a.checkout(FakeContext())
    assert shop.ui.path == "/checkout"
    assert isinstance(result, ToolResult) and result.reply_required is False
    assert result.output["order_id"] == "order-9"


async def test_leaving_checkout_hands_the_request_back(monkeypatch):
    monkeypatch.setattr(checkout_module, "CheckoutTask", _StubTask)
    monkeypatch.setattr(_StubTask, "result", CheckoutResult(placed=False, request="ضيف بيبسي"))
    shop = make_shop()
    shop.ui.cart.append({"mealId": SALAD, "quantity": 1, "addonIds": [], "spicy": False})
    result = await Assistant(instructions="x", shop=shop).checkout(FakeContext())
    assert result["checkout_paused"] is True and result["caller_wants"] == "ضيف بيبسي"


async def test_rejected_phone_says_what_was_heard_and_why():
    # Live 2026-09-24: an 11-digit number was rejected six times with no reason given.
    task, shop = task_with_cart()
    result = await task.set_customer_details(FakeContext(), phone="05123456789")
    problem = result["problems"]["phone"]
    assert problem["heard_digits"] == "05123456789"
    assert problem["digit_count"] == 11
    assert problem["heard_say_ar"].startswith("صفر خمسة واحد اثنين")
    assert "can_continue_without_phone" not in problem


async def test_after_two_rejected_numbers_she_may_go_on_without_one():
    task, shop = task_with_cart()
    await task.set_customer_details(FakeContext(), phone="05123456789")
    result = await task.set_customer_details(FakeContext(), phone="051234567899")
    assert result["problems"]["phone"]["can_continue_without_phone"] is True
    await task.set_customer_details(FakeContext(), phone="0551234567")
    assert shop.state.checkout.phone == "0551234567"
    assert shop.state.checkout.phone_attempts_rejected == 0
