"""Canonical Saudi spoken forms for catalog names and loanwords.

The LLM emits partial tashkeel for TTS, but it invents wrong interior vowels.
VoxCPM then reads those vowels literally. This module is the source of truth for
how food words should sound: lookup ignores whatever diacritics the model chose
and replaces known words/phrases with vetted spellings (final letter bare).

``clean_for_speech`` in ``catalog`` applies this after mechanical tashkeel
sanitize. Tool payloads also expose ``name_say_ar`` so سلمى can read catalog
names verbatim the same way she reads ``price_say_ar``.
"""

from __future__ import annotations

import re
import unicodedata
from functools import lru_cache
from typing import Any, Optional

# Same folding as catalog.normalize_ar — kept local so this module has no
# import cycle with catalog (catalog imports speakable helpers from here).
_MARKS = re.compile("[ً-ْٰـ]")
_LETTER_FOLDS = str.maketrans(
    {
        "أ": "ا",
        "إ": "ا",
        "آ": "ا",
        "ٱ": "ا",
        "ة": "ه",
        "ى": "ي",
        "ئ": "ي",
        "ؤ": "و",
    }
)
_WORD_RE = re.compile(r"[ؠ-ٰٟ]+")
_TASHKEEL_CODEPOINTS = frozenset(range(0x064B, 0x0653))
# fathatan / dammatan / kasratan. إعراب, not dialect; live logs still emit
# "أَهْلًا" and "مَعْذِرَةً" even though the last-letter rule should have
# caught some of them (tanween often sits on the letter *before* a final alef).
_TANWEEN_CODEPOINTS = frozenset({0x064B, 0x064C, 0x064D})
_SUKUN = 0x0652


def normalize_key(text: Any) -> str:
    if text is None:
        return ""
    s = unicodedata.normalize("NFC", str(text))
    s = _MARKS.sub("", s)
    s = s.translate(_LETTER_FOLDS)
    return " ".join(s.split()).lower()


def _is_arabic_letter(ch: str) -> bool:
    return 0x0620 <= ord(ch) <= 0x064A


# Multi-word catalog names / phrases. Matched longest-first after normalize_key.
# Values follow the TTS rule: every letter diacritized except the last of each word.
SPOKEN_PHRASES: dict[str, str] = {
    # restaurants
    "بيت الشوايه": "بَيْت الشَّوَايَة",
    "مذاق الشام": "مَذَاق الشَام",
    "اصيل برجر": "أَصِيل بَرْجَر",
    "سوشي نوي": "سُوشِي نُوي",
    "حلويات الرياض": "حَلَوِيّات الرِّيَاض",
    "بيتزا روما": "بِيتْزَا رُومَا",
    "هندي ماسالا": "هِنْدِي مَاسَالَا",
    "دار الكبسه": "دَار الكَبْسَة",
    "ساحل الماكولات البحريه": "سَاحِل المَأْكُولَات البَحْرِيَّة",
    "بول الصحيه": "بُول الصِّحِّيَّة",
    "ماركت لقمه": "مَارْكِت لُقْمَة",
    "مختبر الستربس": "مُخْتَبَر السِّتْرِبْس",
    "بيت الباكت": "بَيْت البَاكِت",
    "كريسبي باكت": "كْرِيسْبِي بَاكِت",
    "ساحه المقرمش": "سَاحَة المُقَرْمَش",
    "فرايد العائله": "فْرَايْد العَائِلَة",
    "فخذ النار": "فَخْذ النَار",
    "الجناح الذهبي": "الجَنَاح الذَّهَبِي",
    "شارع الوينجز": "شَارِع الوِينْجِز",
    "ستربس دجاج": "أَسْتِرِبْس دِجَاج",
    "وينجز حاره": "وِينْجِز حَارَّة",
    # common multi-word meals / loan phrases
    "ميلك شيك": "مِيلْك شِيك",
    "ميلك شيك فانيلا": "مِيلْك شِيك فَانِيلَا",
    "برجر دجاج مقرمش": "بَرْجَر دِجَاج مُقَرْمَش",
    "برجر كلاسيك": "بَرْجَر كْلَاسِيك",
    "برجر باربكيو": "بَرْجَر بَارْبِكْيُو",
    "برجر مشروم": "بَرْجَر مَشْرُوم",
    "دبل تشيز": "دَبِل تْشِيز",
    "بطر تشيكن": "بَطَر تْشِيكِن",
    "طبق مشاوي عائلي": "طَبَق مَشَاوِي عَائِلِي",
    "مشكل مشاوي": "مُشَكَّل مَشَاوِي",
    "مشكل مقبلات": "مُشَكَّل مُقَبِّلَات",
    "مشكل بحري": "مُشَكَّل بَحْرِي",
    "شاورما دجاج": "شَاوِرْمَا دِجَاج",
    "شاورما لحم": "شَاوِرْمَا لَحْم",
    "كبسه دجاج": "كَبْسَة دِجَاج",
    "كبسه لحم": "كَبْسَة لَحْم",
    "كبسه عائليه": "كَبْسَة عَائِلِيَّة",
    "مندي دجاج": "مَنْدِي دِجَاج",
    "تكه دجاج": "تِكَّة دِجَاج",
    "دجاج علي الفحم": "دِجَاج عَلَى الفَحْم",
    "كفته لحم": "كُفْتَة لَحْم",
    "ارز بخاري": "أُرْز بُخَارِي",
    "كباب حلبي": "كَبَاب حَلَبِي",
    "كبده مشويه": "كِبْدَة مَشْوِيَّة",
    "شوربه حريره": "شُورْبَة حَرِيرَة",
    "صحن فلافل": "صَحْن فَلَافِل",
    "حمص باللحمه": "حُمُّص بِاللَّحْمَة",
    "كبه مقليه": "كِبَّة مَقْلِيَّة",
    "مناقيش زعتر": "مَنَاقِيش زَعْتَر",
    "فول مدمس": "فُول مُدَمَّس",
    "شوربه عدس": "شُورْبَة عَدَس",
    "بطاطس مقليه": "بَطَاطِس مَقْلِيَّة",
    "حلقات بصل": "حَلَقَات بَصَل",
    "وجبه كومبو": "وَجْبَة كُومْبُو",
    "وجبه اطفال": "وَجْبَة أَطْفَال",
    "رول سلمون": "رُول سَلَمُون",
    "نينيري تونا": "نِينِيرِي تُونَا",
    "رول تمبورا": "رُول تَمْبُورَا",
    "شوربه ميسو": "شُورْبَة مِيسُو",
    "دراغون رول": "دْرَاغُون رُول",
    "رول خضار": "رُول خُضَار",
    "سيت سوشي": "سِيت سُوشِي",
    "رامن دجاج": "رَامِن دِجَاج",
    "كنافه نابلسيه": "كُنَافَة نَابُلْسِيَّة",
    "بسبوسه بالقشطه": "بَسْبُوسَة بِالقِشْطَة",
    "بقلاوه مشكله": "بَقْلَاوَة مُشَكَّلَة",
    "تشيز كيك فراوله": "تْشِيز كِيك فَرَاوْلَة",
    "ام علي": "أُم عَلِي",
    "معمول تمر": "مَعْمُول تَمْر",
    "بوكس حلويات": "بُوكْس حَلَوِيّات",
    "قهوه عربيه": "قَهْوَة عَرَبِيَّة",
    "ايس كريم فستق": "آيْس كْرِيم فُسْتُق",
    "اربع اجبان": "أَرْبَع أَجْبَان",
    "بيتزا خضار": "بِيتْزَا خُضَار",
    "باستا بولونيز": "بَاسْتَا بُولُونِيز",
    "فيتوتشيني الفريدو": "فِيتُوتْشِينِي أَلْفَرِيدُو",
    "خبز بالثوم": "خُبْز بِالثُّوم",
    "بيتزا عائليه": "بِيتْزَا عَائِلِيَّة",
    "برياني دجاج": "بِرْيَانِي دِجَاج",
    "تكا مسايا": "تِكَّا مَسَايَا",
    "سمبوسه هنديه": "سَمْبُوسَة هِنْدِيَّة",
    "خبز نان": "خُبْز نَان",
    "دال هندي": "دَال هِنْدِي",
    "بانير تكا": "بَانِير تِكَّا",
    "ثالي هندي": "ثَالِي هِنْدِي",
    "لاسي مانجو": "لَاسِي مَانْجُو",
    "فيندالو لحم": "فِينْدَالُو لَحْم",
    "مطبق لحم": "مُطَبَّق لَحْم",
    "تمر بالقشطه": "تَمْر بِالقِشْطَة",
    "لبن طازج": "لَبَن طَازِج",
    "روبيان مشوي": "رُوبْيَان مَشْوِي",
    "سمك هامور": "سَمَك هَامُور",
    "كاليماري مقلي": "كَالِيمَارِي مَقْلِي",
    "ساندويتش سمك": "سَانْدَوِيتْش سَمَك",
    "شوربه سمك": "شُورْبَة سَمَك",
    "سلمون مشوي": "سَلَمُون مَشْوِي",
    "ارز بالروبيان": "أُرْز بِالرُّوبْيَان",
    "بطاطس ساحليه": "بَطَاطِس سَاحِلِيَّة",
    "بول كينوا": "بُول كِينْوَا",
    "سلطه دجاج مشوي": "سَلَطَة دِجَاج مَشْوِي",
    "توست افوكادو": "تُوسْت أَفُوكَادُو",
    "سموذي اخضر": "سَمُوذِي أَخْضَر",
    "بوكيه سلمون": "بُوكِيه سَلَمُون",
    "راب دجاج صحي": "رَاب دِجَاج صِحِّي",
    "شوربه خضار": "شُورْبَة خُضَار",
    "وجبه طاقه": "وَجْبَة طَاقَة",
    "بول فلافل": "بُول فَلَافِل",
    "زبادي بالعسل": "زَبَادِي بِالعَسَل",
    "ريش غنم": "رِيش غَنَم",
    "صلصه اضافيه": "صَلْصَة إِضَافِيَّة",
    "خبز اضافي": "خُبْز إِضَافِي",
}

# Single-token overrides. Keys are normalize_key results (no tashkeel, folded).
WORD_FIXES: dict[str, str] = {
    # existing production fixes
    # Prosthetic alef + interior vowels: bare "استربس" still makes VoxCPM stumble.
    "ستربس": "أَسْتِرِبْس",
    "ستريبس": "أَسْتِرِبْس",
    "ستريب": "أَسْتِرِبْس",
    "استربس": "أَسْتِرِبْس",
    "برجر": "بَرْجَر",
    "مقرمش": "مُقَرْمَش",
    # Words that make up the offer and family-meal titles. These are read out
    # verbatim from `name_say_ar`, and they arrived half-voweled — "اشتري كَباب
    # واحصل على الثاني مجانا" has tashkeel on one word out of six, which is
    # what makes the delivery wobble in the middle of a name. Catalog names
    # carry no diacritics of their own, so the recurring words are pinned here.
    "عرض": "عَرْض",
    "عايليه": "عَائِلِيَّة",
    "اشخاص": "أَشْخَاص",
    "شخص": "شَخْص",
    "مع": "مَع",
    "اشتري": "اِشْتَرِي",
    "واحصل": "وَاحْصَل",
    # "على" is deliberately absent: it folds to the same key as the given name
    # علي, and pinning it would respell someone's name back at them. An
    # everyday particle the TTS already reads correctly is not worth that.
    "مجانا": "مَجّانَا",
    "الثاني": "الثَّانِي",
    "الثانيه": "الثَّانِيَة",
    "كامله": "كَامِلَة",
    "فرديه": "فَرْدِيَّة",
    "بسعر": "بِسِعْر",
    "ثابت": "ثَابِت",
    "تشكيله": "تَشْكِيلَة",
    "خفيفه": "خَفِيفَة",
    "مياه": "مِيَاه",
    "عصير": "عَصِير",
    "حلا": "حَلَا",
    "فطور": "فُطُور",
    "مطبخ": "مَطْبَخ",
    "وجبه": "وَجْبَة",
    "اضافي": "إِضَافِي",
    "اضافيه": "إِضَافِيَّة",
    # Addon and side-dish vocabulary. Addons repeat on every meal, so these are
    # the words سلمى reads most often after the dish names themselves.
    "مخلل": "مُخَلَّل",
    "صغيره": "صَغِيرَة",
    "مشروب": "مَشْرُوب",
    "غازي": "غَازِي",
    "جبنه": "جِبْنَة",
    "زياده": "زِيَادَة",
    "كبيره": "كَبِيرَة",
    "مكسرات": "مُكَسَّرَات",
    "خبز": "خُبْز",
    "عربي": "عَرَبِي",
    "بيض": "بَيْض",
    "مسلوق": "مَسْلُوق",
    "يوناني": "يُونَانِي",
    "زنجبيل": "زَنْجَبِيل",
    "ليمون": "لَيْمُون",
    "مختبر": "مُخْتَبَر",
    "شارع": "شَارِع",
    "فحم": "فَحْم",
    "ركن": "رُكْن",
    "زقاق": "زُقَاق",
    # Restaurant-name vocabulary from the 119-restaurant catalog. Stored bare;
    # the proclitic path re-attaches ال and the sun-letter pass restores the
    # shadda (دخان → الدُّخان).
    "طاوله": "طَاوِلَة",
    "صندوق": "صُنْدُوق",
    "مجالس": "مَجَالِس",
    "دخان": "دُخَان",
    "سريع": "سَرِيع",
    "جمر": "جَمْر",
    "قصر": "قَصْر",
    "وادي": "وَادِي",
    "نخيل": "نَخِيل",
    "فرن": "فُرْن",
    "خفيف": "خَفِيف",
    "مقرمشه": "مُقَرْمَشَة",
    "مقرمشات": "مُقَرْمَشَات",
    # Common turn words the model misvowels (seen in live logs).
    "كثيره": "كَثِيرَة",
    "كثير": "كَثِير",
    "عندنا": "عِنْدَنَا",
    "عندك": "عِنْدَك",
    "عندهم": "عِنْدَهُم",
    "ابشر": "أَبْشِر",
    "تبي": "تِبِي",
    "تبيه": "تِبِيه",
    "ادور": "أَدَوِّر",
    "معين": "مُعَيَّن",
    "صنف": "صِنْف",
    # loanwords / brand-ish food terms
    "بيتزا": "بِيتْزَا",
    "سوشي": "سُوشِي",
    "كولا": "كُولَا",
    "ميلك": "مِيلْك",
    "شيك": "شِيك",
    "تشيز": "تْشِيز",
    "كيك": "كِيك",
    "باربكيو": "بَارْبِكْيُو",
    "مشروم": "مَشْرُوم",
    "تمبورا": "تَمْبُورَا",
    "رامن": "رَامِن",
    "كينوا": "كِينْوَا",
    "افوكادو": "أَفُوكَادُو",
    "بيبروني": "بِيبَرُونِي",
    "مارغريتا": "مَارْغَرِيتَا",
    "تيراميسو": "تِيرَامِيسُو",
    "برياني": "بِرْيَانِي",
    "بانير": "بَانِير",
    "لاسي": "لَاسِي",
    "فيندالو": "فِينْدَالُو",
    "كاليماري": "كَالِيمَارِي",
    "هامور": "هَامُور",
    "غيوزا": "غْيُوزَا",
    "ادمامي": "إِدَامَامِي",
    "نينيري": "نِينِيرِي",
    "دراغون": "دْرَاغُون",
    "سيت": "سِيت",
    "ميسو": "مِيسُو",
    "فيتوتشيني": "فِيتُوتْشِينِي",
    "الفريدو": "أَلْفَرِيدُو",
    "بولونيز": "بُولُونِيز",
    "كالزوني": "كَالْزُونِي",
    "سموذي": "سَمُوذِي",
    "توست": "تُوسْت",
    "كومبو": "كُومْبُو",
    "كلاسيك": "كْلَاسِيك",
    "دبل": "دَبِل",
    "تشيكن": "تْشِيكِن",
    "بوكيه": "بُوكِيه",
    "ساندويتش": "سَانْدَوِيتْش",
    "فانيلا": "فَانِيلَا",
    "مانجو": "مَانْجُو",
    "فراوله": "فَرَاوْلَة",
    "بوكس": "بُوكْس",
    "رول": "رُول",
    "بول": "بُول",
    "ماركت": "مَارْكِت",
    "ماسالا": "مَاسَالَا",
    "نوي": "نُوي",
    "روما": "رُومَا",
    # Arabic food terms the model often misvowels
    "مشوي": "مَشْوِي",
    "مشويه": "مَشْوِيَّة",
    "مقلي": "مَقْلِي",
    "مقليه": "مَقْلِيَّة",
    "مشكل": "مُشَكَّل",
    # Do NOT map مشكله/مشكلة here. normalize_key folds ة→ه, so the ordinary
    # word "مشكلة" (problem) would become the food adjective "مُشَكَّلَة".
    # Feminine dish forms are covered by SPOKEN_PHRASES ("بقلاوه مشكله").
    "محمص": "مُحَمَّص",
    "متبل": "مُتَبَّل",
    "مدخن": "مُدَخَّن",
    "مدمس": "مُدَمَّس",
    "كبسه": "كَبْسَة",
    "مندي": "مَنْدِي",
    "شاورما": "شَاوِرْمَا",
    "كفته": "كُفْتَة",
    "كباب": "كَبَاب",
    "حمص": "حُمُّص",
    "فلافل": "فَلَافِل",
    "فتوش": "فَتُّوش",
    "مناقيش": "مَنَاقِيش",
    "لقيمات": "لُقَيْمَات",
    "كنافه": "كُنَافَة",
    "بقلاوه": "بَقْلَاوَة",
    "بسبوسه": "بَسْبُوسَة",
    "معمول": "مَعْمُول",
    "مظبي": "مَظْبِي",
    "سليق": "سَلِيق",
    "جريش": "جَرِيش",
    "مطبق": "مُطَبَّق",
    "صياديه": "صَيّادِيَّة",
    "حريره": "حَرِيرَة",
    "فول": "فُول",
    "كبه": "كِبَّة",
    "تكه": "تِكَّة",
    "تكا": "تِكَّا",
    "بخاري": "بُخَارِي",
    "كبده": "كِبْدَة",
    "ريش": "رِيش",
    "غنم": "غَنَم",
    "مشاوي": "مَشَاوِي",
    "مقبلات": "مُقَبِّلَات",
    "عدس": "عَدَس",
    "شوربه": "شُورْبَة",
    "زعتر": "زَعْتَر",
    "قشطه": "قِشْطَة",
    "روبيان": "رُوبْيَان",
    "سلمون": "سَلَمُون",
    "تونا": "تُونَا",
    "بطاطس": "بَطَاطِس",
    "دجاج": "دِجَاج",
    "لحم": "لَحْم",
    "لحمه": "لَحْمَة",
    "سمك": "سَمَك",
    "خضار": "خُضَار",
    "سلطه": "سَلَطَة",
    "عائلي": "عَائِلِي",
    "عائليه": "عَائِلِيَّة",
    "لقمه": "لُقْمَة",
    "اصيل": "أَصِيل",
    # Stored bare (no leading shadda): standalone "شواية القصر" must not start
    # with a doubling mark. The ال re-attachment path below adds the sun-letter
    # shadda, so "الشوايه" still comes out "الشَّوايَة".
    "شوايه": "شَوَايَة",
    "حلويات": "حَلَوِيّات",
    "هندي": "هِنْدِي",
    "هنديه": "هِنْدِيَّة",
    "بحري": "بَحْرِي",
    "بحريه": "بَحْرِيَّة",
    "صحي": "صِحِّي",
    "صحيه": "صِحِّيَّة",
    "اضافي": "إِضَافِي",
    "اضافيه": "إِضَافِيَّة",
    "صلصه": "صَلْصَة",
    "فستق": "فُسْتُق",
    "تمر": "تَمْر",
    "زبادي": "زَبَادِي",
    "عسل": "عَسَل",
    "ثوم": "ثُوم",
    "بصل": "بَصَل",
    "نان": "نَان",
    "دال": "دَال",
    "ثالي": "ثَالِي",
    "مسابا": "مَسَايَا",
    "مسايا": "مَسَايَا",
    "سمبوسه": "سَمْبُوسَة",
    "نابلسيه": "نَابُلْسِيَّة",
    "عربيه": "عَرَبِيَّة",
    "قهوه": "قَهْوَة",
    "ارز": "أُرْز",
    "صحن": "صَحْن",
    "طبق": "طَبَق",
    "وجبه": "وَجْبَة",
    "حلقات": "حَلَقَات",
    "اطفال": "أَطْفَال",
    "طاقه": "طَاقَة",
    "اخضر": "أَخْضَر",
    "طازج": "طَازِج",
    "ساحليه": "سَاحِلِيَّة",
    "ساحل": "سَاحِل",
    "ماكولات": "مَأْكُولَات",
    "الرياض": "الرِّيَاض",
    "الشام": "الشَام",
    "بيت": "بَيْت",
    "مذاق": "مَذَاق",
    "دار": "دَار",
    "راب": "رَاب",
    "بطر": "بَطَر",
    "باستا": "بَاسْتَا",
    "ايس": "آيْس",
    "كريم": "كْرِيم",
}


def _is_pausal_taa(word: str) -> bool:
    """True when this word ends in a تاء مربوطة, written ة or already pausal.

    Checked on the word as it arrives, before any marks are stripped, so that
    re-running the sanitizer on its own output is a no-op. A bare final ``ه``
    is a root consonant (وَجْه, فِيه) and does not qualify — only a ``ه`` this
    module already marked with a sukun.
    """
    letters = [i for i, c in enumerate(word) if _is_arabic_letter(c)]
    if not letters:
        return False
    last = letters[-1]
    if word[last] == "ة":
        return True
    return word[last] == "ه" and chr(_SUKUN) in word[last + 1 :]


def _write_pausal_taa(word: str) -> str:
    """Rewrite the final letter as ``هْ``. The caller has checked the word."""
    letters = [i for i, c in enumerate(word) if _is_arabic_letter(c)]
    if not letters:
        return word
    last = letters[-1]
    # Drop whatever the model put after the letter, then write the sukun, so a
    # stray vowel cannot survive beside it.
    return word[:last] + "ه" + chr(_SUKUN) + _MARKS.sub("", word[last + 1 :])


def _pause_taa_marbuta(word: str) -> str:
    """Write a word-final تاء مربوطة as a pausal ``هْ``, when the word has one.

    Two rules meet on this letter. Word-final ة is pause /h/, not /t/, or the
    TTS flips between them. And it is the one final letter that carries a mark:
    every other word ends bare, because a vowel there is إعراب and makes the
    delivery sound like a news broadcast, while a sukun on the pausal haa says
    the opposite — stop here, no vowel — which is how a Najdi speaker ends the
    word. Left bare, VoxCPM guesses a vowel and reads لُقْمَه as "luqmat".

    Safe on any word: one that does not end in ة is returned untouched, so this
    may be mapped over a whole sentence.
    """
    return _write_pausal_taa(word) if _is_pausal_taa(word) else word


def _strip_final_marks(word: str) -> str:
    letters = [i for i, c in enumerate(word) if _is_arabic_letter(c)]
    if not letters:
        return word
    last = letters[-1]
    head, tail = word[: last + 1], word[last + 1 :]
    return head + _MARKS.sub("", tail)


def sanitize_tashkeel(text: str) -> str:
    """Enforce last-letter bare + drop phonotactically broken words to bare."""
    text = "".join(ch for ch in text if ord(ch) not in _TANWEEN_CODEPOINTS)

    def fix_word(match: re.Match[str]) -> str:
        word = match.group(0)
        letters = [i for i, c in enumerate(word) if _is_arabic_letter(c)]
        if not letters:
            return word

        marks: list[set[int]] = []
        for idx in letters:
            found: set[int] = set()
            j = idx + 1
            while j < len(word) and ord(word[j]) in _TASHKEEL_CODEPOINTS:
                found.add(ord(word[j]))
                j += 1
            marks.append(found)

        starts_with_sukun = _SUKUN in marks[0]
        double_sukun = any(
            _SUKUN in marks[i] and _SUKUN in marks[i + 1]
            for i in range(len(marks) - 1)
        )
        pausal_taa = _is_pausal_taa(word)
        if starts_with_sukun or double_sukun:
            word = _MARKS.sub("", word)
        else:
            word = _strip_final_marks(word)
        return _write_pausal_taa(word) if pausal_taa else word

    return _WORD_RE.sub(fix_word, text)


@lru_cache(maxsize=1)
def _phrases_longest_first() -> tuple[tuple[str, str], ...]:
    return tuple(
        sorted(SPOKEN_PHRASES.items(), key=lambda kv: len(kv[0].split()), reverse=True)
    )


def _replace_phrase_map(
    text: str, phrases: tuple[tuple[str, str], ...], allow_proclitic: bool = False
) -> str:
    """Replace known multi-word phrases; scan left-to-right, longest match wins.

    ``allow_proclitic`` lets a glued و/ب/ل/ك/ال on the first word survive the
    match ("وآيس كريم"), re-emitted ahead of the replacement. Off by default so
    SPOKEN_PHRASES keeps its exact-token behaviour.
    """
    # Work on a normalized parallel for matching, but rebuild from original spans
    # via word tokens to keep punctuation/spacing.
    words = list(_WORD_RE.finditer(text))
    if not words:
        return text

    keys = [normalize_key(m.group(0)) for m in words]
    n = len(words)
    out: list[str] = []
    cursor = 0
    i = 0
    while i < n:
        replaced = False
        for phrase_key, spoken in phrases:
            parts = phrase_key.split()
            L = len(parts)
            if i + L > n:
                continue
            if keys[i + 1 : i + L] != parts[1:]:
                continue
            lead = ""
            if keys[i] != parts[0]:
                if not allow_proclitic:
                    continue
                split = next(
                    (p for p, rest in _prefix_splits(keys[i]) if rest == parts[0]),
                    None,
                )
                if split is None:
                    continue
                lead = f"{_original_prefix(words[i].group(0), split)} "
            start = words[i].start()
            end = words[i + L - 1].end()
            out.append(text[cursor:start])
            out.append(f"{lead}{spoken}")
            cursor = end
            i += L
            replaced = True
            break
        if not replaced:
            i += 1
    out.append(text[cursor:])
    return "".join(out)


def _replace_phrases(text: str) -> str:
    return _replace_phrase_map(text, _phrases_longest_first())


def _original_prefix(word: str, prefix_letters: str) -> str:
    """Keep the model's tashkeel on a glued Arabic prefix (و/ف/ب/ل/ك/ال)."""
    if not prefix_letters:
        return ""
    needed = len(prefix_letters)
    taken = 0
    i = 0
    while i < len(word) and taken < needed:
        ch = word[i]
        if _is_arabic_letter(ch):
            taken += 1
            i += 1
            while i < len(word) and ord(word[i]) in _TASHKEEL_CODEPOINTS:
                i += 1
        else:
            i += 1
    # Do not strip marks on the prefix: وَ / بِ / فَ are real vowels on a
    # one-letter proclitic, not إعراب on a finished word. A bare one gets its
    # vowel: «وخمسين» copied bare from a tool was read with a bare و.
    prefix = word[:i] if i else prefix_letters
    if any(ord(c) in _TASHKEEL_CODEPOINTS for c in prefix):
        return prefix
    return _vowel_bare_prefix(prefix)


_PROCLITIC_VOWEL = {"و": "\u064e", "ف": "\u064e", "ك": "\u064e", "ب": "\u0650", "ل": "\u0650"}


def _vowel_bare_prefix(prefix: str) -> str:
    """وَ فَ كَ بِ لِ on a bare proclitic; the ال (or لل's article lam) stays bare."""
    if prefix.endswith("ال"):
        head = prefix[:-2]
    elif prefix == "لل":
        head = "ل"
    else:
        head = prefix
    return "".join(ch + _PROCLITIC_VOWEL.get(ch, "") for ch in head) + prefix[len(head):]


def fix_pronunciation(text: str) -> str:
    """Respell known words, then multi-word phrases (phrases win).

    Phrases run second on purpose: a word-level fix can be a weaker form of a
    catalog name (``شوايه`` vs ``الشَّوايَة``), and the full phrase must be the
    final spelling the TTS hears.
    """

    def replace(match: re.Match[str]) -> str:
        word = match.group(0)
        key = normalize_key(word)
        fixed = WORD_FIXES.get(key)
        if fixed is not None:
            return fixed

        # Prefixes stack: و + ب + ال in "وبالستربس".
        prefix_match = re.match(r"^([وف]?[بلك]?(?:ال)?)(.+)$", key)
        if prefix_match:
            prefix, rest = prefix_match.groups()
            if prefix and rest:
                fixed = WORD_FIXES.get(rest)
                if fixed is not None:
                    spoken_prefix = _original_prefix(word, prefix)
                    # ال assimilates into a sun letter: الدخان is ad-dukhaan,
                    # and the shadda that says so lives on the stem.
                    if prefix.endswith("ال"):
                        fixed = _assimilate_sun_letter(fixed)
                    return f"{spoken_prefix}{fixed}"
        return word

    return _replace_phrases(_WORD_RE.sub(replace, text))


# Tag-question openers that often follow a comma in سلمى's replies. The comma
# leaves a continuation rise on the previous clause; VoxCPM then sounds like it
# is about to keep talking when the turn actually ends.
_TAG_QUESTION_RE = re.compile(
    r"[،,]\s*(?=(?:تِ?بِ?(?:ي|يه)|وِ?ش|تَب(?:غى|ي)|ودّ?ك|تِرِيد))"
)
_TRAILING_OPEN_RE = re.compile(r"[،,;؛…]+(?:\s*[\"'»«)])*\s*$")
_HAS_TERMINAL_RE = re.compile(r"[.!?؟۔]\s*$")

# Stall phrases and leaked prompt text the model sometimes speaks before a tool
# returns (live failures 2026-08-12). Matched on normalize_key output.
_STALL_FOLDED_RE = re.compile(
    r"(?:"
    r"ثمان(?:ي|يه)?\s+ثوان|"
    r"\d+\s+ثوان|"
    r"ثوان(?:ي)?\s+(?:و\s*)?(?:ارجع|ارد)|"
    r"(?:ارجع|ارد)\s+لك|"
    r"انتظرني|"
    r"لحظ(?:ه|ات)\s+و|"
    r"\bلحظ(?:ه|ة)\b|"
    r"اشوف\s+لك|"
    r"ادور\s+لك|"
    r"ممنوع\s+الوعد|"
    r"الوعد\s+المؤجل|"
    r"انا\s+بدور\s+لك|"
    r"لاثمانيه\s+اشخاص|"
    r"انت\s+قلت\s+سته"
    r")"
)

# A capability ("I can look for meals") is not a promise to return later.
# Mask only an immediately modal lookup for stall detection, never the whole
# sentence: "أقدر أدور لك... انتظرني" must still trigger the wait rule.
_CAPABILITY_LOOKUP_RE = re.compile(
    r"\b(?:و?اقدر|و?نقدر|يمكنني|بامكاني)\s+"
    r"(?:(?:اني|ان|كمان|بعد|ايضا)\s+)?"
    r"(?:ادور\s+لك|اشوف\s+(?:لك|العروض)|اشيك(?:\s+لك)?)\b"
)

# Descriptions of how meal selection works are capabilities even without
# "أقدر". Match just the lookup words so explicit waits in the same sentence
# remain visible to the stall rules. Do not exempt a bare lookup promise.
_DESCRIPTIVE_LOOKUP_RE = re.compile(
    r"\b(?:ادور\s+لك|اشوف\s+لك|اشيك\s+لك)\b"
    r"(?=\s+(?:(?:على|علي)\s+)?(?:احسن\s+)?"
    r"(?:الوجبات|الوجبه|الخيارات|العروض)\s+حسب\b)"
)


def mask_capability_lookups(folded: str) -> str:
    """Exclude capability wording from stall checks, not from speech."""
    return _DESCRIPTIVE_LOOKUP_RE.sub(" ", _CAPABILITY_LOOKUP_RE.sub(" ", folded))


_BARE_TEMPLATE_WORDS = frozenset(
    {"thought", "analysis", "commentary", "final", "channel", "assistant", "system"}
)
# Gemma sometimes prints a tool invocation as plain text instead of using the
# function-calling channel (live failure 2026-08-12: the user heard
# "call:show_meal{meal_id:formaggio-offer-2...}").
_TOOL_CALL_TEXT_RE = re.compile(r"\bcall\s*:", re.IGNORECASE)
_TOOL_CALL_MARKER_RE = re.compile(r"<\s*tool_call\b", re.IGNORECASE)
_SENTENCE_PIECES_RE = re.compile(r"([.!?؟۔]+)")
# VoxCPM sometimes appends a ghost word after the falling breath at the end of
# a turn. The user hears it as "تابونة". Drop it from the text if the model
# ever writes it. Waveform energy cannot reliably identify a ghost word;
# the audio path preserves final speech instead of guessing.
_WEIRD_SPOKEN_RE = re.compile(
    r"(?:^|\s)(?:تابون[ةه]|طابون[ةه]|taboo?nah?|tabuna)(?:\s|$)",
    re.IGNORECASE,
)
_BRACE_JUNK_RE = re.compile(r"[{}|<>]+")
_LATEX_DISPLAY_RE = re.compile(r"\$\$(.*?)\$\$", re.DOTALL)
_LATEX_INLINE_RE = re.compile(r"\$(.*?)\$", re.DOTALL)
_LATEX_TEXT_CMD_RE = re.compile(r"\\text\s*\{(.*?)\}", re.DOTALL)
_LATEX_CMD_RE = re.compile(r"\\[a-zA-Z]+")
_MD_BOLD_RE = re.compile(r"\*\*(.*?)\*\*", re.DOTALL)
_MD_ITALIC_RE = re.compile(r"(?<!\*)\*(?!\*)(.*?)(?<!\*)\*(?!\*)", re.DOTALL)
_MD_CODE_RE = re.compile(r"`{1,3}")
_MD_HEADER_RE = re.compile(r"^#+\s+", re.MULTILINE)


def strip_markup_for_speech(text: str) -> str:
    """Drop LaTeX/markdown so TTS never reads $$\\text{...}$$ aloud."""
    out = str(text or "")
    if not out:
        return ""
    out = _LATEX_DISPLAY_RE.sub(lambda m: m.group(1), out)
    out = _LATEX_TEXT_CMD_RE.sub(lambda m: m.group(1), out)
    out = _LATEX_INLINE_RE.sub(lambda m: m.group(1), out)
    out = _LATEX_CMD_RE.sub("", out)
    out = _MD_BOLD_RE.sub(lambda m: m.group(1), out)
    out = _MD_ITALIC_RE.sub(lambda m: m.group(1), out)
    out = _MD_CODE_RE.sub("", out)
    out = _MD_HEADER_RE.sub("", out)
    out = out.replace("$$", " ").replace("$", " ")
    return " ".join(out.split()).strip()


# Live 2026-08-16: the model spoke the tool field wrapper itself —
# `{ "say_to_user_ar": "شِلْت لَك …" }` — instead of the Arabic alone.
_SAY_TO_USER_JSON_RE = re.compile(
    r"""\{\s*["']?say_to_user_ar["']?\s*:\s*["']([^"']+)["']\s*,?\s*\}""",
    re.IGNORECASE,
)
_JSON_KEY_NOISE_RE = re.compile(
    r"""["']?(?:say_to_user_ar|on_success|message|ok)["']?\s*:\s*""",
    re.IGNORECASE,
)
# Live 2026-08-17: Gemma leaked "thought 思考完毕" into the spoken turn.
_CJK_RE = re.compile(r"[\u3400-\u9fff\uf900-\ufaff]+")
_THOUGHT_LEAK_RE = re.compile(
    r"\b(?:thought|analysis|commentary|final|channel|assistant|system)\b"
    r"|思考完毕|思考中|思考",
    re.IGNORECASE,
)
# Live 2026-08-30: pair TTS started with "arرِيّضْ مَضْبُوطَة" — a language
# tag glued onto the first Arabic word.
_LATIN_GLUED_TO_ARABIC_RE = re.compile(
    r"(?i)(?:^|\s)[a-z]{1,6}(?=[\u0600-\u06FF])"
)
# Live 2026-08-30: ASR junk echoed back as English ("socio-nanny") and
# latinize_loanwords used to inject muffin/sushi/barger mid-sentence.
# After we stopped that swap, leftover Latin tokens are still garbage.
_STRAY_LATIN_TOKEN_RE = re.compile(
    r"(?i)(?<![A-Za-z0-9])[A-Za-z][A-Za-z0-9\-]{2,}(?![A-Za-z0-9])"
)
# Hyphenated English is ASR/channel junk (socio-nanny). Bare pizza/burger stays.
_HYPHEN_LATIN_RE = re.compile(
    r"(?i)(?<![A-Za-z0-9])[A-Za-z]+-[A-Za-z0-9\-]+(?![A-Za-z0-9])"
)


def strip_speech_artifacts(text: str) -> str:
    """Drop stall promises, leaked prompt rules, and bare template keywords.

    The model sometimes narrates its own instructions ("ممنوع الوعد المؤجل")
    or promises to come back ("ثماني ثواني وأرجع لك") while a tool is still
    running. Those must never reach the speaker.
    """
    raw = " ".join(str(text or "").split())
    if not raw:
        return ""
    raw = strip_markup_for_speech(raw)
    if not raw:
        return ""
    # Prefer the Arabic payload if the whole turn is a tool-field JSON blob.
    say_match = _SAY_TO_USER_JSON_RE.search(raw)
    if say_match:
        raw = say_match.group(1).strip()
    raw = _JSON_KEY_NOISE_RE.sub(" ", raw)
    raw = _CJK_RE.sub(" ", raw)
    raw = _THOUGHT_LEAK_RE.sub(" ", raw)
    raw = _LATIN_GLUED_TO_ARABIC_RE.sub(" ", raw)
    raw = _WEIRD_SPOKEN_RE.sub(" ", raw)
    raw = _BRACE_JUNK_RE.sub(" ", raw)
    raw = raw.replace("\"", " ").replace("'", " ").replace("«", " ").replace("»", " ")
    raw = " ".join(raw.split())
    if not raw:
        return ""
    if normalize_key(raw.rstrip(".")) in _BARE_TEMPLATE_WORDS:
        return ""
    # Failed function call rendered as assistant text — never speech.
    if _TOOL_CALL_TEXT_RE.search(raw) or _TOOL_CALL_MARKER_RE.search(raw):
        return ""

    pieces = _SENTENCE_PIECES_RE.split(raw)
    kept: list[str] = []
    i = 0
    while i < len(pieces):
        chunk = pieces[i].strip()
        delim = pieces[i + 1] if i + 1 < len(pieces) else ""
        if chunk:
            folded = normalize_key(chunk)
            if folded and not _STALL_FOLDED_RE.search(mask_capability_lookups(folded)):
                kept.append(chunk + delim)
        i += 2 if delim else 1

    return " ".join(s.strip() for s in kept if s.strip()).strip()


def finalize_prosody(text: str) -> str:
    """Close unfinished text while preserving intentional ellipsis pauses."""
    text = " ".join(str(text or "").split())
    if not text:
        return text
    # Use the requested three-dot pause spelling; leave decimals intact.
    text = re.sub(r"\.{2,}|…+", "...", text)
    text = _TAG_QUESTION_RE.sub(". ", text)
    text = _TRAILING_OPEN_RE.sub(".", text)
    if not _HAS_TERMINAL_RE.search(text):
        text = text + "."
    return text


# ── Loanwords pair-pitch treats as English stems (do not inject into TTS) ────
#
# The catalog writes borrowed dish names in Arabic script (برجر, تشيز كيك,
# رامن). Live 2026-08-30: swapping them to muffin / sushi / barger mid-sentence
# made VoxCPM (language=Arabic) throw garbage. ``clean_for_speech`` keeps the
# Arabic spelling. This table is only for ``_tts_latinizes`` so pair-pitch
# says «مشروب» then the Arabic name after a pause — one language at a time.
#
# Entries are keyed on normalize_key output, and every one is a word that
# actually appears in the catalog. False friends are deliberately absent:
# "دون" (also "without"), "توت" (berries, native), "كريم" (also a name — only
# mapped inside the "ايس كريم" phrase).
#
# A dish word only. "بروتين" was here and had to come out: it is a nutrition
# term سلمى says in ordinary sentences, and "وَالبْرُوتِين ثَلاثين" went to the
# voice as "وَال protein ثَلاثين" mid-answer. The test is whether the word names
# food on a menu, not whether it was borrowed.
LATIN_PHRASES: dict[str, str] = {
    # Live 2026-08-30: extras «آيس كريم / شوكولاتة / كريمة / دجاج» read as
    # English mid-Arabic and the customer could not understand the upsell.
    "تشيز كيك": "cheesecake",
    "تشيزكيك": "cheesecake",
    "كول سلو": "coleslaw",
    "دبل ديك": "double deck",
    "تشيز ستاك": "cheese stack",
    "ميلك شيك": "milkshake",
    "ميلكشيك": "milkshake",
    "باد تاي": "pad thai",
    "فيتوتشيني الفريدو": "fettuccine alfredo",
    "دبل تشيز": "double cheese",
    "بطر تشيكن": "butter chicken",
    "سي فود": "seafood",
    "سبرينغ رولز": "spring rolls",
    "تكا مسايا": "tikka masala",
    "فاير جرل": "fire grill",
    "اوربان بان": "urban bun",
    "ريب آي": "ribeye",
    "ريب اي": "ribeye",
    "ريب آي ستيك": "ribeye steak",
    "ريب اي ستيك": "ribeye steak",
    "فرنش فرايز": "french fries",
    "سلطة سيزر": "caesar salad",
}

LATIN_WORDS: dict[str, str] = {
    "برجر": "burger",
    "برغر": "burger",
    # Soft-j /dʒ/. Latin "dajaaj" still came out hard-g on Hoda under
    # language=Arabic (same trap as Arabic دَجاج). The English lexicon word
    # is the one spelling that reliably keeps the soft ج.
    "بيتزا": "pizza",
    "باستا": "pasta",
    "سوشي": "sushi",
    "رول": "roll",
    "رامن": "ramen",
    "تمبورا": "tempura",
    "ساشيمي": "sashimi",
    "جيوزا": "gyoza",
    "ادامامي": "edamame",
    "إدامامي": "edamame",
    "ستيك": "steak",
    "ستاك": "steak",
    "تريب": "steak",
    "تيرب": "steak",
    "ريستورانت": "restaurant",
    "ريستورنت": "restaurant",
    "ساندوتش": "sandwich",
    "ساندويتش": "sandwich",
    "ترياكي": "teriyaki",
    "كاتسو": "katsu",
    "تونكوستو": "tonkotsu",
    "موشي": "mochi",
    "كاليفورنيا": "california",
    "وافل": "waffle",
    "توست": "toast",
    "براوني": "brownie",
    "تيراميسو": "tiramisu",
    "كيك": "cake",
    "تشيز": "cheese",
    "لاتيه": "latte",
    "راب": "wrap",
    "سماش": "smash",
    "كينوا": "quinoa",
    "بول": "bowl",
    "ستاك": "steak",
    "تاكو": "taco",
    "كلاسيك": "classic",
    "كرسبي": "crispy",
    "ماشروم": "mushroom",
    "مشروم": "mushroom",
    "سويس": "swiss",
    "بيكون": "bacon",
    "تريبل": "triple",
    "دبل": "double",
    "سبايسي": "spicy",
    "رانش": "ranch",
    "باربكيو": "barbecue",
    "تشيكن": "chicken",
    "تشيكين": "chicken",
    "كولسلو": "coleslaw",
    "فانيلا": "vanilla",
    "مارغريتا": "margherita",
    "بيبروني": "pepperoni",
    "لازانيا": "lasagna",
    "برياني": "biryani",
    "ماسالا": "masala",
    "تيكا": "tikka",
    "تكا": "tikka",
    "كاري": "curry",
    "نان": "naan",
    "دال": "dal",
    "ثالي": "thali",
    "لاسي": "lassi",
    "مانجو": "mango",
    "كاليماري": "calamari",
    "سلمون": "salmon",
    "سالمون": "salmon",
    "بوكس": "box",
    "كومبو": "combo",
    "سيت": "set",
    "ميسو": "miso",
    "سموذي": "smoothie",
    "سموزي": "smoothie",
    # Fried chicken strips: the single word this table was originally built to
    # rescue. WORD_FIXES still respells it in Arabic first, so the post-fix key
    # "استربس" has to be here too or the Arabic spelling wins.
    "ستربس": "strips",
    "ستريبس": "strips",
    "ستريب": "strips",
    "استربس": "strips",
    "وينجز": "wings",
    "باكت": "bucket",
    "فرايد": "fried",
    "كريسبي": "crispy",
    # High-frequency addon vocabulary. صوص / آيس كريم / دجاج stay Arabic —
    # live extras read as "sauce chocolate, scoop ice cream, chicken".
    "كولا": "cola",
    "بارميزان": "parmesan",
    "صويا": "soya",
    "سويا": "soya",
    "رايتا": "raita",
    "شيبس": "chips",
    "تونه": "tuna",
    "تونا": "tuna",
    # Italian
    "كاربونارا": "carbonara",
    "بيني": "penne",
    "ارابياتا": "arrabbiata",
    "ريزوتو": "risotto",
    "سيزر": "caesar",
    "بروشيتا": "bruschetta",
    "كالتزوني": "calzone",
    "كالزوني": "calzone",
    "بولونيز": "bolognese",
    "الفريدو": "alfredo",
    "فيتوتشيني": "fettuccine",
    "موزاريلا": "mozzarella",
    # Indian
    "بالتي": "balti",
    "ماخاني": "makhani",
    "بانيير": "paneer",
    "بانير": "paneer",
    "صاموسا": "samosa",
    "سمبوسه": "samosa",
    "تشات": "chaat",
    "فيندالو": "vindaloo",
    "بسمتي": "basmati",
    "تندور": "tandoor",
    "مسايا": "masala",
    # Japanese / East Asian
    "نينيري": "nigiri",
    "غيوزا": "gyoza",
    "دراغون": "dragon",
    "واسابي": "wasabi",
    "نوري": "nori",
    "اومامي": "umami",
    "كيمتشي": "kimchi",
    "بيبيمباب": "bibimbap",
    "ساتيه": "satay",
    "نودلز": "noodles",
    "رولز": "rolls",
    "باو": "bao",
    "فو": "pho",
    "بوريتو": "burrito",
    "بوكيه": "poke",
    "ساكورا": "sakura",
    "طوكيو": "tokyo",
    # Bakery / dessert / cafe
    "كرواسون": "croissant",
    "سينابون": "cinnabon",
    "دونات": "donut",
    "دوناتس": "donut",
    "دونتس": "donut",
    "مافن": "muffin",
    "مولتن": "molten",
    "نوتيلا": "nutella",
    "كافيه": "cafe",
    "كريمري": "creamery",
    "اتولييه": "atelier",
    "ساندويتش": "sandwich",
    "كريسبي": "crispy",
    "باتي": "patty",
    # Brand-ish restaurant words
    "ماركت": "market",
    "اكسبرس": "express",
    "بلس": "plus",
    "جرل": "grill",
    "فاير": "fire",
    "يارد": "yard",
    "سبوت": "spot",
    "اوربان": "urban",
    "فييستا": "fiesta",
    "دلهي": "delhi",
    "بازيليكو": "basilico",
    "فيزوفيو": "vesuvio",
    "فورماجيو": "formaggio",
    "اميكو": "amico",
}


# Proclitics that can glue onto a noun, stacking as و + ب + ال ("وبالبرجر").
# "لل" is ل + ال after the alif elides (للبرجر = for the burger) — without it
# as its own form, "للبرجر" only splits to ل+لبرجر and never latinizes.
_PREFIX_RE = re.compile(r"^(?:[وف]?[بلك]?(?:ال)?|لل)$")


def _prefix_splits(key: str) -> list[tuple[str, str]]:
    """Candidate (proclitic, stem) splits, shortest proclitic first.

    Order matters. A single greedy regex reads "وبول" as و + ب + "ول" and loses
    the word, because ب is both a preposition and the first letter of بول
    (bowl). Trying the shortest prefix first means the longest surviving stem
    wins, and a stem that is actually in the lexicon beats a plausible parse.
    """
    out: list[tuple[str, str]] = []
    # Longest legal proclitic is "وبال" (4 letters), so cuts stop at 4.
    for cut in range(1, min(5, len(key))):
        prefix, rest = key[:cut], key[cut:]
        if rest and _PREFIX_RE.fullmatch(prefix):
            out.append((prefix, rest))
    return out


@lru_cache(maxsize=1)
def _latin_phrases_longest_first() -> tuple[tuple[str, str], ...]:
    return tuple(
        sorted(LATIN_PHRASES.items(), key=lambda kv: len(kv[0].split()), reverse=True)
    )


def latinize_loanwords(text: str) -> str:
    """Respell borrowed dish words in Latin script for the TTS.

    A glued Arabic proclitic is split off rather than blocking the swap:
    "وَرامِن" becomes "وَ ramen", which the voice reads as *wa ramen* — the
    natural pronunciation. Leaving it whole meant the commonest form of every
    loanword in running speech ("وبرجر", "بالبيتزا") never reached the English
    phonetics at all.
    """
    text = _replace_phrase_map(
        text, _latin_phrases_longest_first(), allow_proclitic=True
    )

    def replace(match: re.Match[str]) -> str:
        word = match.group(0)
        key = normalize_key(word)
        latin = LATIN_WORDS.get(key)
        if latin is not None:
            return latin

        for prefix, rest in _prefix_splits(key):
            latin = LATIN_WORDS.get(rest)
            if latin is not None:
                return f"{_original_prefix(word, prefix)} {latin}"
        return word

    return _WORD_RE.sub(replace, text)


# ── Spoken numbers, repaired ────────────────────────────────────────────────
#
# Prices and macros reach the model already spelled out in `*_say_ar`, but it
# retypes them to add tashkeel and occasionally slips a letter. Live: the tool
# returned carbs_say_ar="خمسين" and the voice said "ثَمْسين", which is not a
# word in any dialect. A wrong number is worse than a wrong vowel — it is a
# wrong order — so the closed vocabulary that number_to_arabic_words emits is
# repeated here and used to snap near-misses back.
#
# Values carry the canonical vowels, which also fixes the commoner case of the
# right word with the wrong tashkeel.
NUMBER_WORDS: dict[str, str] = {
    "واحد": "وَاحِد", "اثنين": "اثْنَيْن", "ثلاثه": "ثَلَاثَة", "اربعه": "أَرْبَعَة",
    "خمسه": "خَمْسَة", "سته": "سِتَّة", "سبعه": "سَبْعَة", "ثمانيه": "ثَمَانِيَة",
    "تسعه": "تِسْعَة", "عشره": "عَشَرَة", "عشرين": "عِشْرِين", "ثلاثين": "ثَلَاثِين",
    "اربعين": "أَرْبَعِين", "خمسين": "خَمْسِين", "ستين": "سِتِّين", "سبعين": "سَبْعِين",
    "ثمانين": "ثَمَانِين", "تسعين": "تِسْعِين", "الف": "أَلْف", "الفين": "أَلْفَيْن", "الاف": "آلَاف",
    # Keys are normalize_key forms: ئ folds to ي, so مئة/مية is "ميه" and مائة
    # "مايه". The key used to be "مياه", which is water: «تبي مياه معدنية؟»
    # was read «تِبِي مِئَهْ». Hundreds are said the Saudi way, مِيَّة / مِيتَيْن.
    "ميه": "مِيَّة", "مايه": "مِيَّة", "ميتين": "مِيتَيْن", "مايتين": "مِيتَيْن",
    # The rest of what number_to_arabic_words and sar_words emit («أحد عشر»,
    # «ثلاث مئة», «ريالين»), so a price copied bare from a tool is completed.
    "احد": "أَحَد", "اثنا": "اثْنَا", "عشر": "عَشَر", "ثلاث": "ثَلَاث", "اربع": "أَرْبَع",
    "خمس": "خَمْس", "ست": "سِت", "سبع": "سَبْع", "ثمان": "ثَمَان", "تسع": "تِسْع",
    "ريال": "رِيَال", "ريالين": "رِيَالَيْن",
}

# Observed corruptions, listed one by one. Nothing is guessed.
#
# An earlier version repaired anything within one letter of a number word.
# That is too clever: "الحِين" — the commonest word in the dialect — is one
# letter from "ألفين", and the voice asked "وش تبي ندور ألفين؟". Turning
# everyday speech into a number is a worse failure than the rare typo it was
# fixing, and no length or uniqueness guard rules it out, because both words
# are ordinary. Add entries here as they are seen in the logs.
NUMBER_TYPOS: dict[str, str] = {
    "ثمسين": "خَمْسِين",
}


def repair_number_words(text: str) -> str:
    """Normalise number-word vowels, and undo corruptions seen in production."""

    def lookup(key: str) -> Optional[str]:
        return NUMBER_TYPOS.get(key) or NUMBER_WORDS.get(key)

    def replace(match: re.Match[str]) -> str:
        word = match.group(0)
        key = normalize_key(word)
        fixed = lookup(key)
        if fixed is not None:
            return fixed
        # "وخمسين" / "بمية" are one word to the tokenizer but a proclitic +
        # number to the lexicon. Stems under three letters stay out: «بست»
        # is not «بِسِت».
        for prefix, rest in _prefix_splits(key):
            if "ال" in prefix or len(rest) < 3:
                continue
            fixed = lookup(rest)
            if fixed is not None:
                return f"{_original_prefix(word, prefix)}{fixed}"
        return word

    return _WORD_RE.sub(replace, text)


# Latin the model types by itself, mid-Arabic-sentence. Observed live:
# "وَفِيهَا ثَمَانِيَة وَعِشْرين غِرَام PROTIN" — the LLM romanised بروتين on its
# own and the TTS read the letters. Latinisation is this module's job and it
# happens after the model is done; anything Latin arriving *from* the model is a
# slip, so the known ones are put back into Arabic before latinize_loanwords
# runs. Listed explicitly, and kept disjoint from the words we deliberately
# romanise, so this never fights LATIN_WORDS.
ROMAN_STRAYS: dict[str, str] = {
    "protin": "بْرُوتِين",
    "protein": "بْرُوتِين",
    "calories": "سُعْرَة",
    "riyal": "رِيَال",
    "sar": "رِيَال",
}

_ROMAN_STRAY_RE = re.compile(r"(?<![A-Za-z])[A-Za-z]+(?![A-Za-z])")


def deromanize_strays(text: str) -> str:
    """Undo Latin words the model typed instead of Arabic."""
    if not text:
        return text

    def replace(match: re.Match[str]) -> str:
        return ROMAN_STRAYS.get(match.group(0).lower(), match.group(0))

    return _ROMAN_STRAY_RE.sub(replace, text)


# ── Words pinned to one spelling, whatever the model typed ──────────────────
#
# دجاج drifted between the Egyptian hard "g" and the classical ج المعطشة, the
# palatal /dʒ/ of the Levant, the Gulf and Quranic recitation. Fully voweling
# it — including the sukun on the final ج, which is otherwise banned — is what
# holds the voice to that reading.
#
# The final sukun is the one deliberate exception to the "no mark on the last
# letter" rule enforced in sanitize_tashkeel. It survives because this pass
# runs after that one, and nothing re-sanitizes afterwards.
#
# Running last also keeps phrase lookup intact: SPOKEN_PHRASES entries such as
# "شاورما دجاج" still match on the plain spelling before it is replaced.
DIALECT_RESPELL: dict[str, str] = {
    # Najdi pronunciation: دِجاج (di-JAJ) with kasra, never Modern Standard Arabic دَجاج
    "دجاج": "دِجَاج",
    "دجاجه": "دِجَاجَه",
    "دجاجة": "دِجَاجَه",
    # Model typos for words it must not mangle, same principle as NUMBER_TYPOS:
    # observed in the logs, never guessed. "رِجَّعْت لَك صَفْحَة المَطَاعِع".
    # Stored bare: the ال in "المطاعع" is re-attached by the proclitic path.
    "مطاعع": "مَطَاعِم",
    "اضضت": "أَضَفْت",
    "أضضت": "أَضَفْت",
    # Interior vowels the model gets wrong. sanitize_tashkeel only strips marks
    # off the *last* letter, so a wrong vowel in the middle survives every pass
    # and the TTS reads a different word. Counted over 198 live lines on
    # 2026-09-16: شَيء 28, كِل 5, مَن 3, كِلها 2, أُعَطِيك 1.
    "من": "مِن",          # "مَن كْرِيسْبِي يَارْد" was read /man/, not /min/
    "كل": "كُل",
    "كلها": "كِلَّهَا",     # missing shadda: read /kilha/
    "اعطيك": "أَعْطِيك",
    "أعطيك": "أَعْطِيك",
    # MSA/Egyptian forms of words سلمى already has a Najdi spelling for. The
    # prompt bans these and they still came through, so they are pinned here
    # too — same principle as دجاج above.
    "شيء": "شَي",
    "هذه": "هَذِي",
    "كده": "كِذَا",
    "كمان": "بَعَد",
}


_SHADDA = "\u0651"
# ال assimilates into a following sun letter: الدجاج is read ad-dajaaj, not
# al-dajaaj. The shadda that marks it sits on the stem, so re-attaching a
# stored bare stem to a kept ال drops it unless it is put back here.
_SUN_LETTERS = frozenset("تثدذرزسشصضطظلن")


def _assimilate_sun_letter(stem: str) -> str:
    if not stem or stem[0] not in _SUN_LETTERS:
        return stem
    marks = 1
    while marks < len(stem) and ord(stem[marks]) in _TASHKEEL_CODEPOINTS:
        marks += 1
    if _SHADDA in stem[1:marks]:
        return stem
    # NFC reorders the marks into canonical class order (fatha before shadda);
    # emitting them the other way round is equivalent but not what the rest of
    # the pipeline produces.
    return unicodedata.normalize("NFC", f"{stem[0]}{_SHADDA}{stem[1:]}")


def dialect_respell(text: str) -> str:
    """Final pass pinning specific words to a single spoken spelling."""

    def replace(match: re.Match[str]) -> str:
        word = match.group(0)
        key = normalize_key(word)
        fixed = DIALECT_RESPELL.get(key)
        if fixed is not None:
            return fixed
        for prefix, rest in _prefix_splits(key):
            fixed = DIALECT_RESPELL.get(rest)
            if fixed is not None:
                if prefix.endswith("ال"):
                    fixed = _assimilate_sun_letter(fixed)
                # Arabic proclitic glues straight onto an Arabic stem, unlike
                # the Latin path where the two need a space to be read apart.
                return f"{_original_prefix(word, prefix)}{fixed}"
        return word

    return _WORD_RE.sub(replace, text)


# Feminine customer addressing patterns that should always be converted to masculine
_FEMININE_ADDRESS_RE = (
    (re.compile(r"\bأَبْ?شِري\b", re.IGNORECASE), "أَبْشِر"),
    (re.compile(r"\bأبْ?شِري\b", re.IGNORECASE), "أَبْشِر"),
    (re.compile(r"\bيَا\s+حَبِيبَ[ةه]\b", re.IGNORECASE), "يا غالي"),
    (re.compile(r"\bالسِّ?مَاحَ[ةه]\s+مِنِّ?كِ?\b", re.IGNORECASE), "السماحة منك"),
    (re.compile(r"\bحَقَّ?كِ?\s+عَلَيَّ?\b", re.IGNORECASE), "حقك علي"),
    (re.compile(r"\bتِ?بِين\b", re.IGNORECASE), "تبي"),
    (re.compile(r"\bتَ?بِين\b", re.IGNORECASE), "تبي"),
    (re.compile(r"\bوِدِّ?كِ\b", re.IGNORECASE), "ودك"),
    (re.compile(r"\bلَكِ\b", re.IGNORECASE), "لَك"),
    (re.compile(r"\bعَلَيْكِ\b", re.IGNORECASE), "عَلَيْك"),
    (re.compile(r"\bمِنْكِ\b", re.IGNORECASE), "مِنْك"),
    (re.compile(r"\bتَطْلُبِين\b", re.IGNORECASE), "تطلب"),
    (re.compile(r"\bتِشُوفِين\b", re.IGNORECASE), "تشوف"),
    (re.compile(r"\bتَاخْذِين\b", re.IGNORECASE), "تاخذ"),
)


def sanitize_gender_for_male_customer(text: str) -> str:
    """Normalize any accidental feminine customer address into masculine forms."""
    if not text:
        return text
    res = text
    for pattern, replacement in _FEMININE_ADDRESS_RE:
        res = pattern.sub(replacement, res)
    return res


def clean_for_speech(text: str, *, arabic_only: bool = False) -> str:
    """Full TTS text pass: tashkeel rules, lexicon respell, loanword latinization, and prosody close.

    ``arabic_only`` is for the CPU Pocket TTS, which reads Arabic script only:
    no loanword latinization, and Latin words and digits arriving from the
    model are put into Arabic (see ``arabize_for_speech``).
    """
    if arabic_only:
        text = spell_digits(text)
    cleaned = strip_speech_artifacts(text)
    if not cleaned.strip():
        return ""
    cleaned = sanitize_gender_for_male_customer(cleaned)
    # Proclitic tatweel leftovers like الـ / بـ: glued back onto the next
    # Arabic word («بـ ستة» → «بستة» → «بِسِتَّهْ»). Left apart, a lone «ب» is
    # a bare letter the TTS can read as its name.
    cleaned = re.sub(r"(?<![ء-ي])([وبفل]?(?:ال)?)ـ\s*(?=[ء-ي])", r"\1", cleaned)
    # Strip tatweel (kashida) which breaks token matching and pronunciation
    cleaned = cleaned.replace("\u0640", "")
    cleaned = re.sub(r"\b([وبفل]?(?:ال)?)ـ\s*", r"\1 ", cleaned)
    if arabic_only:
        # «إدامامي (Edamame)»: keep the Arabic, drop the gloss. Done here, so
        # the lexicon passes below vowel whatever Arabic this produces.
        cleaned = re.sub(r"\s*\(([A-Za-z\s\-]+)\)", " ", cleaned)
        cleaned = arabize_for_speech(cleaned)
    # Collapse 1-2 transliterated Arabic words immediately followed by parenthesized English
    # e.g. "إدامامي (Edamame)" -> "Edamame", "تِرِب (Ribeye Steak)" -> "Ribeye Steak"
    cleaned = re.sub(r"[\u0600-\u06FF\-]+\s*\(([A-Za-z\s\-]+)\)", r" \1 ", cleaned)
    cleaned = re.sub(r"\(([A-Za-z\s\-]+)\)", r" \1 ", cleaned)

    spoken = dialect_respell(
        deromanize_strays(
            repair_number_words(fix_pronunciation(sanitize_tashkeel(cleaned)))
        )
    )
    # Ensure all forms of دجاج are strictly vocalized as Najdi دِجاج (di-JAJ)
    spoken = re.sub(
        r"(?<!\w)([وبفل]?)(ال)?د[َُِ]?ّ?ج[َُِ]?ا?ج([هة]?)",
        lambda m: f"{m.group(1)}{m.group(2) or ''}{'دِّ' if m.group(2) else 'دِ'}جَاج{m.group(3) or ''}",
        spoken,
    )
    spoken = sanitize_gender_for_male_customer(spoken)
    spoken = _WORD_RE.sub(lambda m: _pause_taa_marbuta(m.group(0)), spoken)
    spoken = _HYPHEN_LATIN_RE.sub(" ", spoken)
    if not arabic_only:
        spoken = latinize_loanwords(spoken)
    # Deduplicate repeated words (e.g. ribeye Ribeye Steak -> Ribeye Steak)
    spoken = re.sub(r"\b([A-Za-z]+)\s+\1\b", r"\1", spoken, flags=re.IGNORECASE)
    spoken = " ".join(spoken.split())
    return finalize_prosody(spoken)


# ── Arabic-only speech (CPU Pocket TTS) ─────────────────────────────────────
#
# Live 2026-10-15 on the CPU voice: «بَرْجَر … جِرْل بَرْجَر» went out as
# «burger … grill burger» (latinize_loanwords, built for VoxCPM) and the
# Pocket model, which only reads Arabic script, produced 14.9s of garbled audio
# for a ~6s sentence. It also cannot read digits such as «123». So in cpu mode
# nothing Latin and no digit reaches it: the loanword tables are run backwards,
# digits are spelled out, and any Latin word we have no Arabic for is dropped,
# since reading it would garble the whole request.

_LATIN_TOKEN_RE = re.compile(r"[A-Za-z]+(?:'[A-Za-z]+)?")
# A decimal is one number («1.5» → واحد فاصلة خمسة), not two.
_NUMBER_RE = re.compile(r"\d+(?:[.,٫]\d+)?")


@lru_cache(maxsize=1)
def _arabic_for_latin() -> tuple[tuple[tuple[str, ...], str], ...]:
    """Latin word sequences → Arabic, longest first. First Arabic spelling wins."""
    table: dict[tuple[str, ...], str] = {}
    for mapping in (LATIN_PHRASES, LATIN_WORDS, _NAME_TTS_LATIN):
        for arabic, latin in mapping.items():
            table.setdefault(tuple(latin.lower().split()), arabic)
    for latin, arabic in ROMAN_STRAYS.items():
        table.setdefault((latin,), arabic)
    return tuple(sorted(table.items(), key=lambda kv: len(kv[0]), reverse=True))


def _spell_number(token: str) -> str:
    from src.catalog import number_to_arabic_words  # catalog imports this module

    whole, _, fraction = re.sub(r"[,٫]", ".", token).partition(".")
    if not fraction:
        # Phone numbers and other long or zero-led digit strings are read digit
        # by digit, the way she reads a number back.
        if len(whole) >= 5 or (len(whole) > 1 and whole.startswith("0")):
            return digits_say_ar(whole)
        return number_to_arabic_words(int(whole))
    return f"{number_to_arabic_words(int(whole))} فاصلة {digits_say_ar(fraction)}"


def spell_digits(text: str) -> str:
    """Digits to Arabic words. Runs before sentence splitting, which cuts «4.5»."""
    if not text:
        return text
    text = text.translate(_ARABIC_INDIC_DIGITS)
    text = re.sub(r"\s*[×]\s*(?=\d)", " ", text)
    text = re.sub(r"(\d)\s*[%٪]", r"\1 بالمية", text)
    text = _NUMBER_RE.sub(lambda m: f" {_spell_number(m.group(0))} ", text)
    return _tidy_spaces(text)


def _tidy_spaces(text: str) -> str:
    return re.sub(r"\s+([،,.؟?!؛:])", r"\1", " ".join(text.split()))


def arabize_for_speech(text: str) -> str:
    """Arabic script only: Latin words to their Arabic spelling, digits to words."""
    if not text:
        return text
    text = spell_digits(text)
    matches = list(_LATIN_TOKEN_RE.finditer(text))
    if matches:
        table = _arabic_for_latin()
        out: list[str] = []
        pos = 0
        i = 0
        while i < len(matches):
            m = matches[i]
            out.append(text[pos:m.start()])
            replaced = False
            for latin, arabic in table:
                n = len(latin)
                window = matches[i:i + n]
                if len(window) < n:
                    continue
                # A phrase must be one run of words, not words across a clause.
                if any(text[a.end():b.start()].strip() for a, b in zip(window, window[1:])):
                    continue
                words = tuple(w.group(0).lower() for w in window)
                if words == latin or (n == 1 and words[0].endswith("s") and (words[0][:-1],) == latin):
                    out.append(arabic)
                    pos = window[-1].end()
                    i += n
                    replaced = True
                    break
            if not replaced:
                pos = m.end()  # no Arabic for it: drop rather than garble
                i += 1
        out.append(text[pos:])
        text = "".join(out)
    return _tidy_spaces(text)


# Counts written as digits inside catalog names. 75 family meals are named
# "وجبة عائلية مشاوي لـ 3 أشخاص", and the spoken form handed to the TTS kept the
# bare "3" — a Latin numeral in an Arabic sentence, read as a letter or dropped.
# The count is the whole point of those names, so it is spelled out.
_COUNT_WORDS: dict[str, str] = {
    "1": "وَاحِد", "2": "اثْنَيْن", "3": "ثَلَاثَة", "4": "أَرْبَعَة", "5": "خَمْسَة",
    "6": "سِتَّة", "7": "سَبْعَة", "8": "ثَمَانِيَة", "9": "تِسْعَة", "10": "عَشَرَة",
    "11": "إِحْدَى عَشَر", "12": "اثْنَا عَشَر", "30": "ثَلَاثِين",
}

# "لـ" is لام الجر glued on with a tatweel. Spoken it is لِ + the count.
_LAM_COUNT_RE = re.compile(r"لـ\s*(\d+)")
# Catalog extras write "جيوزا ×٢". TTS reads the multiply sign as junk.
_TIMES_COUNT_RE = re.compile(r"[×xX]\s*(\d+)")
# Decimals are left alone: "زيت ذرة 1.5 لتر" must not become "واحِد.خَمْسَة".
_COUNT_RE = re.compile(r"(?<![\d.])\d+(?![\d.])")


def speak_counts(text: str) -> str:
    """Spell out digit counts inside a name."""
    if not text:
        return text
    out = text.translate(_ARABIC_INDIC_DIGITS)
    out = _TIMES_COUNT_RE.sub(
        lambda m: f" {_COUNT_WORDS[m.group(1)]}" if m.group(1) in _COUNT_WORDS else m.group(0),
        out,
    )
    # Leftover × in "جيوزا ×اثْنَين" — TTS reads the sign as junk.
    out = re.sub(r"[×xX]\s*", " ", out)
    out = _LAM_COUNT_RE.sub(
        lambda m: f"لِ{_COUNT_WORDS[m.group(1)]}" if m.group(1) in _COUNT_WORDS else m.group(0),
        out,
    )
    return _COUNT_RE.sub(lambda m: _COUNT_WORDS.get(m.group(0), m.group(0)), out)


# Isolated catalog titles only. Live 2026-09-08: كاليماري / ميسو / سموذي / جيوزا
# were unreadable in Arabic script. Do not run the full LATIN_WORDS table here —
# that turned برجر into barger mid-pitch.
_NAME_TTS_LATIN = {
    "كاليماري": "calamari",
    "كالماري": "calamari",
    "كلماري": "calamari",
    "كاليمارى": "calamari",
    "ميسو": "miso",
    "سموذي": "smoothie",
    "سموزي": "smoothie",
    "جيوزا": "gyoza",
    "ادامامي": "edamame",
}


def _latinize_name_loans(text: str) -> str:
    if not text:
        return text

    def replace(match: re.Match[str]) -> str:
        word = match.group(0)
        key = normalize_key(word)
        latin = _NAME_TTS_LATIN.get(key)
        if latin is not None:
            return latin
        for prefix, rest in _prefix_splits(key):
            latin = _NAME_TTS_LATIN.get(rest)
            if latin is not None:
                return f"{_original_prefix(word, prefix)} {latin}"
        return word

    return _WORD_RE.sub(replace, text)


def speakable_name(name: str) -> str:
    """Canonical spoken form for a catalog restaurant/meal/addon name."""
    if not name:
        return ""
    # Names are fragments, not turns — skip prosody finalization so we do not
    # append a period onto every meal title in tool payloads.
    spoken = speak_counts(fix_pronunciation(sanitize_tashkeel(str(name))))
    return _latinize_name_loans(spoken)


_ARABIC_INDIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")


def written_text(value: Any) -> str:
    """Plain Arabic for anything the user will *read*, not hear.

    The prompt makes the model diacritize everything, which is right for the
    TTS and wrong for a form field: the checkout page showed "مُحَمَّد" and
    "الحَي الأَوَّل" because the spoken spelling was written straight into the
    input. Written surfaces get the bare spelling, with Arabic-Indic digits
    folded to ASCII so validation and delivery data stay machine-readable.
    """
    if value is None:
        return ""
    s = unicodedata.normalize("NFC", str(value))
    s = _MARKS.sub("", s)
    s = s.translate(_ARABIC_INDIC_DIGITS)
    return " ".join(s.split())


_SAUDI_MOBILE_LOCAL = re.compile(r"^05\d{8}$")
_SAUDI_MOBILE_E164 = re.compile(r"^\+9665\d{8}$")

# Longest first. Used only while reading a phone, not catalog names.
_PHONE_DIGIT_WORDS: tuple[tuple[str, str], ...] = (
    ("عشرين", "20"),
    ("تلاتين", "30"),
    ("ثلاثين", "30"),
    ("اربعين", "40"),
    ("أربعين", "40"),
    ("خمسين", "50"),
    ("ستين", "60"),
    ("سبعين", "70"),
    ("ثمانين", "80"),
    ("تسعين", "90"),
    ("عشرة", "10"),
    ("عشره", "10"),
    ("ثمانيه", "8"),
    ("ثمانية", "8"),
    ("تمانية", "8"),
    ("ثلاثة", "3"),
    ("ثلاثه", "3"),
    ("تلاتة", "3"),
    ("تلاته", "3"),
    ("اثنين", "2"),
    ("إثنين", "2"),
    ("اتنين", "2"),
    ("اثنان", "2"),
    ("اربعة", "4"),
    ("أربعة", "4"),
    ("اربعه", "4"),
    ("خمسة", "5"),
    ("خمسه", "5"),
    ("ستة", "6"),
    ("سته", "6"),
    ("سبعة", "7"),
    ("سبعه", "7"),
    ("تسعة", "9"),
    ("تسعه", "9"),
    ("واحد", "1"),
    ("صفر", "0"),
    ("تمن", "8"),
    ("ثمان", "8"),
    ("عشر", "10"),
)
_PHONE_REPEAT_RE = re.compile(
    r"(تمن|ثمان(?:يه|ية|ي)?|8)\s*(?:من\s+)?(خمسات)",
)


def _expand_spoken_phone_tokens(text: str) -> str:
    """Turn spoken/grouped phone tokens into digits. Format only, no names."""
    if not text:
        return ""
    out = unicodedata.normalize("NFC", str(text))
    out = _MARKS.sub("", out)
    out = out.translate(_ARABIC_INDIC_DIGITS)

    def _repeat(match: re.Match[str]) -> str:
        return "5" * 8

    out = _PHONE_REPEAT_RE.sub(_repeat, out)
    for word, digits in _PHONE_DIGIT_WORDS:
        out = re.sub(re.escape(word), digits, out)
    return out


def _kept_phone_digits(raw: str) -> str:
    kept = "".join(ch for ch in raw if ch.isdigit() or ch == "+")
    if kept.startswith("00966"):
        kept = "+" + kept[2:]
    if kept.startswith("966") and len(kept) == 12:
        kept = "+" + kept
    if kept.startswith("+9665") and len(kept) == 13:
        return "0" + kept[4:]
    if kept.startswith("9665") and len(kept) == 12:
        return "0" + kept[3:]
    if kept.startswith("5") and len(kept) == 9:
        return "0" + kept
    return kept


def _is_local_or_e164_mobile(digits: str) -> bool:
    return bool(
        _SAUDI_MOBILE_LOCAL.fullmatch(digits) or _SAUDI_MOBILE_E164.fullmatch(digits)
    )


def written_phone(value: Any) -> str:
    """Digits-only Saudi mobile, normalized to 05XXXXXXXX where possible.

    Live 2026-08-26: ``0, 5, 20, 30, 40, 50`` and ``صفر خمسة … تمن خمسات``
    must join into 05XXXXXXXX. Commas/spaces are separators, not extra digits.
    Already-written digits win so ``خمسة أشخاص`` in an address is not glued on.
    """
    text = written_text(value) if value is not None else ""
    if not text and value is not None:
        text = str(value)
    from_digits = _kept_phone_digits(text)
    if _is_local_or_e164_mobile(from_digits):
        return from_digits
    # Leftover 05-runs are already a phone attempt. Don't glue "واحد" from
    # an address onto 0555555555555.
    if from_digits.startswith("05") and len(from_digits) >= 10:
        return from_digits
    return _kept_phone_digits(_expand_spoken_phone_tokens(text))


_DIGIT_WORDS = ("صفر", "واحد", "اثنين", "ثلاثة", "أربعة", "خمسة", "ستة", "سبعة", "ثمانية", "تسعة")


def digits_say_ar(digits: str) -> str:
    """Digits one by one, as she reads a number back: «صفر خمسة واحد…»."""
    return " ".join(_DIGIT_WORDS[int(d)] for d in str(digits or "") if d.isdigit())


def resolve_checkout_phone(phone_arg: Any = "", last_user: Any = "") -> tuple[str, str]:
    """Saudi mobile from the tool arg, or from the last spoken turn.

    Returns ``(normalized, rejected)``. ``rejected`` is set only when the tool
    arg was a number that still is not 05XXXXXXXX after grouped/spoken parsing.
    """
    arg = written_phone(phone_arg) if phone_arg not in (None, "") else ""
    if is_saudi_mobile(arg):
        return arg, ""
    spoken = written_phone(last_user) if last_user not in (None, "") else ""
    if is_saudi_mobile(spoken):
        return spoken, ""
    if arg:
        return "", arg
    return "", ""


def is_saudi_mobile(value: Any) -> bool:
    """True for a Saudi mobile after ``written_phone`` (same shape as checkout).

    Accepts 05 + 8 digits, or +9665 / 9665 / 5… forms that normalize to that.
    Wrong length, a leading 01/03, or leftover digits after a 05 prefix fail.
    """
    digits = written_phone(value)
    return bool(
        _SAUDI_MOBILE_LOCAL.fullmatch(digits) or _SAUDI_MOBILE_E164.fullmatch(digits)
    )


# Back-compat alias used by older call sites / tests.
PRONUNCIATION_FIXES = WORD_FIXES
