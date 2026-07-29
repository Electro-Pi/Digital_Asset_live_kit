"""U.S. Preventive Services Task Force recommendation statements.

The Task Force's own A–Z index is rendered client-side, so the document list is
curated rather than crawled. Every slug below was confirmed to resolve; the
selection covers the screening, prevention and counselling topics named in the
PoC's clinical scope.

USPSTF pages carry a large evidence-review tail (scope of review, public
comment responses, reference lists) that is of no use to a patient-facing
assistant and would otherwise dominate the index, so extraction stops at the
supporting-evidence section.
"""

from __future__ import annotations

from src.ingestion.catalog import GuidelineSource, SourceDocument

_BASE = "https://www.uspreventiveservicestaskforce.org/uspstf/recommendation/"


def _doc(
    slug: str, title: str, *, tags: tuple[str, ...], audience: str = "clinician"
) -> SourceDocument:
    return SourceDocument(
        doc_id=f"uspstf-{slug}",
        guideline_title=f"USPSTF Recommendation: {title}",
        url=_BASE + slug,
        topic_tags=tags,
        audience=audience,
    )


SOURCE = GuidelineSource(
    name="uspstf",
    publisher="USPSTF",
    jurisdiction="US",
    license_note=(
        "USPSTF recommendations are freely available to the public; "
        "reproduced with attribution to the U.S. Preventive Services Task Force."
    ),
    # robots.txt asks for 5 seconds between requests.
    crawl_delay_s=5.0,
    # Everything from the evidence review onward is written for methodologists,
    # not patients, and the public-comment section is mostly reference lists —
    # left in, it accounted for a quarter of the entire corpus.
    drop_sections=frozenset(
        {
            "additional information",
            "recommendation information",
            "other related uspstf recommendations",
            "additional tools and resources",
            "response to public comment",
            "scope of review",
            "accuracy of screening tests",
            "benefits of early detection",
            "harms of screening",
            "harms of early detection",
            "how does evidence fit",
            "research needs and gaps",
            "update of previous",
            "supporting evidence",
            "recommendations of others",
            "estimate of magnitude of net benefit",
            "member of the u.s. preventive services task force",
            "copyright and source information",
        }
    ),
    documents=(
        # ─── Cancer screening ────────────────────────────────────────────────
        _doc("colorectal-cancer-screening", "Colorectal Cancer: Screening",
             tags=("screening_colorectal",)),
        _doc("breast-cancer-screening", "Breast Cancer: Screening",
             tags=("screening_breast",)),
        _doc("cervical-cancer-screening", "Cervical Cancer: Screening",
             tags=("screening_cervical",)),
        _doc("lung-cancer-screening", "Lung Cancer: Screening",
             tags=("screening_lung",)),
        _doc("prostate-cancer-screening", "Prostate Cancer: Screening",
             tags=("screening_colorectal",)),
        _doc("skin-cancer-counseling", "Skin Cancer Prevention: Behavioral Counseling",
             tags=("dermatology", "lifestyle")),
        # ─── Cardiometabolic ─────────────────────────────────────────────────
        _doc("screening-for-prediabetes-and-type-2-diabetes",
             "Prediabetes and Type 2 Diabetes: Screening",
             tags=("screening_diabetes", "diabetes")),
        _doc("hypertension-in-adults-screening", "Hypertension in Adults: Screening",
             tags=("hypertension",)),
        _doc("statin-use-in-adults-preventive-medication",
             "Statin Use for the Primary Prevention of Cardiovascular Disease in Adults",
             tags=("hyperlipidemia", "screening_lipids", "cardiology")),
        _doc("chronic-kidney-disease-ckd-screening",
             "Chronic Kidney Disease: Screening", tags=("ckd",)),
        _doc("abdominal-aortic-aneurysm-screening",
             "Abdominal Aortic Aneurysm: Screening", tags=("vascular",)),
        # ─── Weight, lifestyle, counselling ──────────────────────────────────
        _doc("obesity-in-adults-interventions",
             "Weight Loss to Prevent Obesity-Related Morbidity and Mortality in Adults",
             tags=("obesity", "lifestyle")),
        _doc("obesity-in-children-and-adolescents-screening",
             "Obesity in Children and Adolescents: Screening",
             tags=("obesity", "pediatrics")),
        _doc("healthy-diet-and-physical-activity-counseling-adults-with-high-risk-of-cvd",
             "Healthy Diet and Physical Activity for Cardiovascular Disease Prevention "
             "in Adults With Risk Factors",
             tags=("lifestyle", "nutrition", "cardiology")),
        _doc("healthy-lifestyle-and-physical-activity-for-cvd-prevention-adults-"
             "without-known-risk-factors-behavioral-counseling",
             "Healthy Lifestyle and Physical Activity for Cardiovascular Disease "
             "Prevention in Adults Without Known Risk Factors",
             tags=("lifestyle", "nutrition")),
        _doc("tobacco-use-in-adults-and-pregnant-women-counseling-and-interventions",
             "Tobacco Smoking Cessation in Adults, Including Pregnant Persons",
             tags=("lifestyle",)),
        _doc("unhealthy-alcohol-use-in-adolescents-and-adults-screening-and-"
             "behavioral-counseling-interventions",
             "Unhealthy Alcohol Use in Adolescents and Adults: Screening and "
             "Behavioral Counseling",
             tags=("lifestyle", "mental_health")),
        # ─── Endocrine, bone, nutrition ──────────────────────────────────────
        _doc("thyroid-dysfunction-screening", "Thyroid Dysfunction: Screening",
             tags=("thyroid",)),
        _doc("osteoporosis-screening", "Osteoporosis to Prevent Fractures: Screening",
             tags=("osteoporosis",)),
        _doc("vitamin-d-deficiency-screening", "Vitamin D Deficiency in Adults: Screening",
             tags=("nutrition",)),
        # ─── Infection ───────────────────────────────────────────────────────
        _doc("hepatitis-c-screening", "Hepatitis C Virus Infection in Adolescents and "
             "Adults: Screening", tags=("infection",)),
        _doc("human-immunodeficiency-virus-hiv-infection-screening",
             "HIV Infection: Screening", tags=("infection",)),
        # ─── Pregnancy and perinatal ─────────────────────────────────────────
        _doc("gestational-diabetes-screening", "Gestational Diabetes: Screening",
             tags=("pregnancy", "diabetes")),
        _doc("folic-acid-for-the-prevention-of-neural-tube-defects-preventive-medication",
             "Folic Acid Supplementation to Prevent Neural Tube Defects",
             tags=("pregnancy", "nutrition")),
        _doc("breastfeeding-primary-care-interventions",
             "Breastfeeding: Primary Care Interventions",
             tags=("pregnancy", "pediatrics", "nutrition")),
        _doc("perinatal-depression-preventive-interventions",
             "Perinatal Depression: Preventive Interventions",
             tags=("pregnancy", "mental_health")),
    ),
)
