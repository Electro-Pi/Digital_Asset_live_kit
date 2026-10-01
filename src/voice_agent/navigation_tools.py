"""Moving the caller's screen: Alawaly pages, projects, interactive map, contact."""

from __future__ import annotations

from typing import Any

from livekit.agents import RunContext, ToolError, function_tool
from loguru import logger

from src import catalog


class NavigationTools:
    """Tools for navigating the Alawaly Real Estate website in sync with voice."""

    @function_tool()
    async def open_page(
        self,
        context: RunContext,
        page: str,
        project_name: str = "",
        section: str = "",
    ) -> dict[str, Any]:
        """Move the caller's screen to any page or project on https://www.alawaly.sa/ar.

        Use this tool whenever the caller asks to see a page or project
        («افتح المشاريع», «وديني على عن العوالي», «وريني مشروع آكام هومز»,
        «افتح الخريطة التفاعلية», «وديني على صفحة التواصل», «افتح الأخبار»).

        When navigating to 'about' (عن العوالي), the assistant speaks freely
        and proudly about the company's journey since 2011, leadership, mission,
        and visionary achievements.

        Args:
            page: One of:
                  - "home" (الرئيسية: /ar)
                  - "about" (عن العوالي: /ar/about)
                  - "projects" (مشاريعنا: /ar/projects)
                  - "project" (صفحة مشروع معين: /ar/project/<slug>)
                  - "map" (الخريطة التفاعلية: https://alawaly-project.vercel.app/)
                  - "contact" (العناية بالعملاء: /ar/contact)
                  - "communication_channels" (قنوات التواصل: /ar/contact#Communication-channels)
                  - "mobile_app" (تطبيق العوالي: /ar/contact#App)
                  - "news" (المركز الإعلامي: /ar/news)
                  - "register_interest" (سجل اهتمامك: #contact-section)
            project_name: With page "project": the project name or slug (e.g. "آكام هومز", "العوالي تاور", "التيسير", "المروة", "بولاريس").
            section: Optional section hash (e.g. "contact-section", "Communication-channels", "App").
        """
        raw_page = (page or "").strip()
        key = raw_page.lower()
        path = ""
        title = ""

        # Check if project was specified directly or inside page
        target_proj = None
        if key == "project" or project_name:
            search_term = project_name or raw_page
            target_proj = catalog.get_project(search_term)
            if target_proj:
                path = target_proj["page_path"]
                title = target_proj["name"]
                key = "project"

        if not path:
            resolved = catalog.resolve_page(raw_page)
            if resolved:
                path, title = resolved
            else:
                target_proj = catalog.get_project(raw_page)
                if target_proj:
                    path = target_proj["page_path"]
                    title = target_proj["name"]
                    key = "project"
                else:
                    path = "/ar"
                    title = "الرئيسية"

        if section and not path.endswith(f"#{section}"):
            sep = "" if section.startswith("#") else "#"
            path = f"{path}{sep}{section}"

        # Trigger screen navigation over LiveKit RPC
        outcome = self.screen(await self.ui.navigate(path, title=title))
        self.state.current_page = path
        if target_proj:
            self.state.current_project_id = target_proj["id"]

        logger.info("Navigation executed: page={!r} path={!r} ok={}", key, path, outcome.ok)

        # Contextual guidance when landing on About Us
        about_company_prompt = ""
        if "/about" in path or key in ("about", "عن العوالي", "من نحن"):
            company = catalog.get_company()
            ceo = company.get("ceo", {})
            stats = company.get("stats", {})
            about_company_prompt = (
                "تم الانتقال بنجاح إلى صفحة «عن العوالي» (/ar/about). "
                "تحدّث الآن بطلاقة وفخر وترحاب عن شركة العوالي العقارية: "
                f"تأسست عام {company.get('founded_year')}م ومقرها الرئيسي في {company.get('headquarters')}، "
                f"بقيادة الرئيس التنفيذي {ceo.get('name')} ومقولته: «{ceo.get('quote')}»، "
                f"محفظتها تضم {stats.get('total_signed_projects')} بأكثر من {stats.get('total_products')} بقيمة تتجاوز {stats.get('total_portfolio_value')} في {stats.get('cities_covered')}، "
                "وشراكتها الاستراتيجية مع الشركة الوطنية للإسكان (NHC)، ودورها في تعزيز جودة الحياة ورؤية السعودية 2030."
            )

        return {
            "ok": outcome.ok,
            "page": key,
            "path": path,
            "title": title,
            "project": target_proj["name"] if target_proj else None,
            "about_guidance": about_company_prompt,
        }
