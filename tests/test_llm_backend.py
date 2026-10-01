"""The LLM_PROVIDER toggle: Coda by default, the instance or Gemini when asked.

Run: .venv/bin/python tests/test_llm_backend.py
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.llm_backend import (  # noqa: E402
    CODA,
    CODA_MODEL,
    CODA_PLACEHOLDER_KEY,
    GEMINI,
    GEMINI_BASE_URL,
    INSTANCE,
    INSTANCE_BASE_URL,
    INSTANCE_MODEL,
    GEMINI_MODEL,
    ensure_non_system_turn,
    resolve_backend,
    resolve_provider,
)


class FakeMessage:
    """Stands in for livekit's ChatMessage, down to the pydantic copy."""

    type = "message"

    def __init__(self, role: str, text: str) -> None:
        self.role = role
        self.text = text

    def model_copy(self, update: dict):
        return FakeMessage(update.get("role", self.role), self.text)

    def __repr__(self) -> str:
        return f"{self.role}:{self.text}"


class FakeCall:
    type = "function_call"
    role = None


class FakeHandoff:
    """session.start() leaves one of these in the context. It carries no
    content but is not a system message either."""

    type = "agent_handoff"


class FakeCtx:
    def __init__(self, items):
        self.items = items


class TestProviderChoice(unittest.TestCase):
    def test_unset_stays_on_coda(self):
        # The deployments already running must not move because this landed.
        backend = resolve_backend({})
        self.assertEqual(backend.provider, CODA)
        self.assertEqual(backend.model, CODA_MODEL)
        self.assertEqual(backend.api_key, CODA_PLACEHOLDER_KEY)
        self.assertFalse(backend.api_key_set)
        self.assertIsNone(backend.reasoning_effort)

    def test_coda_reads_the_existing_vars(self):
        backend = resolve_backend(
            {
                "LLM_BASE_URL": "http://host:8001/v1",
                "LLM_MODEL": "gemma4",
                "LLM_API_KEY": "secret",
                # Gemini's settings are inert while Coda is selected.
                "GEMINI_MODEL": "gemini-3.8-flash",
            }
        )
        self.assertEqual(backend.base_url, "http://host:8001/v1")
        self.assertEqual(backend.api_key, "secret")
        self.assertTrue(backend.api_key_set)
        self.assertEqual(backend.openai_kwargs()["model"], "gemma4")

    def test_aliases(self):
        self.assertEqual(resolve_provider({"LLM_PROVIDER": "Google"}), GEMINI)
        self.assertEqual(resolve_provider({"LLM_PROVIDER": " GEMINI "}), GEMINI)
        self.assertEqual(resolve_provider({"LLM_PROVIDER": "vllm"}), CODA)
        self.assertEqual(resolve_provider({"LLM_PROVIDER": "gemma"}), CODA)
        self.assertEqual(resolve_provider({"LLM_PROVIDER": "Instance"}), INSTANCE)
        self.assertEqual(resolve_provider({"LLM_PROVIDER": "self-hosted"}), INSTANCE)

    def test_a_typo_fails_at_boot(self):
        # Silently falling back to Coda would route a whole deployment at the
        # wrong model with nothing in the log to say so.
        with self.assertRaises(ValueError):
            resolve_provider({"LLM_PROVIDER": "gemeni"})


class TestInstance(unittest.TestCase):
    def test_defaults_to_the_instance_without_thinking(self):
        backend = resolve_backend(
            {
                "LLM_PROVIDER": "instance",
                "INSTANCE_API_KEY": "k",
                # Coda's settings are inert while the instance is selected.
                "LLM_MODEL": "Coda-V2",
                "LLM_API_KEY": "coda-secret",
            }
        )
        self.assertEqual(backend.provider, INSTANCE)
        self.assertEqual(backend.base_url, INSTANCE_BASE_URL)
        self.assertEqual(backend.model, INSTANCE_MODEL)
        self.assertEqual(backend.api_key, "k")
        self.assertTrue(backend.api_key_set)
        kwargs = backend.openai_kwargs()
        # Every model served there has thought by default, and the thinking lands
        # before the reply.
        self.assertEqual(kwargs["reasoning_effort"], "none")
        self.assertTrue(kwargs["_strict_tool_schema"])
        self.assertFalse(backend.needs_non_system_turn)

    def test_a_missing_key_fails_at_boot(self):
        with self.assertRaises(ValueError):
            resolve_backend({"LLM_PROVIDER": "instance"})

    def test_overrides(self):
        backend = resolve_backend(
            {
                "LLM_PROVIDER": "instance",
                "INSTANCE_API_KEY": "k",
                "INSTANCE_BASE_URL": "http://host:1/v1",
                "INSTANCE_MODEL": "some-other-model",
                "INSTANCE_REASONING_EFFORT": "off",
                "INSTANCE_TEMPERATURE": "0.4",
            }
        )
        self.assertEqual(backend.base_url, "http://host:1/v1")
        self.assertEqual(backend.model, "some-other-model")
        self.assertNotIn("reasoning_effort", backend.openai_kwargs())
        self.assertEqual(backend.openai_kwargs()["temperature"], 0.4)


class TestGemini(unittest.TestCase):
    def test_defaults_to_flash_without_thinking(self):
        backend = resolve_backend({"LLM_PROVIDER": "gemini", "GEMINI_API_KEY": "k"})
        self.assertEqual(backend.provider, GEMINI)
        self.assertEqual(backend.base_url, GEMINI_BASE_URL)
        self.assertEqual(backend.model, GEMINI_MODEL)
        self.assertEqual(backend.reasoning_effort, "none")
        kwargs = backend.openai_kwargs()
        self.assertEqual(kwargs["reasoning_effort"], "none")
        # Strict schemas make Gemini pad every optional argument with a null.
        self.assertFalse(kwargs["_strict_tool_schema"])
        self.assertNotIn("temperature", kwargs)

    def test_google_api_key_is_accepted(self):
        backend = resolve_backend({"LLM_PROVIDER": "gemini", "GOOGLE_API_KEY": "k"})
        self.assertEqual(backend.api_key, "k")

    def test_a_missing_key_fails_at_boot(self):
        with self.assertRaises(ValueError):
            resolve_backend({"LLM_PROVIDER": "gemini"})

    def test_overrides(self):
        backend = resolve_backend(
            {
                "LLM_PROVIDER": "gemini",
                "GEMINI_API_KEY": "k",
                "GEMINI_MODEL": "gemini-3.5-flash",
                "GEMINI_REASONING_EFFORT": "low",
                "GEMINI_TEMPERATURE": "0.6",
            }
        )
        self.assertEqual(backend.model, "gemini-3.5-flash")
        self.assertEqual(backend.openai_kwargs()["reasoning_effort"], "low")
        self.assertEqual(backend.openai_kwargs()["temperature"], 0.6)

    def test_thinking_can_be_left_to_the_model(self):
        backend = resolve_backend(
            {
                "LLM_PROVIDER": "gemini",
                "GEMINI_API_KEY": "k",
                "GEMINI_REASONING_EFFORT": "off",
            }
        )
        self.assertIsNone(backend.reasoning_effort)
        self.assertNotIn("reasoning_effort", backend.openai_kwargs())

    def test_a_level_gemini_rejects_fails_at_boot(self):
        # "minimal" is an OpenAI level; Gemini 3.x answers it with a 400, which
        # would otherwise surface as سلمى going silent on the first turn.
        with self.assertRaises(ValueError):
            resolve_backend(
                {
                    "LLM_PROVIDER": "gemini",
                    "GEMINI_API_KEY": "k",
                    "GEMINI_REASONING_EFFORT": "minimal",
                }
            )

    def test_a_bad_temperature_fails_at_boot(self):
        with self.assertRaises(ValueError):
            resolve_backend(
                {
                    "LLM_PROVIDER": "gemini",
                    "GEMINI_API_KEY": "k",
                    "GEMINI_TEMPERATURE": "warm",
                }
            )


class TestOpeningTurn(unittest.TestCase):
    """The opening turn Gemini answers with a 400 because nothing serialises."""

    def test_gemini_needs_it_and_coda_does_not(self):
        gemini = resolve_backend({"LLM_PROVIDER": "gemini", "GEMINI_API_KEY": "k"})
        self.assertTrue(gemini.needs_non_system_turn)
        self.assertFalse(resolve_backend({}).needs_non_system_turn)

    def test_the_live_opening_context(self):
        # Exactly what a call starts with: persona, the handoff session.start
        # adds, then the intro generate_reply(instructions=...) appends.
        ctx = FakeCtx(
            [
                FakeMessage("system", "persona"),
                FakeHandoff(),
                FakeMessage("system", "intro"),
            ]
        )
        ctx, relabelled = ensure_non_system_turn(ctx)
        self.assertTrue(relabelled)
        self.assertEqual(
            [getattr(i, "role", i.type) for i in ctx.items],
            ["system", "agent_handoff", "user"],
        )
        # Relabelled, not rewritten — no greeting is invented for the caller.
        self.assertEqual(ctx.items[-1].text, "intro")

    def test_a_handoff_alone_is_not_content(self):
        # The regression: requiring every item to be a system message meant the
        # handoff made this look answerable, and the 400 stood.
        ctx = FakeCtx([FakeMessage("system", "persona"), FakeHandoff()])
        ctx, relabelled = ensure_non_system_turn(ctx)
        self.assertTrue(relabelled)
        self.assertEqual([getattr(i, "role", i.type) for i in ctx.items],
                         ["user", "agent_handoff"])

    def test_a_real_turn_is_left_alone(self):
        items = [FakeMessage("system", "persona"), FakeMessage("user", "ابغى برجر")]
        ctx = FakeCtx(items)
        ctx, relabelled = ensure_non_system_turn(ctx)
        self.assertFalse(relabelled)
        self.assertIs(ctx.items[1], items[1])

    def test_an_assistant_turn_is_content(self):
        ctx = FakeCtx([FakeMessage("system", "p"), FakeMessage("assistant", "هلا")])
        _, relabelled = ensure_non_system_turn(ctx)
        self.assertFalse(relabelled)

    def test_a_post_tool_turn_is_left_alone(self):
        items = [FakeMessage("system", "persona"), FakeCall()]
        ctx = FakeCtx(items)
        ctx, relabelled = ensure_non_system_turn(ctx)
        self.assertFalse(relabelled)
        self.assertEqual(ctx.items, items)

    def test_developer_counts_as_system(self):
        ctx = FakeCtx([FakeMessage("developer", "persona"), FakeMessage("system", "intro")])
        ctx, relabelled = ensure_non_system_turn(ctx)
        self.assertTrue(relabelled)
        self.assertEqual([i.role for i in ctx.items], ["developer", "user"])

    def test_nothing_to_relabel_is_survivable(self):
        self.assertEqual(ensure_non_system_turn(None), (None, False))
        ctx = FakeCtx([])
        self.assertEqual(ensure_non_system_turn(ctx), (ctx, False))
        # A handoff on its own: no system message to promote, so leave it.
        ctx = FakeCtx([FakeHandoff()])
        self.assertEqual(ensure_non_system_turn(ctx), (ctx, False))


if __name__ == "__main__":
    unittest.main(verbosity=2)
