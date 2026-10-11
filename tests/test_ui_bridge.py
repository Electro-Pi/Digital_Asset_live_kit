"""The browser bridge and addon matching: live failures of 2026-08-12.

1. The ASR delivered «صوص ثوم» as "Sauce Tomb" and the model declared the
   addon unavailable. Addon references resolve loosely against the meal's
   real addons.
2. UI RPCs died with CONNECTION_TIMEOUT mid-session. The bridge re-resolves the
   browser participant instead of latching a stale identity, and retries once
   on a transient transport error.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from livekit.rtc import RpcError

from src.catalog import Meal
from src.ui_bridge import UIBridge
from src.voice_agent.cart_tools import match_addon

ADDONS = (
    {"id": "garlic-sauce", "name": "صوص ثوم", "price": 3},
    {"id": "pickles", "name": "مخلل", "price": 3},
    {"id": "extra-rice", "name": "أرز إضافي", "price": 6},
    {"id": "cola", "name": "كولا", "price": 6},
    {"id": "side-salad", "name": "سلطة صغيرة", "price": 8},
)
MEAL = SimpleNamespace(addons=ADDONS)


@pytest.mark.parametrize("requested,expected", [
    ("garlic-sauce", "garlic-sauce"), ("صوص ثوم", "garlic-sauce"), ("كولا", "cola"),
    ("Sauce Tomb", "garlic-sauce"), ("sauce", "garlic-sauce"),
    ("صوص الثوم", "garlic-sauce"), ("الكولا", "cola"), ("سلطه", "side-salad"), ("ارز اضافي", "extra-rice"),
])
def test_addon_references_resolve(requested, expected):
    assert match_addon(requested, MEAL) == expected


@pytest.mark.parametrize("requested", ["بيبسي دايت", "شاورما", ""])
def test_unrelated_words_are_rejected(requested):
    assert match_addon(requested, MEAL) is None


class _Participant:
    def __init__(self, behaviour):
        self.behaviour = behaviour
        self.calls: list[str] = []

    async def perform_rpc(self, *, destination_identity, method, payload, response_timeout):
        self.calls.append(destination_identity)
        action = self.behaviour.get(destination_identity)
        if isinstance(action, Exception):
            raise action
        return action if isinstance(action, str) else "{}"


class _Room:
    def __init__(self, identities, behaviour):
        self.remote_participants = {i: SimpleNamespace(identity=i, attributes={"luqma_path": "/"}) for i in identities}
        self.local_participant = _Participant(behaviour)


def _timeout():
    return RpcError(RpcError.ErrorCode.CONNECTION_TIMEOUT, "Connection timeout")


async def test_bridge_retargets_after_page_reload():
    room = _Room(["user-new"], {"user-new": '{"ok": true}'})
    result = await UIBridge(room=room, identity="user-old").call("luqma.getCart", {})
    assert result.ok and room.local_participant.calls == ["user-new"]


async def test_bridge_retries_once_on_timeout():
    room = _Room(["user-1"], {"user-1": _timeout()})
    result = await UIBridge(room=room, identity="user-1").call("luqma.navigate", {"path": "/offers"})
    assert not result.ok
    assert result.reason == f"rpc_error_{RpcError.ErrorCode.CONNECTION_TIMEOUT}"
    assert room.local_participant.calls == ["user-1", "user-1"]


async def test_bridge_retry_succeeds_after_transient_timeout():
    class Flaky(_Participant):
        async def perform_rpc(self, *, destination_identity, method, payload, response_timeout):
            self.calls.append(destination_identity)
            if len(self.calls) == 1:
                raise _timeout()
            return '{"ok": true, "cartCount": 1}'

    room = _Room(["user-1"], {})
    room.local_participant = Flaky({})
    result = await UIBridge(room=room, identity="user-1").call("luqma.addToCart", {"mealId": "x"})
    assert result.ok and (result.data or {}).get("cartCount") == 1


async def test_bridge_reports_missing_participant():
    result = await UIBridge(room=_Room([], {}), identity="user-gone").call("luqma.getCart", {})
    assert not result.ok and result.reason == "no_ui_participant"


async def test_every_mutation_reaches_the_ledger():
    from fakes import FakeUI
    from src.turn_ledger import CART_ADD, TurnLedger

    ledger = TurnLedger()
    ledger.begin("turn-1")
    ui = FakeUI()
    ui.on_effect = ledger.record
    await ui.add_to_cart(meal_id="protein-box-2", quantity=1)
    await ui.get_cart()  # a read records nothing
    assert ledger.did(CART_ADD, target="protein-box-2")
    assert len(ledger.snapshot()["effects"]) == 1


def test_meal_type_is_annotated():
    assert "type" in Meal.__dataclass_fields__
