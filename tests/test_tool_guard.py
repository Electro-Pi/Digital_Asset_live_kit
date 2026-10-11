"""The tool guard: schema validation, loops and contradictions.

Order-state checks live in the tools (tests/test_voice_tools.py).
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402
from src.tool_guard import ToolSpec, build_tool_specs, plan_turn, validate_call  # noqa: E402


SPECS = {
    "search_meals": ToolSpec(
        name="search_meals",
        properties={
            "query": {"type": "string"},
            "max_price": {"type": "integer"},
            "spicy": {"type": "boolean"},
            "tags": {"type": "array"},
        },
        required=frozenset({"query"}),
    ),
    "show_meal": ToolSpec(
        name="show_meal",
        properties={"meal_id": {"type": "string"}, "reason": {"type": "string"}},
        required=frozenset({"meal_id"}),
    ),
    "respond_to_offer": ToolSpec(
        name="respond_to_offer",
        properties={"offer_id": {"type": "string"}, "accepted": {"type": "boolean"}, "quantity": {"type": "integer"}},
        required=frozenset({"offer_id", "accepted"}),
    ),
    "checkout": ToolSpec(name="checkout"),
    # Optional[int] renders as anyOf, the shape pydantic actually emits.
    "lookup_weather": ToolSpec(
        name="lookup_weather",
        properties={"days": {"anyOf": [{"type": "integer"}, {"type": "null"}]}},
        required=frozenset(),
    ),
}


class ValidateCallTests(unittest.TestCase):
    def test_accepts_a_well_formed_call(self):
        call, reason = validate_call("search_meals", {"query": "برجر"}, SPECS)
        self.assertEqual(reason, "")
        self.assertEqual(call.arguments, {"query": "برجر"})

    def test_rejects_unknown_tool(self):
        call, reason = validate_call("order_pizza", {}, SPECS)
        self.assertIsNone(call)
        self.assertIn("unknown tool", reason)

    def test_rejects_malformed_json_string(self):
        call, reason = validate_call("search_meals", "{not json", SPECS)
        self.assertIsNone(call)
        self.assertIn("not valid JSON", reason)

    def test_parses_arguments_given_as_a_json_string(self):
        call, _ = validate_call("search_meals", '{"query": "دجاج"}', SPECS)
        self.assertEqual(call.arguments["query"], "دجاج")

    def test_rejects_non_object_arguments(self):
        call, reason = validate_call("search_meals", ["query"], SPECS)
        self.assertIsNone(call)
        self.assertIn("not an object", reason)

    def test_drops_invented_argument_but_keeps_the_call(self):
        call, reason = validate_call(
            "search_meals", {"query": "برجر", "vibe": "cozy"}, SPECS
        )
        self.assertEqual(reason, "")
        self.assertEqual(call.arguments, {"query": "برجر"})

    def test_missing_required_argument_fails(self):
        call, reason = validate_call("show_meal", {"reason": "لذيذ"}, SPECS)
        self.assertIsNone(call)
        self.assertIn("missing required meal_id", reason)

    def test_coerces_numeric_string_to_integer(self):
        call, _ = validate_call(
            "search_meals", {"query": "برجر", "max_price": "50"}, SPECS
        )
        self.assertEqual(call.arguments["max_price"], 50)

    def test_coerces_string_boolean(self):
        call, _ = validate_call(
            "search_meals", {"query": "برجر", "spicy": "true"}, SPECS
        )
        self.assertIs(call.arguments["spicy"], True)

    def test_wraps_bare_string_into_array(self):
        call, _ = validate_call("search_meals", {"query": "برجر", "tags": "حار"}, SPECS)
        self.assertEqual(call.arguments["tags"], ["حار"])

    def test_drops_uncoercible_optional_argument(self):
        call, reason = validate_call(
            "search_meals", {"query": "برجر", "max_price": "رخيص"}, SPECS
        )
        self.assertEqual(reason, "")
        self.assertNotIn("max_price", call.arguments)

    def test_uncoercible_required_argument_fails_the_call(self):
        call, reason = validate_call("show_meal", {"meal_id": {"id": 1}}, SPECS)
        self.assertIsNone(call)
        self.assertIn("wrong type", reason)

    def test_optional_anyof_type_is_honoured(self):
        call, _ = validate_call("lookup_weather", {"days": "3"}, SPECS)
        self.assertEqual(call.arguments["days"], 3)

    def test_missing_arguments_default_to_empty(self):
        call, reason = validate_call("checkout", None, SPECS)
        self.assertEqual(reason, "")
        self.assertEqual(call.arguments, {})


class PlanTurnTests(unittest.TestCase):
    def test_keeps_good_calls_and_reports_refusals(self):
        calls = [
            ("search_meals", {"query": "برجر"}, "a"),
            ("order_pizza", {}, "b"),
            ("checkout", {"cart": "x"}, "c"),
        ]
        approved, refused = plan_turn(calls, SPECS)
        self.assertEqual([c.name for c in approved], ["search_meals", "checkout"])
        self.assertEqual(len(refused), 1)

    def test_collapses_exact_duplicates(self):
        calls = [
            ("show_meal", {"meal_id": "x"}, "a"),
            ("show_meal", {"meal_id": "x"}, "b"),
        ]
        approved, refused = plan_turn(calls, SPECS)
        self.assertEqual(len(approved), 1)
        self.assertIn("duplicate", refused[0])

    def test_keeps_two_different_calls_to_the_same_tool(self):
        calls = [
            ("show_meal", {"meal_id": "x"}, "a"),
            ("show_meal", {"meal_id": "y"}, "b"),
        ]
        approved, _ = plan_turn(calls, SPECS)
        self.assertEqual(len(approved), 2)

    def test_preserves_order(self):
        calls = [
            ("search_meals", {"query": "a"}, "1"),
            ("show_meal", {"meal_id": "m"}, "2"),
        ]
        approved, _ = plan_turn(calls, SPECS)
        self.assertEqual([c.name for c in approved], ["search_meals", "show_meal"])

    def test_no_arabic_phrase_decides_anything(self):
        """The same call survives regardless of how the turn was worded."""
        for query in ("ابغى برجر", "show me a burger", "ماشي شوفيلي خيارات تانية"):
            approved, _ = plan_turn(
                [("search_meals", {"query": query}, "x")], SPECS
            )
            self.assertEqual(len(approved), 1, query)


class ContradictionTests(unittest.TestCase):
    YES = ("respond_to_offer", {"offer_id": "pitch:x", "accepted": True}, "a")
    NO = ("respond_to_offer", {"offer_id": "pitch:x", "accepted": False}, "a")

    def test_search_supersedes_accepting(self):
        approved, refused = plan_turn([self.YES, ("search_meals", {"query": "دجاج"}, "b")], SPECS)
        self.assertEqual([c.name for c in approved], ["search_meals"])
        self.assertIn("contradicted by", refused[0])

    def test_show_meal_supersedes_accepting(self):
        approved, _ = plan_turn([self.YES, ("show_meal", {"meal_id": "y"}, "b")], SPECS)
        self.assertEqual([c.name for c in approved], ["show_meal"])

    def test_declining_and_searching_is_not_a_contradiction(self):
        approved, refused = plan_turn([self.NO, ("search_meals", {"query": "برجر"}, "b")], SPECS)
        self.assertEqual([c.name for c in approved], ["respond_to_offer", "search_meals"])
        self.assertEqual(refused, [])

    def test_a_lone_answer_survives(self):
        approved, refused = plan_turn([self.YES], SPECS)
        self.assertEqual([c.name for c in approved], ["respond_to_offer"])
        self.assertEqual(refused, [])

    def test_order_of_the_two_calls_does_not_matter(self):
        search = ("search_meals", {"query": "a"}, "1")
        for calls in ([search, self.YES], [self.YES, search]):
            approved, _ = plan_turn(calls, SPECS)
            self.assertEqual([c.name for c in approved], ["search_meals"])


class UnknownSchemaTests(unittest.TestCase):
    """A tool we could not introspect must still be callable."""

    def test_offered_tool_without_a_spec_passes_through(self):
        call, reason = validate_call(
            "mystery_tool", {"anything": 1}, SPECS, allowed={"mystery_tool"}
        )
        self.assertIsNotNone(call, reason)
        self.assertEqual(call.arguments, {"anything": 1})

    def test_tool_that_is_not_offered_is_still_refused(self):
        call, reason = validate_call("mystery_tool", {}, SPECS, allowed={"other"})
        self.assertIsNone(call)
        self.assertIn("unknown tool", reason)

    def test_passthrough_still_requires_valid_json(self):
        call, reason = validate_call(
            "mystery_tool", "{broken", SPECS, allowed={"mystery_tool"}
        )
        self.assertIsNone(call)
        self.assertIn("not valid JSON", reason)


class RepeatCallTests(unittest.TestCase):
    """The model re-issuing a call it already ran is a loop, not a request."""

    def test_identical_repeat_is_refused(self):
        approved, refused = plan_turn(
            [("search_meals", {"query": "برجر دجاج"}, "a")],
            SPECS,
            already_ran=[("search_meals", {"query": "برجر دجاج"})],
        )
        self.assertEqual(approved, [])
        self.assertIn("identical call already ran", refused[0])

    def test_same_tool_with_new_arguments_is_allowed(self):
        approved, _ = plan_turn(
            [("search_meals", {"query": "برجر دجاج", "max_price": 37}, "a")],
            SPECS,
            already_ran=[("search_meals", {"query": "برجر دجاج"})],
        )
        self.assertEqual([c.name for c in approved], ["search_meals"])

    def test_argument_order_does_not_defeat_the_check(self):
        approved, _ = plan_turn(
            [("search_meals", {"max_price": 50, "query": "برجر"}, "a")],
            SPECS,
            already_ran=[("search_meals", {"query": "برجر", "max_price": 50})],
        )
        self.assertEqual(approved, [])

    def test_a_different_tool_is_unaffected(self):
        approved, _ = plan_turn(
            [("show_meal", {"meal_id": "x"}, "a")],
            SPECS,
            already_ran=[("search_meals", {"query": "برجر"})],
        )
        self.assertEqual([c.name for c in approved], ["show_meal"])


class RealSchemaTests(unittest.TestCase):
    """The specs must come from the live tools, not a hand-written copy."""

    def test_builds_specs_from_the_real_assistant(self):
        from src.ui_bridge import UIBridge
        from src.voice_agent.assistant import Assistant
        from src.voice_agent.base import Shop

        assistant = Assistant(instructions="test", shop=Shop(ui=UIBridge(room=None, identity=None)))
        specs = build_tool_specs(assistant.tools)

        self.assertIn("search_meals", specs)
        self.assertIn("respond_to_offer", specs)
        self.assertEqual(specs["respond_to_offer"].required, frozenset({"offer_id", "accepted"}))
        self.assertTrue(specs["search_meals"].accepts("query"))
        self.assertFalse(specs["search_meals"].accepts("not_a_real_argument"))

        # A call the real schema rejects must be refused.
        call, reason = validate_call("show_meal", {}, specs)
        self.assertIsNone(call, reason)


if __name__ == "__main__":
    unittest.main(verbosity=2)


@pytest.mark.parametrize("junk", ["', 'NULL'", "', NULL, 'NULL", "null", "'', ''", "None"])
def test_serialisation_debris_in_a_string_argument_is_dropped(junk):
    spec = {"search_meals": ToolSpec("search_meals", {"query": {"type": "string"}, "offer_kind": {"type": "string"}})}
    call, reason = validate_call("search_meals", {"query": "أي شي", "offer_kind": junk}, spec)
    assert reason == "" and call.arguments == {"query": "أي شي"}


def test_other_arguments_spilled_into_one_string_are_dropped():
    spec = {"search_meals": ToolSpec("search_meals", {
        "query": {"type": "string"}, "cuisine": {"type": "string"}, "max_price": {"type": "number"},
    })}
    spilled = "', NULL, 'NULL, dish_type: 'main', max_price: 30, query: 'غدا'"
    call, reason = validate_call("search_meals", {"query": "غدا", "cuisine": spilled}, spec)
    assert reason == "" and call.arguments == {"query": "غدا"}


def test_real_values_with_quotes_or_colons_survive():
    spec = {"search_meals": ToolSpec("search_meals", {"query": {"type": "string"}, "cuisine": {"type": "string"}})}
    call, _ = validate_call("search_meals", {"query": "برجر 'دبل'", "cuisine": "مشاوي: لحم"}, spec)
    assert call.arguments == {"query": "برجر 'دبل'", "cuisine": "مشاوي: لحم"}
