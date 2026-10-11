"""Write the structured attributes the agent searches on: dish type and protein.

The frontend's catalog has no "what kind of dish is this" field, and the agent
used to guess it at runtime from words in each dish's name. That guess decided
ranking and pairing, so a wrong guess (salad as a filter word, a salad as a
side dish) silently changed what the caller was offered.

This script makes the guess once, offline, and writes it to
``resturant's-catalog/meal_attributes.json``. The runtime only reads that file.
A human can correct any row; corrected rows carry ``"reviewed": true`` and are
never overwritten by a rerun. Rerun after the catalog changes:

    python scripts/annotate_catalog.py            # add rows for new meals
    python scripts/annotate_catalog.py --check    # exit 1 if a meal lacks a row

Types: main, side, salad, soup, drink, dessert, grocery.
Proteins: meat, chicken, seafood, vegetarian (vegetarian only from an allow-list,
never from the absence of a meat word).
Tags: crispy.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from src.catalog_text import has_keyword, normalize_ar  # noqa: E402

CATALOG_PATH = REPO_ROOT / "resturant's-catalog" / "catalog.json"
ATTRIBUTES_PATH = REPO_ROOT / "resturant's-catalog" / "meal_attributes.json"

TYPES = ("main", "side", "salad", "soup", "drink", "dessert", "grocery")
PROTEINS = ("meat", "chicken", "seafood", "vegetarian")
TAGS = ("crispy",)
CRISPY_WORDS = ("مقرمش", "كرسبي", "كريسبي", "crispy")

GROCERY_CUISINES = {"بقاله"}
DESSERT_CUISINES = {"حلويات"}

PROTEIN_KEYWORDS = {
    "meat": (
        "لحم", "لحوم", "لحمه", "غنم", "بقري", "بقر", "كفته", "كباب", "ستيك",
        "كبده", "بيبروني", "بولونيز", "فيندالو", "برجر", "ريش",
    ),
    "chicken": ("دجاج", "دجاجه", "تشيكن", "فراخ", "مظبي", "طاووق", "وينجز", "اجنحه", "ستربس"),
    "seafood": (
        "سمك", "سمكه", "روبيان", "جمبري", "سلمون", "تونا", "تونه", "هامور", "كاليماري",
        "حبار", "صياديه", "انقليس", "تمبورا", "بحري", "سوشي", "فيليه",
    ),
}
VEGETARIAN_KEYWORDS = (
    "خضار", "سلطه", "فلافل", "حمص", "فول", "عدس", "دال", "بانير", "كينوا",
    "افوكادو", "سموذي", "زبادي", "ادمامي", "توفو", "مناقيش", "زعتر", "فتوش",
    "سبانخ", "متبل", "بطاطس", "بصل", "خيار", "جزر", "مشروم", "زيتون", "جبن",
    "موزاريلا",
)
DRINK_NAME = (
    "قهوه", "لاتيه", "ميلك شيك", "عصير", "مياه", "مشروب", "شاي", "سموذي", "كولا",
    "غازي", "لبن", "حليب", "كرك", "موهيتو", "ليموناضه", "كابتشينو", "اسبريسو",
    "موكا", "ماتشا", "مانجو لاسي", "لاسي مانجو", "ايس تي",
)
DESSERT_NAME = (
    "حلا", "حلويات", "حلاوه", "كنافه", "بقلاوه", "بسبوسه", "لقيمات", "معمول",
    "كيك", "كيكه", "تشيز كيك", "براوني", "تيراميسو", "مولتن", "وافل", "كريب",
    "دونات", "دوناتس", "دونتس", "مافن", "سينابون", "ايس كريم", "مثلجات", "جيلاتي",
    "قطايف", "مهلبيه", "ام علي", "نوتيلا", "شوكولاته", "تمر محشي", "بوكس حلويات",
    "تمر بالقشطه", "تمر وسمن", "زبادي بالعسل", "زبادي يوناني",
)
SAVOURY_NAME = (
    "توست", "ساندويتش", "راب", "سلطه", "بيض", "جبنه", "دجاج", "لحم", "برجر",
    "بيتزا", "شوربه", "فلافل", "حمص", "مناقيش", "منقوشه", "بعجين", "مسخن",
    "فطور", "صحن", "نقانق", "بيكون",
)
SALAD_NAME = ("سلطه", "سلطات", "فتوش", "تبوله", "كول سلو")
SOUP_NAME = ("شوربه",)
SIDE_NAME = (
    "بطاطس", "مخلل", "ثوم", "بروشيتا", "بروسكيتا", "مقبلات", "ادامامي",
    "ورق عنب", "سمبوسه", "سبرينغ رول", "متبل", "ذره", "خبز", "نان", "كبه",
    "حلقات بصل", "صاموسا", "جيوزا", "غيوزا",
)
# A dish named after a side but served as a plate: «حمص باللحمة», «صحن فلافل».
PLATE_WORDS = ("صحن", "باللحمه", "بالدجاج", "بول")


def classify_type(meal: dict, cuisine: str) -> str:
    name = normalize_ar(meal.get("name") or "")
    description = normalize_ar(meal.get("description") or "")
    if cuisine in GROCERY_CUISINES:
        return "grocery"
    if meal.get("isCombo") or meal.get("offerKind") in ("combo", "family"):
        # A bundle is a meal. Its drink or salad is a side of it, unless the
        # bundle is itself salads or sweets («تشكيلة حلا عائلية»).
        if has_keyword(name, SALAD_NAME):
            return "salad"
        if has_keyword(name, DESSERT_NAME):
            return "dessert"
        return "main"
    if has_keyword(name, DESSERT_NAME):
        return "dessert"
    if has_keyword(name, DRINK_NAME):
        return "drink"
    if has_keyword(name, SALAD_NAME):
        return "salad"
    if has_keyword(name, SOUP_NAME):
        return "soup"
    if cuisine in DESSERT_CUISINES and not has_keyword(name, SAVOURY_NAME):
        return "drink" if has_keyword(description, ("مشروب", "قهوه", "شاي")) else "dessert"
    if has_keyword(name, SIDE_NAME) and not has_keyword(name, PLATE_WORDS):
        return "side"
    return "main"


def classify_proteins(meal: dict) -> list[str]:
    name = normalize_ar(meal.get("name") or "")
    description = normalize_ar(meal.get("description") or "")
    includes = normalize_ar(" ".join(meal.get("comboIncludes") or []))
    blob = f"{name} {description} {includes}"
    tags = [tag for tag, words in PROTEIN_KEYWORDS.items() if has_keyword(blob, words)]
    # Vegetarian reads the dish's own words only: a bundle's side salad says
    # nothing about its main course.
    if not tags and has_keyword(f"{name} {description}", VEGETARIAN_KEYWORDS):
        tags.append("vegetarian")
    return tags


def classify_tags(meal: dict) -> list[str]:
    blob = normalize_ar(f"{meal.get('name') or ''} {meal.get('description') or ''}")
    return ["crispy"] if has_keyword(blob, CRISPY_WORDS) else []


def build(catalog: dict, existing: dict) -> dict:
    out: dict[str, dict] = {}
    for restaurant in catalog.get("restaurants") or []:
        cuisine = normalize_ar(restaurant.get("cuisine") or "")
        for meal in restaurant.get("meals") or []:
            meal_id = str(meal.get("id"))
            kept = existing.get(meal_id)
            if kept and kept.get("reviewed"):
                out[meal_id] = kept
                continue
            out[meal_id] = {
                "name": meal.get("name"),
                "type": classify_type(meal, cuisine),
                "protein": classify_proteins(meal),
                "tags": classify_tags(meal),
            }
    return dict(sorted(out.items()))


def validate(catalog: dict, attributes: dict) -> list[str]:
    problems = []
    ids = {str(m.get("id")) for r in catalog.get("restaurants") or [] for m in r.get("meals") or []}
    for meal_id in sorted(ids - set(attributes)):
        problems.append(f"{meal_id}: no attributes row")
    for meal_id, row in attributes.items():
        if row.get("type") not in TYPES:
            problems.append(f"{meal_id}: bad type {row.get('type')!r}")
        bad = [p for p in row.get("protein") or [] if p not in PROTEINS]
        if bad:
            problems.append(f"{meal_id}: bad protein {bad}")
        bad_tags = [t for t in row.get("tags") or [] if t not in TAGS]
        if bad_tags:
            problems.append(f"{meal_id}: bad tags {bad_tags}")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--check", action="store_true", help="validate only, write nothing")
    args = parser.parse_args()
    catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    existing = json.loads(ATTRIBUTES_PATH.read_text(encoding="utf-8")) if ATTRIBUTES_PATH.exists() else {}
    if args.check:
        problems = validate(catalog, existing)
        for p in problems:
            print(p)
        return 1 if problems else 0
    attributes = build(catalog, existing)
    ATTRIBUTES_PATH.write_text(json.dumps(attributes, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    counts: dict[str, int] = {}
    for row in attributes.values():
        counts[row["type"]] = counts.get(row["type"], 0) + 1
    print(f"wrote {len(attributes)} rows to {ATTRIBUTES_PATH.name}: {counts}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
