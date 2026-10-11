"""سلمى, the shopping agent: eight tools over one shared ``Shop``.

    search_meals      find dishes (read-only unless open_top)
    show_meal         open one dish and pitch it
    respond_to_offer  the caller's yes or no to an open offer
    get_cart          read the cart
    update_cart       change or remove a cart line
    open_page         move the screen to a page
    saved_meals       saved dishes
    checkout          hand over to the checkout task
"""

from __future__ import annotations

from typing import Any

from livekit.agents import Agent

from src.voice_agent.base import HarnessNodes, Shop
from src.voice_agent.cart_tools import CartTools
from src.voice_agent.catalog_tools import CatalogTools
from src.voice_agent.checkout import CheckoutTools
from src.voice_agent.navigation_tools import NavigationTools
from src.voice_agent.offer_tools import OfferTools
from src.voice_agent.saved_tools import SavedTools


class Assistant(
    HarnessNodes,
    CatalogTools,
    OfferTools,
    CartTools,
    NavigationTools,
    SavedTools,
    CheckoutTools,
    Agent,
):
    def __init__(self, *, instructions: str, shop: Shop, chat_ctx: Any = None) -> None:
        self.shop = shop
        self._turn_ctx = None
        super().__init__(instructions=instructions, chat_ctx=chat_ctx)
