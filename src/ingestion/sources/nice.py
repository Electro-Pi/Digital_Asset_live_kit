"""NICE clinical guidelines (UK).

The ``/chapter/Recommendations`` view is used rather than the guidance overview
page: the overview is a landing page of links, while the Recommendations chapter
is the actual numbered guidance.

Licensing: NICE guidance is free to access and may be reused for non-commercial
purposes with attribution, but NICE restricts commercial reuse. Every chunk is
tagged ``jurisdiction=UK`` so the agent can flag when UK-specific advice may not
transfer, and the licence note is reproduced in the corpus report for KFSHRC's
review. If the PoC converts to a commercial engagement, NICE syndication terms
need to be revisited before these documents ship.
"""

from __future__ import annotations

from src.ingestion.catalog import GuidelineSource, SourceDocument


def _doc(code: str, title: str, *, tags: tuple[str, ...]) -> SourceDocument:
    return SourceDocument(
        doc_id=f"nice-{code}",
        guideline_title=f"NICE {code.upper()}: {title}",
        url=f"https://www.nice.org.uk/guidance/{code}/chapter/Recommendations",
        topic_tags=tags,
        audience="clinician",
    )


SOURCE = GuidelineSource(
    name="nice",
    publisher="NICE",
    jurisdiction="UK",
    license_note=(
        "© NICE. Reproduced with permission for non-commercial use with "
        "attribution. NICE guidance is prepared for the UK National Health "
        "Service and may not apply in other jurisdictions."
    ),
    crawl_delay_s=2.0,
    drop_sections=frozenset(
        {"Terms used in this guideline", "Recommendations for research", "Context"}
    ),
    documents=(
        _doc("ng28", "Type 2 diabetes in adults: management", tags=("diabetes",)),
        _doc("ng17", "Type 1 diabetes in adults: diagnosis and management",
             tags=("diabetes",)),
        _doc("ng136", "Hypertension in adults: diagnosis and management",
             tags=("hypertension",)),
        _doc("ng238", "Cardiovascular disease: risk assessment and reduction, "
             "including lipid modification",
             tags=("hyperlipidemia", "cardiology", "screening_lipids")),
        _doc("ng185", "Acute coronary syndromes", tags=("cardiology",)),
        _doc("ng106", "Chronic heart failure in adults: diagnosis and management",
             tags=("cardiology",)),
        _doc("ng203", "Chronic kidney disease: assessment and management",
             tags=("ckd",)),
        _doc("ng115", "Chronic obstructive pulmonary disease in over 16s: "
             "diagnosis and management", tags=("respiratory",)),
        _doc("ng226", "Osteoarthritis in over 16s: diagnosis and management",
             tags=("osteoarthritis", "msk")),
        _doc("ng59", "Low back pain and sciatica in over 16s: assessment and "
             "management", tags=("msk",)),
        _doc("ng158", "Venous thromboembolic diseases: diagnosis, management and "
             "thrombophilia testing", tags=("vascular",)),
        _doc("ng12", "Suspected cancer: recognition and referral",
             tags=("screening_colorectal", "screening_breast", "screening_lung")),
        _doc("ng233", "Otitis media with effusion in under 12s",
             tags=("pediatrics", "infection")),
    ),
)
