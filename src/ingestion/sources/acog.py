"""American College of Obstetricians and Gynecologists patient FAQs.

ACOG's Practice Bulletins — the clinician-facing guidance — are members-only,
so the corpus uses the public patient FAQ series. For a patient-facing assistant
this is arguably the better source anyway: it is ACOG's own plain-language
guidance, already written for the person asking.
"""

from __future__ import annotations

from src.ingestion.catalog import GuidelineSource, SourceDocument

_BASE = "https://www.acog.org/womens-health/faqs/"


def _doc(slug: str, title: str, *, tags: tuple[str, ...]) -> SourceDocument:
    return SourceDocument(
        doc_id=f"acog-{slug}",
        guideline_title=f"ACOG FAQ: {title}",
        url=_BASE + slug,
        topic_tags=tags,
        audience="patient",
    )


SOURCE = GuidelineSource(
    name="acog",
    publisher="ACOG",
    jurisdiction="US",
    license_note=(
        "© American College of Obstetricians and Gynecologists. Public patient "
        "FAQ content, reproduced with attribution. Practice Bulletins are not "
        "publicly available and are not included."
    ),
    crawl_delay_s=2.0,
    drop_sections=frozenset({"Glossary", "Article continues below", "Related Content"}),
    documents=(
        _doc("bleeding-during-pregnancy", "Bleeding During Pregnancy",
             tags=("pregnancy", "obgyn")),
        _doc("ectopic-pregnancy", "Ectopic Pregnancy", tags=("pregnancy", "obgyn")),
        _doc("early-pregnancy-loss", "Early Pregnancy Loss",
             tags=("pregnancy", "obgyn")),
        _doc("prenatal-care", "Prenatal Care", tags=("pregnancy",)),
        _doc("gestational-diabetes", "Gestational Diabetes",
             tags=("pregnancy", "diabetes")),
        _doc("preeclampsia-and-high-blood-pressure-during-pregnancy",
             "Preeclampsia and High Blood Pressure During Pregnancy",
             tags=("pregnancy", "hypertension")),
        _doc("abnormal-uterine-bleeding", "Abnormal Uterine Bleeding",
             tags=("obgyn",)),
        _doc("heavy-menstrual-bleeding", "Heavy Menstrual Bleeding", tags=("obgyn",)),
        _doc("perimenopausal-bleeding-and-bleeding-after-menopause",
             "Perimenopausal Bleeding and Bleeding After Menopause", tags=("obgyn",)),
        _doc("cervical-cancer-screening", "Cervical Cancer Screening",
             tags=("screening_cervical", "obgyn")),
    ),
)
