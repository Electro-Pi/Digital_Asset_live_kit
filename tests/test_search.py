"""Search quality: what a caller's words should find.

Each case is a query the model sends and a check on the answer. Cases marked
"live" reproduce a real call. Run after any change to src/search.py,
data/search_vocab.json, the catalog or the attributes file.
"""

from __future__ import annotations

import pytest

from src import catalog
from src.search import SearchRequest, search, stem

CAT = catalog.load_catalog()


def run(query: str = "", **kw):
    return catalog.search_meals(query=query, **kw)


def top_names(result, n=3):
    return [r["name"] for r in result["results"][:n]]


# ─── Named dishes ────────────────────────────────────────────────────────────

NAMED = [
    # (query, a word the top result's name must contain)
    ("سلطة سيزر", "سيزر"),
    ("سلطة دجاج", "سلطة دجاج"),
    ("سلطة تونة", "تونة"),
    ("فتوش", "فتوش"),
    ("كبسة لحم", "كبسة"),
    ("ابي كبسة لحم", "كبسة لحم"),
    ("شاورما لحم", "شاورما لحم"),
    ("برجر دجاج", "برجر دجاج"),
    ("كبة مقلية", "كبة"),
    ("حمص", "حمص"),
    ("تشيز كيك", "تشيز كيك"),
    ("ستربس", "ستربس"),
    ("ستريبس", "ستربس"),  # ASR spelling
    ("دوناتس", "دونات"),  # not «كاتسو دون»
    ("ايسكريم", "آيس كريم"),
    ("وجبة مشاوي فردية", "وجبة مشاوي فردية"),  # live: a meat guess hid this chicken combo
    ("بطاطس", "بطاطس"),
    ("قهوة", "قهوة"),
    ("مشاوي", "مشاوي"),
    ("عندكم سوشي", "سوشي"),
    ("دَجاج مَشْوي", "دجاج"),  # tashkeel from the model
    ("البرجر", "برجر"),
]


@pytest.mark.parametrize("query,expected", NAMED)
def test_named_dish_is_found(query, expected):
    result = run(query)
    assert result["results"], result
    assert expected in result["results"][0]["name"], top_names(result)
    assert result["matched_by"] in {"name", "text"}


# ─── Dish types ──────────────────────────────────────────────────────────────

TYPED = [
    ("سلطة", "salad"),  # live 2026-09-24: opened a chicken breast
    ("ممكن تجيبي لي سلطة", "salad"),
    ("سلطات", "salad"),
    ("حلا", "dessert"),
    ("أرخص حلا", "dessert"),
    ("شوربة", "soup"),
    ("عصير", "drink"),
    ("مشروب بارد", "drink"),
]


@pytest.mark.parametrize("query,dish_type", TYPED)
def test_type_word_filters_to_that_type(query, dish_type):
    result = run(query)
    assert result["results"]
    assert all(r["type"] == dish_type for r in result["results"]), [(r["name"], r["type"]) for r in result["results"]]


def test_dish_type_argument_filters():
    result = run("", dish_type="drink")
    assert result["results"] and all(r["type"] == "drink" for r in result["results"])


@pytest.mark.parametrize("query,head", [
    ("كبسة مع سلطة", "كبسة"),
    ("برجر مع بطاطس", "برجر"),
    ("شاورما مع بطاطس", "شاورما"),
])
def test_accompaniment_after_with_does_not_take_over(query, head):
    result = run(query)
    assert head in result["results"][0]["name"], top_names(result)


def test_a_bare_side_is_found_as_itself():
    result = run("بطاطس")
    assert result["results"][0]["type"] == "side"


# ─── Not found, and never relabelled ─────────────────────────────────────────

@pytest.mark.parametrize("query", ["ناجتس", "بيبسي"])
def test_missing_dish_is_reported_not_substituted(query):
    result = run(query)
    assert result["matched_by"] == "none"
    assert result["not_found"]
    assert result["results"] == []  # substitutes are never presented as the dish
    assert result.get("alternatives")


def test_missing_drink_gets_drink_alternatives():
    result = run("بيبسي")
    assert all(a["type"] == "drink" for a in result["alternatives"])


def test_open_request_is_a_category_search_not_not_found():
    for query in ("اقترحي لي شي", "جوعان", "ابغى شي خفيف"):
        result = run(query)
        assert result["matched_by"] == "category", query
        assert result["results"], query


def test_exists_but_filtered_is_reported():
    result = run("شاورما", max_price=5)
    assert not result["results"]
    assert result.get("cheapest_available") or result.get("exists_but_filtered")


def test_impossible_filters_are_reported_not_dropped():
    result = run("سمك", max_calories=100, max_price=10)
    assert result["results"] == []
    assert result.get("cheapest_available") or result.get("exists_but_filtered")


# ─── Headcounts ──────────────────────────────────────────────────────────────

def test_headcount_in_words_ranks_the_exact_size_first():
    result = run("بيتزا لثلاث أشخاص")
    assert result["serves_requested"] == 3
    assert result["results"][0]["serves_people"] == 3


def test_serves_argument_does_the_same():
    result = run("بيتزا", serves=3)
    assert result["results"][0]["serves_people"] == 3


def test_group_gets_the_closest_platter_when_no_exact_size():
    result = run("شاورما لثلاثة اشخاص")
    assert result["serves_exact_available"] is False
    first = result["results"][0]
    assert "شاورما" in first["name"] and first["serves_people"] == 4


@pytest.mark.parametrize("query,count", [
    ("أنا وأمي وأبوي", 3),
    ("احنا ستة", 6),
    ("لشخصين", 2),
])
def test_headcount_phrases(query, count):
    assert run(query)["serves_requested"] == count


# ─── Filters and sorts from words ────────────────────────────────────────────

def test_high_protein_sorts_by_protein_and_applies_the_floor():
    result = run("بروتين عالي")
    assert result["sort"] == "protein_desc"
    assert all(r["protein_g"] >= 30 for r in result["results"])


def test_healthy_excludes_rich_dishes():
    result = run("", healthy=True)
    for r in result["results"]:
        meal = CAT.by_id(r["meal_id"])
        assert "rich" not in meal.diet_tags or "light" in meal.diet_tags


def test_cheap_word_sorts_by_price():
    result = run("أرخص برجر")
    prices = [r["price_sar"] for r in result["results"]]
    assert prices == sorted(prices)


def test_offer_kind_from_word():
    result = run("كومبو")
    assert result["results"] and all(r.get("offer_kind") == "combo" for r in result["results"])


def test_cashback_filter():
    result = run("كاش باك")
    assert result["results"] and all(r.get("cashback_percent") for r in result["results"])


def test_crispy_and_keto_are_filters():
    for query, tag in (("مقرمش", "crispy"), ("كيتو", "keto")):
        result = run(query)
        assert result["results"], query
        assert all(tag in CAT.by_id(r["meal_id"]).diet_tags for r in result["results"])


def test_groceries_hidden_unless_asked():
    assert all(r["type"] != "grocery" for r in run("حليب")["results"])
    assert run("حليب", include_grocery=True)["results"]


def test_unknown_restaurant_is_reported_and_ignored():
    result = run("برجر", restaurant_id="مطعم ما له وجود")
    assert "restaurant" in result.get("ignored_filters", [])
    assert result["results"]


def test_restaurant_filter_by_arabic_name():
    result = run("", restaurant_id="أصيل برجر")
    assert result["results"]
    assert {CAT.by_id(r["meal_id"]).restaurant_id for r in result["results"]} == {"aseel-burger"}


def test_open_restaurant_ranks_first_without_filtering():
    result = run("برجر", prefer_restaurant_id="aseel-burger")
    assert CAT.by_id(result["results"][0]["meal_id"]).restaurant_id == "aseel-burger"


# ─── Determinism and shape ───────────────────────────────────────────────────

@pytest.mark.parametrize("query", ["برجر", "وجبة مشاوي فردية", "اقترحي لي شي", "حلا"])
def test_same_query_same_answer(query):
    first = [r["meal_id"] for r in run(query)["results"]]
    for _ in range(3):
        assert [r["meal_id"] for r in run(query)["results"]] == first


def test_rows_are_compact():
    result = run("برجر")
    allowed = {
        "meal_id", "name", "name_say_ar", "restaurant_say_ar", "type", "price_sar",
        "price_say_ar", "calories", "calories_say_ar", "serves_people", "offer_kind",
        "has_discount", "cashback_percent", "protein_g", "protein_say_ar", "match",
    }
    for r in result["results"]:
        assert set(r) <= allowed, set(r) - allowed
    assert "session" not in result


def test_limit_is_respected_and_capped():
    assert len(run("برجر", limit=2)["results"]) == 2
    assert len(run("برجر", limit=100)["results"]) <= 8


def test_stemming_is_symmetric():
    assert stem("سلطه") == stem("السلطه")
    assert stem("مشويات") == "مشوي"
    assert stem("دونات") == "دونات"


def test_search_request_is_the_whole_input():
    # No transcript, no session: the same request always gives the same answer.
    a = search(SearchRequest(query="سلطة"), CAT)
    b = search(SearchRequest(query="سلطة"), CAT)
    assert a == b


@pytest.mark.parametrize("query,cuisine,word", [("برجر", "برجر", "برجر"), ("شاورما", "شامي", "شاورما")])
def test_a_kitchen_name_that_is_also_a_dish_still_picks_dishes(query, cuisine, word):
    # Live 2026-09-24: query+cuisine «برجر» opened «تشيكن كرسبي».
    result = run(query, cuisine=cuisine, dish_type="main")
    assert all(word in r["name"] for r in result["results"]), top_names(result)


def test_a_kitchen_name_alone_browses_the_kitchen():
    result = run("فرايد تشيكن", cuisine="فرايد تشيكن")
    assert result["matched_by"] == "category" and result["total_matches"] > 50


def test_asking_for_a_recommendation_is_not_a_missing_dish():
    for query in ("وش ترشحين", "أفضل الأطباق", "ممكن ترشحي لي إيه؟"):
        result = catalog.search_meals(query=query)
        assert "query_not_found" not in result and result["total_matches"] > 100, query


@pytest.mark.parametrize("query,asks,taste", [
    ("وش ترشحين لي", True, False),
    ("جوعان", True, False),
    ("اقترحي علي شي دجاج", True, True),
    ("أبي برجر", False, True),
    ("عزيمة لعشرة", False, False),  # a family offer says how much, not what
    ("شي ثاني", False, False),
])
def test_discovery_facts(query, asks, taste):
    facts = catalog.search_meals(query=query)["discovery"]
    assert (facts["asks_recommendation"], facts["taste_given"]) == (asks, taste), query
    if not taste:
        assert len(facts["taste_options_say_ar"]) == 2
