"""المساعد الذكي لشركة العوالي العقارية (Alawaly Real Estate Voice Assistant).

Tools:
- search_projects: Find projects by city, property type, readiness, Sakani.
- get_project_details: Deep dive into project models, specs, amenities, landmarks.
- get_company_overview: Company history, CEO, quote, stats, awards, partners.
- get_contact_and_channels: Phone, WhatsApp, email, mobile apps.
- get_latest_news: News, global summits, partnerships.
- open_page: Control website navigation (/ar, /ar/about, /ar/projects, map, contact, etc.).
"""

from __future__ import annotations

from typing import Any

from livekit.agents import Agent

from src.voice_agent.base import HarnessNodes, Shop
from src.voice_agent.navigation_tools import NavigationTools
from src.voice_agent.project_tools import ProjectTools


class Assistant(
    HarnessNodes,
    ProjectTools,
    NavigationTools,
    Agent,
):
    def __init__(self, *, instructions: str, shop: Shop, chat_ctx: Any = None) -> None:
        self.shop = shop
        self._turn_ctx = None
        super().__init__(instructions=instructions, chat_ctx=chat_ctx)
