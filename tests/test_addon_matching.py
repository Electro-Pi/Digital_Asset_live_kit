"""Regression tests for the live failures of 2026-08-12.

1. The user asked for صوص ثوم on بيت الشواية's kebab offer; the ASR delivered
   "Sauce Tomb", and the model declared the addon unavailable although the
   catalog lists it. Addon references must now resolve loosely against the
   meal's real addons.
2. UI RPCs died with CONNECTION_TIMEOUT (1501) mid-session. The bridge must
   re-resolve the browser participant instead of latching a stale identity,
   and retry once on a transient transport error.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent import _match_addon_loose  # noqa: E402
from livekit.rtc import RpcError  # noqa: E402
from src.ui_bridge import UIBridge  # noqa: E402


ADDONS = [
    {"id": "garlic-sauce", "name": "صوص ثوم", "price": 3},
    {"id": "pickles", "name": "مخلل", "price": 3},
    {"id": "extra-rice", "name": "أرز إضافي", "price": 6},
    {"id": "cola", "name": "كولا", "price": 6},
    {"id": "side-salad", "name": "سلطة صغيرة", "price": 8},
]


def test_exact_id_and_name_pass_through() -> None:
    assert _match_addon_loose("garlic-sauce", ADDONS) == "garlic-sauce"
    assert _match_addon_loose("صوص ثوم", ADDONS) == "garlic-sauce"
    assert _match_addon_loose("كولا", ADDONS) == "cola"


def test_asr_garbled_english_resolves() -> None:
    # The live failure verbatim: صوص ثوم transcribed as "Sauce Tomb".
    assert _match_addon_loose("Sauce Tomb", ADDONS) == "garlic-sauce"
    assert _match_addon_loose("sauce", ADDONS) == "garlic-sauce"


def test_loose_arabic_variants_resolve() -> None:
    assert _match_addon_loose("صوص الثوم", ADDONS) == "garlic-sauce"
    assert _match_addon_loose("الكولا", ADDONS) == "cola"
    assert _match_addon_loose("سلطه", ADDONS) == "side-salad"
    assert _match_addon_loose("ارز اضافي", ADDONS) == "extra-rice"


def test_unrelated_words_are_rejected() -> None:
    assert _match_addon_loose("بيبسي دايت", ADDONS) is None
    assert _match_addon_loose("شاورما", ADDONS) is None
    assert _match_addon_loose("", ADDONS) is None


class _FakeLocalParticipant:
    def __init__(self, behaviour: dict[str, object]) -> None:
        # behaviour: identity -> Exception to raise or str payload to return.
        self.behaviour = behaviour
        self.calls: list[str] = []

    async def perform_rpc(self, *, destination_identity, method, payload, response_timeout):
        self.calls.append(destination_identity)
        action = self.behaviour.get(destination_identity)
        if isinstance(action, Exception):
            raise action
        return action if isinstance(action, str) else "{}"


class _FakeRoom:
    def __init__(self, remote_identities: list[str], behaviour: dict[str, object]) -> None:
        self.remote_participants = {
            i: SimpleNamespace(identity=i, attributes={"luqma_path": "/"})
            for i in remote_identities
        }
        self.local_participant = _FakeLocalParticipant(behaviour)


def _timeout_error() -> RpcError:
    return RpcError(RpcError.ErrorCode.CONNECTION_TIMEOUT, "Connection timeout")


def test_bridge_retargets_after_page_reload() -> None:
    # Bridge was built for "user-old", but the tab reloaded and rejoined as
    # "user-new". The call must go to the participant actually in the room.
    room = _FakeRoom(["user-new"], {"user-new": '{"ok": true}'})
    bridge = UIBridge(room=room, identity="user-old")

    result = asyncio.run(bridge.call("luqma.getCart", {}))

    assert result.ok
    assert room.local_participant.calls == ["user-new"]


def test_bridge_retries_once_on_timeout() -> None:
    room = _FakeRoom(["user-1"], {"user-1": _timeout_error()})
    bridge = UIBridge(room=room, identity="user-1")

    result = asyncio.run(bridge.call("luqma.navigate", {"path": "/offers"}))

    assert not result.ok
    assert result.reason == f"rpc_error_{RpcError.ErrorCode.CONNECTION_TIMEOUT}"
    assert room.local_participant.calls == ["user-1", "user-1"]


def test_bridge_retry_succeeds_after_transient_timeout() -> None:
    class _FlakyParticipant(_FakeLocalParticipant):
        async def perform_rpc(self, *, destination_identity, method, payload, response_timeout):
            self.calls.append(destination_identity)
            if len(self.calls) == 1:
                raise _timeout_error()
            return '{"ok": true, "cartCount": 1}'

    room = _FakeRoom(["user-1"], {})
    room.local_participant = _FlakyParticipant({})
    bridge = UIBridge(room=room, identity="user-1")

    result = asyncio.run(bridge.call("luqma.addToCart", {"mealId": "x"}))

    assert result.ok
    assert (result.data or {}).get("cartCount") == 1
    assert len(room.local_participant.calls) == 2


def test_bridge_reports_missing_participant() -> None:
    room = _FakeRoom([], {})
    bridge = UIBridge(room=room, identity="user-gone")

    result = asyncio.run(bridge.call("luqma.getCart", {}))

    assert not result.ok
    assert result.reason == "no_ui_participant"


if __name__ == "__main__":
    import asyncio
    from test_luqma_flow import FakeUI
    from agent import Assistant

    async def _cart_readd_after_addons() -> None:
        ui = FakeUI()
        assistant = Assistant(instructions="test", ui=ui)  # type: ignore[arg-type]
        meal_id = "formaggio-offer-2"
        await assistant.add_to_cart(None, meal_id=meal_id, quantity=1)
        result = await assistant.set_meal_options(
            None, meal_id=meal_id, addon_ids=["extra-sauce", "parmesan"]
        )
        cart_calls = [p for m, p in ui.calls if m == "luqma.addToCart"]
        assert len(cart_calls) == 1, cart_calls
        assert result.get("added_to_cart") is False
        assert "cart_readded_with_addons" not in result
        assert len(ui.cart) == 1, ui.cart

    async def _open_page_arabic_cart() -> None:
        ui = FakeUI()
        assistant = Assistant(instructions="test", ui=ui)  # type: ignore[arg-type]
        result = await assistant.open_page(None, page="السلة")
        assert result.get("ok") is True, result
        assert ui.current_path == "/cart"
        nav = [p for m, p in ui.calls if m == "luqma.navigate"]
        assert nav and nav[-1].get("path") == "/cart"

    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn) and not asyncio.iscoroutinefunction(fn):
            fn()
            print(f"PASS {name}")
    asyncio.run(_cart_readd_after_addons())
    print("PASS test_cart_readd_after_addons")
    asyncio.run(_open_page_arabic_cart())
    print("PASS test_open_page_arabic_cart")
    print("all addon/bridge tests passed")
