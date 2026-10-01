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
                  - "news" (المركز الإعلامي: /ar/news/latest)
                  - "privacy" (سياسة الخصوصية: /ar/legal/privacy-policy)
                  - "terms" (الشروط والأحكام: /ar/legal/terms-conditions)
                  - "disclaimers" (إخلاء المسؤولية: /ar/legal/disclaimers)
                  - "register_interest" (يفتح فورم سجل اهتمامك داخل القائمة الجانبية، بدون انتقال لصفحة)
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
                path = "/ar/projects"
                title = "مشاريعنا"
                key = "projects"

        if not path and project_name:
            article = catalog.get_news_item(project_name)
            if article:
                path = "/ar/news/latest"
                title = article["title"]
                key = "news"

        if not path:
            resolved = catalog.resolve_page(raw_page)
            if resolved:
                path, title = resolved
            else:
                target_proj = catalog.get_project(raw_page)
                if target_proj:
                    path = "/ar/projects"
                    title = "مشاريعنا"
                    key = "projects"
                else:
                    article = catalog.get_news_item(raw_page)
                    if article:
                        path = "/ar/news/latest"
                        title = article["title"]
                        key = "news"
                    else:
                        path = "/ar"
                        title = "الرئيسية"

        if path == "sidebar:interest" or key in ("register_interest", "سجل اهتمامك"):
            outcome = self.screen(await self.ui.fill_contact())
            logger.info("Opened sidebar interest form ok={}", outcome.ok)
            return {
                "ok": outcome.ok,
                "page": "register_interest",
                "form": "sidebar",
                "title": "سجل اهتمامك",
                "say_to_user_ar": "فتحت لك نموذج سجل اهتمامك في القائمة الجانبية.",
            }

        if target_proj:
            path = "/ar/projects"
            title = "مشاريعنا"
            key = "projects"

        if section and path.startswith("/") and not path.endswith(f"#{section}"):
            sep = "" if section.startswith("#") else "#"
            path = f"{path}{sep}{section}"

        outcome = self.screen(await self.ui.navigate(path))
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

    @function_tool()
    async def fill_interest_form(
        self,
        context: RunContext,
        name: str = "",
        phone: str = "",
        identity_type: str = "",
        email: str = "",
        property_type: str = "",
        project_type: str = "",
        location: str = "",
        budget: str = "",
        submit: bool = False,
    ) -> dict[str, Any]:
        """Open «سجل اهتمامك» in the sidebar and fill the fields the caller said.

        The form stays in the sidebar. It does not navigate to another page.
        Call this as soon as the caller gives any field, and again as more fields arrive.
        Set submit=True only after the caller explicitly asks to send the form.

        Args:
            name: الاسم الكامل.
            phone: رقم الجوال.
            identity_type: نوع الهوية.
            email: البريد الإلكتروني.
            property_type: نوع العقار الذي يبحث عنه.
            project_type: نوع المشروع الذي يناسبه.
            location: أين يبحث عن العقار.
            budget: ميزانية الشراء.
            submit: True only when the caller says to send the form.
        """
        del context
        outcome = self.screen(
            await self.ui.fill_contact(
                submit=submit,
                name=name,
                phone=phone,
                identityType=identity_type,
                email=email,
                propertyType=property_type,
                projectType=project_type,
                location=location,
                budget=budget,
            )
        )
        logger.info("Sidebar interest form filled ok={} submit={}", outcome.ok, submit)
        return {
            "ok": outcome.ok,
            "form": "sidebar",
            "submitted": bool(submit),
            "filled": {
                "name": name,
                "phone": phone,
                "identityType": identity_type,
                "email": email,
                "propertyType": property_type,
                "projectType": project_type,
                "location": location,
                "budget": budget,
            },
        }
