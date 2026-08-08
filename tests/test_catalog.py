"""Deterministic catalog tests — no LLM, no network, runs in under a second.

Everything asserted here is a bug that actually shipped and was found either by
auditing all 110 meals or by reading production logs. They are written down so
they cannot come back quietly.

Run:  .venv/bin/python tests/test_catalog.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.catalog import (  # noqa: E402
    load_catalog,
    meal_details,
    number_to_arabic_words,
    resolve_restaurant,
    sar_words,
    search_meals,
)

failures: list[str] = []


def check(label: str, got, expected) -> None:
    if got != expected:
        failures.append(f"{label}: got {got!r}, expected {expected!r}")
        print(f"FAIL {label}: got {got!r}, expected {expected!r}")
    else:
        print(f"OK   {label}")


def check_true(label: str, condition: bool, detail: str = "") -> None:
    if not condition:
        failures.append(f"{label}{f' — {detail}' if detail else ''}")
        print(f"FAIL {label} {detail}")
    else:
        print(f"OK   {label}")


cat = load_catalog()


def tags_of(name: str) -> set[str]:
    for meal in cat.meals:
        if meal.name == name:
            return set(meal.protein_tags)
    raise AssertionError(f"no meal named {name}")


print("\n── protein tagging: dish text only, never the restaurant's tags ──")
# A milkshake at أصيل برجر inherited برجر; غيوزا at سوشي نوي inherited سوشي.
check("ميلك شيك فانيلا is not meat", tags_of("ميلك شيك فانيلا"), set())
check("غيوزا is meat only", tags_of("غيوزا"), {"meat"})
check("تكة دجاج is chicken only", tags_of("تكة دجاج"), {"chicken"})
check("بطاطس ساحلية is vegetarian", tags_of("بطاطس ساحلية"), {"vegetarian"})

print("\n── keyword matching respects word boundaries and Arabic prefixes ──")
# "ريش" (ribs) is a substring of "جريش"; "لحم" needs to survive "باللحمة".
check("جريش is not meat", tags_of("جريش"), set())
check("بانير تكّا is vegetarian", tags_of("بانير تكّا"), {"vegetarian"})
check("حمص باللحمة is meat", tags_of("حمص باللحمة"), {"meat"})
check("أرز بالروبيان is seafood", tags_of("أرز بالروبيان"), {"seafood"})
check("مشكل مشاوي is both", tags_of("مشكل مشاوي"), {"meat", "chicken"})

print("\n── vegetarian is an allow-list, never 'no meat keyword found' ──")
check("شوربة حريرة is untagged, not vegetarian", tags_of("شوربة حريرة"), set())

print("\n── restaurant resolution (ids the model invented in production) ──")
check("aseel-burger-id", resolve_restaurant("aseel-burger-id", cat), "aseel-burger")
check("mazaq_alsham_id", resolve_restaurant("mazaq_alsham_id", cat), "madhaq-alsham")
check("bayt_alshawaya_id", resolve_restaurant("bayt_alshawaya_id", cat), "bayt-alshawaya")
check("Arabic name", resolve_restaurant("أصيل برجر", cat), "aseel-burger")
check("real id passes through", resolve_restaurant("aseel-burger", cat), "aseel-burger")
check("nonsense resolves to None", resolve_restaurant("مطعم غير موجود أبدا", cat), None)

print("\n── the live failure: burger search must not come back empty ──")
burger = search_meals(query="برجر", cuisine="برجر", protein_type="meat",
                      restaurant_id="aseel-burger-id")
check_true("burger search returns results", burger["total_matches"] > 0,
           f"total={burger['total_matches']}")
check_true("burger result has a usable meal_id",
           bool(burger["results"] and burger["results"][0]["meal_id"]))

unknown = search_meals(query="برجر", restaurant_id="مطعم ما له وجود")
check_true("unknown restaurant degrades instead of zeroing",
           unknown["total_matches"] > 0 and unknown.get("ignored_restaurant") is not None)

print("\n── search basics ──")
low_meat = search_meals(protein_type="meat", max_calories=500)
check_true("low-calorie meat search returns only meat under 500 cal",
           all("meat" in r["kind"] and 0 < r["calories"] <= 500 for r in low_meat["results"]))

tashkeel_query = search_meals(query="دَجاج مَشْوي")
check_true("tashkeel in the query still matches", tashkeel_query["total_matches"] > 0)

groceries = search_meals(query="حليب")
check_true("groceries hidden by default",
           all("ماركت" not in r["restaurant"] for r in groceries["results"]))

impossible = search_meals(protein_type="seafood", max_calories=100, max_price=10)
check_true("impossible search advises what to relax",
           impossible["total_matches"] == 0 and "no_results_advice" in impossible)

print("\n── spoken numbers (the model gets these wrong on its own) ──")
check("32", number_to_arabic_words(32), "اثنين وثلاثين")
check("46", number_to_arabic_words(46), "ستة وأربعين")
check("780", number_to_arabic_words(780), "سبع مئة وثمانين")
check("2200", number_to_arabic_words(2200), "ألفين ومئتين")
check("sar", sar_words(8), "ثمانية ريال")

print("\n── details carry what the addon flow needs ──")
details = meal_details("aseel-burger-classic")
check_true("details include addons with ids and spoken prices",
           bool(details) and bool(details["addons"])
           and all("addon_id" in a and "price_say_ar" in a for a in details["addons"]))
check_true("details include the frontend path",
           bool(details) and details["page_path"] == "/meals/aseel-burger-classic")

print("\n" + "=" * 60)
if failures:
    print(f"{len(failures)} FAILURE(S):")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("ALL PASS")
