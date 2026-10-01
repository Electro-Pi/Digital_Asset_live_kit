"""Does a sentence she is about to say claim an action?

The LLM node streams her reply to the voice as it is generated. A sentence
that claims an action ("أضفتها لك", "ثبّت طلبك", "فتحت لك الصفحة") or promises
a lookup ("لحظة أشوف لك") is held back until the turn's tools have actually
run; everything else is spoken immediately. These read *her* words, never the
caller's.
"""

from __future__ import annotations

import re

from src.spoken_lexicon import mask_capability_lookups

_TASHKEEL = re.compile(r"[ً-ْٰ]")


def fold(text: str) -> str:
    return (
        _TASHKEEL.sub("", str(text or ""))
        .replace("أ", "ا").replace("إ", "ا").replace("آ", "ا")
        .replace("ة", "ه").replace("ى", "ي")
        .lower()
    )


_CART_WORDS = ("سله", "سلتك", "بالسله", "للسله", "كارت", "cart", "طلبك", "بالحساب")
# A past-tense verb (as a whole word) next to a cart word is a claim that
# something went in. «ننزلها لك بالسلة؟» is a question, not a claim.
_CART_VERBS = re.compile(
    r"(?<!\w)(?:نزلت|نزلتها|نزل|نزلنا|نزلناها|نزلنها|سجلت|سجلتها|سجلنا|صار|صارت|حطيت|حطينا|ضفت|ضفنا|اضفت|اضفنا|زدت|زدنا|دخلت|حسبت)(?!\w)"
)
_ADD_PHRASES = (
    "حطيت", "حطيتها", "حطيته", "حطيتلك", "حطيت لك", "حطينالك", "حطينا لك", "حطيناها", "حطينا",
    "ضفت", "ضفتها", "ضفته", "ضفتلك", "ضفت لك", "ضفناها", "ضفنا لك",
    "اضفت", "اضفتها", "اضفته", "اضفتلك", "اضفت لك", "اضفناها", "اضفنا لك",
    "زدت", "زدتها", "زدته", "زدتلك", "زدت لك", "زدناها", "زدنا لك",
    "نزلتها", "نزلته", "نزلت لك", "نزلناها", "نزلناه", "نزلنها", "نزلنا لك", "نزلنالك",
    "سجلناها", "سجلنا لك", "صار بطلبك", "صارت بطلبك", "صار بسلتك", "صارت بسلتك",
    "تمت الاضافه", "تمت اضافتها", "تمت اضافته", "تم اضافتها", "تم اضافته",
    "سجلت لك", "سجلتها لك", "سفرتك جاهزه", "سفرتك كامله", "نزلت معك", "نزل معك",
)
_ORDER_PHRASES = (
    "ثبت طلبك", "ثبتت طلبك", "ثبتنا طلبك", "اكدت طلبك", "تم الطلب", "تم طلبك", "طلبك تم",
    "طلبك بالطريق", "طلبك في الطريق", "تم تثبيت", "ثبتناه", "ثبته لك", "رابط الدفع",
)
_OPEN_PHRASES = (
    "فتحت لك", "فتحتها لك", "فتحت الصفحه", "عرضت لك", "عرضتها لك", "طلعت لك", "طلعتها لك",
    "وريتك", "قدامك على الشاشه", "قدامك الحين",
)
_STALL = re.compile(
    r"لحظ|اشوف\s+لك|ادور\s+لك|خليني\s+(?:اشوف|ادور|اشيك)|اشيك|ثوان|ارجع\s+لك|بدور",
    re.IGNORECASE,
)


def claims_added_to_cart(text: str) -> bool:
    folded = fold(text)
    if not folded:
        return False
    if any(p in folded for p in _ADD_PHRASES):
        return True
    return any(w in folded for w in _CART_WORDS) and bool(_CART_VERBS.search(folded))


def claims_order_placed(text: str) -> bool:
    folded = fold(text)
    return bool(folded) and any(p in folded for p in _ORDER_PHRASES)


def claims_page_opened(text: str) -> bool:
    folded = fold(text)
    return bool(folded) and any(p in folded for p in _OPEN_PHRASES)


def claims_stall(text: str) -> bool:
    """A promise to go and look something up."""
    folded = fold(text)
    return bool(folded) and bool(_STALL.search(mask_capability_lookups(folded)))


def claims_action(text: str) -> bool:
    """Any claim that only a tool can make true."""
    return (
        claims_added_to_cart(text)
        or claims_order_placed(text)
        or claims_page_opened(text)
        or claims_stall(text)
    )


# ─── Catalog claims: a dish found, a price quoted ────────────────────────────
#
# Live 2026-09-24: «آكل كبسة» got «لقيت لك كبسة حاشي مع الرز البشاور بتسعة
# وأربعين ريال» with no search. The dish does not exist. A price or a "found"
# is a catalog fact, so it must come from a tool (or the prompt), like an add
# must come from the cart.

_FOUND = re.compile(r"(?<!\w)(?:ما\s+)?(?:لقيت|لقيتلك|لقيتها|لقيته|لقينا|حصلت\s+لك|حصلتلك)(?!\w)")

_UNITS = {
    "واحد": 1, "وحده": 1, "اثنين": 2, "اثنان": 2, "ثنتين": 2, "اتنين": 2,
    "ثلاث": 3, "ثلاثه": 3, "تلات": 3, "تلاته": 3, "اربع": 4, "اربعه": 4,
    "خمس": 5, "خمسه": 5, "ست": 6, "سته": 6, "سبع": 7, "سبعه": 7,
    "ثمان": 8, "ثماني": 8, "ثمانيه": 8, "ثمنيه": 8, "تمان": 8, "تمانيه": 8,
    "تسع": 9, "تسعه": 9, "عشر": 10, "عشره": 10,
}
_TEENS = {
    "احدعش": 11, "حدعش": 11, "اطنعش": 12, "اثنعش": 12, "ثلطعش": 13, "تلطعش": 13,
    "اربعطعش": 14, "خمسطعش": 15, "ستطعش": 16, "سبعطعش": 17, "ثمنطعش": 18,
    "تمنطعش": 18, "تسعطعش": 19,
}
_TENS_WORDS = {
    "عشرين": 20, "ثلاثين": 30, "تلاتين": 30, "اربعين": 40, "خمسين": 50,
    "ستين": 60, "سبعين": 70, "ثمانين": 80, "تمانين": 80, "تسعين": 90,
}
_HUNDRED_WORDS = {"ميه": 100, "مئه": 100, "ميتين": 200, "مئتين": 200, "مية": 100}
_THOUSAND_WORDS = {"الف": 1000, "الفين": 2000, "الاف": 1000}


def _number_word(token: str) -> tuple[str, int] | None:
    """(kind, value) for one folded number word, trying the ب/و prefixes."""
    for candidate in (token, token[1:] if token[:1] in "بو" else "", token[2:] if token[:2] in {"وب", "بو"} else ""):
        if not candidate:
            continue
        if candidate.isdigit():
            return "digits", int(candidate)
        if candidate in _UNITS:
            return "unit", _UNITS[candidate]
        if candidate in _TEENS:
            return "teen", _TEENS[candidate]
        if candidate in _TENS_WORDS:
            return "ten", _TENS_WORDS[candidate]
        if candidate in _HUNDRED_WORDS:
            return "hundred", _HUNDRED_WORDS[candidate]
        if candidate in _THOUSAND_WORDS:
            return "thousand", _THOUSAND_WORDS[candidate]
        for unit, value in _UNITS.items():  # ثلاثمية، خمسميه
            for hundred in ("ميه", "مئه", "ميت"):
                if candidate == unit + hundred and value < 10:
                    return "hundred", value * 100
    return None


def _amount(words: list[tuple[str, int]]) -> int:
    total, current, i = 0, 0, 0
    while i < len(words):
        kind, value = words[i]
        nxt = words[i + 1] if i + 1 < len(words) else None
        if kind == "unit" and nxt and nxt[0] == "unit" and nxt[1] == 10 and value < 10:
            current += value + 10  # ثلاثة عشر
            i += 2
            continue
        if kind == "unit" and nxt and nxt[0] == "hundred" and nxt[1] == 100:
            current += value * 100  # ثلاث مية
            i += 2
            continue
        if kind == "thousand":
            if value == 1000:
                total += (current or 1) * 1000
            else:
                total += value
            current = 0
        else:
            current += value
        i += 1
    return total + current


# Live eval 2026-09-27: Coda-V2 wrote its reasoning into the reply, «Since the
# user said the budget is not important … I need to pitch it. دامك تبي لحم …
# Wait, the tool summary says: هذي …». She writes no English at all (loanwords
# are romanised later, by spoken_lexicon), so three Latin words in a row are
# never speech.
_REASONING_RUN = re.compile(r"[A-Za-z][\w'’-]*(?:[\s,.:;!?()\"-]+[A-Za-z][\w'’-]*){2,}[\s,.:;!?()\"]*")


def has_reasoning_leak(text: str) -> bool:
    return _REASONING_RUN.search(text or "") is not None


def strip_reasoning_leak(text: str) -> str:
    """Remove English reasoning; what is left is cut after its first question.

    The model that leaks tends to restate its reply after the leak («Wait, …
    هذي … تبيها؟»), so only the first question is kept.
    """
    if not has_reasoning_leak(text):
        return text
    kept = " ".join(_REASONING_RUN.sub(" ", text).split())
    first = min((i for i in (kept.find("؟"), kept.find("?")) if i >= 0), default=-1)
    return kept[: first + 1] if first >= 0 else kept


_ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")


def said_number(text: str) -> int | None:
    """The first number in ``text``, with or without «ريال» («أربعة», «حدود خمسين», «٥٠»)."""
    words: list[tuple[str, int]] = []
    for token in re.findall(r"\w+", fold(text).translate(_ARABIC_DIGITS)):
        parsed = _number_word(token)
        if parsed is not None:
            words.append(parsed)
        elif words:
            break
    return _amount(words) if words else None


def spoken_amounts(text: str) -> list[int]:
    """Every amount of riyals said in ``text``, as integers.

    «بتسعة وأربعين ريال» → [49]; «مئتين وأربعة وثلاثين ريال» → [234].
    A «ريال» with no number before it («بكم ريال؟») is not an amount.
    """
    tokens = re.findall(r"\w+", fold(text))
    amounts: list[int] = []
    for i, token in enumerate(tokens):
        # «بريالين»، «ريال واحد»: the number comes with or after the word.
        bare = token[1:] if token[:1] in "بو" and token[1:] in {"ريالين", "ريال"} else token
        if bare == "ريالين":
            amounts.append(2)
            continue
        if bare == "ريال" and i + 1 < len(tokens) and tokens[i + 1] in {"واحد", "وحده"} and (
            i == 0 or _number_word(tokens[i - 1]) is None
        ):
            amounts.append(1)
            continue
        if token not in {"ريال", "ريالات", "رس"}:
            continue
        words: list[tuple[str, int]] = []
        j = i - 1
        while j >= 0:
            parsed = _number_word(tokens[j])
            if parsed is None:
                break
            words.insert(0, parsed)
            j -= 1
        if words:
            amounts.append(_amount(words))
    return amounts


def claims_found(text: str) -> bool:
    """«لقيت لك …»: a claim that a search just returned something."""
    folded = fold(text)
    return bool(folded) and bool(_FOUND.search(folded))


def claims_catalog_fact(text: str) -> bool:
    """A price or a "found" in her words: facts only a tool can supply."""
    return claims_found(text) or bool(spoken_amounts(text))
