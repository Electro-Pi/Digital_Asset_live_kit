"""Real estate project and company information tools for Alawaly voice assistant."""

from __future__ import annotations

from typing import Any, Optional

from livekit.agents import RunContext, ToolError, function_tool
from loguru import logger

from src import catalog


class ProjectTools:
    """Tools enabling the assistant to retrieve rich Alawaly Real Estate facts."""

    @function_tool()
    async def search_projects(
        self,
        context: RunContext,
        city: str = "",
        property_type: str = "",
        status: str = "",
        sakani_supported: Optional[bool] = None,
        query: str = "",
    ) -> dict[str, Any]:
        """Search and filter Alawaly Real Estate projects.

        Use this tool when the client asks about available projects, residential
        or commercial units, projects in Riyadh or Jeddah, ready vs under-construction
        projects, or Sakani support.

        Args:
            city: "الرياض" or "جدة" (or empty for all cities).
            property_type: "سكني" (residential) or "مكاتب" / "تجاري" (commercial/offices).
            status: "جاهز" (ready/completed), "تحت الإنشاء" (under construction), or "مباع بالكامل" (fully sold).
            sakani_supported: True if client requires Sakani housing support, False otherwise.
            query: Free-text search (e.g. "فلل", "شقق روف", "أبراج", "الصحافة", "مريخ").
        """
        results = catalog.search_projects(
            query=query,
            city=city,
            property_type=property_type,
            status=status,
            sakani=sakani_supported,
        )

        formatted = []
        for p in results:
            specs = p.get("unit_specs", {})
            spec_summary = ""
            if "bedrooms" in specs:
                spec_summary = f"{specs.get('bedrooms')} غرف نوم، مساحة {specs.get('area_sqm')}م²"
            elif "area_sqm" in specs:
                spec_summary = f"مساحة {specs.get('area_sqm')}م²"

            formatted.append({
                "id": p["id"],
                "name": p["name"],
                "name_say_ar": p.get("name_say_ar", p["name"]),
                "city": p["city"],
                "property_type": p["property_type_ar"],
                "status": p["status_ar"],
                "completion_rate": p.get("completion_rate"),
                "total_units": p.get("total_units"),
                "available_units": p.get("available_units"),
                "sakani_support": p.get("sakani_support"),
                "specs": spec_summary,
                "page_path": p["page_path"],
                "tagline": p.get("tagline", ""),
            })

        logger.info(
            "Projects searched: query={!r} city={!r} type={!r} found={}",
            query,
            city,
            property_type,
            len(formatted),
        )
        return {
            "found_count": len(formatted),
            "projects": formatted,
            "filter_applied": {
                "city": city or "الكل",
                "property_type": property_type or "الكل",
                "status": status or "الكل",
                "sakani": sakani_supported if sakani_supported is not None else "الكل",
            },
        }

    @function_tool()
    async def get_project_details(
        self,
        context: RunContext,
        project_name: str,
    ) -> dict[str, Any]:
        """Get complete specifications, amenities, location, and landmarks for a specific project.

        Use this tool when the client asks about details of a specific project
        (such as آكام هومز, العوالي هومز التيسير, آكام جدة هايتس, العوالي رايز,
        بولاريس, مركز العوالي للأعمال, العوالي هومز المروة, العوالي تاور).

        Args:
            project_name: Name or slug of the project (e.g. "آكام هومز", "العوالي تاور", "التيسير", "المروة", "بولاريس").
        """
        proj = catalog.get_project(project_name)
        if not proj:
            raise ToolError(
                f"لم نجد مشروعاً باسم '{project_name}'. المشاريع المتاحة هي: آكام هومز، العوالي هومز التيسير، آكام جدة هايتس، العوالي رايز، بولاريس، مركز العوالي للأعمال، العوالي هومز المروة، العوالي تاور."
            )

        # Update session state
        self.state.current_project_id = proj["id"]

        logger.info("Project details fetched: id={}", proj["id"])
        return {
            "id": proj["id"],
            "name": proj["name"],
            "name_say_ar": proj.get("name_say_ar", proj["name"]),
            "city": proj["city"],
            "property_type": proj["property_type_ar"],
            "location": proj.get("location_detail", ""),
            "partner": proj.get("partner", ""),
            "status": proj["status_ar"],
            "completion_rate": proj.get("completion_rate"),
            "total_units": proj.get("total_units"),
            "available_units": proj.get("available_units"),
            "buildings_count": proj.get("buildings_count"),
            "award": proj.get("award", ""),
            "unit_specs": proj.get("unit_specs", {}),
            "amenities": proj.get("amenities", []),
            "nearby_landmarks": proj.get("nearby_landmarks", []),
            "page_path": proj["page_path"],
            "virtual_tour_url": proj.get("virtual_tour_url", ""),
            "brochure_url": proj.get("brochure_url", ""),
            "description": proj.get("description", ""),
        }

    @function_tool()
    async def get_company_overview(
        self,
        context: RunContext,
        topic: str = "general",
    ) -> dict[str, Any]:
        """Get rich authoritative details about Alawaly Real Estate company.

        Use this tool when the client asks about the company history, CEO,
        leadership, vision, achievements, awards, or partners.

        Args:
            topic: "general", "ceo", "stats", "partners", or "awards".
        """
        company = catalog.get_company()
        ceo = company.get("ceo", {})
        stats = company.get("stats", {})

        return {
            "name": company.get("name"),
            "founded_year": company.get("founded_year"),
            "headquarters": company.get("headquarters"),
            "overview": company.get("overview"),
            "ceo": {
                "name": ceo.get("name"),
                "quote": ceo.get("quote"),
            },
            "stats": stats,
            "strategic_partners": company.get("strategic_partners", []),
            "awards": company.get("awards", []),
            "other_cities_projects": catalog.get_other_projects(),
        }

    @function_tool()
    async def get_contact_and_channels(
        self,
        context: RunContext,
    ) -> dict[str, Any]:
        """Get Alawaly official contact phone numbers, WhatsApp, customer care, and mobile app details.

        Use this tool when the client asks how to reach customer care, sales,
        whatsapp, or how to download the Alawaly mobile app.
        """
        contact = catalog.get_contact()
        return {
            "unified_sales_number": contact.get("unified_sales_number"),
            "unified_inquiries_number": contact.get("unified_inquiries_number"),
            "whatsapp": contact.get("whatsapp"),
            "whatsapp_link": contact.get("whatsapp_url"),
            "email": contact.get("email"),
            "app_store": contact.get("app_store_url"),
            "google_play": contact.get("google_play_url"),
            "app_features": contact.get("app_features", []),
        }

    @function_tool()
    async def get_latest_news(
        self,
        context: RunContext,
    ) -> dict[str, Any]:
        """Get the latest official news, agreements, and international partnerships of Alawaly Real Estate.

        Use this tool when the caller asks about recent events, the Paris summit,
        GStones partnership, London real estate exhibition, or sponsorships.
        """
        news = catalog.get_news()
        return {
            "news_count": len(news),
            "articles": news,
        }
