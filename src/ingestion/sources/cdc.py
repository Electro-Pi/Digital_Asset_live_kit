"""Centers for Disease Control and Prevention public health pages.

CDC content is US Government work in the public domain, and its pages are
written for patients — which makes this the source the assistant can quote most
directly. Curated by topic against the PoC's clinical scope.
"""

from __future__ import annotations

from src.ingestion.catalog import GuidelineSource, SourceDocument


def _doc(
    doc_id: str, title: str, path: str, *, tags: tuple[str, ...]
) -> SourceDocument:
    return SourceDocument(
        doc_id=f"cdc-{doc_id}",
        guideline_title=f"CDC: {title}",
        url=f"https://www.cdc.gov/{path}",
        topic_tags=tags,
        audience="patient",
    )


SOURCE = GuidelineSource(
    name="cdc",
    publisher="CDC",
    jurisdiction="US",
    license_note=(
        "CDC content is a work of the U.S. Government and is in the public domain."
    ),
    crawl_delay_s=1.5,
    drop_sections=frozenset({"Resources", "References", "Related Pages"}),
    documents=(
        # ─── Diabetes ────────────────────────────────────────────────────────
        _doc("diabetes-about", "About Diabetes", "diabetes/about/index.html",
             tags=("diabetes",)),
        _doc("diabetes-prevention", "Preventing Type 2 Diabetes",
             "diabetes/prevention-type-2/index.html",
             tags=("diabetes", "screening_diabetes", "lifestyle")),
        _doc("diabetes-treatment", "Treatment of Diabetes",
             "diabetes/treatment/index.html", tags=("diabetes",)),
        # ─── Cardiovascular ──────────────────────────────────────────────────
        _doc("bp-about", "About High Blood Pressure",
             "high-blood-pressure/about/index.html", tags=("hypertension",)),
        _doc("bp-prevention", "Preventing High Blood Pressure",
             "high-blood-pressure/prevention/index.html",
             tags=("hypertension", "lifestyle")),
        _doc("cholesterol-about", "About Cholesterol",
             "cholesterol/about/index.html", tags=("hyperlipidemia",)),
        _doc("heart-disease-about", "About Heart Disease",
             "heart-disease/about/index.html", tags=("cardiology",)),
        _doc("heart-attack", "About Heart Attack Symptoms, Risk, and Recovery",
             "heart-disease/about/heart-attack.html", tags=("cardiology",)),
        _doc("stroke-about", "About Stroke", "stroke/about/index.html",
             tags=("neurology",)),
        _doc("stroke-signs", "Stroke Signs and Symptoms",
             "stroke/signs-symptoms/index.html", tags=("neurology",)),
        # ─── Cancer screening ────────────────────────────────────────────────
        _doc("crc-screening", "Colorectal Cancer Screening",
             "colorectal-cancer/screening/index.html", tags=("screening_colorectal",)),
        _doc("breast-screening", "Breast Cancer Screening",
             "breast-cancer/screening/index.html", tags=("screening_breast",)),
        _doc("cervical-screening", "Cervical Cancer Screening",
             "cervical-cancer/screening/index.html", tags=("screening_cervical",)),
        _doc("lung-screening", "Lung Cancer Screening",
             "lung-cancer/screening/index.html", tags=("screening_lung",)),
        # ─── Respiratory ─────────────────────────────────────────────────────
        _doc("asthma-about", "About Asthma", "asthma/about/index.html",
             tags=("respiratory",)),
        _doc("copd-about", "About COPD", "copd/about/index.html",
             tags=("respiratory",)),
        # ─── Weight, kidney ──────────────────────────────────────────────────
        _doc("obesity-adults", "Adult Obesity Facts",
             "obesity/adult-obesity-facts/index.html", tags=("obesity",)),
        _doc("healthy-weight", "Healthy Weight and Growth",
             "healthy-weight-growth/about/index.html",
             tags=("obesity", "nutrition", "lifestyle")),
        _doc("ckd-about", "About Chronic Kidney Disease",
             "kidney-disease/about/index.html", tags=("ckd",)),
        # ─── Infection and immunization ──────────────────────────────────────
        _doc("adult-immunization", "Adult Immunization Schedule by Age",
             "vaccines/hcp/imz-schedules/adult-age.html", tags=("immunization",)),
        _doc("sepsis-about", "About Sepsis", "sepsis/about/index.html",
             tags=("infection",)),
        _doc("norovirus-about", "About Norovirus", "norovirus/about/index.html",
             tags=("gastrointestinal", "infection")),
        _doc("dengue-about", "About Dengue", "dengue/about/index.html",
             tags=("infection",)),
        # ─── Lifestyle ───────────────────────────────────────────────────────
        _doc("physical-activity", "Physical Activity Guidelines",
             "physical-activity-basics/guidelines/index.html", tags=("lifestyle",)),
        _doc("nutrition-about", "About Nutrition", "nutrition/php/about/index.html",
             tags=("nutrition",)),
        _doc("quit-smoking", "How to Quit Smoking",
             "tobacco/campaign/tips/quit-smoking/index.html", tags=("lifestyle",)),
    ),
)
