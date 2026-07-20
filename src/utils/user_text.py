import re

_ASR_WRAPPER_RE = re.compile(
    r"^language\s+[\w-]+<asr_text>",
    re.IGNORECASE,
)


def extract_user_message(text: str | None) -> str:
    """Strip STT wrapper prefixes and normalize user text."""
    if not text:
        return ""
    cleaned = _ASR_WRAPPER_RE.sub("", text).strip()
    return re.sub(r"\s+", " ", cleaned)
