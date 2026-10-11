"""The turn ledger: what this turn did, recorded where it happened.

Guards ask the ledger whether an action happened instead of reading the
transcript (see src/turn_ledger.py). These tests pin its mechanics; the
effects themselves are recorded by UIBridge.call, which tests/test_voice_tools
exercises through the real tools.
"""

from __future__ import annotations

import unittest

from types import SimpleNamespace

from livekit.agents.llm import ChatContext

from src.turn_ledger import CART_ADD, MEAL_PAGE, TurnLedger, user_turn_key


class _Chat:
    """A chat context stating only what the customer said."""

    def __init__(self, text: str) -> None:
        self.items = [{"role": "user", "content": text}]


class _Ctx:
    def __init__(self, text: str = "") -> None:
        self.chat_ctx = _Chat(text) if text else SimpleNamespace(items=[])
        self.session = None


def _activity(stream):
    return SimpleNamespace(
        llm=SimpleNamespace(chat=lambda **_: stream),
        session=SimpleNamespace(conn_options=SimpleNamespace(llm_conn_options=None)),
    )


class LedgerMechanicsTests(unittest.TestCase):
    def test_turn_rolls_over_on_a_new_user_message(self):
        ledger = TurnLedger()
        ledger.begin("msg-1")
        ledger.record(CART_ADD, target="leaf-bowl-7")
        self.assertTrue(ledger.did(CART_ADD))
        self.assertTrue(ledger.begin("msg-2"))
        self.assertFalse(ledger.did(CART_ADD), "a new turn starts with nothing done")

    def test_repeating_the_same_key_keeps_the_record(self):
        """Every generation step of one turn calls begin with the same key."""
        ledger = TurnLedger()
        ledger.begin("msg-1")
        ledger.record(CART_ADD, target="x")
        self.assertFalse(ledger.begin("msg-1"))
        self.assertTrue(ledger.did(CART_ADD))

    def test_an_unidentifiable_turn_never_wipes_the_record(self):
        ledger = TurnLedger()
        ledger.begin("msg-1")
        ledger.record(CART_ADD, target="x")
        self.assertFalse(ledger.begin(""))
        self.assertTrue(ledger.did(CART_ADD))

    def test_a_failed_effect_does_not_license_the_claim(self):
        ledger = TurnLedger()
        ledger.begin("t")
        ledger.record(CART_ADD, target="x", ok=False)
        self.assertFalse(ledger.did(CART_ADD), "a failed RPC added nothing")
        self.assertTrue(ledger.attempted(CART_ADD))

    def test_mock_promotion_makes_the_demo_speakable(self):
        """_continue_as_mock decides to carry on; the record must agree."""
        ledger = TurnLedger()
        ledger.begin("t")
        ledger.record(CART_ADD, target="x", ok=False)
        promoted = ledger.promote_last_failure()
        self.assertIsNotNone(promoted)
        self.assertTrue(ledger.did(CART_ADD))

    def test_promotion_leaves_a_successful_effect_alone(self):
        ledger = TurnLedger()
        ledger.begin("t")
        ledger.record(CART_ADD, target="x", ok=True)
        ledger.promote_last_failure()
        self.assertEqual(ledger.snapshot()["effects"][0]["mocked"], False)

    def test_targets_distinguish_which_meal(self):
        ledger = TurnLedger()
        ledger.begin("t")
        ledger.record(MEAL_PAGE, target="olive-crust-7")
        self.assertTrue(ledger.did(MEAL_PAGE, target="olive-crust-7"))
        self.assertFalse(ledger.did(MEAL_PAGE, target="leaf-bowl-7"))

    def test_no_search_and_an_empty_search_are_different_answers(self):
        """None lets show_meal keep an older id; [] is what makes it refuse."""
        ledger = TurnLedger()
        ledger.begin("t")
        self.assertIsNone(ledger.search_rows())
        ledger.record_search([])
        self.assertEqual(ledger.search_rows(), [])

    def test_a_narrowing_search_that_finds_nothing_does_not_retract_the_hits(self):
        ledger = TurnLedger()
        ledger.begin("t")
        ledger.record_search([{"meal_id": "a"}])
        ledger.record_search([])
        self.assertEqual(ledger.search_rows(), [{"meal_id": "a"}])

    def test_the_newest_non_empty_search_wins(self):
        """The live bug: show_meal was judged against an earlier search."""
        ledger = TurnLedger()
        ledger.begin("t")
        ledger.record_search([{"meal_id": "leaf-bowl-7"}])
        ledger.record_search([{"meal_id": "formaggio-7"}])
        self.assertEqual(ledger.search_rows(), [{"meal_id": "formaggio-7"}])

    def test_user_turn_key_tracks_the_newest_user_message(self):
        ctx = ChatContext()
        ctx.add_message(role="user", content="وريني سلطة")
        first = user_turn_key(ctx)
        self.assertTrue(first)
        ctx.add_message(role="assistant", content="أبشر")
        self.assertEqual(user_turn_key(ctx), first, "an agent reply is not a new turn")
        ctx.add_message(role="user", content="ضفها للسلة")
        self.assertNotEqual(user_turn_key(ctx), first)

    def test_no_user_message_yet_is_not_a_turn(self):
        self.assertEqual(user_turn_key(ChatContext()), "")
