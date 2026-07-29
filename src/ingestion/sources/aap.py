"""American Academy of Pediatrics, via its HealthyChildren.org patient site.

AAP's formal clinical reports in *Pediatrics* are largely paywalled. The public
subset here is HealthyChildren.org, which AAP publishes and writes for parents —
the right register for a patient assistant fielding questions about a child.
"""

from __future__ import annotations

from src.ingestion.catalog import GuidelineSource, SourceDocument

_BASE = "https://www.healthychildren.org/English/"


def _doc(doc_id: str, title: str, path: str, *, tags: tuple[str, ...]) -> SourceDocument:
    return SourceDocument(
        doc_id=f"aap-{doc_id}",
        guideline_title=f"AAP HealthyChildren: {title}",
        url=_BASE + path,
        topic_tags=tags,
        audience="patient",
    )


SOURCE = GuidelineSource(
    name="aap",
    publisher="AAP",
    jurisdiction="US",
    license_note=(
        "© American Academy of Pediatrics, HealthyChildren.org. Public patient "
        "education content, reproduced with attribution."
    ),
    crawl_delay_s=2.0,
    drop_sections=frozenset({"Additional Information", "More Information", "About Dr"}),
    # Article pages, not the category landing pages. The landing pages return
    # HTTP 200 and extract to one or two chunks of link text, which looks like
    # coverage in the corpus report without being any.
    documents=(
        _doc("fever-without-fear", "Fever Without Fear: Information for Parents",
             "health-issues/conditions/fever/Pages/Fever-Without-Fear.aspx",
             tags=("pediatrics", "infection")),
        _doc("fever-when-to-call", "Fever: When to Call the Pediatrician",
             "health-issues/conditions/fever/Pages/When-to-Call-the-Pediatrician.aspx",
             tags=("pediatrics", "infection")),
        _doc("ear-infection", "Ear Infections in Children",
             "health-issues/conditions/ear-nose-throat/Pages/Ear-Infection-Information.aspx",
             tags=("pediatrics", "infection")),
        _doc("diarrhea", "Diarrhea in Children",
             "health-issues/conditions/abdominal/Pages/Diarrhea.aspx",
             tags=("pediatrics", "gastrointestinal")),
        _doc("constipation", "Constipation in Children",
             "health-issues/conditions/abdominal/Pages/Constipation.aspx",
             tags=("pediatrics", "gastrointestinal")),
        _doc("eczema", "Eczema in Children",
             "health-issues/conditions/skin/Pages/Eczema.aspx",
             tags=("pediatrics", "dermatology")),
        _doc("immunization-schedule", "The AAP Recommended Immunization Schedule",
             "safety-prevention/immunizations/Pages/Recommended-Immunization-Schedules.aspx",
             tags=("pediatrics", "immunization")),
        _doc("nutrition", "Childhood Nutrition",
             "healthy-living/nutrition/Pages/Childhood-Nutrition.aspx",
             tags=("pediatrics", "nutrition")),
        _doc("physical-activity", "Encouraging Your Child to Be Physically Active",
             "healthy-living/fitness/Pages/Encouraging-Your-Child-to-be-Physically-Active.aspx",
             tags=("pediatrics", "lifestyle")),
        _doc("breastfeeding-immunity", "Breastfeeding Benefits Your Baby's Immune System",
             "ages-stages/baby/breastfeeding/Pages/Breastfeeding-Benefits-Your-Babys-Immune-System.aspx",
             tags=("pediatrics", "nutrition")),
    ),
)
