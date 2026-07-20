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


def is_crm_action(message: str) -> bool:
    """
    Determines if the user message is attempting a CRM action
    (creating a ticket, checking status) versus a general knowledge query.
    """
    normalized = _normalize_arabic(message)

    # 1. Check for digits (National IDs or Ticket numbers)
    if any(char.isdigit() for char in normalized):
        return True

    # 2. Check for exact CRM-specific phrases (normalized forms)
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
        "متابعة شكوى",
        "متابعه شكوى",
        "حالة شكوى",
        "حاله شكوى",
        "رقم شكوتي",
        "رقم البلاغ",
        "اتابع الطلب",
        "اعتراض",
    ]

    for keyword in crm_keywords:
        if keyword in normalized:
            return True

    # 3. Complaint/ticket nouns with action intent (dialect-tolerant)
    complaint_nouns = ["شكوى", "شكوه", "شكواء", "بلاغ", "طلب", "اعتراض"]
    action_patterns = [
        r"(?:ا|أ)?قدم",
        r"ارفع",
        r"سجل",
        r"تابع",
        r"شوف",
        r"شيك",
        r"ابغى",
        r"ابي",
        r"ابا",
        r"ودي",
    ]

    has_complaint_noun = any(noun in normalized for noun in complaint_nouns)
    has_action_intent = any(re.search(pat, normalized) for pat in action_patterns)

    if has_complaint_noun and has_action_intent:
        return True

    # 4. Active filing language (not just informational "how to")
    if has_complaint_noun and any(
        phrase in normalized
        for phrase in ("ابغى", "ابي", "ودي", "ارفع", "سجل", "قدم")
    ):
        return True

    return False


_GREETING = re.compile(
    r"^(?:هلا+|مرحبا+|السلام\s+عليكم|سلام|صباح\s+الخير|مساء\s+الخير|"
    r"شكرا+|تمام|اهلا+|أهلا+|هاي|حياك|كيف\s+حالك|وعليكم\s+السلام)[\s!.؟?]*$",
    re.IGNORECASE,
)

_KNOWLEDGE_HINTS = re.compile(
    r"خدم|تعريف|فاتور|شكو|كهرب|هيئ|نظام|لائح|حق|تعويض|عداد|إيصال|ايصال|"
    r"طاق|شمس|شحن|تكلف|سعر|استهلاك|اشتراك|توصيل|تنظيم|لوائح|اجراء|إجراء",
    re.IGNORECASE,
)

_QUESTION_HINTS = ("وش", "كيف", "وين", "ليش", "لماذا", "متى", "ممكن", "عندك", "تقدر", "تقدم")


def is_greeting(message: str) -> bool:
    normalized = _normalize_arabic(message)
    return bool(_GREETING.match(normalized))


def is_knowledge_query(message: str) -> bool:
    """General SERA knowledge — should hit backend, not LLM memory."""
    if not message or is_crm_action(message) or is_greeting(message):
        return False

    normalized = _normalize_arabic(message)
    if _KNOWLEDGE_HINTS.search(normalized):
        return True
    if any(hint in normalized for hint in _QUESTION_HINTS):
        return True
    return "؟" in message or "?" in message


_GREETING = re.compile(
    r"^(?:هلا+|مرحبا+|السلام\s+عليكم|سلام|صباح\s+الخير|مساء\s+الخير|"
    r"شكرا+|تمام|اهلا+|أهلا+|هاي|حياك|كيف\s+حالك|وعليكم\s+السلام)[\s!.؟?]*$",
    re.IGNORECASE,
)

_KNOWLEDGE_HINTS = re.compile(
    r"خدم|تعريف|فاتور|شكو|كهرب|هيئ|نظام|لائح|حق|تعويض|عداد|إيصال|ايصال|"
    r"طاق|شمس|شحن|تكلف|سعر|استهلاك|اشتراك|توصيل|تنظيم|لوائح|اجراء|إجراء",
    re.IGNORECASE,
)

_QUESTION_HINTS = ("وش", "كيف", "وين", "ليش", "لماذا", "متى", "ممكن", "عندك", "تقدر", "تقدم")


def is_greeting(message: str) -> bool:
    normalized = _normalize_arabic(message)
    return bool(_GREETING.match(normalized))


def is_knowledge_query(message: str) -> bool:
    """General SERA knowledge — should hit backend, not LLM memory."""
    if not message or is_crm_action(message) or is_greeting(message):
        return False

    normalized = _normalize_arabic(message)
    if _KNOWLEDGE_HINTS.search(normalized):
        return True
    if any(hint in normalized for hint in _QUESTION_HINTS):
        return True
    return "؟" in message or "?" in message