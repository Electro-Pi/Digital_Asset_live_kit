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
    STALL_FALLBACK_SAY_AR,
    build_offers_path,
    claims_page_opened,
    claims_stall,
    contains_text_tool_call,
    extract_text_tool_calls,
    latest_search_meals,
    recover_implied_add_to_cart,
    recover_implied_get_cart,
    recover_implied_get_saved,
    recover_implied_open_page,
    recover_implied_search_meals,
    recover_implied_set_meal_options,
    recover_implied_set_ui_filter,
    recover_implied_show_meal,
    recover_stalled_turn,
    should_speak_cutout_fallback,
    should_run_add_to_cart,
    user_asked_add_to_cart,
    user_asked_extras,
    user_blocks_add_to_cart,
    user_cancels_repeat_add,
    claims_went_to_cart,
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

    print("\n── hallucination recoveries (tester bugs 001-009) ──")

    class _User:
        def __init__(self, text: str) -> None:
            self.role = "user"
            self.text_content = text

    class _Chat:
        def __init__(self, text: str) -> None:
            self.items = [_User(text)]

    kitchen_claim = "فَتَحت لَك صَفْحَة المَطَاعِم."
    implied = recover_implied_open_page(kitchen_claim)
    check_true("open-claim restaurants recovers open_page", implied is not None)
    assert implied is not None
    check("open-claim page is restaurants", implied["arguments"]["page"], "restaurants")

    implied = recover_implied_open_page(
        "أبشر",
        _Chat("ادخل على المطبخ"),
    )
    check_true("user kitchen request recovers open_page", implied is not None)
    assert implied is not None
    check("kitchen maps to restaurants", implied["arguments"]["page"], "restaurants")

    implied = recover_implied_open_page(
        "",
        _Chat("افتح مطبخ دمشق"),
    )
    check(
        "named kitchen restaurant does not steal open_page",
        implied,
        None,
    )

    implied = recover_implied_open_page(
        "أبشر",
        _Chat("افتحي صفحة التوصيل سريعا"),
    )
    check_true("fast-delivery page recovers open_page", implied is not None)
    assert implied is not None
    check("fast-delivery page key", implied["arguments"]["page"], "fast")

    implied = recover_implied_open_page(
        "أبشر",
        _Chat("مكتوب عليها سريع"),
    )
    check_true("chip label سريع recovers open_page", implied is not None)
    assert implied is not None
    check("chip label page is fast", implied["arguments"]["page"], "fast")

    implied = recover_implied_open_page(
        "أبشر",
        _Chat("افتحي كده البقالة"),
    )
    check_true("grocery page recovers open_page", implied is not None)
    assert implied is not None
    check("grocery page key", implied["arguments"]["page"], "grocery")

    implied = recover_implied_open_page(
        "فَتَحت لَك صَفْحَة المَطَاعِم.",
        _Chat("افتحي صفحة التوصيل سريعا"),
    )
    check_true("user fast intent beats restaurants speech-claim", implied is not None)
    assert implied is not None
    check(
        "user fast intent page",
        implied["arguments"]["page"],
        "fast",
    )

    class _PrevOpen:
        name = "open_page"
        arguments = {"page": "restaurants"}

    class _AlreadyOpened:
        def __init__(self) -> None:
            self.items = [_User("افتحي المطاعم"), _PrevOpen()]

    check(
        "do not re-open restaurants after a successful open_page",
        recover_implied_open_page(
            "فَتَحت لَك صَفْحَة المَطَاعِم.",
            _AlreadyOpened(),
        ),
        None,
    )

    stalled_fast = recover_stalled_turn(
        "أَبْشِر. بَدَوِّر لَك عَلَى أَسْرَع المَطَاعِم.",
        _Chat("افتحي صفحة التوصيل سريعا"),
    )
    check_true("stall on fast page recovers a tool", bool(stalled_fast))
    check("stall on fast page opens filter", stalled_fast[0]["name"], "open_page")
    check("stall on fast page key", stalled_fast[0]["arguments"]["page"], "fast")

    implied = recover_implied_search_meals(
        "ما عندنا مطعم بهذا الاسم",
        _Chat("دور لي شاورما لخمسة أفراد"),
    )
    check_true("no-restaurant + shawarma recovers search_meals", implied is not None)
    assert implied is not None
    check("search query is the user turn", implied["arguments"]["query"],
          "دور لي شاورما لخمسة أفراد")
    check(
        "missing restaurant name does not search meals",
        recover_implied_search_meals(
            "ما عندنا مطعم بهذا الاسم",
            _Chat("افتح مطعم أبو فلان"),
        ),
        None,
    )

    implied = recover_implied_get_saved(
        "المحفوظات فاضية",
        _Chat("وريني المحفوظات"),
    )
    check_true("empty-bookmark claim recovers get_saved", implied is not None)

    implied = recover_implied_get_cart(
        "المجموع ألف وست مئة سعرة",
        _Chat("احسبي توتال السعرات اللي في السلة"),
    )
    check_true("cart calorie total recovers get_cart", implied is not None)
    check(
        "single-meal calories do not recover get_cart",
        recover_implied_get_cart("سعراته أربعمئة", _Chat("كم سعرات الشاورما")),
        None,
    )

    implied = recover_implied_set_ui_filter(
        "طبقت الفلتر",
        _Chat("اعملي فلتر على قنبو وعائلي"),
    )
    check_true("combo+family filter recovers set_ui_filter", implied is not None)
    assert implied is not None
    check_true(
        "offers_kind includes combo",
        "combo" in str(implied["arguments"].get("offers_kind") or ""),
    )
    implied = recover_implied_set_ui_filter(
        "",
        _Chat("وريني الكريسبي والكيتو بس"),
    )
    check_true("crispy/keto filter recovers set_ui_filter", implied is not None)

    implied = recover_implied_set_ui_filter("", _Chat("كومبو"))
    check_true("bare combo chip recovers set_ui_filter", implied is not None)
    assert implied is not None
    check("bare combo kind", implied["arguments"].get("offers_kind"), "combo")

    implied = recover_implied_set_ui_filter("", _Chat("عائلي"))
    check_true("bare family chip recovers set_ui_filter", implied is not None)
    assert implied is not None
    check("bare family kind", implied["arguments"].get("offers_kind"), "family")

    implied = recover_implied_set_ui_filter("", _Chat("عرض"))
    check_true("bare deal chip recovers set_ui_filter", implied is not None)
    assert implied is not None
    check("bare deal kind", implied["arguments"].get("offers_kind"), "deal")

    implied = recover_implied_set_ui_filter("", _Chat("عرض شاورما"))
    check(
        "عرض + dish is not the deal chip",
        implied,
        None,
    )

    check(
        "cashback+combo path keeps both params",
        build_offers_path(kind="combo", cashback=True),
        "/offers?cashback=1&kind=combo",
    )

    implied = recover_implied_open_page("أبشر", _Chat("24/7"))
    check_true("24/7 chip recovers open_page", implied is not None)
    assert implied is not None
    check("24/7 page key", implied["arguments"]["page"], "24h")

    implied = recover_implied_open_page("أبشر", _Chat("سريع"))
    check_true("bare سريع chip recovers open_page", implied is not None)
    assert implied is not None
    check("bare سريع page key", implied["arguments"]["page"], "fast")

    print("\n── cashback stall (Bug-010 live 2026-08-17) ──")
    LIVE_CASHBACK_STALL = "أَبْشِر، لَحْظَة أَشُوف لَك العَرَوض اللي فِيهَا كَاش بَاك."
    check_true("stall detector sees لحظة أشوف", claims_stall(LIVE_CASHBACK_STALL))
    implied = recover_implied_search_meals(
        LIVE_CASHBACK_STALL,
        _Chat("عايز حاجة عليا كاش باك"),
    )
    check_true("cashback stall recovers search_meals", implied is not None)
    assert implied is not None
    check("cashback search flag is true", implied["arguments"].get("cashback"), True)
    implied = recover_implied_open_page(
        LIVE_CASHBACK_STALL,
        _Chat("عايز عروض الكاش باك"),
    )
    check_true("cashback request recovers open_page", implied is not None)
    assert implied is not None
    check("cashback page key", implied["arguments"]["page"], "cashback")

    pizza_stall = "لحظة أدور لك على بيتزا."
    implied = recover_implied_search_meals(pizza_stall, _Chat("أبغى بيتزا"))
    check_true("generic stall recovers search_meals", implied is not None)
    assert implied is not None
    check("generic stall tool is search_meals", implied["name"], "search_meals")

    class _SearchOut:
        name = "search_meals"
        output = json.dumps(
            {"results": [{"meal_id": "formaggio-offer-2", "name": "بيتزا"}]},
            ensure_ascii=False,
        )

    class _AfterSearch:
        items = [_User("أبغى بيتزا"), _SearchOut()]

    stalled_calls = recover_stalled_turn(pizza_stall, _AfterSearch())
    check_true("stall after search recovers a tool", bool(stalled_calls))
    check("stall after search opens the hit", stalled_calls[0]["name"], "show_meal")
    check(
        "stall after search uses last meal_id",
        stalled_calls[0]["arguments"]["meal_id"],
        "formaggio-offer-2",
    )
    check(
        "stall with no user and no search is empty recover",
        recover_stalled_turn(pizza_stall, type("C", (), {"items": []})()),
        [],
    )
    check_true("fallback sentence is speakable Arabic", "عيد" in STALL_FALLBACK_SAY_AR)

    print("\n── live 2026-08-17 pizza session regressions ──")
    offer_speech = "أقترح عليك بيتزا من أميكو. تبي أوريك الصفحة؟"
    check_true(
        "ordinary recommend is still not an open claim",
        not claims_page_opened(offer_speech),
    )
    implied = recover_implied_show_meal(offer_speech, _AfterSearch())
    check_true("offer-to-open recovers show_meal", implied is not None)
    assert implied is not None
    check("offer-to-open tool", implied["name"], "show_meal")
    check(
        "offer-to-open uses last search hit",
        implied["arguments"]["meal_id"],
        "formaggio-offer-2",
    )

    class _AfterDetails:
        items = [
            _User("عايزك تضيفه لي للسنة"),
            type("D", (), {"name": "get_meal_details", "arguments": {"meal_id": "amico-kitchen-offer-1"}})(),
        ]

    check_true(
        "add request detected on للسنة ASR",
        user_asked_add_to_cart("عايزك تضيفه لي للسنة"),
    )
    implied = recover_implied_add_to_cart("", _AfterDetails())
    check_true("add request recovers add_to_cart", implied is not None)
    assert implied is not None
    check("recovered add meal_id", implied["arguments"]["meal_id"], "amico-kitchen-offer-1")
    check(
        "new dish request is not an implied add",
        recover_implied_add_to_cart("", _Chat("ضيف لي بيتزا")),
        None,
    )

    check_true("دي تاني معلش cancels repeat add", user_cancels_repeat_add("دي تاني معلش"))
    check_true("مش تاني cancels", user_cancels_repeat_add("مش تاني"))
    check_true("ضيفها تاني still means add again", not user_cancels_repeat_add("ضيفها تاني"))
    check_true(
        "cancelled add is not an add request",
        not user_asked_add_to_cart("دي تاني معلش"),
    )

    class _PostTool:
        items = [
            _User("وريني البيتزا"),
            type("S", (), {"name": "show_meal", "arguments": {"meal_id": "x"}})(),
        ]

    check_true(
        "post-tool empty turn is not a cutout",
        not should_speak_cutout_fallback(_PostTool()),
    )
    check_true(
        "empty first turn still gets the fallback",
        should_speak_cutout_fallback(_Chat("هذا هو")),
    )
    check(
        "thought 思考完毕 is silent",
        clean_for_speech("thought 思考完毕"),
        "",
    )
    check(
        "strip_control_tokens drops CJK thought",
        strip_control_tokens("thought 思考完毕"),
        "",
    )

    implied = recover_implied_set_ui_filter(
        "",
        _Chat("كنت عايز عرض لتلات اصوات"),
    )
    check("عرض for N people is not the deal chip", implied, None)

    class _AfterDealFilter:
        items = [
            _User("كنت عايز عرض لتلات اصوات"),
            type("F", (), {"name": "set_ui_filter", "arguments": {"offers_kind": "deal"}})(),
            type(
                "S",
                (),
                {
                    "name": "search_meals",
                    "output": json.dumps(
                        {"results": [{"meal_id": "amico-kitchen-offer-2", "name": "بيتزا", "restaurant": "أميكو"}]},
                        ensure_ascii=False,
                    ),
                },
            )(),
        ]

    check(
        "do not re-apply deal filter after it already ran",
        recover_implied_set_ui_filter("", _AfterDealFilter()),
        None,
    )
    class _WhereIsIt:
        items = [
            type(
                "S",
                (),
                {
                    "name": "search_meals",
                    "output": json.dumps(
                        {"results": [{"meal_id": "amico-kitchen-offer-2", "name": "بيتزا"}]},
                        ensure_ascii=False,
                    ),
                },
            )(),
            _User("فين العرض ده أو فين الوجبة دي؟"),
        ]

    implied = recover_implied_show_meal("أبشر", _WhereIsIt())
    check_true("where-is-the-meal recovers show_meal", implied is not None)
    assert implied is not None
    check("where-is-the-meal id", implied["arguments"]["meal_id"], "amico-kitchen-offer-2")

    class _Asst:
        def __init__(self, text: str) -> None:
            self.role = "assistant"
            self.text_content = text

    class _ConfirmAdd:
        items = [
            type("S", (), {"name": "show_meal", "arguments": {"meal_id": "amico-kitchen-offer-2"}})(),
            _Asst("تبي أضيفها للسلة؟"),
            _User("في هذا السالب ده ماشي"),
        ]

    check_true(
        "السالب+ماشي is an add request",
        user_asked_add_to_cart("في هذا السالب ده ماشي"),
    )
    implied = recover_implied_add_to_cart(
        "أَبْشِر، ضِفْت الوَجْبَة لِلسَّلَّة.",
        _ConfirmAdd(),
    )
    check_true("confirm-add recovers add_to_cart", implied is not None)
    assert implied is not None
    check("confirm-add meal_id", implied["arguments"]["meal_id"], "amico-kitchen-offer-2")

    print("\n── live 2026-08-17 duplicate-cart regressions ──")
    after_add = type(
        "Chat",
        (),
        {
            "items": [
                type("S", (), {"name": "show_meal", "arguments": {"meal_id": "berry-bake-offer-2"}})(),
                _Asst("أضفتها للسلة. تبي تكمل الطلب؟"),
                _User("تمام عايز ادخل على السلة الان"),
            ]
        },
    )()
    check_true(
        "open cart is not an add request",
        not user_asked_add_to_cart("تمام عايز ادخل على السلة الان"),
    )
    check_true(
        "open cart blocks add",
        user_blocks_add_to_cart("تمام عايز ادخل على السلة الان"),
    )
    check(
        "open cart does not recover add_to_cart",
        recover_implied_add_to_cart("فتحت لك السلة", after_add),
        None,
    )
    check_true(
        "calories in cart is not add",
        not should_run_add_to_cart(
            "تمام أنا عايز أعرف السعرات اللي في السلة قد إيه؟",
            after_add,
        ),
    )
    check_true(
        "reduce quantity is not add",
        not should_run_add_to_cart(
            "تمام أنا عايز أقلل كمية واحدة من تشكيلة الحلا",
            after_add,
        ),
    )
    check_true(
        "إتمام طلب is not add (تمام substring)",
        not should_run_add_to_cart("لا بس اعملي إتمام طلب.", after_add),
    )
    check_true(
        "extras without للسلة is not add",
        not user_asked_add_to_cart("تمام ضيفي جبنة برمزان"),
    )
    check_true(
        "هنضيفها للسلة is add",
        user_asked_add_to_cart("هنضيفها للسلة بس هنزود عليها صوص شوكولاتة"),
    )
    check_true(
        "explicit add to cart still works",
        user_asked_add_to_cart("عايزك تضيفي لي وجبة مشاوي فردية للسلة"),
    )
    check_true(
        "زين ودي فيها is a bare confirm",
        should_run_add_to_cart(
            "زين، ودي فيها",
            type(
                "C",
                (),
                {
                    "items": [
                        type("S", (), {"name": "show_meal", "arguments": {"meal_id": "x"}})(),
                        _Asst("تبي أضيفها للسلة؟"),
                        _User("زين، ودي فيها"),
                    ]
                },
            )(),
        ),
    )

    print("\n── live 2026-08-25 pizza home / cross-sell / ASR yes ──")
    pizza_shown = type(
        "C",
        (),
        {
            "items": [
                _User("طيب ممكن توريني البيتزا؟"),
                type(
                    "S",
                    (),
                    {
                        "name": "show_meal",
                        "arguments": {"meal_id": "amico-kitchen-offer-2"},
                    },
                )(),
            ]
        },
    )()
    check(
        "showing pizza does not recover open_page home",
        recover_implied_open_page(
            "فتحت لك صفحة وجبة عائلية بيتزا لثلاثة أشخاص. تبي تضيف لها شيء؟",
            pizza_shown,
        ),
        None,
    )
    check(
        "البيتزا is not the home page",
        recover_implied_open_page(
            "فتحت لك صفحة وجبة بيتزا",
            _Chat("طيب ممكن توريني البيتزا؟"),
        ),
        None,
    )
    check_true(
        "stretched اي is not an add request",
        not user_asked_add_to_cart("أي" + "ي" * 40),
    )

    class _AfterPizzaAdd:
        items = [
            type(
                "S",
                (),
                {
                    "name": "add_to_cart",
                    "arguments": {"meal_id": "amico-kitchen-offer-2"},
                    "output": json.dumps(
                        {
                            "ok": True,
                            "meal_id": "amico-kitchen-offer-2",
                            "meal_name": "وجبة عائلية بيتزا لـ 3 أشخاص",
                            "pair_meal_id": "cardamom-cafe-tiramisu",
                            "pair_name": "تيراميسو صغير",
                        },
                        ensure_ascii=False,
                    ),
                },
            )(),
            _Asst("أضفت البيتزا. لو ودك حلا بعدها، تيراميسو صغير."),
            _User("ماشي ضيفي له السلة"),
        ]

    implied = recover_implied_add_to_cart("", _AfterPizzaAdd())
    check_true("cross-sell yes recovers add_to_cart", implied is not None)
    assert implied is not None
    check(
        "cross-sell yes adds the pair not the pizza",
        implied["arguments"]["meal_id"],
        "cardamom-cafe-tiramisu",
    )

    check_true(
        "رحت للسلة is a cart-nav hallucination",
        claims_went_to_cart("أبشر، رحت للسلة. وش تبي تطلب الحين؟"),
    )
    check(
        "extras question plus cart-claim does not open home or cart",
        recover_implied_open_page(
            "أبشر، رحت للسلة. وش تبي تطلب الحين؟",
            _Chat("ما يشد فيا السلة"),
        ),
        None,
    )
    extras_ctx = type(
        "E",
        (),
        {
            "items": [
                type(
                    "S",
                    (),
                    {
                        "name": "show_meal",
                        "arguments": {"meal_id": "amico-kitchen-offer-2"},
                    },
                )(),
                _Asst("فتحت لك صفحة البيتزا. تبي تضيف لها شيء؟"),
                _User("ضيف extras ثوم وبطاطس"),
            ]
        },
    )()
    extras_call = recover_implied_set_meal_options("", extras_ctx)
    check_true("named extras recover set_meal_options", extras_call is not None)
    assert extras_call is not None
    check(
        "extras recover the open meal",
        extras_call["arguments"]["meal_id"],
        "amico-kitchen-offer-2",
    )
    check_true("garlic/fries count as extras", user_asked_extras("ضيف ثوم وبطاطس"))
    check_true(
        "stretched اي is not extras",
        not user_asked_extras("أي" + "ي" * 40),
    )

    if failures:
        print(f"\n{len(failures)} FAILURE(S)")
        for f in failures:
            print(" -", f)
        raise SystemExit(1)
    print("\nALL PASS")

if __name__ == "__main__":
    main()
