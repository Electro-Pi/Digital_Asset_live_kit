"""Text-leaked tool calls must become real function calls, never speech.

Replays the exact 2026-08-13 pizza failure where Gemma wrote
``call:show_meal{…}`` as assistant text instead of using function calling.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.plugins.voxcpm_tts_plugin import (  # noqa: E402
    strip_control_tokens,
)
from src.spoken_lexicon import clean_for_speech  # noqa: E402
from src.text_tool_calls import (  # noqa: E402
    claims_page_opened,
    contains_text_tool_call,
    extract_text_tool_calls,
    latest_search_meals,
    recover_implied_show_meal,
)

failures: list[str] = []


def check(label: str, got, expected) -> None:
    if got != expected:
        failures.append(f"{label}: got {got!r}, expected {expected!r}")
        print(f"FAIL {label}: got {got!r}, expected {expected!r}")
    else:
        print(f"OK   {label}")


def check_true(label: str, cond: bool, detail: str = "") -> None:
    if not cond:
        failures.append(f"{label}: {detail or 'condition false'}")
        print(f"FAIL {label}: {detail or 'condition false'}")
    else:
        print(f"OK   {label}")


# Exact leak from the live journal (2026-08-13 21:27).
LIVE_LEAK = (
    'call:show_meal{meal_id:<|"|>amico-kitchen-offer-2<|"|>,'
    'reason:<|"|>هَذِهِ وَجْبَة عائِلِيَّة مِثَالِيَّة لِثَلاثَة أَشْخاص. <|"|>}'
    "<tool_call|>"
)

ALLOWED = {
    "show_meal",
    "search_meals",
    "add_to_cart",
    "get_cart",
    "set_cart_quantity",
    "open_page",
}


def main() -> None:
    print("\n── live pizza leak recovers show_meal ──")
    speech, calls = extract_text_tool_calls(LIVE_LEAK, allowed_names=ALLOWED)
    check("speech is empty (nothing to say)", speech, "")
    check("one recovered call", len(calls), 1)
    check("tool name is show_meal", calls[0]["name"], "show_meal")
    check(
        "meal_id survives control-token junk",
        calls[0]["arguments"].get("meal_id"),
        "amico-kitchen-offer-2",
    )
    check_true(
        "reason kept as Arabic text",
        "عائ" in str(calls[0]["arguments"].get("reason", "")),
        repr(calls[0]["arguments"]),
    )
    check_true("call_id assigned", bool(calls[0].get("call_id")))

    print("\n── simpler leaks and mixed speech ──")
    speech, calls = extract_text_tool_calls(
        "call:show_meal{meal_id:formaggio-offer-2,reason:test}<tool_call|>",
        allowed_names=ALLOWED,
    )
    check("simple leak → show_meal", calls[0]["name"], "show_meal")
    check("simple meal_id", calls[0]["arguments"]["meal_id"], "formaggio-offer-2")
    check("simple leak silent", speech, "")

    speech, calls = extract_text_tool_calls(
        "أَبْشِر. call:add_to_cart{meal_id:bayt-alshawaya-offer-1,quantity:1}",
        allowed_names=ALLOWED,
    )
    check("speech before call kept", speech, "أَبْشِر.")
    check("add_to_cart recovered", calls[0]["name"], "add_to_cart")
    check("quantity parsed as int", calls[0]["arguments"]["quantity"], 1)

    speech, calls = extract_text_tool_calls(
        "call:set_cart_quantity{meal_id:amico-kitchen-offer-2,quantity:0,spicy:false}",
        allowed_names=ALLOWED,
    )
    check("set_cart_quantity recovered", calls[0]["name"], "set_cart_quantity")
    check("quantity 0 means remove", calls[0]["arguments"]["quantity"], 0)
    check("bool false parsed", calls[0]["arguments"]["spicy"], False)

    print("\n── unknown / malformed never becomes a fake tool ──")
    speech, calls = extract_text_tool_calls(
        "call:hack_the_planet{x:1}",
        allowed_names=ALLOWED,
    )
    check("unknown tool dropped", calls, [])
    check("unknown tool not spoken", speech, "")

    speech, calls = extract_text_tool_calls(
        "call:show_meal{not-valid",
        allowed_names=ALLOWED,
    )
    check("broken braces → no call", calls, [])
    check_true("detector still flags it", contains_text_tool_call("call:show_meal{not-valid}"))

    print("\n── ordinary speech untouched ──")
    speech, calls = extract_text_tool_calls(
        "هَلا واللَّه، مَعَك سَلْمَى مِن لُقْمَة.",
        allowed_names=ALLOWED,
    )
    check("greeting unchanged", speech, "هَلا واللَّه، مَعَك سَلْمَى مِن لُقْمَة.")
    check("no false-positive tools", calls, [])
    check_true(
        "detector quiet on normal Arabic",
        not contains_text_tool_call("هَلا واللَّه، مَعَك سَلْمَى مِن لُقْمَة."),
    )

    print("\n── TTS path never speaks the leak ──")
    check(
        "clean_for_speech kills live leak",
        clean_for_speech(LIVE_LEAK),
        "",
    )
    check(
        "strip_control_tokens kills live leak",
        strip_control_tokens(LIVE_LEAK),
        "",
    )
    check(
        "thought-only turn is silent",
        strip_control_tokens("<|channel>thought <channel|>"),
        "",
    )

    print("\n── recovered args are JSON-serializable for FunctionToolCall ──")
    _, calls = extract_text_tool_calls(LIVE_LEAK, allowed_names=ALLOWED)
    payload = json.dumps(calls[0]["arguments"], ensure_ascii=False)
    check_true("arguments dump to JSON", "amico-kitchen-offer-2" in payload)
    check("round-trip meal_id", json.loads(payload)["meal_id"], "amico-kitchen-offer-2")

    print("\n── open-claim without show_meal (live 2026-08-16 pizza) ──")
    LIVE_CLAIM = (
        "مَطْلَبَك عِنْدِي. فَتَحت لَك صَفْحَة وَجْبَة عائِلِيَّة بِيتْزا "
        "لِثَلاثَة أَشْخاص مِن مَطْبَخ أميكو. السِّعْر واحد وَسَبْعين رِيال."
    )
    check_true("detector sees فتحت لك صفحة", claims_page_opened(LIVE_CLAIM))
    check_true(
        "ordinary recommend is not an open claim",
        not claims_page_opened("أقترح عليك وجبة عائلية بيتزا من مطبخ أميكو."),
    )
    search_payload = {
        "results": [
            {
                "meal_id": "amico-kitchen-offer-2",
                "name": "وجبة عائلية بيتزا لـ 3 أشخاص",
                "restaurant": "مطبخ أميكو",
                "price_sar": 71,
            },
            {
                "meal_id": "anchor-fish-offer-2",
                "name": "وجبة عائلية بحرية لـ 3 أشخاص",
                "restaurant": "سمك المرساة",
                "price_sar": 78,
            },
            {
                "meal_id": "aseel-burger-offer-2",
                "name": "وجبة عائلية برجر لـ 3 أشخاص",
                "restaurant": "أصيل برجر",
                "price_sar": 64,
            },
        ]
    }
    class _Out:
        name = "search_meals"
        output = json.dumps(search_payload, ensure_ascii=False)

    class _Ctx:
        items = [_Out()]

    implied = recover_implied_show_meal(LIVE_CLAIM, _Ctx())
    check_true("implied show_meal recovered", implied is not None)
    assert implied is not None
    check("implied tool is show_meal", implied["name"], "show_meal")
    check(
        "implied meal is amico pizza",
        implied["arguments"]["meal_id"],
        "amico-kitchen-offer-2",
    )
    check(
        "latest_search_meals reads chat",
        latest_search_meals(_Ctx())[0]["meal_id"],
        "amico-kitchen-offer-2",
    )
    check(
        "no claim → no implied tool",
        recover_implied_show_meal(
            "أقترح عليك وجبة عائلية بيتزا من مطبخ أميكو.",
            _Ctx(),
        ),
        None,
    )
    check(
        "claim with no candidates → None",
        recover_implied_show_meal(LIVE_CLAIM, type("C", (), {"items": []})()),
        None,
    )

    # Chunked leak (how the LLM stream actually arrives).
    print("\n── stream chunks reassemble into one recoverable call ──")
    chunks = [
        "call:show_meal{meal_id:<|\"|>amico-",
        "kitchen-offer-2<|\"|>,reason:<|\"|>test<|\"|>}",
        "<tool_call|>",
    ]
    speech, calls = extract_text_tool_calls("".join(chunks), allowed_names=ALLOWED)
    check("chunked leak silent", speech, "")
    check("chunked leak → show_meal", calls[0]["name"], "show_meal")
    check("chunked meal_id", calls[0]["arguments"]["meal_id"], "amico-kitchen-offer-2")

    if failures:
        print(f"\n{len(failures)} FAILURE(S)")
        for f in failures:
            print(" -", f)
        raise SystemExit(1)
    print("\nALL PASS")

if __name__ == "__main__":
    main()
