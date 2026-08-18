import re


def _normalize_arabic(message: str) -> str:
    """Loose normalization for dialect/STT spelling variants."""
    text = message.strip().lower()
    text = re.sub(r"[أإآٱ]", "ا", text)
    text = re.sub(r"ى", "ي", text)
    text = re.sub(r"ة", "ه", text)
    text = re.sub(r"ؤ", "و", text)
    text = re.sub(r"ئ", "ي", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


# Phrases that unambiguously signal the user wants to *perform* a CRM action,
# not just learn about it.  "كيف أقدم شكوى؟" = knowledge; "أبغى أقدم شكوى" = CRM.
_INFORMATIONAL_PREFIXES = re.compile(
    r"(?:كيف|ازاي|إزاي|وين|ليش|لماذا|متى|وش|ما هو|ما هي|شو|ايش|أيش|"
    r"اعرف|اشرح|وضح|اخبرني|خبرني|فهمني|شرح|معنى|ما معنى)",
    re.IGNORECASE,
)


def is_crm_action(message: str) -> bool:
    """
    Returns True only when the user wants to *execute* a CRM action
    (file/track a complaint), NOT when they ask *how* to do it.
    """
    normalized = _normalize_arabic(message)

    # Informational questions ("كيف أقدم شكوى؟") are never CRM actions.
    if _INFORMATIONAL_PREFIXES.search(normalized):
        return False

    # Digit → ticket or national ID lookup.
    if any(char.isdigit() for char in normalized):
        return True

    # Exact CRM-intent phrases.
    crm_keywords = [
        "تقديم شكوى",
        "تقديم شكوه",
        "ارفع شكوى",
        "ارفع شكوه",
        "ابغى ارفع",
        "ابي ارفع",
        "ابغى اقدم",
        "ابي اقدم",
        "سجل شكوى",
        "سجل شكوه",
        "متابعه شكوى",
        "متابعه شكوه",
        "حاله شكوى",
        "حاله شكوه",
        "رقم شكوتي",
        "رقم البلاغ",
        "اتابع الطلب",
        "اعتراض على",
    ]
    for keyword in crm_keywords:
        if keyword in normalized:
            return True

    # After normalization: ى→ي, أ→ا, ة→ه
    # complaint nouns (normalized forms)
    complaint_nouns = ["شكوي", "شكوه", "شكواء", "بلاغ", "اعتراض"]
    # intent verbs: ابغي (أبغى), ابي (أبي), ابا, ودي, ارفع, سجل, قدم
    strong_action = re.compile(
        r"(?:ابغي|ابي|ابا|ودي)\s+(?:اقدم|ارفع|سجل|قدم)",
        re.IGNORECASE,
    )
    direct_imperative = re.compile(
        r"(?:ارفع|سجل|قدم)\s+(?:شكوي|شكوه|بلاغ)",
        re.IGNORECASE,
    )
    has_complaint = any(noun in normalized for noun in complaint_nouns)
    has_action = (
        bool(strong_action.search(normalized))
        or bool(direct_imperative.search(normalized))
        or (has_complaint and any(p in normalized for p in ("ابغي", "ابي", "ابا", "ودي")))
    )

    if has_complaint and has_action:
        return True

    return False


_GREETING = re.compile(
    r"^(?:هلا+|هلو+|مرحبا+|السلام\s+عليكم|سلام|صباح\s+الخير|مساء\s+الخير|"
    r"شكرا+|تمام|اهلا+|أهلا+|هاي|حياك|كيف\s+حالك|وعليكم\s+السلام|"
    r"والله\s*هلا|هلا\s*والله)[\s!.؟?]*$",
    re.IGNORECASE,
)

_KNOWLEDGE_HINTS = re.compile(
    r"خدم|تعريف|فاتور|شكو|كهرب|هيئ|نظام|لائح|حق|تعويض|عداد|إيصال|ايصال|"
    r"طاق|شمس|شحن|تكلف|سعر|استهلاك|اشتراك|توصيل|تنظيم|لوائح|اجراء|إجراء|"
    r"قطع|انقطاع|ضغط|عطل|صيانه|صيانة|عداد|سيرا|هيئة|سيره|خدمه|خدمات",
    re.IGNORECASE,
)

_QUESTION_WORDS = (
    "وش", "كيف", "وين", "ليش", "لماذا", "متى", "ممكن", "عندك",
    "تقدر", "تقدم", "ازاي", "إزاي", "ايش", "أيش", "شو", "فين",
    "عايز", "عاوز", "اريد", "أريد", "ابغى",
)


def is_greeting(message: str) -> bool:
    normalized = _normalize_arabic(message)
    return bool(_GREETING.match(normalized))


def is_knowledge_query(message: str) -> bool:
    """Returns True for general SERA knowledge questions (not CRM actions, not greetings)."""
    if not message or is_greeting(message):
        return False
    if is_crm_action(message):
        return False

    normalized = _normalize_arabic(message)
    if _KNOWLEDGE_HINTS.search(normalized):
        return True
    if any(hint in normalized for hint in _QUESTION_WORDS):
        return True
    return "؟" in message or "?" in message
