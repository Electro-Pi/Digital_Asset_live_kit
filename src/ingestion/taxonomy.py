"""Controlled topic vocabulary for the corpus.

The tags mirror the clinical scope in the PoC document rather than any general
medical ontology, because their job is to answer one question during review:
"does the corpus actually cover what KFSHRC asked for?" A coverage report is
only meaningful if the tag set and the scope list are the same list.
"""

from __future__ import annotations

# tag -> human label used in the coverage report
TOPICS: dict[str, str] = {
    # ─── Urgent care presentations (Category 4 / stable Category 3) ───────────
    "msk": "Musculoskeletal (knee/back/shoulder/neck pain, trauma)",
    "respiratory": "Respiratory (URTI/LRTI, asthma, COPD, PE)",
    "gastrointestinal": "Gastrointestinal (gastroenteritis, abdominal pain, GERD, constipation)",
    "cardiology": "Cardiology (chest pain, ACS, hypertensive emergency, heart failure)",
    "vascular": "Vascular (DVT, varicose veins, sickle cell crisis)",
    "neurology": "Neurology (TIA/stroke, headache, dizziness)",
    "ophthalmology": "Ophthalmology (red eye, foreign body, vision problems)",
    "dermatology": "Dermatology (eczema, urticaria, abscess, acne)",
    "obgyn": "OB/GYN (PV bleeding, ectopic, AUB, menorrhagia, fetal movements)",
    "infection": "Infections (sepsis, abscess)",
    "endocrine_acute": "Endocrine emergencies (hypo/hyperglycemia, DKA, thyroid crisis)",
    "procedural": "Procedural (catheter change, NG tube)",
    # ─── Chronic disease management ──────────────────────────────────────────
    "diabetes": "Diabetes mellitus",
    "hypertension": "Hypertension",
    "hyperlipidemia": "Hyperlipidemia / dyslipidemia",
    "thyroid": "Hypothyroidism / thyroid disease",
    "obesity": "Obesity and weight management",
    "ckd": "Chronic kidney disease",
    "osteoarthritis": "Osteoarthritis",
    "osteoporosis": "Osteoporosis",
    # ─── Prevention and screening ────────────────────────────────────────────
    "screening_diabetes": "Screening: diabetes / prediabetes",
    "screening_lipids": "Screening: dyslipidemia",
    "screening_breast": "Screening: breast cancer",
    "screening_cervical": "Screening: cervical cancer",
    "screening_colorectal": "Screening: colorectal cancer",
    "screening_lung": "Screening: lung cancer",
    "immunization": "Immunization and vaccination",
    # ─── Counseling ──────────────────────────────────────────────────────────
    "lifestyle": "Lifestyle counseling (activity, smoking, alcohol)",
    "nutrition": "Nutrition counseling",
    "pediatrics": "Pediatric care and well-child",
    "pregnancy": "Pregnancy and prenatal care",
    "mental_health": "Mental health (depression, anxiety screening)",
}

TOPIC_TAGS: tuple[str, ...] = tuple(TOPICS)

# Coarse keyword fallback used when the enrichment LLM is unavailable or its
# output fails validation. Deliberately high-precision: a missing tag only
# weakens an optional filter, a wrong tag pollutes the coverage report.
_KEYWORD_HINTS: dict[str, tuple[str, ...]] = {
    "diabetes": ("diabetes", "diabetic", "hba1c", "glycemic", "insulin", "metformin"),
    "screening_diabetes": ("prediabetes", "screening for diabetes", "glucose screening"),
    "hypertension": ("hypertension", "blood pressure", "antihypertensive"),
    "hyperlipidemia": ("cholesterol", "lipid", "statin", "ldl", "dyslipidemia"),
    "screening_lipids": ("lipid screening", "cholesterol screening"),
    "thyroid": ("thyroid", "hypothyroid", "hyperthyroid", "levothyroxine", "tsh"),
    "obesity": ("obesity", "overweight", "body mass index", "weight loss", "bmi"),
    "ckd": ("chronic kidney", "renal", "egfr", "albuminuria", "nephropathy"),
    "osteoarthritis": ("osteoarthritis", "joint pain", "knee pain"),
    "osteoporosis": ("osteoporosis", "bone mineral density", "fracture risk", "dexa"),
    "screening_breast": ("breast cancer", "mammograph"),
    "screening_cervical": ("cervical cancer", "cervical cytology", "pap ", "hpv test"),
    "screening_colorectal": ("colorectal", "colonoscopy", "sigmoidoscopy", "fit test"),
    "screening_lung": ("lung cancer", "low-dose computed tomography", "ldct"),
    "cardiology": ("chest pain", "myocardial", "coronary", "heart failure", "angina"),
    "vascular": ("deep vein thrombosis", "varicose", "sickle cell", "thromboembolism"),
    "neurology": ("stroke", "transient ischemic", "headache", "migraine", "dizziness"),
    "respiratory": ("asthma", "copd", "pneumonia", "bronchitis", "pulmonary embolism"),
    "gastrointestinal": ("gastroenteritis", "gerd", "reflux", "constipation", "abdominal pain"),
    "msk": ("low back pain", "shoulder pain", "neck pain", "musculoskeletal", "sprain"),
    "dermatology": ("eczema", "urticaria", "acne", "rash", "dermatitis"),
    "ophthalmology": ("conjunctivitis", "red eye", "visual acuity", "vision"),
    "obgyn": ("uterine bleeding", "menorrhagia", "ectopic", "menopaus", "contracept"),
    "pregnancy": ("pregnan", "prenatal", "gestational", "fetal"),
    "pediatrics": ("infant", "child", "adolescent", "newborn", "well-child"),
    "immunization": ("vaccin", "immuniz"),
    "infection": ("sepsis", "abscess", "cellulitis", "antibiotic"),
    "lifestyle": ("physical activity", "tobacco", "smoking cessation", "alcohol use"),
    "nutrition": ("diet", "nutrition", "healthy eating", "sodium intake"),
    "mental_health": ("depression", "anxiety", "suicide risk", "mental health"),
    "endocrine_acute": ("hypoglycemia", "ketoacidosis", "hyperglycemic crisis"),
}


def guess_topics(text: str, *, limit: int = 4) -> tuple[str, ...]:
    """Best-effort tagging from surface keywords."""
    lowered = text.lower()
    scored: list[tuple[int, str]] = []
    for tag, hints in _KEYWORD_HINTS.items():
        hits = sum(1 for hint in hints if hint in lowered)
        if hits:
            scored.append((hits, tag))
    scored.sort(reverse=True)
    return tuple(tag for _, tag in scored[:limit])


def valid_topics(tags: object) -> tuple[str, ...]:
    """Filter arbitrary model output down to the controlled vocabulary."""
    if not isinstance(tags, (list, tuple)):
        return ()
    return tuple(
        tag for tag in dict.fromkeys(str(t).strip().lower() for t in tags)
        if tag in TOPICS
    )
