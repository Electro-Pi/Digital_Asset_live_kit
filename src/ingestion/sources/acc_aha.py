"""American College of Cardiology / American Heart Association.

Access note, which matters for the corpus report: the full guideline texts in
Circulation and JACC sit behind Cloudflare and are not machine-fetchable, and
only some are deposited in PubMed Central. What is publicly available and
reliably retrievable is used instead:

* the 2019 primary-prevention guideline, via its PMC deposit — full guideline text;
* ACC's "Ten Points to Remember" summary of the 2021 chest pain guideline;
* AHA and American Stroke Association patient-education pages, which are the
  same organisations' own plain-language renderings of their guidance and are a
  better fit for a patient-facing assistant than journal prose.

If KFSHRC wants the complete guideline texts indexed, that needs a licensed
feed rather than a scrape.
"""

from __future__ import annotations

from src.ingestion.catalog import GuidelineSource, SourceDocument


def _doc(
    doc_id: str, title: str, url: str, *, tags: tuple[str, ...], audience: str
) -> SourceDocument:
    return SourceDocument(
        doc_id=f"accaha-{doc_id}",
        guideline_title=title,
        url=url,
        topic_tags=tags,
        audience=audience,
    )


SOURCE = GuidelineSource(
    name="acc_aha",
    publisher="ACC/AHA",
    jurisdiction="US",
    license_note=(
        "American Heart Association / American College of Cardiology content, "
        "reproduced with attribution. Full guideline texts in Circulation and "
        "JACC are not openly retrievable; public summaries, the PMC-deposited "
        "primary prevention guideline, and AHA patient education are used."
    ),
    crawl_delay_s=2.0,
    # The PMC deposit carries the full journal apparatus; appendices alone were
    # 50 chunks of literature-search strings and disclosure tables.
    drop_sections=frozenset(
        {"references", "footnotes", "disclosures", "supplementary material",
         "author information", "acknowledgments", "appendix", "abbreviations",
         "presidential advisory", "aha scientific statements"}
    ),
    documents=(
        _doc("primary-prevention-2019",
             "2019 ACC/AHA Guideline on the Primary Prevention of Cardiovascular "
             "Disease",
             "https://pmc.ncbi.nlm.nih.gov/articles/PMC8351755/",
             tags=("cardiology", "lifestyle", "hyperlipidemia", "hypertension"),
             audience="clinician"),
        _doc("chest-pain-2021",
             "2021 AHA/ACC Chest Pain Guideline: Key Points",
             "https://www.acc.org/latest-in-cardiology/ten-points-to-remember/"
             "2021/10/27/14/06/2021-guideline-for-chest-pain-gl_chestpain",
             tags=("cardiology",), audience="clinician"),
        _doc("heart-attack-warning-signs",
             "AHA: Warning Signs of a Heart Attack",
             "https://www.heart.org/en/health-topics/heart-attack/"
             "warning-signs-of-a-heart-attack",
             tags=("cardiology",), audience="patient"),
        _doc("bp-readings",
             "AHA: Understanding Blood Pressure Readings",
             "https://www.heart.org/en/health-topics/high-blood-pressure/"
             "understanding-blood-pressure-readings",
             tags=("hypertension",), audience="patient"),
        _doc("bp-manage",
             "AHA: Changes You Can Make to Manage High Blood Pressure",
             "https://www.heart.org/en/health-topics/high-blood-pressure/"
             "changes-you-can-make-to-manage-high-blood-pressure",
             tags=("hypertension", "lifestyle"), audience="patient"),
        _doc("cholesterol-about",
             "AHA: What Is Cholesterol?",
             "https://www.heart.org/en/health-topics/cholesterol/about-cholesterol",
             tags=("hyperlipidemia",), audience="patient"),
        _doc("cholesterol-treatment",
             "AHA: Prevention and Treatment of High Cholesterol",
             "https://www.heart.org/en/health-topics/cholesterol/"
             "prevention-and-treatment-of-high-cholesterol-hyperlipidemia",
             tags=("hyperlipidemia", "lifestyle"), audience="patient"),
        _doc("heart-failure-about",
             "AHA: What Is Heart Failure?",
             "https://www.heart.org/en/health-topics/heart-failure/"
             "what-is-heart-failure",
             tags=("cardiology",), audience="patient"),
        _doc("heart-failure-signs",
             "AHA: Heart Failure Signs and Symptoms",
             "https://www.heart.org/en/health-topics/heart-failure/"
             "warning-signs-of-heart-failure",
             tags=("cardiology",), audience="patient"),
        _doc("afib-about",
             "AHA: What Is Atrial Fibrillation?",
             "https://www.heart.org/en/health-topics/atrial-fibrillation/"
             "what-is-atrial-fibrillation-afib-or-af",
             tags=("cardiology",), audience="patient"),
        _doc("diabetes-risks",
             "AHA: Diabetes Complications and Risks",
             "https://www.heart.org/en/health-topics/diabetes/"
             "diabetes-complications-and-risks",
             tags=("diabetes", "cardiology"), audience="patient"),
        _doc("diet-lifestyle",
             "AHA Diet and Lifestyle Recommendations",
             "https://www.heart.org/en/healthy-living/healthy-eating/eat-smart/"
             "nutrition-basics/aha-diet-and-lifestyle-recommendations",
             tags=("nutrition", "lifestyle"), audience="patient"),
        _doc("physical-activity",
             "AHA Recommendations for Physical Activity in Adults and Kids",
             "https://www.heart.org/en/healthy-living/fitness/fitness-basics/"
             "aha-recs-for-physical-activity-in-adults",
             tags=("lifestyle",), audience="patient"),
        _doc("stroke-symptoms",
             "American Stroke Association: Stroke Symptoms and Warning Signs",
             "https://www.stroke.org/en/about-stroke/stroke-symptoms",
             tags=("neurology",), audience="patient"),
        _doc("stroke-risk",
             "American Stroke Association: Stroke Risk Factors",
             "https://www.stroke.org/en/about-stroke/stroke-risk-factors",
             tags=("neurology",), audience="patient"),
        _doc("tia",
             "American Stroke Association: Transient Ischemic Attack (TIA)",
             "https://www.stroke.org/en/about-stroke/types-of-stroke/"
             "tia-transient-ischemic-attack",
             tags=("neurology",), audience="patient"),
    ),
)
