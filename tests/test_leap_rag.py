from __future__ import annotations

import asyncio
import json
import re
import sqlite3
from pathlib import Path

import pytest

from src.arabic_text import SPOKEN, WRITTEN, DualFormStream, resolve_dual_form
from src.leap_rag import (
    LeapRag,
    LeapRagConfig,
    VisitorStore,
    participant_channel,
    participant_test_mode,
)
from src.prompts.leap_prompt import (
    LEAP_INTRO,
    LEAP_NAME_REQUEST,
    LEAP_REGISTRATION_REMINDER,
    LEAP_SYSTEM_PROMPT,
    LEAP_WEB_SYSTEM_PROMPT,
    LEAP_WELCOME,
)


DATA_DIR = Path("/home/bassem/Coda/Call-center/coda-voice-pack")


@pytest.fixture(scope="module")
def rag() -> LeapRag:
    return LeapRag(
        LeapRagConfig(
            data_dir=DATA_DIR,
            embedding_index=DATA_DIR / "missing-test-index.npz",
        )
    )


def test_exact_company_lookup_is_local_and_grounded(rag: LeapRag) -> None:
    result = asyncio.run(rag.lookup_company("STC"))
    assert result["ok"] is True
    assert result["matches"][0]["name"] == "Saudi Telecom Company (stc)"
    assert result["matches"][0]["stand"] == "H1.K10"
    assert result["matches"][0]["retrieval"] == "exact"


def test_arabic_alias_resolves_without_gemini(rag: LeapRag) -> None:
    result = asyncio.run(rag.lookup_company("سدايا"))
    assert result["ok"] is True
    assert result["matches"][0]["stand"] == "H1.K30"


def test_route_is_deterministic(rag: LeapRag) -> None:
    result = rag.route("H2.K30", "Saudi Data and AI Authority (SDAIA)")
    assert result["ok"] is True
    assert result["minutes"] >= 1
    assert result["spoken_directions_ar"]


def test_nearest_amenity_resolves_arabic_alias(rag: LeapRag) -> None:
    result = rag.nearest_amenity("H2.K30", "prayer")
    assert result["ok"] is True
    assert result["destination"]["id"] == "LM.PRAYER_M"


def test_visitor_store_normalizes_and_upserts(tmp_path: Path) -> None:
    path = tmp_path / "visitors.sqlite"
    store = VisitorStore(path)
    assert store.save("call-1", "  باسم  محمد ", "٠٥٦ ٣١١ ٥٨٥٣")["saved"] is True
    store.save("call-1", "باسم محمد", "+966563115853", "telephone_test")
    with sqlite3.connect(path) as connection:
        row = connection.execute(
            "SELECT name, phone_number, source, COUNT(*) FROM visitors WHERE session_id='call-1'"
        ).fetchone()
    assert row == ("باسم محمد", "+966563115853", "telephone_test", 1)


@pytest.mark.parametrize("phone", ["12", "not a number", "1234567890123456"])
def test_visitor_store_rejects_invalid_phone(phone: str) -> None:
    with pytest.raises(ValueError, match="phone_invalid"):
        VisitorStore.normalize_phone(phone)


def test_participant_channel_is_strict() -> None:
    assert participant_channel(json.dumps({"channel": "telephone"})) == "telephone"
    assert participant_channel(json.dumps({"channel": "other"})) == "web"
    assert participant_channel("not-json") == "web"
    assert participant_test_mode(json.dumps({"testMode": True})) is True
    assert participant_test_mode(json.dumps({"testMode": "true"})) is False


def test_leap_prompt_requires_dual_written_and_spoken_form() -> None:
    for prompt in (LEAP_SYSTEM_PROMPT, LEAP_WEB_SYSTEM_PROMPT):
        # The name is taken from the lookup, never transliterated by the model.
        assert "[[HAMS AI|همس إي آي]]" in prompt
        assert "`name_ar` from that same result copied exactly" in prompt
        assert "transliterate or translate a name yourself" in prompt
        # Booth codes and numbers carry both halves too.
        assert "[[H1.K30|القاعة إتش واحد، الجناح كيه ثلاثين]]" in prompt
        assert "[[100 متر|مئة متر]]" in prompt
        # Latin is allowed only inside a marker's written half.
        assert "Outside the brackets the reply carries no Latin" in prompt
        assert "no Western or Arabic-Indic numeric digits" in prompt
        # The phonetic fallback survives for a company with no stored name.
        assert "سيلز فورس" in prompt


def test_leap_onboarding_asks_one_field_and_recovers_from_silence() -> None:
    assert LEAP_NAME_REQUEST == "حياك الله، ممكن أعرف اسمك؟"
    assert "اسم المتصل فقط" in LEAP_INTRO
    assert "ask for the name first" in LEAP_SYSTEM_PROMPT
    assert "Ask again for only that one field" in LEAP_REGISTRATION_REMINDER


def test_strong_local_match_skips_live_semantic_request() -> None:
    rag = LeapRag(
        LeapRagConfig(
            data_dir=DATA_DIR,
            embedding_index=DATA_DIR / "03-sqlite" / "company-embeddings.npz",
            gemini_api_key="configured-for-test",
        )
    )

    class FailingEmbedder:
        enabled = True

        async def embed_query(self, query: str):
            raise AssertionError(f"unexpected semantic request for {query}")

    rag._embedder = FailingEmbedder()  # type: ignore[assignment]
    result = asyncio.run(rag.lookup_company("Saudi Telecom", limit=3))
    assert result["ok"] is True
    assert result["semantic_used"] is False
    assert len(result["matches"]) <= 3


def test_voice_prompt_caps_direction_length() -> None:
    assert "at most three essential" in LEAP_SYSTEM_PROMPT
    assert "under seventy spoken words" in LEAP_SYSTEM_PROMPT


def test_web_prompt_keeps_leap_grounding_without_the_lead_gate() -> None:
    # The website runs the call centre's assistant, so every grounding and
    # speech-normalization rule has to survive the copy.
    for rule in (
        "call lookup_company",
        "call route_to_booth",
        "no Western or Arabic-Indic numeric digits",
        "[[H1.K30|القاعة إتش واحد، الجناح كيه ثلاثين]]",
        "at most three essential",
        "under seventy spoken words",
    ):
        assert rule in LEAP_WEB_SYSTEM_PROMPT

    # ...and only the telephone lead capture is dropped.
    assert "save_visitor" not in LEAP_WEB_SYSTEM_PROMPT
    assert "mobile number" not in LEAP_WEB_SYSTEM_PROMPT
    assert "never ask for a name or a phone number" in LEAP_WEB_SYSTEM_PROMPT
    assert LEAP_WELCOME in LEAP_WEB_SYSTEM_PROMPT


def test_web_prompt_is_not_the_saudi_cultural_guide() -> None:
    from src.prompts.agent_prompt import female_system_prompt

    assert "cultural guide" in female_system_prompt
    assert "cultural guide" not in LEAP_WEB_SYSTEM_PROMPT
    assert "LEAP 2026" in LEAP_WEB_SYSTEM_PROMPT


def test_web_assistant_drops_the_registration_tool_and_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LEAP_VISITOR_DB", str(tmp_path / "visitors.sqlite"))
    import agent

    web = agent.LeapWebAssistant(rag=agent.leap_rag, session_id="web-1")
    telephone = agent.LeapAssistant(
        rag=agent.leap_rag, visitors=agent.leap_visitors, session_id="call-1"
    )

    def tool_names(assistant: agent.LeapAssistant) -> set[str]:
        return {getattr(tool, "__name__", "") for tool in assistant.tools}

    # Same RAG instance and the same three grounded tools on both channels.
    assert web._rag is telephone._rag
    assert tool_names(web) == {"lookup_company", "route_to_booth", "nearest_amenity"}
    # The phone keeps its lead capture; the browser is never offered it, and the
    # weather tool of the retired cultural guide is gone from both.
    assert "save_visitor" in tool_names(telephone)
    assert "lookup_weather" not in tool_names(telephone) | tool_names(web)

    # The web session starts past the gate, so a lookup answers on turn one.
    assert web.visitor_saved is True
    assert telephone.visitor_saved is False
    assert web._registration_required() is None
    assert telephone._registration_required()["error"] == "registration_required"


def test_lookup_company_carries_the_spoken_arabic_name(rag: LeapRag) -> None:
    # The bug this exists for: with only the Latin spelling to go on, the model
    # read HAMS AI as "هامز" instead of همس. The pronunciation is data now.
    result = asyncio.run(rag.lookup_company("HAMS AI"))
    match = result["matches"][0]
    assert match["name"] == "HAMS AI"
    assert match["stand"] == "H1A.D20"
    assert match["name_ar"] == "همس إي آي"


def test_every_company_has_a_speakable_arabic_name(rag: LeapRag) -> None:
    rows = rag.all_companies()
    missing = [r["name"] for r in rows if not r.get("name_ar")]
    assert not missing, f"{len(missing)} companies have no name_ar, e.g. {missing[:5]}"
    # Anything the TTS cannot pronounce would be read out as garbage.
    bad = [r["name_ar"] for r in rows if re.search(r"[A-Za-z0-9٠-٩]", r["name_ar"])]
    assert not bad, f"name_ar containing Latin or digits: {bad[:5]}"


def test_dual_form_splits_written_from_spoken() -> None:
    reply = "تفضل، [[HAMS AI|همس إي آي]] في [[H1A.D20|القاعة إتش واحد إيه، الجناح دي عشرين]]."
    assert resolve_dual_form(reply, WRITTEN) == "تفضل، HAMS AI في H1A.D20."
    assert resolve_dual_form(reply, SPOKEN) == (
        "تفضل، همس إي آي في القاعة إتش واحد إيه، الجناح دي عشرين."
    )


@pytest.mark.parametrize("chunk", [1, 2, 3, 5, 11])
def test_dual_form_survives_markers_split_across_deltas(chunk: int) -> None:
    # The model streams in tokenizer-sized pieces, so "[[", the "|" and "]]"
    # all land mid-marker routinely. Streaming must match the whole-string result.
    reply = "زين، [[BARQ Systems|برق سيستمز]] في [[H2.K30|القاعة إتش تو]] بعد [[100 متر|مئة متر]]."
    for side in (WRITTEN, SPOKEN):
        stream = DualFormStream(side)
        out = "".join(stream.feed(reply[i : i + chunk]) for i in range(0, len(reply), chunk))
        assert out + stream.flush() == resolve_dual_form(reply, side)


def test_dual_form_salvages_a_marker_that_never_closes() -> None:
    # A truncated reply must not swallow the text it was holding back.
    stream = DualFormStream(WRITTEN)
    assert stream.feed("الجناح [[H2.K30|القاعة") + stream.flush() == "الجناح H2.K30"
    stream = DualFormStream(SPOKEN)
    assert stream.feed("اسمه [[Coda") + stream.flush() == "اسمه Coda"
