"""American Academy of Family Physicians clinical recommendation topics.

Note on scope: familydoctor.org, AAFP's patient-education site, was evaluated
and rejected. Most of its condition URLs resolve to a generic A–Z index page
with HTTP 200, and one returned Spanish content — indexing it would fill the
corpus with plausible-looking chunks attributed to the wrong topic. The
aafp.org clinical-recommendation hubs below return real, topic-specific content.
"""

from __future__ import annotations

from src.ingestion.catalog import GuidelineSource, SourceDocument

_BASE = (
    "https://www.aafp.org/family-physician/patient-care/"
    "clinical-recommendations/all-clinical-recommendations/"
)


def _doc(slug: str, title: str, *, tags: tuple[str, ...]) -> SourceDocument:
    return SourceDocument(
        doc_id=f"aafp-{slug}",
        guideline_title=f"AAFP: {title}",
        url=f"{_BASE}{slug}.html",
        topic_tags=tags,
        audience="clinician",
    )


SOURCE = GuidelineSource(
    name="aafp",
    publisher="AAFP",
    jurisdiction="US",
    license_note=(
        "© American Academy of Family Physicians. Publicly accessible clinical "
        "recommendation pages, reproduced with attribution."
    ),
    crawl_delay_s=2.0,
    drop_sections=frozenset(
        {"Related Content", "More From AAFP", "Additional Resources", "Navigation"}
    ),
    documents=(
        _doc("type2-diabetes", "Diabetes: Clinical Guidelines and Practice Resources",
             tags=("diabetes", "screening_diabetes")),
        _doc("hypertension", "Hypertension Guidelines for Adult Care",
             tags=("hypertension",)),
        _doc("obesity", "Obesity Care: Clinical Guidance and Practice Resources",
             tags=("obesity", "nutrition", "lifestyle")),
        _doc("depression", "Mental and Behavioral Health: Clinical Guidance",
             tags=("mental_health",)),
        _doc("immunizations", "Immunization and Vaccine Schedules",
             tags=("immunization", "pediatrics")),
    ),
)
