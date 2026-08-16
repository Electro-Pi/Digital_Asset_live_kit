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
    HIGH_PROTEIN_MIN,
    cart_nutrition,
    clean_for_speech,
    fix_pronunciation,
    load_catalog,
    meal_details,
    repair_asr_query,
    repair_user_transcript,
    restaurant_details,
    number_to_arabic_words,
    resolve_restaurant,
    sanitize_tashkeel,
    sar_words,
    search_meals,
    speakable_name,
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

print("\n── a category request is not a missing dish ──")
# "asked for a dessert, got a burger": حلى normalizes to حلي, which sits inside
# الحليب and ساحلية, so substring scoring answered with سليق and بطاطس ساحلية.
# Asserted on the meal's own tag, not on its restaurant's name. The name test
# only ever worked while "حلويات الرياض" was the single sweets shop; the
# catalog now has seventeen, called زقاق السكر, عش النوتيلا, كريمري الرياض…
for phrasing in ("حلا", "حلى", "تحلية", "ديزرت", "شي حلو"):
    result = search_meals(query=phrasing)
    check_true(f"{phrasing} returns desserts only",
               result["total_matches"] > 0
               and all("dessert" in cat.by_id(r["meal_id"]).diet_tags
                       for r in result["results"]),
               str([r["name"] for r in result["results"]]))
# Cafés and bakeries share the "حلويات" cuisine, so dessert has to be decided
# per dish: a latte is not an answer to "أبغى تحلية".
for drink in ("لاتيه", "شاي كرك", "قهوة عربية"):
    check_true(f"{drink} is not tagged dessert",
               all("dessert" not in m.diet_tags for m in cat.meals if m.name == drink))
check_true("and never reports a category as not found",
           "query_not_found" not in search_meals(query="تحلية"))
check_true("milk is not a dessert",
           "حليب" in search_meals(query="حليب", include_grocery=True)["results"][0]["name"])

# "لاسي" (lassi) matched inside "كلاسيك" once a ك prefix was allowed, tagging
# برجر كلاسيك as a drink — which a category filter then acted on.
classic = next(m for m in cat.meals if m.name == "برجر كلاسيك")
check_true("برجر كلاسيك is not a drink", "drink" not in classic.diet_tags)
check_true("but the lassi still is",
           "drink" in next(m for m in cat.meals if m.name == "لاسي مانجو").diet_tags)
check_true("a healthy search still matches the صحي cuisine",
           search_meals(query="شي صحي")["total_matches"] > 0)

print("\n── a dish the catalog does not have must not return something else ──")
# Live failure: a missing dish used to come back as 100 matches and the agent
# read out a burger. A named dish that matches nothing has to report nothing.
# ستربس now exists (fried-chicken catalog, 2026-08-13); ناجتس still does not.
nuggets = search_meals(query="ناجتس")
check("ناجتس returns no matches", nuggets["total_matches"], 0)
check("and names what was not found", nuggets.get("query_not_found"), "ناجتس")
check("and hands over the exact sentence to say",
      nuggets.get("say_to_user_ar"), "ما عندنا ناجتس")
check_true("never offers a substitute to relabel", "alternatives" not in nuggets)

nuggets_filtered = search_meals(query="ناجتس", cuisine="برجر",
                                protein_type="chicken", restaurant_id="أصيل برجر")
check("filtered ناجتس also returns nothing", nuggets_filtered["total_matches"], 0)
check_true("and offers no substitute even with filters set",
           "alternatives" not in nuggets_filtered)

print("\n── ستربس is a real dish now, not a burger by another name ──")
strips = search_meals(query="ستربس")
check_true("ستربس returns the chicken strips",
           strips["total_matches"] > 0
           and all(r["name"] == "ستربس دجاج" for r in strips["results"]))
check_true("and they are chicken, not a burger relabel",
           all("chicken" in r["kind"] and "meat" not in r["kind"]
               for r in strips["results"]))
strips_spelling = search_meals(query="ستريبس")
check_true("ستريبس ASR spelling still finds ستربس",
           strips_spelling["total_matches"] > 0
           and strips_spelling["results"][0]["name"] == "ستربس دجاج")

check_true("a stopword-only query still falls back to results",
           search_meals(query="ابغى شي خفيف")["total_matches"] > 0)
check_true("a real dish still matches", search_meals(query="كبسة")["total_matches"] > 0)

print("\n── pronunciation respelling, with tashkeel kept everywhere ──")
# ستربس opens on a cluster Arabic cannot start a word with, so the TTS reads it
# wrong however it is voweled; "استربس" is the spelling that sounds right.
check("respelled", fix_pronunciation("تِبِي سْتِرِبْس؟"), "تِبِي أَسْتِرِبس؟")
# Glued prefixes keep the model's own marks; only the food stem is replaced.
check("stacked prefixes و+ب+ال", fix_pronunciation("وَبِالسْتِرِبْس"), "وَبِالأَسْتِرِبس")
check("single-letter prefix keeps its vowel",
      fix_pronunciation(sanitize_tashkeel("وَسْتِرِبْس")), "وَأَسْتِرِبس")
check("spelling variant folds to the same fix",
      fix_pronunciation("ستريبس"), "أَسْتِرِبس")
# The model voweled this as "مِقْرِمِش" (migrimish) and the TTS read it that way.
# The table's vowels win over whatever the model guessed.
check("mis-voweled word corrected",
      fix_pronunciation("بَرْجَر دَجاج مِقْرِمِش"), "بَرْجَر دَجاج مُقَرْمَش")
check("corrected through a prefix",
      fix_pronunciation("وَالمِقْرِمِش"), "وَالمُقَرْمَش")
check("loanword pizza lexicon", fix_pronunciation("بيتزا"), "بِيتْزا")
check("multi-word restaurant phrase",
      fix_pronunciation("اصيل برجر"), "أَصِيل بَرْجَر")
check("ordinary function words untouched",
      fix_pronunciation("المَجْمُوع سِتَّة وَأَرْبَعِين رِيال"),
      "المَجْمُوع سِتَّة وَأَرْبَعِين رِيال")

print("\n── tashkeel rules enforced in code, not left to the prompt ──")
# Adding these as prompt instructions was measured over ~170 words and made
# things worse (4.3% -> 6.4%); enforced here it is 7.8% -> 1.2%.
# See tests/tashkeel_audit.py.
check("final sukun removed", clean_for_speech("هَذا مِنْ الأَكْثَر"), "هَذا مِن الأَكْثَر.")
check("final shadda removed", clean_for_speech("لِأَنَّ"), "لِأَن.")
check("final fatha removed", clean_for_speech("هِيَ"), "هِي.")
check("single-letter word left bare", clean_for_speech("وَ سِتِّين"), "و سِتِّين.")
check("initial sukun word reduced to bare letters",
      clean_for_speech("شْوَي"), "شوي.")
# Double sukun is stripped, then the lexicon restores the Saudi spelling.
# Asserted on fix_pronunciation, not clean_for_speech: the full TTS pass now
# hands borrowed words to the voice in Latin script (see the section below).
check("double sukun then lexicon respell",
      fix_pronunciation(sanitize_tashkeel("بِيْتْزا")), "بِيتْزا")
check("broken word then respelled in one pass",
      fix_pronunciation(sanitize_tashkeel("سْتِرِبْس")), "أَسْتِرِبس")
check("correct text passes through untouched",
      clean_for_speech("أَبْشِر، عِنْدَك خِيارات زَيْنَة وَقَريبَة مِنْك."),
      "أَبْشِر، عِنْدَك خِيارات زَيْنَة وَقَريبَة مِنْك.")
check("tag-question comma becomes a period for falling prosody",
      clean_for_speech("عِنْدَنَا كَبْسَة لَحْم، تِبِيها؟"),
      "عِنْدَنا كَبْسَة لَحْم. تِبِيها؟")
check("pizza roma phrase keeps interior fatha",
      fix_pronunciation(sanitize_tashkeel("بِيتْزا رُومَا")),
      "بِيتْزا رُومَا")

print("\n── borrowed dish words reach the TTS in Latin script ──")
# Diacritizing a loanword describes an Arabic word that does not exist, and
# VoxCPM reads the invention literally. Latin script lets it use English
# phonetics instead. This happens at the TTS boundary only.
check("burger spoken as English", clean_for_speech("بَرْجَر دَجاج مُقَرْمَش"),
      "barger chicken مُقَرْمَش.")
check("multi-word loan phrase", clean_for_speech("تْشِيز كِيك"), "cheesecake.")
# The word this whole lexicon started with: the Arabic respelling still lost to
# the voice, so it now reaches the TTS in English like every other loanword.
check("strips spoken as English", clean_for_speech("سْتِرِبْس"), "strips.")
check("strips through a prefix", clean_for_speech("وَسْتِرِبْس"), "وَ strips.")
check("proclitic on a phrase", clean_for_speech("وَآيس كْرِيم فُسْتُق"),
      "وَ ice cream فُسْتُق.")
# A glued proclitic must not block the swap — "وبرجر" is the commonest form in
# running speech. The stem wins over the plausible parse: ب in "وبول" is the
# word, not a preposition.
check("proclitic split off", clean_for_speech("وَرامِن دَجاج"), "وَ ramen chicken.")

print("\n── spoken numbers repaired before they reach the voice ──")
# Live: carbs_say_ar was "خمسين" and the model spoke "ثَمْسين", not a word.
check("corrupted number snapped back",
      clean_for_speech("وَالكَرْب ثَمْسين"), "وَالكَرْب خَمْسين.")
# The repair must never drop the و — "خمس مئة وثمانين" is 580, "…ثمانين" is not.
check("waw survives the repair",
      clean_for_speech("خَمْس مئة وَثَمَانين"), "خَمْس مئة وَثَمانين.")
# Repair is by explicit table, never by similarity. A one-letter rule turned
# "الحين" — the commonest word in the dialect — into "ألفين", and the voice
# asked "وش تبي ندور ألفين؟".
check("ordinary speech is never read as a number",
      clean_for_speech("وِش تَبِي نِدَوِّر الحِين؟"), "وِش تِبِي نِدَوِّر الحِين؟")
check("and neither are near-miss words",
      clean_for_speech("أَبْغَى عَشا مِن سِنِين"), "أَبْغَى عَشا مِن سِنِين.")

print("\n── دجاج / برجر reach TTS as Latin phonetics ──")
# Soft-j دجاج and hard-g برجر: Arabic voweling still lost to the Hoda voice.
# دجاج → English "chicken" (Latin dajaaj still hard-g under language=Arabic).
# name_say_ar stays Arabic.
check("dajaj → chicken", clean_for_speech("دَجاج مَشْوِي"), "chicken مَشْوِي.")
check("respelled through ال", clean_for_speech("بِالدَّجاج"), "بِال chicken.")
check("burger + dajaj both latin",
      clean_for_speech("بَرْجَر الدَّجاج"), "barger ال chicken.")
check("phrase still matches, then latinized",
      clean_for_speech("شاورما دجاج"), "شاوِرْما chicken.")
check("observed model typo pinned too",
      clean_for_speech("صَفْحَة المَطَاعِع"), "صَفْحَة المَطَاعِم.")
check("bowl is not و+ب+ول", clean_for_speech("وَبُول كِينْوا"), "وَ bowl quinoa.")
check("stacked و+ب+ال", clean_for_speech("وَبِالبَرْجَر"), "وَبِال barger.")
check("لل proclitic latinizes burger",
      clean_for_speech("لِلْبَرْجَر"), "لِلْ barger.")
check("native Arabic is untouched",
      clean_for_speech("كَبْسَة لَحْم مَع أَرُز بُخارِي"),
      "كَبْسَة لَحْم مَع أَرُز بُخارِي.")
# The written surface the model and the user see stays Arabic.
check("name_say_ar stays Arabic", speakable_name("برجر دجاج مقرمش"),
      "بَرْجَر دَجاج مُقَرْمَش")

print("\n── tool payloads expose name_say_ar ──")
burger = search_meals(query="برجر دجاج مقرمش")["results"][0]
check("meal name_say_ar", burger["name_say_ar"],
      speakable_name(burger["name"]))
check_true("restaurant_say_ar present", bool(burger.get("restaurant_say_ar")))
details = meal_details(burger["meal_id"])
assert details is not None
check_true("addon name_say_ar present",
           all("name_say_ar" in a for a in details["addons"]))
bayt_spoken = restaurant_details("بيت الشواية")
assert bayt_spoken is not None
check("restaurant name_say_ar",
      bayt_spoken["name_say_ar"], "بَيْت الشَّوايَة")
check("speakable strips final marks",
      speakable_name("مُقَرْمَشْ"), "مُقَرْمَش")

print("\n── spoken numbers (the model gets these wrong on its own) ──")
check("32", number_to_arabic_words(32), "اثنين وثلاثين")
check("46", number_to_arabic_words(46), "ستة وأربعين")
check("780", number_to_arabic_words(780), "سبع مئة وثمانين")
check("2200", number_to_arabic_words(2200), "ألفين ومئتين")
check("sar", sar_words(8), "ثمانية ريال")

print("\n── restaurant pages ──")
bayt = restaurant_details("بيت الشواية")
check("Arabic name resolves to a page path", bayt["page_path"], "/restaurants/bayt-alshawaya")
check("mangled id resolves too",
      restaurant_details("aseel-burger-id")["page_path"], "/restaurants/aseel-burger")
check_true("popular dishes ride along so the follow-up needs no second call",
           len(bayt["popular_meals"]) == 3
           and all(m["meal_id"] and m["price_say_ar"] for m in bayt["popular_meals"]))
check("unknown restaurant returns None", restaurant_details("مطعم ما له وجود أبدا"), None)
check_true("spoken min order", bayt["min_order_say_ar"].endswith("ريال"))

print("\n── details carry what the addon flow needs ──")
details = meal_details("aseel-burger-classic")
check_true("details include addons with ids and spoken prices",
           bool(details) and bool(details["addons"])
           and all("addon_id" in a and "price_say_ar" in a for a in details["addons"]))
check_true("details include the frontend path",
           bool(details) and details["page_path"] == "/meals/aseel-burger-classic")

print("\n── a bundle is classified by what it contains ──")
# The grill combo's chicken is listed only in comboIncludes. Classifying without
# that field tagged it vegetarian and drink, and protein_type=meat then hid the
# card the user was pointing at on screen.
grill_combo = load_catalog().by_id("bayt-alshawaya-offer-1")
check_true("combo takes protein from its contents",
           "chicken" in grill_combo.protein_tags)
check_true("a side drink does not make a combo a drink",
           "drink" not in grill_combo.diet_tags)
check_true("a side salad does not make a combo vegetarian",
           "vegetarian" not in grill_combo.protein_tags)
ramen = [m for m in load_catalog().meals if m.name == "عرض رامن مع جيوزا"][0]
check_true("edamame in a bundle is not a vegetarian claim",
           "vegetarian" not in ramen.protein_tags)
wings_deal = next(m for m in load_catalog().meals if m.name == "عرض وينجز مع صوصات")
check_true("wings deal is chicken, not vegetarian from the fries",
           "chicken" in wings_deal.protein_tags
           and "vegetarian" not in wings_deal.protein_tags)

print("\n── an offer is a bundle, not a discount ──")
combo_row = search_meals(query="وجبة مشاوي فردية")["results"][0]
check_true("offer rows say which kind they are", combo_row["offer_kind"] == "combo")
check("no offerPrice means no discount", combo_row["has_discount"], False)
check_true("a claim rule rides along when there is no discount",
           "خصم" in combo_row["price_claim_rule_ar"])
check_true("bundle contents are itemised, not left to the model",
           "شيش طاووق" in combo_row["includes"])
plain = search_meals(query="شاورما دجاج", restaurant_id="مذاق الشام")["results"][0]
check("a plain menu item is not an offer", plain["is_offer"], False)

print("\n── headcount is stated only when the catalog states it ──")
family = [m for m in load_catalog().meals if m.name == "وجبة عائلية مشاوي لـ 3 أشخاص"][0]
check("headcount parsed from the name", family.serves, 3)
check("'لشخص واحد' parses as one", grill_combo.serves, 1)
check("an unstated headcount stays unknown, not one", plain["serves_people"], None)
check_true("and says so, so no number gets invented",
           "لا تقولين رقم" in plain["serves_rule_ar"])

print("\n── the user's words outrank the model's guess about them ──")
# Live failure: asked to open "وجبة مشاوي فردية", the model guessed
# protein_type=meat, the chicken combo was filtered out, and a buy-one-get-one
# kebab was read out as if it were the requested meal.
guessed = search_meals(query="مشاوي فردية", cuisine="مشاوي",
                       protein_type="meat", only_offers=True)
check("the named dish comes back first",
      guessed["results"][0]["name"], "وجبة مشاوي فردية")
check_true("and the dropped guess is reported",
           "protein_type" in guessed["dropped_filters"])

print("\n── 'not under these filters' is not 'not in the catalog' ──")
# Three turns of "ما عندنا شاورما" while shawarma sat on the menu at 25 riyals.
filtered = search_meals(query="كبة مقلية", only_offers=True)
check("no false absence claim", filtered.get("query_not_found"), None)
check_true("the dish is reported as existing",
           filtered["exists_but_filtered"]["count"] > 0)
check_true("the spoken sentence says it exists",
           "موجودة عندنا" in filtered["say_to_user_ar"])
check_true("an example carries its real price",
           filtered["exists_but_filtered"]["example_price_say_ar"].endswith("ريال"))
absent = search_meals(query="ناجتس")
check("a dish that truly does not exist still says so",
      absent["say_to_user_ar"], "ما عندنا ناجتس")

print("\n── digits and offer titles reach the TTS speakable ──")
check("a headcount in a name is spelled out",
      speakable_name("وجبة عائلية مشاوي لـ 3 أشخاص"),
      "وَجْبَة عائِلِيَّة مَشاوِي لِثَلاثَة أَشْخاص")
check_true("offer titles are no longer half-voweled",
           speakable_name("اشتري كباب واحصل على الثاني مجاناً").startswith("اِشْتَرِي"))
check_true("decimals are left alone",
           "1.5" in speakable_name("زيت ذرة 1.5 لتر"))
check_true("a given name is not respelled as a particle",
           speakable_name("علي") == "علي")

print("\n── a stated headcount drives the pick ──")
# Live failure: "بيتزا لثلاث أشخاص" was searched as bare "بيتزا" and a
# single-person combo was recommended to a table of three.
r = search_meals(query="بيتزا لثلاث أشخاص")
check("headcount parsed from words in the query", r["serves_requested"], 3)
check("the family meal for three wins", r["results"][0]["name"],
      "وجبة عائلية بيتزا لـ 3 أشخاص")
r = search_meals(query="بيتزا", serves=3)
check("explicit serves param does the same", r["results"][0]["name"],
      "وجبة عائلية بيتزا لـ 3 أشخاص")
r = search_meals(query="برجر لـ 6 أشخاص")
check_true("digit headcounts parse too", r["serves_requested"] == 6
           and r["results"][0]["serves_people"] == 6)
r = search_meals(query="شاورما", serves=6)
check("family shawarma for six is in the catalog now",
      r["results"][0]["serves_people"], 6)
check_true("and is actually shawarma",
           "شاورما" in r["results"][0]["name"])
r = search_meals(query="ستربس", serves=6)
check("no strips meal feeds six — said honestly, not papered over",
      r["no_meal_serves_requested"], True)
check_true("with advice to order multiples instead",
           "أكثر من وحدة" in r["serves_advice_ar"])
r = search_meals(query="بيتزا")
check("no headcount, no serves logic", r.get("serves_requested"), None)

print("\n── me + named family is three, not four ──")
# Live failure 2026-08-13: "أكل أنا وأمي وأبوي" ranked a popular mashawi tray
# for 4, then she doubled down ("يكفيكم أنت وأمك وأبوك ويزيد") instead of
# switching to an exact-3 meal.
r = search_meals(query="أكلة أنا وأمي وأبوي")
check("me+mom+dad is three people", r["serves_requested"], 3)
r = search_meals(query="حاجة عائلية أنا وأمي وأبويا ناكلها")
check("egyptian ابويا still counts as dad", r["serves_requested"], 3)
r = search_meals(query="أنا ومعايا خمسة")
check("me plus five others is six", r["serves_requested"], 6)
r = search_meals(query="وجبة عائلية", cuisine="سعودي", protein_type="meat", serves=3)
check("exact 3-person meal beats a popular tray for 4",
      r["results"][0]["serves_people"], 3)
check_true("not the 4-person mashawi tray",
           r["results"][0]["meal_id"] != "mashawi-alnakheel-9")
check_true("and the tool says pick the exact size",
           r.get("serves_exact_available") is True)

print("\n── spoken price matches the on-screen digits ──")
# Live failure: pizza-for-3 is 71 SAR on screen; she said 61 twice.
pizza3 = meal_details("amico-kitchen-offer-2")
check("family pizza for 3 is 71", pizza3["price_sar"], 71)
check("spoken form is seventy-one, not sixty-one",
      pizza3["price_say_ar"], "واحد وسبعين ريال")
check("full sentence is ready to read",
      pizza3["price_sentence_ar"], "السعر واحد وسبعين ريال")

print("\n── restaurant resolution prefers exact names over prefixes ──")
check("ماركت لقمة السريع is not ماركت لقمة",
      restaurant_details("ماركت لقمة السريع")["restaurant_id"], "luqma-express-mart")
check("the shorter name still resolves to itself",
      restaurant_details("ماركت لقمة")["restaurant_id"], "luqma-mart")

print("\n── sun letters assimilate after ال, and only after ال ──")
check("bare شواية has no leading shadda",
      speakable_name("شواية القصر"), "شَوايَة القَصْر")
check("ال + sun letter gets the shadda",
      speakable_name("بيت الدخان"), "بَيْت الدُّخان")
check("moon letters are left alone",
      speakable_name("نار الجمر"), "نار الجَمْر")

print("\n── Latin the model typed by itself goes back to Arabic ──")
check_true("PROTIN is not read as letters",
           "بروتين" in clean_for_speech("غِرَام PROTIN"))
check_true("but words we latinise on purpose stay Latin",
           "barger" in clean_for_speech("بَرْجَر دَجاج"))

print("\n── stall phrases and prompt leaks never reach TTS ──")
check("bare thought is silent", clean_for_speech("thought"), "")
check_true("stall promise stripped",
           "ثوان" not in clean_for_speech("أَبْشِر، ثَمَانِي ثَوَانِي وَأَرْجِع لَك."))
check_true("prompt rule never spoken",
           "ممنوع" not in clean_for_speech("لَا، مَمْنُوع الوَعْد المُؤَجَّل."))
check_true("meta correction before tool is dropped",
           "انت قلت" not in clean_for_speech(
               "لِثَمَانِيَة أَشْخاص؟ لَا، أَنْت قُلْت سِتَّة أَشْخاص."))
check_true("real answer survives artifact strip",
           "عائِلِي" in clean_for_speech(
               "ثَمَانِي ثَوَانِي وَأَرْجِع لَك. مَا عِنْدَنَا عَرْض عائِلِي."))

print("\n── ASR repairs for live mishearings ──")
check("الشوارع → شاورما in offer context",
      repair_asr_query("عرض على الشوارع"), "عرض شاورما")

print("\n── tool-call text never reaches TTS ──")
check_true("call:show_meal leak is silent",
           clean_for_speech('call:show_meal{meal_id:formaggio-offer-2,reason:test}<tool_call|>') == "")
check_true(
    "live pizza leak is silent too",
    clean_for_speech(
        'call:show_meal{meal_id:<|"|>amico-kitchen-offer-2<|"|>,'
        'reason:<|"|>هَذِهِ وَجْبَة.<|"|>}<tool_call|>'
    ) == "",
)
check_true("أضضت typo repaired",
           "أَضَفْت" in clean_for_speech("تَمّ. أَضَضْت لَك الوَجْبَة."))
check_true("تابونة ghost word never spoken",
           "تابون" not in clean_for_speech("أَبْشِر. فَتَحت لَك الصَّفحَة. تابونة"))
check_true("brace junk stripped",
           "{" not in clean_for_speech("أَبْشِر {meal_id}"))

print("\n── ASR repairs for cart navigation ──")
check("السالفة → السلة",
      repair_user_transcript("إي لأ روحي عالسالفة بقى."),
      "إي لأ روحي عالسلة بقى.")
check("الكارت → السلة",
      repair_user_transcript("افتح الكارت"),
      "افتح السلة")

print("\n── high-protein query is a filter, not a missing dish ──")
r = search_meals(query="أكل بروتين عالي")
check_true("بروتين عالي does not report query_not_found",
           not r.get("query_not_found"))
check_true("returns meals", r.get("total_matches", 0) > 0)
check_true("every shown meal meets the protein floor",
           all(x["protein_g"] >= HIGH_PROTEIN_MIN for x in r["results"]))
check_true("sorted protein_desc",
           r["results"][0]["protein_g"] >= r["results"][-1]["protein_g"])

print("\n── دسم is a food quality, not a missing dish ──")
# Live failure 2026-08-13: "حاجة دسمة" / "مليانة دهون" searched as query=دسمة
# and came back query_not_found, so she said "ما عندنا دسمة".
r = search_meals(query="دسمة")
check("دسمة is not a missing dish name", r.get("query_not_found"), None)
check_true("returns hearty meals", r.get("total_matches", 0) > 0)
check_true("intent is rich", "rich" in (r.get("intent_tags") or []))
check_true("top result is actually rich",
           r["results"][0]["fat_g"] >= 30 or r["results"][0]["calories"] >= 650)
r = search_meals(query="أكل دسم")
check_true("أكل دسم still finds food", r.get("total_matches", 0) > 0)
r = search_meals(query="مليانة دهون")
check("مليانة دهون is not query_not_found", r.get("query_not_found"), None)
check_true("and returns meals", r.get("total_matches", 0) > 0)
r = search_meals(query="دسمة", cuisine="سعودي", protein_type="meat",
                 max_price=100, serves=3)
check_true("guessed cuisine must not zero a دسمة search",
           r.get("total_matches", 0) > 0)
check("مديانة ASR → مليانة",
      repair_user_transcript("مديانة دهون"),
      "مليانة دهون")

print("\n── cart nutrition is summed in the tool, not by the model ──")
nut = cart_nutrition([
    {"quantity": 1, "calories": 400, "protein_g": 30, "carbs_g": 20, "fat_g": 10},
    {"quantity": 2, "calories": 250, "protein_g": 20, "carbs_g": 10, "fat_g": 5},
])
check("calories added correctly", nut["calories_total"], 900)
check("protein added with quantity", nut["protein_g_total"], 70)
check_true("spoken total is ready", "سعرة" in nut["nutrition_say_ar"])

print("\n── fried-chicken catalog (8 shops, 104 meals) ──")
check("مختبر الستربس resolves",
      restaurant_details("مختبر الستربس")["restaurant_id"], "strip-lab")
fried = search_meals(cuisine="فرايد تشيكن")
check("cuisine فرايد تشيكن returns the new shops", fried["total_matches"], 104)
check_true("family bucket for 6 is findable",
           search_meals(query="باكت", serves=6)["results"][0]["serves_people"] == 6)

print("\n" + "=" * 60)
if failures:
    print(f"{len(failures)} FAILURE(S):")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("ALL PASS")
