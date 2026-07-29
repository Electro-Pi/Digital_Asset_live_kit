"""Types describing what a guideline source is and which documents it covers.

Every publisher is expressed as a *curated catalog* rather than a crawler.
That is a deliberate trade: the PoC requires vendor clinical experts to validate
that the assistant reads the right source documents, and a reviewable list of
titles and URLs is something a physician can sign off on. A spider's output is
not, and several of these sites render their indexes client-side anyway.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class SourceDocument:
    """One guideline document to fetch."""

    doc_id: str
    guideline_title: str
    url: str
    topic_tags: tuple[str, ...] = ()
    #: "patient" text can be quoted almost as-is; "clinician" text must be
    #: reworded before it reaches a patient. The prompt is told which is which.
    audience: str = "clinician"
    kind: str = "html"  # or "pdf"
    published_on: str = ""
    reviewed_on: str = ""
    grade: str = ""


@dataclass(frozen=True)
class GuidelineSource:
    """A publisher plus everything needed to fetch and attribute it."""

    name: str
    publisher: str
    jurisdiction: str
    license_note: str
    documents: tuple[SourceDocument, ...]
    #: Seconds between requests to this host. Honors published crawl-delay
    #: where the site declares one.
    crawl_delay_s: float = 2.0
    #: Headings whose section bodies carry no clinical content worth indexing
    #: (comment logs, boilerplate, reference lists).
    drop_sections: frozenset[str] = field(default_factory=frozenset)
    #: Once this heading is reached, ignore the rest of the document.
    stop_at_section: str | None = None

    def document(self, doc_id: str) -> SourceDocument | None:
        for doc in self.documents:
            if doc.doc_id == doc_id:
                return doc
        return None
