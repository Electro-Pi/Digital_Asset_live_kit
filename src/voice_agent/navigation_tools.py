"""Moving the caller's screen: pages, a restaurant, filtered offer lists."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

from livekit.agents import RunContext, ToolError, function_tool
from loguru import logger

from src import catalog

PAGES = ("home", "restaurants", "restaurant", "offers", "search", "cart", "orders", "saved")
RESTAURANT_FILTERS = ("all", "grocery", "24h", "fast")
MENU_TABS = ("all", "offers", "popular", "menu")
OFFER_KINDS = ("combo", "family", "deal")

# Tolerance for Arabic page names; the schema asks for the English keys.
_PAGE_ALIASES = {
    "الرئيسيه": "home", "البيت": "home", "المطاعم": "restaurants", "مطاعم": "restaurants",
    "العروض": "offers", "عروض": "offers", "البحث": "search", "السله": "cart", "سله": "cart",
    "الطلبات": "orders", "المحفوظات": "saved", "المفضله": "saved",
}


def offers_path(*, kind: str = "", cashback: bool = False, cuisine: str = "", query: str = "") -> str:
    """The /offers URL with the same chips the frontend shows."""
    params: list[tuple[str, str]] = []
    if cashback:
        params.append(("cashback", "1"))
    if kind and kind != "all":
        params.append(("kind", kind))
    if cuisine:
        params.append(("cuisine", cuisine))
    if query:
        params.append(("q", query))
    return "/offers?" + urlencode(params, safe=",") if params else "/offers"


class NavigationTools:
    @function_tool()
    async def open_page(
        self,
        context: RunContext,
        page: str,
        restaurant: str = "",
        offer_kind: str = "",
        cashback: bool = False,
        cuisine: str = "",
        restaurants_filter: str = "",
        menu_tab: str = "",
        query: str = "",
    ) -> dict[str, Any]:
        """Move the caller's screen to a page of the app.

        Use it when the caller asks to go somewhere («افتح السلة», «وريني
        العروض العائلية», «ادخل مطعم أصيل برجر»). To show one dish use
        show_meal instead. To find dishes use search_meals.

        Args:
            page: "home", "restaurants", "restaurant", "offers", "search", "cart", "orders" or "saved".
            restaurant: With page "restaurant": the restaurant's Arabic name.
            offer_kind: With page "offers": "combo", "family" or "deal". Empty = all offers.
            cashback: With page "offers": True for cashback offers.
            cuisine: With "home", "restaurants" or "offers": a kitchen to filter by.
            restaurants_filter: With page "restaurants": "all", "grocery", "24h" or "fast".
            menu_tab: With page "restaurant": "all", "offers", "popular" or "menu".
            query: With page "search": the search text.
        """
        key = (page or "").strip().lower()
        key = key if key in PAGES else _PAGE_ALIASES.get(catalog.normalize_ar(page), "")
        if not key:
            raise ToolError(f"page must be one of {', '.join(PAGES)}")
        offer_kind = (offer_kind or "").strip().lower()
        if offer_kind and offer_kind not in OFFER_KINDS:
            raise ToolError(f"offer_kind must be one of {', '.join(OFFER_KINDS)} or empty")
        restaurants_filter = (restaurants_filter or "").strip().lower()
        if restaurants_filter and restaurants_filter not in RESTAURANT_FILTERS:
            raise ToolError(f"restaurants_filter must be one of {', '.join(RESTAURANT_FILTERS)} or empty")
        menu_tab = (menu_tab or "").strip().lower()
        if menu_tab and menu_tab not in MENU_TABS:
            raise ToolError(f"menu_tab must be one of {', '.join(MENU_TABS)} or empty")
        cuisine = catalog.resolve_cuisine(cuisine) or (cuisine or "").strip()

        result: dict[str, Any] = {"page": key}
        if key == "restaurant":
            details = catalog.restaurant_details(restaurant or "")
            if details is None:
                raise ToolError(f"No restaurant matches {restaurant!r}. Use a name from the restaurant list.")
            path = details["page_path"] + (f"?tab={menu_tab}" if menu_tab and menu_tab != "all" else "")
            outcome = self.screen(await self.ui.navigate(path))
            if outcome.ok:
                self.state.last_restaurant_id = details["restaurant_id"]
            result.update(
                restaurant={
                    "name_say_ar": details["name_say_ar"],
                    "cuisine": details.get("cuisine"),
                    "delivery_time": details.get("delivery_time"),
                    "delivery_fee_say_ar": details.get("delivery_fee_say_ar"),
                    "min_order_say_ar": details.get("min_order_say_ar"),
                    "popular_dishes": details.get("popular_meals"),
                    "menu_names": details.get("menu_names", [])[:25],
                }
            )
        elif key == "offers":
            path = offers_path(kind=offer_kind, cashback=bool(cashback), cuisine=cuisine)
            outcome = self.screen(
                await self.ui.set_ui_filter(page="offers", offers_kind=offer_kind, cuisine=cuisine, cashback=bool(cashback), path=path)
            )
            if not outcome.ok:
                outcome = self.screen(await self.ui.navigate(path))
            found = catalog.search_meals(only_offers=True, offer_kind=offer_kind or None, cashback=bool(cashback), cuisine=cuisine or None)
            result["offers"] = found.get("results", [])
            result["total_offers"] = found.get("total_matches", 0)
        elif key == "restaurants":
            path = "/restaurants"
            params = []
            if restaurants_filter and restaurants_filter != "all":
                params.append(("filter", restaurants_filter))
            if cuisine:
                params.append(("cuisine", cuisine))
            path += ("?" + urlencode(params)) if params else ""
            outcome = self.screen(await self.ui.navigate(path))
        elif key == "search":
            path = "/search" + (("?" + urlencode([("q", query)])) if query else "")
            outcome = self.screen(await self.ui.navigate(path))
        elif key == "home":
            path = "/" + (("?" + urlencode([("cuisine", cuisine)])) if cuisine else "")
            outcome = self.screen(await self.ui.navigate(path))
        else:
            path = {"cart": "/cart", "orders": "/orders", "saved": "/saved"}[key]
            outcome = self.screen(await self.ui.navigate(path))
            if key == "cart" and outcome.ok:
                result["cart"] = await self._cart_snapshot()

        logger.info("Tool open_page page={} path={} ok={} reason={}", key, path, outcome.ok, outcome.reason)
        result["ok"] = outcome.ok
        if outcome.ok:
            self.state.ui_path = path
            if outcome.data and outcome.data.get("mocked"):
                result["mocked"] = True
            title = result.get("restaurant", {}).get("name_say_ar") if key == "restaurant" else ""
            result["summary_ar"] = f"فتحت لك {title}".strip() if title else "تمام، هذي الصفحة قدامك"
        else:
            result["error"] = "screen_did_not_respond"
            result["summary_ar"] = "الشاشة ما استجابت، خلني أحاول مرة ثانية"
        return result
