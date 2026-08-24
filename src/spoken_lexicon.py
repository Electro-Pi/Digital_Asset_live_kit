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
    "بيت الشوايه": "بَيْت الشَّوايَة",
    "مذاق الشام": "مَذاق الشام",
    "اصيل برجر": "أَصِيل بَرْجَر",
    "سوشي نوي": "سُوشِي نُوي",
    "حلويات الرياض": "حَلَوِيّات الرِّياض",
    "بيتزا روما": "بِيتْزا رُومَا",
    "هندي ماسالا": "هِنْدِي ماسالَا",
    "دار الكبسه": "دار الكَبْسَة",
    "ساحل الماكولات البحريه": "ساحِل المَأْكُولات البَحْرِيَّة",
    "بول الصحيه": "بُول الصِّحِّيَّة",
    "ماركت لقمه": "مارْكِت لُقْمَة",
    "مختبر الستربس": "مُخْتَبَر الستربس",
    "بيت الباكت": "بَيْت الباكت",
    "كريسبي باكت": "كْريسْبي باكت",
    "ساحه المقرمش": "ساحَة المُقَرْمَش",
    "فرايد العائله": "فرايد العائِلَة",
    "فخذ النار": "فَخْذ النار",
    "الجناح الذهبي": "الجَناح الذَّهَبِي",
    "شارع الوينجز": "شارِع الوينجز",
    "ستربس دجاج": "أَسْتِرِبس دَجاج",
    "وينجز حاره": "وينجز حارَّة",
    # common multi-word meals / loan phrases
    "ميلك شيك": "مِيلْك شِيك",
    "ميلك شيك فانيلا": "مِيلْك شِيك فانِيلَا",
    "برجر دجاج مقرمش": "بَرْجَر دَجاج مُقَرْمَش",
    "برجر كلاسيك": "بَرْجَر كْلاسِيك",
    "برجر باربكيو": "بَرْجَر بارْبِكْيُو",
    "برجر مشروم": "بَرْجَر مَشْرُوم",
    "دبل تشيز": "دَبِل تْشِيز",
    "بطر تشيكن": "بَطَر تْشِيكِن",
    "طبق مشاوي عائلي": "طَبَق مَشاوِي عائِلِي",
    "مشكل مشاوي": "مُشَكَّل مَشاوِي",
    "مشكل مقبلات": "مُشَكَّل مُقَبِّلات",
    "مشكل بحري": "مُشَكَّل بَحْرِي",
    "شاورما دجاج": "شاوِرْما دَجاج",
    "شاورما لحم": "شاوِرْما لَحْم",
    "كبسه دجاج": "كَبْسَة دَجاج",
    "كبسه لحم": "كَبْسَة لَحْم",
    "كبسه عائليه": "كَبْسَة عائِلِيَّة",
    "مندي دجاج": "مَنْدِي دَجاج",
    "تكه دجاج": "تِكَّة دَجاج",
    "دجاج علي الفحم": "دَجاج عَلَى الفَحْم",
    "كفته لحم": "كُفْتَة لَحْم",
    "ارز بخاري": "أَرُز بُخارِي",
    "كباب حلبي": "كَباب حَلَبِي",
    "كبده مشويه": "كِبْدَة مَشْوِيَّة",
    "شوربه حريره": "شُورْبَة حَرِيرَة",
    "صحن فلافل": "صَحْن فَلافِل",
    "حمص باللحمه": "حُمُّص باللَّحْمَة",
    "كبه مقليه": "كِبَّة مَقْلِيَّة",
    "مناقيش زعتر": "مَناقِيش زَعْتَر",
    "فول مدمس": "فُول مُدَمَّس",
    "شوربه عدس": "شُورْبَة عَدَس",
    "بطاطس مقليه": "بَطاطِس مَقْلِيَّة",
    "حلقات بصل": "حَلَقات بَصَل",
    "وجبه كومبو": "وَجْبَة كُومْبُو",
    "وجبه اطفال": "وَجْبَة أَطْفال",
    "رول سلمون": "رُول سَلَمُون",
    "نينيري تونا": "نِينِيرِي تُونَا",
    "رول تمبورا": "رُول تَمْبُورَا",
    "شوربه ميسو": "شُورْبَة مِيسُو",
    "دراغون رول": "دْراغُون رُول",
    "رول خضار": "رُول خُضار",
    "سيت سوشي": "سِيت سُوشِي",
    "رامن دجاج": "رامِن دَجاج",
    "كنافه نابلسيه": "كُنافَة نابُلْسِيَّة",
    "بسبوسه بالقشطه": "بَسْبُوسَة بالقِشْطَة",
    "بقلاوه مشكله": "بَقْلاوَة مُشَكَّلَة",
    "تشيز كيك فراوله": "تْشِيز كِيك فَراوْلَة",
    "ام علي": "أُم عَلِي",
    "معمول تمر": "مَعْمُول تَمْر",
    "بوكس حلويات": "بُوكس حَلَوِيّات",
    "قهوه عربيه": "قَهْوَة عَرَبِيَّة",
    "ايس كريم فستق": "آيس كْرِيم فُسْتُق",
    "اربع اجبان": "أَرْبَع أَجْبان",
    "بيتزا خضار": "بِيتْزا خُضار",
    "باستا بولونيز": "باسْتَا بُولُونِيز",
    "فيتوتشيني الفريدو": "فِيتُوتْشِينِي أَلْفَرِيدُو",
    "خبز بالثوم": "خُبْز بالثُّوم",
    "بيتزا عائليه": "بِيتْزا عائِلِيَّة",
    "برياني دجاج": "بِرْيانِي دَجاج",
    "تكا مسايا": "تِكَّا مَسايَا",
    "سمبوسه هنديه": "سَمْبُوسَة هِنْدِيَّة",
    "خبز نان": "خُبْز نان",
    "دال هندي": "دال هِنْدِي",
    "بانير تكا": "بانِير تِكَّا",
    "ثالي هندي": "ثالِي هِنْدِي",
    "لاسي مانجو": "لاسِي مانْجُو",
    "فيندالو لحم": "فِيندالُو لَحْم",
    "مطبق لحم": "مُطَبَّق لَحْم",
    "تمر بالقشطه": "تَمْر بالقِشْطَة",
    "لبن طازج": "لَبَن طازِج",
    "روبيان مشوي": "رُوبْيان مَشْوِي",
    "سمك هامور": "سَمَك هامُور",
    "كاليماري مقلي": "كالِيمارِي مَقْلِي",
    "ساندويتش سمك": "سانْدَوِيتْش سَمَك",
    "شوربه سمك": "شُورْبَة سَمَك",
    "سلمون مشوي": "سَلَمُون مَشْوِي",
    "ارز بالروبيان": "أَرُز بالرُّوبْيان",
    "بطاطس ساحليه": "بَطاطِس ساحِلِيَّة",
    "بول كينوا": "بُول كِينْوا",
    "سلطه دجاج مشوي": "سَلَطَة دَجاج مَشْوِي",
    "توست افوكادو": "تُوست أَفُوكادُو",
    "سموذي اخضر": "سْمُوذِي أَخْضَر",
    "بوكيه سلمون": "بُوكِيه سَلَمُون",
    "راب دجاج صحي": "راب دَجاج صِحِّي",
    "شوربه خضار": "شُورْبَة خُضار",
    "وجبه طاقه": "وَجْبَة طاقَة",
    "بول فلافل": "بُول فَلافِل",
    "زبادي بالعسل": "زَبادِي بالعَسَل",
    "ريش غنم": "رِيش غَنَم",
    "صلصه اضافيه": "صَلْصَة إِضافِيَّة",
    "خبز اضافي": "خُبْز إِضافِي",
}

# Single-token overrides. Keys are normalize_key results (no tashkeel, folded).
WORD_FIXES: dict[str, str] = {
    # existing production fixes
    # Prosthetic alef + interior vowels: bare "استربس" still makes VoxCPM stumble.
    "ستربس": "أَسْتِرِبس",
    "ستريبس": "أَسْتِرِبس",
    "ستريب": "أَسْتِرِبس",
    "استربس": "أَسْتِرِبس",
    "برجر": "بَرْجَر",
    "مقرمش": "مُقَرْمَش",
    # Words that make up the offer and family-meal titles. These are read out
    # verbatim from `name_say_ar`, and they arrived half-voweled — "اشتري كَباب
    # واحصل على الثاني مجانا" has tashkeel on one word out of six, which is
    # what makes the delivery wobble in the middle of a name. Catalog names
    # carry no diacritics of their own, so the recurring words are pinned here.
    "عرض": "عَرْض",
    "عايليه": "عائِلِيَّة",
    "اشخاص": "أَشْخاص",
    "شخص": "شَخْص",
    "مع": "مَع",
    "اشتري": "اِشْتَرِي",
    "واحصل": "وَاحْصَل",
    # "على" is deliberately absent: it folds to the same key as the given name
    # علي, and pinning it would respell someone's name back at them. An
    # everyday particle the TTS already reads correctly is not worth that.
    "مجانا": "مَجّانا",
    "الثاني": "الثَّانِي",
    "الثانيه": "الثَّانِيَة",
    "كامله": "كامِلَة",
    "فرديه": "فَرْدِيَّة",
    "بسعر": "بِسِعْر",
    "ثابت": "ثابِت",
    "تشكيله": "تَشْكِيلَة",
    "خفيفه": "خَفِيفَة",
    "مياه": "مِياه",
    "عصير": "عَصِير",
    "حلا": "حَلا",
    "فطور": "فُطُور",
    "مطبخ": "مَطْبَخ",
    "وجبه": "وَجْبَة",
    "اضافي": "إِضافِي",
    "اضافيه": "إِضافِيَّة",
    # Addon and side-dish vocabulary. Addons repeat on every meal, so these are
    # the words سلمى reads most often after the dish names themselves.
    "مخلل": "مُخَلَّل",
    "صغيره": "صَغِيرَة",
    "مشروب": "مَشْرُوب",
    "غازي": "غازِي",
    "جبنه": "جِبْنَة",
    "زياده": "زِيادَة",
    "كبيره": "كَبِيرَة",
    "مكسرات": "مُكَسَّرات",
    "خبز": "خُبْز",
    "عربي": "عَرَبِي",
    "بيض": "بَيْض",
    "مسلوق": "مَسْلُوق",
    "يوناني": "يُونانِي",
    "زنجبيل": "زَنْجَبِيل",
    "ليمون": "لَيْمُون",
    "مختبر": "مُخْتَبَر",
    "شارع": "شارِع",
    "فحم": "فَحْم",
    "ركن": "رُكْن",
    "زقاق": "زُقاق",
    # Restaurant-name vocabulary from the 119-restaurant catalog. Stored bare;
    # the proclitic path re-attaches ال and the sun-letter pass restores the
    # shadda (دخان → الدُّخان).
    "طاوله": "طاوِلَة",
    "صندوق": "صُنْدُوق",
    "مجالس": "مَجالِس",
    "دخان": "دُخان",
    "سريع": "سَرِيع",
    "جمر": "جَمْر",
    "قصر": "قَصْر",
    "وادي": "وادِي",
    "نخيل": "نَخِيل",
    "فرن": "فُرْن",
    "خفيف": "خَفِيف",
    "مقرمشه": "مُقَرْمَشَة",
    "مقرمشات": "مُقَرْمَشات",
    # Common turn words the model misvowels (seen in live logs).
    "كثيره": "كَثِيرَة",
    "كثير": "كَثِير",
    "عندنا": "عِنْدَنا",
    "عندك": "عِنْدَك",
    "عندهم": "عِنْدَهم",
    "ابشر": "أَبْشِر",
    "تبي": "تِبِي",
    "تبيه": "تِبِيه",
    "ادور": "أَدَوِّر",
    "معين": "مُعَيَّن",
    "صنف": "صِنْف",
    # loanwords / brand-ish food terms
    "بيتزا": "بِيتْزا",
    "سوشي": "سُوشِي",
    "كولا": "كُولا",
    "ميلك": "مِيلْك",
    "شيك": "شِيك",
    "تشيز": "تْشِيز",
    "كيك": "كِيك",
    "باربكيو": "بارْبِكْيُو",
    "مشروم": "مَشْرُوم",
    "تمبورا": "تَمْبُورَا",
    "رامن": "رامِن",
    "كينوا": "كِينْوا",
    "افوكادو": "أَفُوكادُو",
    "بيبروني": "بِيبَرُونِي",
    "مارغريتا": "مارْغَرِيتَا",
    "تيراميسو": "تِيرامِيسُو",
    "برياني": "بِرْيانِي",
    "بانير": "بانِير",
    "لاسي": "لاسِي",
    "فيندالو": "فِيندالُو",
    "كاليماري": "كالِيمارِي",
    "هامور": "هامُور",
    "غيوزا": "غْيُوزَا",
    "ادمامي": "إِدامامِي",
    "نينيري": "نِينِيرِي",
    "دراغون": "دْراغُون",
    "سيت": "سِيت",
    "ميسو": "مِيسُو",
    "فيتوتشيني": "فِيتُوتْشِينِي",
    "الفريدو": "أَلْفَرِيدُو",
    "بولونيز": "بُولُونِيز",
    "كالزوني": "كالْزُونِي",
    "سموذي": "سْمُوذِي",
    "توست": "تُوست",
    "كومبو": "كُومْبُو",
    "كلاسيك": "كْلاسِيك",
    "دبل": "دَبِل",
    "تشيكن": "تْشِيكِن",
    "بوكيه": "بُوكِيه",
    "ساندويتش": "سانْدَوِيتْش",
    "فانيلا": "فانِيلَا",
    "مانجو": "مانْجُو",
    "فراوله": "فَراوْلَة",
    "بوكس": "بُوكس",
    "رول": "رُول",
    "بول": "بُول",
    "ماركت": "مارْكِت",
    "ماسالا": "ماسالَا",
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
    "شاورما": "شاوِرْما",
    "كفته": "كُفْتَة",
    "كباب": "كَباب",
    "حمص": "حُمُّص",
    "فلافل": "فَلافِل",
    "فتوش": "فَتُّوش",
    "مناقيش": "مَناقِيش",
    "لقيمات": "لُقَيْمات",
    "كنافه": "كُنافَة",
    "بقلاوه": "بَقْلاوَة",
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
    "بخاري": "بُخارِي",
    "كبده": "كِبْدَة",
    "ريش": "رِيش",
    "غنم": "غَنَم",
    "مشاوي": "مَشاوِي",
    "مقبلات": "مُقَبِّلات",
    "عدس": "عَدَس",
    "شوربه": "شُورْبَة",
    "زعتر": "زَعْتَر",
    "قشطه": "قِشْطَة",
    "روبيان": "رُوبْيان",
    "سلمون": "سَلَمُون",
    "تونا": "تُونَا",
    "بطاطس": "بَطاطِس",
    "دجاج": "دَجاج",
    "لحم": "لَحْم",
    "لحمه": "لَحْمَة",
    "سمك": "سَمَك",
    "خضار": "خُضار",
    "سلطه": "سَلَطَة",
    "عائلي": "عائِلِي",
    "عائليه": "عائِلِيَّة",
    "لقمه": "لُقْمَة",
    "اصيل": "أَصِيل",
    # Stored bare (no leading shadda): standalone "شواية القصر" must not start
    # with a doubling mark. The ال re-attachment path below adds the sun-letter
    # shadda, so "الشوايه" still comes out "الشَّوايَة".
    "شوايه": "شَوايَة",
    "حلويات": "حَلَوِيّات",
    "هندي": "هِنْدِي",
    "هنديه": "هِنْدِيَّة",
    "بحري": "بَحْرِي",
    "بحريه": "بَحْرِيَّة",
    "صحي": "صِحِّي",
    "صحيه": "صِحِّيَّة",
    "اضافي": "إِضافِي",
    "اضافيه": "إِضافِيَّة",
    "صلصه": "صَلْصَة",
    "فستق": "فُسْتُق",
    "تمر": "تَمْر",
    "زبادي": "زَبادِي",
    "عسل": "عَسَل",
    "ثوم": "ثُوم",
    "بصل": "بَصَل",
    "نان": "نان",
    "دال": "دال",
    "ثالي": "ثالِي",
    "مسابا": "مَسايَا",
    "مسايا": "مَسايَا",
    "سمبوسه": "سَمْبُوسَة",
    "نابلسيه": "نابُلْسِيَّة",
    "عربيه": "عَرَبِيَّة",
    "قهوه": "قَهْوَة",
    "ارز": "أَرُز",
    "صحن": "صَحْن",
    "طبق": "طَبَق",
    "وجبه": "وَجْبَة",
    "حلقات": "حَلَقات",
    "اطفال": "أَطْفال",
    "طاقه": "طاقَة",
    "اخضر": "أَخْضَر",
    "طازج": "طازِج",
    "ساحليه": "ساحِلِيَّة",
    "ساحل": "ساحِل",
    "ماكولات": "مَأْكُولات",
    "الرياض": "الرِّياض",
    "الشام": "الشام",
    "بيت": "بَيْت",
    "مذاق": "مَذاق",
    "دار": "دار",
    "راب": "راب",
    "بطر": "بَطَر",
    "باستا": "باسْتَا",
    "ايس": "آيس",
    "كريم": "كْرِيم",
}


def _strip_final_marks(word: str) -> str:
    letters = [i for i, c in enumerate(word) if _is_arabic_letter(c)]
    if not letters:
        return word
    last = letters[-1]
    head, tail = word[: last + 1], word[last + 1 :]
    return head + _MARKS.sub("", tail)


def sanitize_tashkeel(text: str) -> str:
    """Enforce last-letter bare + drop phonotactically broken words to bare."""

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
        if starts_with_sukun or double_sukun:
            return _MARKS.sub("", word)
        return _strip_final_marks(word)

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
    # one-letter proclitic, not إعراب on a finished word.
    return word[:i] if i else prefix_letters


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
_TRAILING_OPEN_RE = re.compile(r"[،,;…]+(?:\s*[\"'»«)])*\s*$")
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
    r"لحظ(?:ه|ة)\s+اشوف|"
    r"اشوف\s+لك|"
    r"ادور\s+لك|"
    r"ممنوع\s+الوعد|"
    r"الوعد\s+المؤجل|"
    r"انا\s+بدور\s+لك|"
    r"لاثمانيه\s+اشخاص|"
    r"انت\s+قلت\s+سته"
    r")"
)
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
# ever writes it; the audio trimmer cuts the same tail if it is only in the
# waveform.
_WEIRD_SPOKEN_RE = re.compile(
    r"(?:^|\s)(?:تابون[ةه]|طابون[ةه]|taboo?nah?|tabuna)(?:\s|$)",
    re.IGNORECASE,
)
_BRACE_JUNK_RE = re.compile(r"[{}|<>]+")
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


def strip_speech_artifacts(text: str) -> str:
    """Drop stall promises, leaked prompt rules, and bare template keywords.

    The model sometimes narrates its own instructions ("ممنوع الوعد المؤجل")
    or promises to come back ("ثماني ثواني وأرجع لك") while a tool is still
    running. Those must never reach the speaker.
    """
    raw = " ".join(str(text or "").split())
    if not raw:
        return ""
    # Prefer the Arabic payload if the whole turn is a tool-field JSON blob.
    say_match = _SAY_TO_USER_JSON_RE.search(raw)
    if say_match:
        raw = say_match.group(1).strip()
    raw = _JSON_KEY_NOISE_RE.sub(" ", raw)
    raw = _CJK_RE.sub(" ", raw)
    raw = _THOUGHT_LEAK_RE.sub(" ", raw)
    raw = _WEIRD_SPOKEN_RE.sub(" ", raw)
    raw = _BRACE_JUNK_RE.sub(" ", raw)
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
            if folded and not _STALL_FOLDED_RE.search(folded):
                kept.append(chunk + delim)
        i += 2 if delim else 1

    return " ".join(s.strip() for s in kept if s.strip()).strip()


def finalize_prosody(text: str) -> str:
    """Force falling/closed contour so turns do not end on a continuation rise.

    Live failure mode: "...مُقَرْمَش، تِبِيه؟" — the Arabic comma keeps the pitch
    up after مقرمش, and the question mark adds another rise, so playback feels
    like she is about to continue and then just stops. Split the tag question
    onto its own sentence, and never leave a turn ending on a comma/ellipsis.
    """
    text = " ".join(str(text or "").split())
    if not text:
        return text
    text = _TAG_QUESTION_RE.sub(". ", text)
    text = _TRAILING_OPEN_RE.sub(".", text)
    if not _HAS_TERMINAL_RE.search(text):
        text = text + "."
    return text


# ── Loanwords the voice should read as English, not as diacritized Arabic ────
#
# The catalog writes borrowed dish names in Arabic script (برجر, تشيز كيك,
# رامن). Diacritizing those is the wrong tool: the vowels describe an Arabic
# word that does not exist, and VoxCPM reads the invention literally. Handing
# the model's own spelling to the TTS in Latin letters lets it use its English
# phonetics instead.
#
# This runs ONLY inside clean_for_speech — the TTS boundary. Tool payloads,
# `name_say_ar`, and the transcript keep the Arabic spelling, because those are
# read by the user and by the model, not spoken.
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
    "ايس كريم": "ice cream",
    "تشيز كيك": "cheesecake",
    "ميلك شيك": "milkshake",
    "باد تاي": "pad thai",
    "فيتوتشيني الفريدو": "fettuccine alfredo",
    "دبل تشيز": "double cheese",
    "بطر تشيكن": "butter chicken",
    "سي فود": "seafood",
    "سبرينغ رولز": "spring rolls",
    "تكا مسايا": "tikka masala",
    "فاير جرل": "fire grill",
    "اوربان بان": "urban bun",
}

LATIN_WORDS: dict[str, str] = {
    "برجر": "barger",
    # Soft-j /dʒ/. Latin "dajaaj" still came out hard-g on Hoda under
    # language=Arabic (same trap as Arabic دَجاج). The English lexicon word
    # is the one spelling that reliably keeps the soft ج.
    "دجاج": "chicken",
    "بيتزا": "pizza",
    "باستا": "pasta",
    "سوشي": "sushi",
    "رول": "roll",
    "رامن": "ramen",
    "تمبورا": "tempura",
    "ساشيمي": "sashimi",
    "جيوزا": "gyoza",
    "ادامامي": "edamame",
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
    "سموذي": "smoothie",
    "كينوا": "quinoa",
    "افوكادو": "avocado",
    "بول": "bowl",
    "راب": "wrap",
    "ستاك": "steak",
    "تاكو": "taco",
    "كلاسيك": "classic",
    "كرسبي": "crispy",
    "سماش": "smash",
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
    # High-frequency addon vocabulary — صوص alone appears 1079 times.
    "صوص": "sauce",
    "كولا": "cola",
    "سكوب": "scoop",
    "كريمه": "cream",
    "شوكولاته": "chocolate",
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
    "واحد": "واحِد", "اثنين": "اثْنين", "ثلاثه": "ثَلاثَة", "اربعه": "أَرْبَعَة",
    "خمسه": "خَمْسَة", "سته": "سِتَّة", "سبعه": "سَبْعَة", "ثمانيه": "ثَمانِيَة",
    "تسعه": "تِسْعَة", "عشره": "عَشَرَة", "عشرين": "عِشْرين", "ثلاثين": "ثَلاثين",
    "اربعين": "أَرْبَعين", "خمسين": "خَمْسين", "ستين": "سِتِّين", "سبعين": "سَبْعين",
    "ثمانين": "ثَمانين", "تسعين": "تِسْعين", "مياه": "مِئَة", "مئه": "مِئَة",
    "مئتين": "مِئْتين", "الف": "أَلْف", "الفين": "أَلْفين", "الاف": "آلاف",
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
    "ثمسين": "خَمْسين",
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
        # "وخمسين" is one word to the tokenizer but و + number to the lexicon.
        if key.startswith("و") and len(key) > 1:
            fixed = lookup(key[1:])
            if fixed is not None:
                return f"{_original_prefix(word, 'و')}{fixed}"
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
    "protin": "بروتين",
    "protein": "بروتين",
    "calories": "سُعْرَة",
    "riyal": "رِيال",
    "sar": "رِيال",
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
    # Model typos for words it must not mangle, same principle as NUMBER_TYPOS:
    # observed in the logs, never guessed. "رِجَّعْت لَك صَفْحَة المَطَاعِع".
    # Stored bare: the ال in "المطاعع" is re-attached by the proclitic path.
    "مطاعع": "مَطَاعِم",
    "اضضت": "أَضَفْت",
    "أضضت": "أَضَفْت",
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


def clean_for_speech(text: str) -> str:
    """Full TTS text pass: tashkeel rules, lexicon respell, then prosody close."""
    cleaned = strip_speech_artifacts(text)
    if not cleaned.strip():
        return ""
    return finalize_prosody(
        dialect_respell(
            latinize_loanwords(
                deromanize_strays(
                    repair_number_words(fix_pronunciation(sanitize_tashkeel(cleaned)))
                )
            )
        )
    )


# Counts written as digits inside catalog names. 75 family meals are named
# "وجبة عائلية مشاوي لـ 3 أشخاص", and the spoken form handed to the TTS kept the
# bare "3" — a Latin numeral in an Arabic sentence, read as a letter or dropped.
# The count is the whole point of those names, so it is spelled out.
_COUNT_WORDS: dict[str, str] = {
    "1": "واحِد", "2": "اثْنَين", "3": "ثَلاثَة", "4": "أَرْبَعَة", "5": "خَمْسَة",
    "6": "سِتَّة", "7": "سَبْعَة", "8": "ثَمانِيَة", "9": "تِسْعَة", "10": "عَشَرَة",
    "11": "إِحْدَى عَشَر", "12": "اثْنا عَشَر", "30": "ثَلاثِين",
}

# "لـ" is لام الجر glued on with a tatweel. Spoken it is لِ + the count.
_LAM_COUNT_RE = re.compile(r"لـ\s*(\d+)")
# Decimals are left alone: "زيت ذرة 1.5 لتر" must not become "واحِد.خَمْسَة".
_COUNT_RE = re.compile(r"(?<![\d.])\d+(?![\d.])")


def speak_counts(text: str) -> str:
    """Spell out digit counts inside a name."""
    if not text:
        return text
    out = text.translate(_ARABIC_INDIC_DIGITS)
    out = _LAM_COUNT_RE.sub(
        lambda m: f"لِ{_COUNT_WORDS[m.group(1)]}" if m.group(1) in _COUNT_WORDS else m.group(0),
        out,
    )
    return _COUNT_RE.sub(lambda m: _COUNT_WORDS.get(m.group(0), m.group(0)), out)


def speakable_name(name: str) -> str:
    """Canonical spoken form for a catalog restaurant/meal/addon name."""
    if not name:
        return ""
    # Names are fragments, not turns — skip prosody finalization so we do not
    # append a period onto every meal title in tool payloads.
    return speak_counts(fix_pronunciation(sanitize_tashkeel(str(name))))


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


def written_phone(value: Any) -> str:
    """Digits-only Saudi mobile, normalized to 05XXXXXXXX where possible."""
    raw = written_text(value)
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


# Back-compat alias used by older call sites / tests.
PRONUNCIATION_FIXES = WORD_FIXES
