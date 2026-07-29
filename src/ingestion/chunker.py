"""Group extracted blocks into retrievable chunks.

Target is roughly 300 tokens: large enough to carry one recommendation plus the
qualifiers that make it safe to act on (age band, risk group, interval), small
enough that four of them fit comfortably in the turn context without pushing
the voice model into long, unspeakable answers.

Token counts are approximated from character length. The corpus is English
guideline prose, where ~4 characters per token is accurate to within a few
percent, and the alternative is adding a tokenizer dependency to get a number
that only feeds a soft threshold.
"""

from __future__ import annotations

from src.ingestion.extract import TextBlock

CHARS_PER_TOKEN = 4
TARGET_TOKENS = 300
OVERLAP_TOKENS = 50
MIN_TOKENS = 30

_TARGET_CHARS = TARGET_TOKENS * CHARS_PER_TOKEN
_OVERLAP_CHARS = OVERLAP_TOKENS * CHARS_PER_TOKEN
_MIN_CHARS = MIN_TOKENS * CHARS_PER_TOKEN


def _split_long_text(text: str) -> list[str]:
    """Split an oversized block on sentence boundaries, with overlap."""
    if len(text) <= _TARGET_CHARS:
        return [text]

    # Sentence-ish boundaries. Guideline prose is dense with abbreviations, so
    # this stays conservative and only breaks on ". " followed by a capital.
    pieces: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + _TARGET_CHARS, len(text))
        if end < len(text):
            window = text.rfind(". ", start + _MIN_CHARS, end)
            if window != -1:
                end = window + 1
        piece = text[start:end].strip()
        if piece:
            pieces.append(piece)
        if end >= len(text):
            break
        start = max(end - _OVERLAP_CHARS, start + 1)
    return pieces


def chunk_blocks(blocks: list[TextBlock]) -> list[TextBlock]:
    """Merge small blocks and split large ones, never crossing a heading.

    Merging matters as much as splitting: guideline pages are full of two-line
    paragraphs that mean nothing alone, and indexing them individually fills the
    top-k with fragments.
    """
    chunks: list[TextBlock] = []
    pending_heading: str | None = None
    pending_parts: list[str] = []
    pending_len = 0

    def flush() -> None:
        nonlocal pending_heading, pending_len
        if pending_parts and pending_heading is not None:
            merged = "\n".join(pending_parts).strip()
            if len(merged) >= _MIN_CHARS:
                chunks.append(TextBlock(pending_heading, merged))
        pending_parts.clear()
        pending_len = 0
        pending_heading = None

    for block in blocks:
        if pending_heading is not None and block.section_path != pending_heading:
            flush()

        for piece in _split_long_text(block.text):
            if pending_heading is None:
                pending_heading = block.section_path
            if pending_len + len(piece) > _TARGET_CHARS and pending_parts:
                flush()
                pending_heading = block.section_path
            pending_parts.append(piece)
            pending_len += len(piece) + 1

    flush()
    return chunks
