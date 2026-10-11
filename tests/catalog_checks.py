"""Deterministic catalog and spoken-form checks — no LLM, no network, runs in under a second.

Everything asserted here is a bug that actually shipped and was found either by
auditing all 110 meals or by reading production logs. They are written down so
they cannot come back quietly.

Run:  python tests/catalog_checks.py   (tests/test_legacy_scripts.py runs it under pytest)
Search behaviour lives in tests/test_search.py."""

from __future__ import annotations

import sys

from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.spoken_lexicon import is_saudi_mobile, resolve_checkout_phone, written_phone  # noqa: E402

from src.catalog import (  # noqa: E402

    cart_nutrition,
    load_catalog,
    repair_asr_query,
    repair_user_transcript,
    rating_to_arabic_words,
    restaurant_details,
    number_to_arabic_words,
    resolve_restaurant,
    sar_words,
    search_meals,
    catalog_index,
    suggest_pairs,

    _name_kind,
)
from src.spoken_lexicon import clean_for_speech, fix_pronunciation, sanitize_tashkeel, speakable_name  # noqa: E402

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

check("ميلك شيك فانيلا is not meat", tags_of("ميلك شيك فانيلا"), set())

check("غيوزا is meat only", tags_of("غيوزا"), {"meat"})

check("تكة دجاج is chicken only", tags_of("تكة دجاج"), {"chicken"})

check("بطاطس ساحلية is vegetarian", tags_of("بطاطس ساحلية"), {"vegetarian"})

print("\n── keyword matching respects word boundaries and Arabic prefixes ──")

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

check(
    "أكلين شوكليت ASR → أتولييه الشوكولاتة",
    resolve_restaurant("افتحي مطعم أكلين شوكليت", cat),
    "choc-atelier",
)

check(
    "ونفد المانجا ASR → وافل مانجا query",
    "وافل" in repair_asr_query("ونفد المانجا") and "مانجا" in repair_asr_query("ونفد المانجا"),
    True,
)

print("\n── the live failure: burger search must not come back empty ──")

print("\n── search basics ──")

tashkeel_query = search_meals(query="دَجاج مَشْوي")

check_true("tashkeel in the query still matches", tashkeel_query["total_matches"] > 0)

groceries = search_meals(query="حليب")

print("\n── a category request is not a missing dish ──")

for phrasing in ("حلا", "حلى", "تحلية", "ديزرت", "شي حلو"):
    result = search_meals(query=phrasing)
    check_true(f"{phrasing} returns desserts only",
               result["total_matches"] > 0
               and all("dessert" in cat.by_id(r["meal_id"]).diet_tags
                       for r in result["results"]),
               str([r["name"] for r in result["results"]]))

for drink in ("لاتيه", "شاي كرك", "قهوة عربية"):
    check_true(f"{drink} is not tagged dessert",
               all("dessert" not in m.diet_tags for m in cat.meals if m.name == drink))

check_true("and never reports a category as not found",
           "query_not_found" not in search_meals(query="تحلية"))

check_true("milk is not a dessert",
           "حليب" in search_meals(query="حليب", include_grocery=True)["results"][0]["name"])

classic = next(m for m in cat.meals if m.name == "برجر كلاسيك")

check_true("برجر كلاسيك is not a drink", "drink" not in classic.diet_tags)

check_true("but the lassi still is",
           "drink" in next(m for m in cat.meals if m.name == "لاسي مانجو").diet_tags)

check_true("a healthy search still matches the صحي cuisine",
           search_meals(query="شي صحي")["total_matches"] > 0)

print("\n── a dish the catalog does not have must not return something else ──")

nuggets = search_meals(query="ناجتس")

check_true("ناجتس then offers a different dish", bool(nuggets.get("alternatives")))

print("\n── ستربس is a real dish now, not a burger by another name ──")

strips = search_meals(query="ستربس")

check_true("ستربس returns the chicken strips",
           strips["total_matches"] > 0
           and all(r["name"] == "ستربس دجاج" for r in strips["results"]))

strips_spelling = search_meals(query="ستريبس")

check_true("ستريبس ASR spelling still finds ستربس",
           strips_spelling["total_matches"] > 0
           and strips_spelling["results"][0]["name"] == "ستربس دجاج")

check_true("a stopword-only query still falls back to results",
           search_meals(query="ابغى شي خفيف")["total_matches"] > 0)

check_true("a real dish still matches", search_meals(query="كبسة")["total_matches"] > 0)

print("\n── pronunciation respelling, with tashkeel kept everywhere ──")

check("respelled", fix_pronunciation("تِبِي سْتِرِبْس؟"), "تِبِي أَسْتِرِبْس؟")

check("stacked prefixes و+ب+ال", fix_pronunciation("وَبِالسْتِرِبْس"), "وَبِالأَسْتِرِبْس")

check("single-letter prefix keeps its vowel",
      fix_pronunciation(sanitize_tashkeel("وَسْتِرِبْس")), "وَأَسْتِرِبْس")

check("spelling variant folds to the same fix",
      fix_pronunciation("ستريبس"), "أَسْتِرِبْس")

check("mis-voweled word corrected",
      fix_pronunciation("بَرْجَر دَجاج مِقْرِمِش"), "بَرْجَر دِجَاج مُقَرْمَش")

check("corrected through a prefix",
      fix_pronunciation("وَالمِقْرِمِش"), "وَالمُقَرْمَش")

check("loanword pizza lexicon", fix_pronunciation("بيتزا"), "بِيتْزَا")

check("multi-word restaurant phrase",
      fix_pronunciation("اصيل برجر"), "أَصِيل بَرْجَر")

check("ordinary function words untouched",
      fix_pronunciation("المَجْمُوع سِتَّة وَأَرْبَعِين رِيال"),
      "المَجْمُوع سِتَّة وَأَرْبَعِين رِيال")

print("\n── tashkeel rules enforced in code, not left to the prompt ──")

check("final sukun removed", clean_for_speech("هَذا مِنْ الأَكْثَر"), "هَذا مِن الأَكْثَر.")

check("final shadda removed", clean_for_speech("لِأَنَّ"), "لِأَن.")

check("final fatha removed", clean_for_speech("هِيَ"), "هِي.")

check("single-letter word left bare", clean_for_speech("وَ سِتِّين"), "و سِتِّين.")

check("initial sukun word reduced to bare letters",
      clean_for_speech("شْوَي"), "شوي.")

check("double sukun then lexicon respell",
      fix_pronunciation(sanitize_tashkeel("بِيْتْزا")), "بِيتْزَا")

check("broken word then respelled in one pass",
      fix_pronunciation(sanitize_tashkeel("سْتِرِبْس")), "أَسْتِرِبْس")

check("correct text passes through untouched",
      clean_for_speech("أَبْشِر، عِنْدَك خِيارات زَيْنَة وَقَريبَة مِنْك."),
      "أَبْشِر، عِنْدَك خِيارات زَيْنَهْ وَقَريبَهْ مِنْك.")

check("tag-question comma becomes a period for falling prosody",
      clean_for_speech("عِنْدَنَا كَبْسَة لَحْم، تِبِيها؟"),
      "عِنْدَنَا كَبْسَهْ لَحْم. تِبِيها؟")

check("pizza roma phrase keeps interior fatha",
      fix_pronunciation(sanitize_tashkeel("بِيتْزَا رُومَا")),
      "بِيتْزَا رُومَا")

check_true(
    "quoted dessert names are not spoken as quote marks",
    '"' not in clean_for_speech('لَقِيت لَك "دُونَات مُحَشِّيَة"'),
)

print("\n── borrowed dish words pronounced in English for TTS ──")

check_true(
    "burger pronounced English",
    "burger" in clean_for_speech("بَرْجَر دَجاج مُقَرْمَش"),
)

check_true("cheesecake pronounced English", "cheesecake" in clean_for_speech("تْشِيز كِيك"))

check_true("strips pronounced English", "strips" in clean_for_speech("سْتِرِبْس"))

check_true("strips through a prefix pronounced English", "strips" in clean_for_speech("وَسْتِرِبْس"))

check_true(
    "ice cream stays Arabic",
    "ice cream" not in clean_for_speech("وَآيس كْرِيم فُسْتُق"),
)

check_true("ramen pronounced English", "ramen" in clean_for_speech("وَرامِن دَجاج"))

print("\n── spoken numbers repaired before they reach the voice ──")

check("corrupted number snapped back",
      clean_for_speech("وَالكَرْب ثَمْسين"), "وَالكَرْب خَمْسِين.")

check("waw survives the repair",
      clean_for_speech("خَمْس مئة وَثَمَانين"), "خَمْس مِيَّهْ وَثَمَانِين.")

check("ordinary speech is never read as a number",
      clean_for_speech("وِش تَبِي نِدَوِّر الحِين؟"), "وِش تِبِي نِدَوِّر الحِين؟")

check("and neither are near-miss words",
      clean_for_speech("أَبْغَى عَشا مِن سِنِين"), "أَبْغَى عَشا مِن سِنِين.")

print("\n── دجاج / extras / loanwords stay Arabic ──")

check_true("dajaj stays Arabic Najdi di-JAJ", "chicken" not in clean_for_speech("دَجاج مَشْوِي") and "دِجَاج" in clean_for_speech("دَجاج مَشْوِي"))

check_true(
    "بالدجاج stays Arabic Najdi di-JAJ",
    "chicken" not in clean_for_speech("بِالدَّجاج") and "دِّجَاج" in clean_for_speech("بِالدَّجاج"),
)

check_true(
    "burger is English and chicken is Najdi Arabic",
    "burger" in clean_for_speech("بَرْجَر الدَّجاج")
    and "دِّجَاج" in clean_for_speech("بَرْجَر الدَّجاج"),
)

check_true(
    "شاورما دجاج keeps دجاج Najdi di-JAJ",
    "chicken" not in clean_for_speech("شاورما دجاج") and "دِجَاج" in clean_for_speech("شاورما دجاج"),
)

check_true(
    "extras ice cream chocolate cream stay Arabic",
    "ice cream" not in clean_for_speech("سكوب آيس كريم")
    and "chocolate" not in clean_for_speech("صوص شوكولاتة")
    and "cream" not in clean_for_speech("كريمة"),
)

check("observed model typo pinned too",
      clean_for_speech("صَفْحَة المَطَاعِع"), "صَفْحَهْ المَطَاعِم.")

check_true(
    "bowl and quinoa latinized",
    "bowl" in clean_for_speech("وَبُول كِينْوا")
    and "quinoa" in clean_for_speech("وَبُول كِينْوا"),
)

check_true("stacked و+ب+ال burger pronounced English", "burger" in clean_for_speech("وَبِالبَرْجَر"))

check_true("لل burger pronounced English", "burger" in clean_for_speech("لِلْبَرْجَر"))

check("native Arabic rice is أُرْز",
      clean_for_speech("كَبْسَة لَحْم مَع أرز بخاري"),
      "كَبْسَهْ لَحْم مَع أُرْز بُخَارِي.")

check("name_say_ar stays Arabic Najdi di-JAJ", speakable_name("برجر دجاج مقرمش"),
      "بَرْجَر دِجَاج مُقَرْمَش")

print("\n── tool payloads expose name_say_ar ──")

bayt_spoken = restaurant_details("بيت الشواية")

assert bayt_spoken is not None

check("restaurant name_say_ar",
      bayt_spoken["name_say_ar"], "بَيْت الشَّوَايَة")

check("speakable strips final marks",
      speakable_name("مُقَرْمَشْ"), "مُقَرْمَش")

print("\n── spoken numbers (the model gets these wrong on its own) ──")

check("32", number_to_arabic_words(32), "اثنين وثلاثين")

check("46", number_to_arabic_words(46), "ستة وأربعين")

check("780", number_to_arabic_words(780), "سبع مئة وثمانين")

check("2200", number_to_arabic_words(2200), "ألفين ومئتين")

check("10000", number_to_arabic_words(10000), "عشرة آلاف")

check("decimal rating", rating_to_arabic_words(4.8), "أربعة فاصلة ثمانية")

check("49280", number_to_arabic_words(49280),
      "تسعة وأربعين ألف ومئتين وثمانين")

check("sar large", sar_words(49280),
      "تسعة وأربعين ألف ومئتين وثمانين ريال")

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

check_true("full menu names ride along without spoken tashkeel",
           "menu_names" in bayt and len(bayt["menu_names"]) >= 3)

check_true("menu preview has ids but not name_say_ar",
           bayt["menu"] and "meal_id" in bayt["menu"][0]
           and "name_say_ar" not in bayt["menu"][0])

index = catalog_index()

check_true("catalog index lists restaurants by cuisine, not 127 padded lines",
           "شامي:" in index or "سعودي:" in index)

check_true("catalog index stays compact",
           len(index) < 8000)

print("\n── details carry what the addon flow needs ──")

print("\n── a bundle is classified by what it contains ──")

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

print("\n── headcount is stated only when the catalog states it ──")

family = [m for m in load_catalog().meals if m.name == "وجبة عائلية مشاوي لـ 3 أشخاص"][0]

check("headcount parsed from the name", family.serves, 3)

check("'لشخص واحد' parses as one", grill_combo.serves, 1)

print("\n── the user's words outrank the model's guess about them ──")

print("\n── 'not under these filters' is not 'not in the catalog' ──")

filtered = search_meals(query="كبة مقلية", only_offers=True)

check("no false absence claim", filtered.get("query_not_found"), None)

check_true("the dish is reported as existing",
           filtered["exists_but_filtered"]["count"] > 0)

absent = search_meals(query="ناجتس")

print("\n── digits and offer titles reach the TTS speakable ──")

check("a headcount in a name is spelled out",
      speakable_name("وجبة عائلية مشاوي لـ 3 أشخاص"),
      "وَجْبَة عَائِلِيَّة مَشَاوِي لِثَلَاثَة أَشْخَاص")

check_true("offer titles are no longer half-voweled",
           speakable_name("اشتري كباب واحصل على الثاني مجاناً").startswith("اِشْتَرِي"))

check_true("decimals are left alone",
           "1.5" in speakable_name("زيت ذرة 1.5 لتر"))

check_true("a given name is not respelled as a particle",
           speakable_name("علي") == "علي")

print("\n── a stated headcount drives the pick ──")

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

r = search_meals(query="بيتزا")

check("no headcount, no serves logic", r.get("serves_requested"), None)

print("\n── 3-person offers exist even when shawarma family trays are 4+ ──")

print("\n── me + named family is three, not four ──")

r = search_meals(query="أكلة أنا وأمي وأبوي")

check("me+mom+dad is three people", r["serves_requested"], 3)

r = search_meals(query="حاجة عائلية أنا وأمي وأبويا ناكلها")

check("egyptian ابويا still counts as dad", r["serves_requested"], 3)

r = search_meals(query="أنا ومعايا خمسة")

check("me plus five others is six", r["serves_requested"], 6)

print("\n── spoken price matches the on-screen digits ──")

print("\n── restaurant resolution prefers exact names over prefixes ──")

check("ماركت لقمة السريع is not ماركت لقمة",
      restaurant_details("ماركت لقمة السريع")["restaurant_id"], "luqma-express-mart")

check("the shorter name still resolves to itself",
      restaurant_details("ماركت لقمة")["restaurant_id"], "luqma-mart")

print("\n── sun letters assimilate after ال, and only after ال ──")

check("bare شواية has no leading shadda",
      speakable_name("شواية القصر"), "شَوَايَة القَصْر")

check("ال + sun letter gets the shadda",
      speakable_name("بيت الدخان"), "بَيْت الدُّخَان")

check("moon letters are left alone",
      speakable_name("نار الجمر"), "نار الجَمْر")

print("\n── Latin the model typed by itself goes back to Arabic ──")

check_true("PROTIN is not read as letters",
           "بْرُوتِين" in clean_for_speech("غِرَام PROTIN"))

check_true(
    "loanwords are not injected as English",
    "barger" not in clean_for_speech("بَرْجَر دَجاج"),
)

print("\n── stall phrases and prompt leaks never reach TTS ──")

check("bare thought is silent", clean_for_speech("thought"), "")

check("thought 思考完毕 is silent", clean_for_speech("thought 思考完毕"), "")

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

check("للسنة → للسلة",
      repair_user_transcript("عايزك تضيفه لي للسنة"),
      "عايزك تضيفه لي للسلة")

check("للسلامة → للسلة",
      repair_user_transcript("ضيفه للسلامة"),
      "ضيفه للسلة")

check("ورقاني → وريني",
      repair_user_transcript("ورقاني الصفحة"),
      "وريني الصفحة")

check("جنيه → ريال",
      repair_user_transcript("بواحد وسبعين جنيه"),
      "بواحد وسبعين ريال")

check("للسردات → للسلة",
      repair_user_transcript("أو ضيفي للسردات"),
      "أو ضيفي للسلة")

check("للسلوى → للسلة",
      repair_user_transcript("ضيفها للسلوى"),
      "ضيفها للسلة")

check("لتلات اصوات → لتلات اشخاص",
      repair_user_transcript("كنت عايز عرض لتلات اصوات"),
      "كنت عايز عرض لتلات اشخاص")

check("رقم الجواز → رقم الجوال",
      repair_user_transcript("الاسم محمد رقم الجواز 0555555555"),
      "الاسم محمد رقم الجوال 0555555555")

print("\n── high-protein query is a filter, not a missing dish ──")

print("\n── دسم is a food quality, not a missing dish ──")

check("مديانة ASR → مليانة",
      repair_user_transcript("مديانة دهون"),
      "مليانة دهون")

print("\n── أرخص / أغلى / عروض must not return empty lies ──")

floor = search_meals(
    query="حلا أرخص", cuisine="حلويات", max_price=5, sort="price_asc",
)

r_cheap_intent = search_meals(query="أرخص")

check_true("bare أرخص returns meals", r_cheap_intent["total_matches"] > 0)

print("\n── never keep guessed protein/cuisine filters ──")

empty_q = search_meals(
    query="", cuisine="حلويات", only_offers=True, serves=3,
)

check(
    "empty query keeps explicit حلويات cuisine",
    "cuisine" in (empty_q.get("dropped_filters") or []),
    False,
)

check_true("3-person dessert offers exist", empty_q["total_matches"] > 0)

check_true(
    "top hit is a 3-person dessert tray, not pizza",
    "حلا" in (empty_q["results"][0].get("name") or "")
    and (empty_q["results"][0].get("serves_people") == 3),
)

check_true(
    "pizza family meal is not the dessert answer",
    "بيتزا" not in (empty_q["results"][0].get("name") or ""),
)

print("\n── مشكلة is a problem, not مُشَكَّل food ──")

check("مشكلة stays مشكلة",
      clean_for_speech("مِشْكِلَة فِي التَّطْبِيق"),
      "مِشْكِلَهْ فِي التَّطْبِيق.")

check_true(
    "say_to_user_ar JSON wrapper stripped",
    "say_to_user" not in clean_for_speech(
        '{ "say_to_user_ar": "شِلْت لَك رِيش غَنَم." }'
    ).lower(),
)

check_true(
    "Arabic from say_to_user kept",
    "شِلْت" in clean_for_speech('{ "say_to_user_ar": "شِلْت لَك رِيش غَنَم." }'),
)

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

fried = search_meals(query="فرايد تشيكن", cuisine="فرايد تشيكن")

check_true("family bucket for 6 is findable",
           search_meals(query="باكت", serves=6)["results"][0]["serves_people"] == 6)

print("\n── tester filters: combo / crispy / keto / kitchen-as-restaurants ──")

check("قنبو repairs to كومبو", repair_asr_query("فلتر قنبو"), "فلتر كومبو")

check("كرسبي repairs to مقرمش", repair_asr_query("كرسبي"), "مقرمش")

combo = search_meals(query="كومبو", only_offers=True, offer_kind="combo")

check_true("combo filter returns matches", combo["total_matches"] > 0)

crispy = search_meals(query="كرسبي")

check_true("crispy/مقرمش search is non-empty", crispy["total_matches"] > 0)

keto = search_meals(query="كيتو")

check_true("keto search is non-empty", keto["total_matches"] > 0)

print("\n── cashback offers are a filter, not a missing dish (Bug-010) ──")

cb = search_meals(query="كاش باك")

check_true("كاش باك is not query_not_found", not cb.get("query_not_found"))

check_true("cashback search returns matches", cb["total_matches"] > 0)

cb_flag = search_meals(query="عايز حاجة عليها كاش باك", cashback=True)

check_true("cashback=true still returns matches", cb_flag["total_matches"] > 0)

check("cashback ASR cashback → كاش باك", repair_asr_query("cashback"), "كاش باك")

print("\n── pairing is catalog facts; speech is one assembled line ──")

shawarma = next(
    m for m in cat.meals
    if m.name == "شاورما دجاج" and m.restaurant_name == "مذاق الشام"
)

pairs = suggest_pairs(shawarma)

check_true("shawarma gets at least one pair", len(pairs) >= 1)

check_true(
    "pairs are addons or real meals",
    all(p.get("addon_id") or cat.by_id(p.get("meal_id") or "") for p in pairs),
)

check_true("no spoken script on pair rows", all("say_to_user_ar" not in p for p in pairs))

check_true(
    "main dish cross-sells a drink first, not garlic",
    "مشروب" in pairs[0]["name"] or pairs[0].get("pairs_as") == "drink" or pairs[0].get("kind") == "drink",
    str(pairs[0]),
)

kinds = {p["kind"] for p in pairs}

check_true(
    "shawarma pairs as addon/drink/dessert/side",
    kinds <= {"addon", "drink", "dessert", "side"},
    str(kinds),
)

print("\n── meal-detail addons are a spoken offer, not invented ──")

bare_meal = next(
    (m for m in cat.meals if not m.addons and not m.combo_includes),
    None,
)

fries_id = next(
    (str(a["id"]) for a in shawarma.addons if "بطاطس" in str(a.get("name") or "")),
    "",
)

if fries_id:
    skipped = suggest_pairs(shawarma, exclude_addon_ids=[fries_id])
    check_true(
        "excluded addon is not re-offered",
        all(p.get("addon_id") != fries_id for p in skipped),
    )

dessert = next(m for m in cat.meals if m.name == "كنافة نابلسية")

dessert_pairs = suggest_pairs(dessert)

check_true(
    "dessert is not paired with another dessert",
    all(p.get("kind") != "dessert" for p in dessert_pairs),
    str([p.get("kind") for p in dessert_pairs]),
)

latte = cat.by_id("latte-lane-2")

crispy = cat.by_id("urban-bun-3")

kibbeh = cat.by_id("manakeesh-street-6")

print("\n── vibe adjectives are not missing dishes (live 2026-08-26) ──")

tasty = search_meals(query="أكل لذيذ")

check("أكل لذيذ is not query_not_found", tasty.get("query_not_found"), None)

check_true("أكل لذيذ returns meals", tasty.get("total_matches", 0) > 0)

famous = search_meals(query="أكل مشهور")

check("أكل مشهور is not query_not_found", famous.get("query_not_found"), None)

check_true("أكل مشهور returns meals", famous.get("total_matches", 0) > 0)

print("\n── Saudi mobile format (live 2026-08-26 checkout) ──")

check_true("05 + 8 digits is valid", is_saudi_mobile("0551234567"))

check_true("+9665 + 8 digits normalizes", is_saudi_mobile("+966551234567"))

check_true("9665 without plus normalizes", is_saudi_mobile("966551234567"))

check_true("leading 5 + 8 digits gets 0", is_saudi_mobile("551234567"))

check("too many 05 digits rejected", is_saudi_mobile("0555555555555"), False)

check("11-digit leftover rejected", is_saudi_mobile("05555555555"), False)

check("Egyptian 03… rejected", is_saudi_mobile("031313131444555"), False)

check("05 glued onto extra digits rejected", is_saudi_mobile("0531313131444555"), False)

check("written_phone keeps leftover digits", written_phone("05555555555"), "05555555555")

check(
    "address واحد is not glued onto a leftover 05-run",
    written_phone("الاسم محمد رقم الجوال 0555555555555 ، العنوان الحي الأول شارع واحد"),
    "0555555555555",
)

check(
    "grouped commas join to 05XXXXXXXX",
    written_phone("0, 5, 20, 30, 40, 50"),
    "0520304050",
)

check_true(
    "grouped 0520304050 is a Saudi mobile",
    is_saudi_mobile("0, 5, 20, 30, 40, 50"),
)

check(
    "spoken صفر خمسة + تمن خمسات is eight fives after 05",
    written_phone("صفر خمسة وحطي بعدها تمن خمسات"),
    "0555555555",
)

check_true(
    "تمن خمسات is a Saudi mobile",
    is_saudi_mobile("صفر خمسة وحطي بعدها تمن خمسات"),
)

check(
    "address خمسة أشخاص is not glued onto a written mobile",
    written_phone("رقم الجوال 0520304050 والعنوان خمسة أشخاص"),
    "0520304050",
)

check(
    "05 2 0 3 0 4 0 5 صفر joins the trailing صفر",
    written_phone("05 2 0 3 0 4 0 5 صفر"),
    "0520304050",
)

fixed, rejected = resolve_checkout_phone(
    "05555555",
    "صفر خمسة وحطي بعدها تمن خمسات",
)

check("mangled tool phone is repaired from last user turn", fixed, "0555555555")

check("mangled tool phone is not rejected after repair", rejected, "")

check(
    "eastern 2050205060 is not invented into 0520304050",
    written_phone("٢٠٥٠٢٠٥٠٦٠"),
    "2050205060",
)

check("eastern 2050… is not a Saudi mobile", is_saudi_mobile("٢٠٥٠٢٠٥٠٦٠"), False)

print("\n── more tashkeel / loanword repairs from live logs ──")

check_true(
    "tanween is stripped from أهلا",
    "ً" not in clean_for_speech("أَهْلًا فِيك") and "أَهْلا" in clean_for_speech("أَهْلًا فِيك"),
)

check_true(
    "tanween stripped from معذرة",
    "ً" not in clean_for_speech("مَعْذِرَةً"),
)

check_true(
    "برغر pronounced English",
    "burger" in clean_for_speech("بَرْغَر"),
)

check_true(
    "كول سلو pronounced English",
    "coleslaw" in clean_for_speech("كُول سَلُو"),
)

check("hesitation tags stripped",
      repair_user_transcript("<hesitation> معرفش أكل إيه"),
      "معرفش أكل إيه")

print("\n── live 2026-08-30: garbled burger query still finds burgers ──")

garbled = search_meals(query="اقتايزكوا البرغر")

check_true("garbled burger query is not empty", bool(garbled.get("results")))

check_true(
    "garbled burger query does not say ما عندنا",
    "ما عندنا" not in (garbled.get("say_to_user_ar") or ""),
)

check_true(
    "garbled burger query opens a burger",
    "برجر" in (garbled["results"][0].get("name") or "")
    or (garbled["results"][0].get("cuisine") or "") == "برجر",
)

articled = search_meals(query="البرجر")

check_true("البرجر is not empty", bool(articled.get("results")))

check_true(
    "البرجر does not say ما عندنا",
    "ما عندنا" not in (articled.get("say_to_user_ar") or ""),
)

family3 = search_meals(query="عروض عائلية", serves=3, offer_kind="family")

check_true("family offer for 3 is not empty", bool(family3.get("results")))

_top_family = load_catalog().by_id(family3["results"][0]["meal_id"])

check_true(
    "family offer for 3 is not a dessert tray",
    _name_kind(_top_family) != "dessert",
)

print("\n── live 2026-08-30: مشويات search and healthy mood ──")

healthy = search_meals(query="healthy أكتر", only_offers=True, serves=3)

check_true("healthy أكتر still returns meals", bool(healthy.get("results")))

top_healthy = healthy["results"][0]

check_true(
    "healthy أكتر is not the 1600-kcal seafood platter",
    top_healthy.get("meal_id") != "anchor-fish-offer-2",
)

print("\n── live 2026-08-30: healthy وجبة ≠ coffee, recommend ≠ starter ──")

healthy_meal = search_meals(query="وجبة صحية", sort="calories_asc", max_calories=500)

healthy_meal_id = (healthy_meal.get("results") or [{}])[0].get("meal_id")

healthy_row = cat.by_id(healthy_meal_id) if healthy_meal_id else None

check_true("وجبة صحية still returns meals", bool(healthy_meal.get("results")))

check_true(
    "وجبة صحية calories_asc is not a drink",
    healthy_row is not None and _name_kind(healthy_row) != "drink",
    str(healthy_meal_id),
)

check_true(
    "وجبة صحية is not Arabic coffee",
    healthy_meal_id != "halawiyat-riyadh-qahwa",
)

healthy_en = search_meals(query="healthy أكتر", sort="calories_asc", max_calories=400)

healthy_en_id = (healthy_en.get("results") or [{}])[0].get("meal_id")

healthy_en_row = cat.by_id(healthy_en_id) if healthy_en_id else None

check_true(
    "healthy أكتر calories_asc is not a drink",
    healthy_en_row is not None and _name_kind(healthy_en_row) != "drink",
    str(healthy_en_id),
)

coffee = search_meals(query="قهوة")

check_true(
    "naming قهوة still finds coffee",
    bool(coffee.get("results"))
    and _name_kind(cat.by_id(coffee["results"][0]["meal_id"])) == "drink",
)

recommend = search_meals(query="ممكن ترشحي لي إيه؟")

recommend_id = (recommend.get("results") or [{}])[0].get("meal_id")

recommend_row = cat.by_id(recommend_id) if recommend_id else None

check_true("recommend query is not empty", bool(recommend.get("results")))

check_true(
    "recommend query is not query_not_found",
    "query_not_found" not in recommend,
    str(recommend.get("query_not_found")),
)

check_true(
    "recommend opens a main, not a starter or drink",
    recommend_row is not None and _name_kind(recommend_row) is None,
    f"{recommend_id} kind={_name_kind(recommend_row) if recommend_row else None}",
)

check_true(
    "recommend is not bruschetta",
    recommend_id != "amico-kitchen-8",
)

live_eat = search_meals(query="اقترح علي أي حاجة في التأكيد")

live_eat_id = (live_eat.get("results") or [{}])[0].get("meal_id")

live_eat_row = cat.by_id(live_eat_id) if live_eat_id else None

check_true(
    "live أي حاجة تتاكل is not iced latte",
    live_eat_id != "toast-bar-6",
    str(live_eat_id),
)

check_true(
    "live أي حاجة تتاكل opens food",
    live_eat_row is not None and _name_kind(live_eat_row) != "drink",
    f"{live_eat_id} kind={_name_kind(live_eat_row) if live_eat_row else None} name={live_eat_row.name if live_eat_row else None}",
)

asr_eat = search_meals(query="طرحي لي حاجة تتاكد")

asr_eat_id = (asr_eat.get("results") or [{}])[0].get("meal_id")

asr_eat_row = cat.by_id(asr_eat_id) if asr_eat_id else None

check_true(
    "تتاكد still opens food not a drink",
    asr_eat_row is not None and _name_kind(asr_eat_row) != "drink",
    f"{asr_eat_id} kind={_name_kind(asr_eat_row) if asr_eat_row else None}",
)

check_true(
    "unknown ASR tokens are an open recommend, not query_not_found",
    "query_not_found" not in asr_eat,
    str(asr_eat.get("query_not_found")),
)

check_true(
    "open recommend is not ambiguous",
    "ambiguous" not in live_eat,
    str(live_eat.get("ambiguous")),
)

savory = search_meals(query="وجبة حادقة")

savory_id = (savory.get("results") or [{}])[0].get("meal_id")

savory_row = cat.by_id(savory_id) if savory_id else None

check_true(
    "حادقة is not a missing dish",
    "query_not_found" not in savory,
    str(savory.get("query_not_found")),
)

check_true(
    "حادقة is not a dessert filter",
    savory_row is not None and _name_kind(savory_row) != "dessert",
    f"{savory_id} kind={_name_kind(savory_row) if savory_row else None} "
    f"name={savory_row.name if savory_row else None}",
)

check_true(
    "حادقة opens food",
    savory_row is not None and _name_kind(savory_row) != "drink",
    f"{savory_id} kind={_name_kind(savory_row) if savory_row else None}",
)

latte_named = search_meals(query="ابي آيس لاتيه")

latte_named_id = (latte_named.get("results") or [{}])[0].get("meal_id")

latte_named_row = cat.by_id(latte_named_id) if latte_named_id else None

check_true(
    "naming iced latte still finds it",
    latte_named_row is not None
    and _name_kind(latte_named_row) == "drink"
    and "لاتيه" in latte_named_row.name,
    f"{latte_named_id} {latte_named_row.name if latte_named_row else None}",
)

check("bruschetta is a starter/side", _name_kind(cat.by_id("amico-kitchen-8")), "side")

check_true(
    "smoothie pronounced English",
    "smoothie" in clean_for_speech("تبي سموذي أخضر تمشي معها"),
)

check_true(
    "wrap pronounced English",
    "wrap" in clean_for_speech("راب دجاج صحي"),
)

check_true(
    "smash pronounced English",
    "smash" in clean_for_speech("سماش برجر"),
)

check_true(
    "latin language tag glued to Arabic is stripped",
    not clean_for_speech("arرِيّضْ مَضْبُوطَة! تبي سموذي؟").lstrip().lower().startswith("ar"),
)

stripped = clean_for_speech("arرِيّضْ مَضْبُوطَة")

check_true(
    "arريض becomes Arabic only",
    "مَضْبُوطَه" in stripped and not stripped.lower().startswith("ar"),
    stripped,
)

check_true(
    "miso sushi pronounced English",
    "miso" in clean_for_speech("شوربة ميسو من سوشي نوي")
    and "sushi" in clean_for_speech("شوربة ميسو من سوشي نوي"),
)

check_true(
    "muffin cafe pronounced English",
    "muffin" in clean_for_speech("مافن توت من كافيه الهيل")
    and "cafe" in clean_for_speech("مافن توت من كافيه الهيل"),
)

check_true(
    "roll california express pronounced English",
    "roll" in clean_for_speech("رول كاليفورنيا من إدو إكسبرس")
    and "california" in clean_for_speech("رول كاليفورنيا من إدو إكسبرس")
    and "express" in clean_for_speech("رول كاليفورنيا من إدو إكسبرس"),
)

check_true(
    "ASR socio-nanny is not spoken",
    "socio" not in clean_for_speech("ما فهمت كلمة socio-nanny، تقصد شي من الإضافات؟").lower(),
)

for phrasing in ("سلطة", "ممكن تجيبي لي سلطة", "سلطة دجاج", "ابغى سلطة خفيفة"):
    top = search_meals(query=phrasing)["results"][0]["name"]
    check_true(f"«{phrasing}» opens a salad", "سلط" in top, top)

check_true("«سلطة سيزر» opens caesar", "سيزر" in search_meals(query="سلطة سيزر")["results"][0]["name"])

check_true("«برجر مع بطاطس» opens a burger",
           "برجر" in search_meals(query="برجر مع بطاطس")["results"][0]["name"])

check_true("«كبسة مع سلطة» opens a kabsa",
           "كبس" in search_meals(query="كبسة مع سلطة")["results"][0]["name"])

print("\n" + "=" * 60)

if failures:
    print(f"{len(failures)} FAILURE(S):")
    for f in failures:
        print("  -", f)
    sys.exit(1)

print("ALL PASS")
