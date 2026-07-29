"""Turn fetched pages into heading-scoped blocks of clean text.

Chunking blind to structure is what produces the classic RAG failure where a
retrieved passage says "this is recommended for all adults in this group"
without ever naming the group. Keeping the heading path attached to every block
gives both the chunker something to split along and the embedder some topical
anchoring.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import trafilatura
from lxml import etree


@dataclass(frozen=True)
class TextBlock:
    """A run of body text under one heading path."""

    section_path: str
    text: str


_WHITESPACE = re.compile(r"[ \t]+")
_BLANK_LINES = re.compile(r"\n{3,}")

# Guideline pages use the publication date as a standalone heading, and the
# body under it is boilerplate disclaimer text rather than clinical content.
_DATE_HEADING = re.compile(
    r"^(?:january|february|march|april|may|june|july|august|september|october|"
    r"november|december)\s+\d{1,2},?\s+\d{4}$",
    re.IGNORECASE,
)


def _clean(text: str) -> str:
    text = _WHITESPACE.sub(" ", text)
    text = _BLANK_LINES.sub("\n\n", text)
    return text.strip()


def _should_drop(heading: str, drop_prefixes: frozenset[str]) -> bool:
    """Decide whether a heading's section is worth indexing.

    Prefix matching rather than equality, because publishers are inconsistent
    about the tail of a heading — "Response to Public Comment" and "Response to
    Public Comments" are the same section on different USPSTF pages, and an
    exact-match list silently misses half of them.
    """
    lowered = heading.lower().strip()
    if not lowered:
        return False
    if _DATE_HEADING.match(lowered):
        return True
    return any(lowered.startswith(prefix) for prefix in drop_prefixes)


def _render_table(node: etree._Element) -> str:
    """Flatten a table into one text block.

    Guideline recommendation grids (population / recommendation / grade) lose
    their meaning if split row-from-header, so a table is always kept whole.
    """
    rows: list[str] = []
    for row in node.iter("row"):
        cells = [
            _clean("".join(cell.itertext()))
            for cell in row.iter("cell")
        ]
        cells = [cell for cell in cells if cell]
        if cells:
            rows.append(" | ".join(cells))
    return "\n".join(rows)


def _extract_with_soup(
    html: str, drop_lower: frozenset[str], stop_lower: str | None
) -> list[TextBlock]:
    """Heading-scoped extraction without trafilatura's content detection.

    Trafilatura gives up on some publisher templates entirely (ACC's guideline
    perspectives pages, for one) and returns nothing at all. Falling back to a
    plain DOM walk recovers the article body; it is noisier, which the length
    threshold and the drop lists absorb.
    """
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "nav", "header", "footer", "noscript", "form"]):
        tag.decompose()

    blocks: list[TextBlock] = []
    current_heading = ""
    skipping = False

    for node in soup.find_all(["h1", "h2", "h3", "h4", "p", "li", "td"]):
        text = _clean(node.get_text(" ", strip=True))
        if not text:
            continue
        if node.name in ("h1", "h2", "h3", "h4"):
            if stop_lower and text.lower().startswith(stop_lower):
                break
            current_heading = text
            skipping = _should_drop(text, drop_lower)
            continue
        if skipping or len(text) < 40:
            continue
        blocks.append(TextBlock(current_heading, text))

    return blocks


# A content-heavy page that yields almost no text means the extractor lost the
# article, not that the article is short. Several NICE guideline chapters return
# ~700 characters from 180KB of HTML — enough that trafilatura reports success,
# little enough that the guideline is effectively missing from the corpus.
_THIN_OUTPUT_CHARS = 2_000
_SUBSTANTIAL_PAGE_CHARS = 30_000


def _text_volume(blocks: list[TextBlock]) -> int:
    return sum(len(block.text) for block in blocks)


def extract_html_blocks(
    html: str,
    *,
    drop_sections: frozenset[str] = frozenset(),
    stop_at_section: str | None = None,
) -> list[TextBlock]:
    """Extract heading-scoped blocks from an HTML page."""
    drop_lower = frozenset(section.lower().strip() for section in drop_sections)
    stop_lower = stop_at_section.lower() if stop_at_section else None

    blocks = _extract_with_trafilatura(html, drop_lower, stop_lower)

    if (
        _text_volume(blocks) < _THIN_OUTPUT_CHARS
        and len(html) > _SUBSTANTIAL_PAGE_CHARS
    ):
        fallback = _extract_with_soup(html, drop_lower, stop_lower)
        if _text_volume(fallback) > _text_volume(blocks):
            return fallback

    return blocks


def _extract_with_trafilatura(
    html: str, drop_lower: frozenset[str], stop_lower: str | None
) -> list[TextBlock]:
    xml = trafilatura.extract(
        html,
        output_format="xml",
        include_tables=True,
        include_formatting=False,
        include_comments=False,
        favor_recall=True,
    )
    if not xml:
        return []

    try:
        root = etree.fromstring(xml.encode("utf-8"))
    except etree.XMLSyntaxError:
        return []

    blocks: list[TextBlock] = []
    current_heading = ""
    skipping = False

    body = root.find("main")
    if body is None:
        body = root

    for node in body.iter():
        tag = node.tag
        if tag == "head":
            heading = _clean("".join(node.itertext()))
            if stop_lower and heading.lower().startswith(stop_lower):
                break
            current_heading = heading
            skipping = _should_drop(heading, drop_lower)
            continue

        if skipping:
            continue

        if tag == "table":
            rendered = _render_table(node)
            if rendered:
                blocks.append(TextBlock(current_heading, rendered))
            continue

        if tag in ("p", "item"):
            # Table cells surface again as their own nodes; skip anything that
            # lives inside a table we already rendered whole.
            if any(ancestor.tag == "table" for ancestor in node.iterancestors()):
                continue
            text = _clean("".join(node.itertext()))
            if len(text) >= 40:
                blocks.append(TextBlock(current_heading, text))

    return blocks


def extract_pdf_blocks(payload: bytes) -> list[TextBlock]:
    """Extract blocks from a PDF, using font size to infer headings.

    ACC/AHA publishes its guidelines as journal PDFs with no semantic markup,
    so heading detection is heuristic: a short line noticeably larger than the
    document's body text is treated as a heading.
    """
    import fitz  # PyMuPDF, imported lazily so the agent never loads it

    document = fitz.open(stream=payload, filetype="pdf")
    try:
        spans: list[tuple[float, str]] = []
        for page in document:
            data = page.get_text("dict")
            for block in data.get("blocks", []):
                for line in block.get("lines", []):
                    text = "".join(span.get("text", "") for span in line.get("spans", []))
                    text = _clean(text)
                    if not text:
                        continue
                    size = max(
                        (span.get("size", 0.0) for span in line.get("spans", [])),
                        default=0.0,
                    )
                    spans.append((size, text))
    finally:
        document.close()

    if not spans:
        return []

    sizes = sorted(size for size, _ in spans)
    body_size = sizes[len(sizes) // 2]
    heading_size = body_size * 1.15

    blocks: list[TextBlock] = []
    current_heading = ""
    buffer: list[str] = []

    def flush() -> None:
        if buffer:
            text = _clean(" ".join(buffer))
            if len(text) >= 40:
                blocks.append(TextBlock(current_heading, text))
            buffer.clear()

    for size, text in spans:
        if size >= heading_size and len(text) <= 120:
            flush()
            current_heading = text
        else:
            buffer.append(text)
    flush()

    return blocks
