"""Alawaly Real Estate (العوالي العقارية) Catalog & Knowledge Base.

Provides structured access to company facts, projects, units, locations,
awards, contact channels, and website page routes.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from loguru import logger

from src.catalog_text import normalize_ar
from src.spoken_lexicon import clean_for_speech

_DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "alawaly_catalog.json"

_CATALOG_CACHE: Optional[dict[str, Any]] = None


def load_catalog(path: Optional[str | Path] = None) -> dict[str, Any]:
    """Load the Alawaly real estate knowledge base into memory."""
    global _CATALOG_CACHE
    target = Path(path) if path else _DATA_PATH
    if not target.exists():
        logger.error("Alawaly catalog not found at {}", target)
        raise FileNotFoundError(f"Catalog file not found: {target}")

    with open(target, "r", encoding="utf-8") as f:
        data = json.load(f)

    _CATALOG_CACHE = data
    logger.info(
        "Alawaly catalog loaded successfully: {} projects, {} news items",
        len(data.get("projects", [])),
        len(data.get("latest_news", [])),
    )
    return _CATALOG_CACHE


def get_catalog() -> dict[str, Any]:
    global _CATALOG_CACHE
    if _CATALOG_CACHE is None:
        return load_catalog()
    return _CATALOG_CACHE


def check_catalog_sync() -> bool:
    """Verify catalog availability and integrity."""
    cat = get_catalog()
    return bool(cat.get("company") and cat.get("projects"))


def get_company() -> dict[str, Any]:
    return get_catalog().get("company", {})


def get_contact() -> dict[str, Any]:
    return get_catalog().get("contact", {})


def get_all_projects() -> list[dict[str, Any]]:
    return get_catalog().get("projects", [])


def get_other_projects() -> list[dict[str, Any]]:
    return get_catalog().get("other_projects", [])


def get_news() -> list[dict[str, Any]]:
    return get_catalog().get("latest_news", [])


def get_news_item(identifier: str) -> Optional[dict[str, Any]]:
    """Look up one article by id, URL, or a piece of its Arabic title."""
    if not identifier:
        return None
    raw = str(identifier).strip()
    norm = normalize_ar(raw)
    hits: list[dict[str, Any]] = []
    for item in get_news():
        if raw in (item.get("id"), item.get("url")):
            return item
        title = normalize_ar(item.get("title", ""))
        if norm and len(norm) >= 4 and norm in title:
            hits.append(item)
    if len(hits) == 1:
        return hits[0]
    return None


def get_pages() -> dict[str, Any]:
    return get_catalog().get("navigation_pages", {})


def get_project(identifier: str) -> Optional[dict[str, Any]]:
    """Look up a project by slug, id, or normalized Arabic name."""
    if not identifier:
        return None
    raw = str(identifier).strip()
    norm = normalize_ar(raw)
    slug_key = raw.lower().replace(" ", "-")

    for p in get_all_projects():
        if p["slug"] == slug_key or p["id"] == slug_key:
            return p
        if norm in normalize_ar(p["name"]) or normalize_ar(p["name"]) in norm:
            return p
        # Check aliases / keywords (e.g. التيسير -> alawaly-homes-al-taysir-jeddah)
        if norm in normalize_ar(p.get("name_say_ar", "")):
            return p
    return None


def search_projects(
    *,
    query: str = "",
    city: str = "",
    property_type: str = "",
    status: str = "",
    sakani: Optional[bool] = None,
) -> list[dict[str, Any]]:
    """Filter and search Alawaly real estate projects."""
    projects = get_all_projects()
    results = []

    norm_query = normalize_ar(query) if query else ""
    norm_city = normalize_ar(city) if city else ""
    norm_type = normalize_ar(property_type) if property_type else ""

    for p in projects:
        # Filter by city (الرياض vs جدة)
        if norm_city:
            p_city = normalize_ar(p.get("city", ""))
            if norm_city not in p_city and p_city not in norm_city:
                continue

        # Filter by property type (سكني vs مكاتب/تجاري)
        if norm_type:
            p_type = normalize_ar(p.get("property_type_ar", ""))
            p_en_type = p.get("property_type", "").lower()
            if (
                norm_type not in p_type
                and norm_type not in p_en_type
                and ("سكن" in norm_type and "commercial" in p_en_type)
                or ("مكتب" in norm_type and "residential" in p_en_type)
            ):
                continue

        # Filter by status (جاهز vs تحت الإنشاء vs مباع بالكامل)
        if status:
            norm_status = normalize_ar(status)
            p_status = normalize_ar(p.get("status_ar", ""))
            if "جاهز" in norm_status and p.get("status") != "ready":
                continue
            if "تحت الانشاء" in norm_status and p.get("status") != "under_construction":
                continue
            if "مباع" in norm_status and p.get("status") != "fully_sold":
                continue

        # Filter by sakani
        if sakani is not None and p.get("sakani_support") != sakani:
            continue

        # Text query matching
        if norm_query:
            p_blob = " ".join([
                normalize_ar(p.get("name", "")),
                normalize_ar(p.get("city", "")),
                normalize_ar(p.get("property_type_ar", "")),
                normalize_ar(p.get("description", "")),
                normalize_ar(p.get("location_detail", "")),
            ])
            if norm_query not in p_blob:
                # Check individual words
                words = norm_query.split()
                if not any(w in p_blob for w in words):
                    continue

        results.append(p)

    return results


def resolve_page(page_name: str) -> Optional[tuple[str, str]]:
    """Resolve user page request to (path, title)."""
    norm = normalize_ar(page_name)
    pages = get_pages()

    # Exact key match
    if page_name in pages:
        p = pages[page_name]
        return p["path"], p["title"]

    # Match aliases
    for key, p in pages.items():
        for alias in p.get("aliases", []):
            if normalize_ar(alias) == norm or normalize_ar(alias) in norm or norm in normalize_ar(alias):
                return p["path"], p["title"]

    # Check if user mentioned a specific project name
    proj = get_project(page_name)
    if proj:
        return proj["page_path"], proj["name"]

    return None


def catalog_index() -> str:
    """Format a rich Arabic summary of company knowledge for LLM system prompt context."""
    company = get_company()
    projects = get_all_projects()
    contact = get_contact()
    ceo = company.get("ceo", {})
    stats = company.get("stats", {})

    proj_lines = []
    for p in projects:
        specs = p.get("unit_specs", {})
        spec_text = ""
        if "bedrooms" in specs:
            spec_text = f" ({specs.get('bedrooms')} غرف نوم، مساحة {specs.get('area_sqm')}م²)"
        elif "area_sqm" in specs:
            spec_text = f" (مساحة {specs.get('area_sqm')}م²)"

        status_str = p.get("status_ar", "")
        avail = p.get("available_units", 0)
        proj_lines.append(
            f"- {p['name']} ({p['city_ar']} - {p['property_type_ar']}): حالة المشروع: {status_str}، الوحدات المتاحة: {avail}{spec_text}، الرابط: {p['page_path']}"
        )

    lines = [
        f"شركة {company.get('name')}:",
        f"المقر الرئيسي: {company.get('headquarters')}، التأسيس: عام {company.get('founded_year')}م.",
        f"الرئيس التنفيذي: {ceo.get('name')}. مقولته: «{ceo.get('quote')}».",
        f"المحفظة: {stats.get('total_signed_projects')}، {stats.get('total_products')}، بقيمة تفوق {stats.get('total_portfolio_value')} في {stats.get('cities_covered')}.",
        f"الرقم الموحد للمبيعات: {contact.get('unified_sales_number')} | الاستفسارات: {contact.get('unified_inquiries_number')} | واتساب: {contact.get('whatsapp')} | الإيميل: {contact.get('email')}.",
        "أهم المشاريع المعروضة على الموقع:",
        *proj_lines,
    ]
    return "\n".join(lines)


__all__ = [
    "load_catalog",
    "get_catalog",
    "check_catalog_sync",
    "get_company",
    "get_contact",
    "get_all_projects",
    "get_other_projects",
    "get_news",
    "get_news_item",
    "get_pages",
    "get_project",
    "search_projects",
    "resolve_page",
    "catalog_index",
    "normalize_ar",
    "clean_for_speech",
]
