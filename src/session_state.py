"""What one call knows: open offers, the cart as we changed it, checkout details.

Replaces the old ``SessionFlow``, whose ``awaiting`` field made a bare «ايه»
mean whatever the server last asked, invisibly to the model. Here every
question she asks the caller is an **offer** with an id the tools return
(``pitch:<meal>``, ``extras:<meal>``, ``upgrade:<meal>``, ``pair:<meal>``,
``checkout``). The model answers with ``respond_to_offer(offer_id, accepted)``
and the code checks that id is open. Nothing here reads the transcript: every
field is written by the tool that caused it.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Optional, Sequence

PITCH = "pitch"
EXTRAS = "extras"
UPGRADE = "upgrade"
PAIR = "pair"
CHECKOUT = "checkout"
OFFER_KINDS = (PITCH, EXTRAS, UPGRADE, PAIR, CHECKOUT)


def offer_id(kind: str, meal_id: str = "") -> str:
    return f"{kind}:{meal_id}" if meal_id else kind


# What she asks, in order, before recommending on an open request.
TASTE = "taste"
PEOPLE = "people"
BUDGET = "budget"
DISCOVERY_QUESTIONS = (TASTE, PEOPLE, BUDGET)


@dataclass
class Discovery:
    """An open request being narrowed down: taste, then people, then budget.

    Written only by ``search_meals``. ``pending`` is the question she was
    handed and ``asked_in`` the user turn it was handed in: the caller's next
    turn answers it, whatever he says («مو مهم» counts), so no question is
    asked twice. ``taste`` keeps the search arguments of his taste answer, so
    the later answers («لشخصين», «بخمسين») narrow that search instead of
    replacing it.
    """

    asked: list[str] = field(default_factory=list)
    answered: set[str] = field(default_factory=set)
    pending: str = ""
    asked_in: str = ""
    taste: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Offer:
    """A question she asked the caller that a yes or a no will answer."""

    kind: str
    meal_id: str = ""
    # For an upgrade: the dish it would replace.
    base_meal_id: str = ""
    # For extras: what he already asked for when he accepted the dish.
    quantity: int = 1
    spicy: bool = False
    # For a pair that is one of the dish's own add-ons (a kitchen with no
    # drinks of its own): it goes on the dish's cart line.
    addon_id: str = ""

    @property
    def id(self) -> str:
        if self.addon_id:
            return offer_id(self.kind, f"{self.meal_id}+{self.addon_id}")
        return offer_id(self.kind, self.meal_id)

    def as_fact(self) -> dict[str, Any]:
        fact = {"offer_id": self.id, "kind": self.kind}
        if self.meal_id:
            fact["meal_id"] = self.meal_id
        if self.base_meal_id:
            fact["instead_of_meal_id"] = self.base_meal_id
        if self.addon_id:
            fact["addon_id"] = self.addon_id
        return fact


@dataclass
class CheckoutDetails:
    name: str = ""
    phone: str = ""
    address: str = ""
    order_type: str = "delivery"  # delivery | pickup
    branch: str = ""
    phone_declined: bool = False
    phone_attempts_rejected: int = 0
    delivery_zone_ok: Optional[bool] = None

    def missing(self) -> list[str]:
        missing = []
        if not self.name:
            missing.append("name")
        if self.order_type == "pickup":
            if not self.branch:
                missing.append("branch")
        elif not self.address:
            missing.append("address")
        if not self.phone and not self.phone_declined:
            missing.append("phone")
        return missing


@dataclass
class SessionState:
    session_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    open_offers: list[Offer] = field(default_factory=list)
    # Facts the caller gave, recorded by the tool that received them.
    party_size: Optional[int] = None
    health_conscious: bool = False
    # Riyals he said he wants to spend; upsells that would pass it are not offered.
    budget: Optional[float] = None
    # The open request she is asking about, if any (see Discovery).
    discovery: Optional[Discovery] = None
    # Sales history.
    pitched_meal_ids: set[str] = field(default_factory=set)
    upgrade_offered_ids: set[str] = field(default_factory=set)
    # Dishes a complement was offered for (one each, not one per call), and
    # the complements themselves, which get none of their own.
    paired_meal_ids: set[str] = field(default_factory=set)
    complement_ids: set[str] = field(default_factory=set)
    declined_offers: int = 0
    last_search_ids: list[str] = field(default_factory=list)
    # Cart, as this session changed it. The browser owns the real cart.
    added_meal_ids: dict[str, int] = field(default_factory=dict)
    meal_options: dict[str, dict[str, Any]] = field(default_factory=dict)
    cart_item_count: Optional[int] = None
    # Screen.
    last_meal_id: str = ""
    last_restaurant_id: str = ""
    ui_path: str = ""
    # Checkout and the order.
    checkout: CheckoutDetails = field(default_factory=CheckoutDetails)
    summary_read: bool = False
    order_placed: bool = False
    completed_order: Optional[dict[str, Any]] = None
    idempotency_key: str = ""
    # Round-robin memory for the few lines spoken word for word.
    used_variants: dict[str, int] = field(default_factory=dict)

    # ─── Offers ──────────────────────────────────────────────────────────

    def open(self, *offers: Offer, replace: bool = True) -> None:
        """Ask new questions. By default they replace every open one."""
        if replace:
            self.open_offers = []
        for offer in offers:
            if offer.id not in {o.id for o in self.open_offers}:
                self.open_offers.append(offer)

    def find(self, offer_id_: str) -> Optional[Offer]:
        wanted = str(offer_id_ or "").strip()
        return next((o for o in self.open_offers if o.id == wanted), None)

    def close(self, *kinds: str) -> None:
        """Drop open offers of these kinds (all of them when none are named)."""
        self.open_offers = [o for o in self.open_offers if kinds and o.kind not in kinds]

    def open_facts(self) -> list[dict[str, Any]]:
        return [o.as_fact() for o in self.open_offers]

    @property
    def cross_sell_fatigued(self) -> bool:
        from src.config import CONFIG

        return self.declined_offers >= CONFIG.rules.max_declined_offers

    # ─── Cart memory ─────────────────────────────────────────────────────

    def note_added(self, meal_id: str) -> int:
        self.added_meal_ids[meal_id] = self.added_meal_ids.get(meal_id, 0) + 1
        return self.added_meal_ids[meal_id]

    def note_removed(self, meal_id: str) -> None:
        self.added_meal_ids.pop(meal_id, None)
        self.meal_options.pop(meal_id, None)

    # ─── Order ───────────────────────────────────────────────────────────

    def order_key(self) -> str:
        """One key per order, so a retried placement cannot order twice."""
        if not self.idempotency_key:
            self.idempotency_key = str(uuid.uuid4())
        return self.idempotency_key

    def begin_checkout(self) -> str:
        self.summary_read = False
        return self.order_key()

    def pick_variant(self, key: str, variants: Sequence[str]) -> str:
        """Next line for ``key``; never the same one twice in a row."""
        options = [str(v) for v in variants if str(v).strip()]
        if not options:
            return ""
        last = self.used_variants.get(key)
        index = 0 if last is None else (last + 1) % len(options)
        self.used_variants[key] = index
        return options[index]
