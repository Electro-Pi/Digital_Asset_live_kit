"""What the agent actually did this turn, recorded as it happened.

Nothing here reads the conversation transcript. A guard asks this record
whether an action really happened; it must never re-derive that from what the
model said, from which tool the model named, or from the chat history.

Two live failures on 2026-09-21, one call apart, came from breaking that rule:

* The model asked for ``resolve_extras``, which adds the pitched meal by
  calling ``add_to_cart`` itself. The speech guard looked for the *name*
  ``add_to_cart`` among the turn's tool calls, found only ``resolve_extras``,
  concluded nothing had been added and suppressed her confirmation. The call
  went silent with the salad already in the cart.
* ``show_meal`` read this turn's search hits from ``Agent.chat_ctx``, which
  LiveKit fills one tool step late (``agent_activity`` hands the step's tool
  messages to the next generation and only inserts them into the agent context
  afterwards). It saw the previous search instead of the current one, judged a
  valid meal id stale, and opened a grilled chicken breast while she described
  a Caesar salad.

Both disappear once the record is written where the thing happens:

* **Effects** are recorded inside :meth:`src.ui_bridge.UIBridge.call`, the one
  point every browser mutation passes through. That sits below the tool layer,
  so the call stack above it does not matter: ``confirm`` →
  ``resolve_extras`` → ``add_to_cart`` records one :data:`CART_ADD`, exactly as
  a bare ``add_to_cart`` would.
* **Findings** a later guard needs — the rows a search returned — are written
  by the tool that produced them, in the same breath.

Effect kinds are what happened to the world, never a tool name. A guard that
asks "was something added to the cart" keeps working when the tool that adds is
renamed, split, or called from inside another one.

The turn key is the id of the user message being answered, so the record rolls
over exactly when the guards' notion of "this turn" does.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Optional


# ─── Effect kinds ────────────────────────────────────────────────────────────
#
# What changed for the customer, not which function changed it.

NAVIGATE = "navigate"
MEAL_PAGE = "meal_page"
CART_ADD = "cart_add"
CART_QUANTITY = "cart_quantity"
MEAL_OPTIONS = "meal_options"
CHECKOUT_FILL = "checkout_fill"
ORDER_COMPLETE = "order_complete"
SAVED = "saved"
UI_FILTER = "ui_filter"

EFFECT_KINDS = frozenset(
    {
        NAVIGATE,
        MEAL_PAGE,
        CART_ADD,
        CART_QUANTITY,
        MEAL_OPTIONS,
        CHECKOUT_FILL,
        ORDER_COMPLETE,
        SAVED,
        UI_FILTER,
    }
)


@dataclass(frozen=True)
class Effect:
    """One change the agent made to the customer's screen or order."""

    kind: str
    target: str = ""
    ok: bool = True
    mocked: bool = False

    @property
    def landed(self) -> bool:
        """True when the session may speak about this as done.

        ``mocked`` is the voice demo continuing past a missing frontend
        handler (:meth:`Assistant._continue_as_mock`). The session has decided
        to treat it as done, so she is not lying by saying so.
        """
        return self.ok or self.mocked


@dataclass
class Turn:
    """The record for the user turn currently being answered."""

    key: str = ""
    effects: list[Effect] = field(default_factory=list)
    searches: list[list[dict[str, Any]]] = field(default_factory=list)


class TurnLedger:
    """Per-turn record of real effects and tool findings.

    Written by the UI bridge and by the tools that produce findings; read by
    the guards. Both sides see the turn currently in progress, so neither has
    to reconstruct it.
    """

    def __init__(self) -> None:
        self._turn = Turn()

    # ─── Turn boundary ───────────────────────────────────────────────────────

    @property
    def key(self) -> str:
        return self._turn.key

    def begin(self, key: str) -> bool:
        """Start a fresh turn when ``key`` names a different user message.

        Returns True when the record rolled over. An empty key is ignored: a
        turn we cannot identify must not wipe a record a guard is about to
        read. Idempotent, so it is safe to call on every generation step of
        the same turn.
        """
        key = str(key or "")
        if not key or key == self._turn.key:
            return False
        self._turn = Turn(key=key)
        return True

    # ─── Writing ─────────────────────────────────────────────────────────────

    def record(
        self,
        kind: str,
        *,
        target: str = "",
        ok: bool = True,
        mocked: bool = False,
    ) -> None:
        """Note that ``kind`` was attempted, and how it went."""
        if kind not in EFFECT_KINDS:
            return
        self._turn.effects.append(
            Effect(kind=kind, target=str(target or ""), ok=bool(ok), mocked=bool(mocked))
        )

    def promote_last_failure(self) -> Optional[Effect]:
        """Mark the newest effect as mocked when the session continues past it.

        Called by :meth:`Assistant._continue_as_mock`, which runs immediately
        after the UI call it is promoting, so the newest entry is that call.
        """
        effects = self._turn.effects
        if not effects:
            return None
        last = effects[-1]
        if last.ok or last.mocked:
            return last
        promoted = Effect(kind=last.kind, target=last.target, ok=False, mocked=True)
        effects[-1] = promoted
        return promoted

    def record_search(self, rows: Iterable[dict[str, Any]] | None) -> None:
        """Note the rows a ``search_meals`` call returned, in call order.

        Recorded here rather than read back from the transcript so a guard in
        the same turn sees the search that just ran, not the one before it.
        """
        kept = [
            row
            for row in (rows or [])
            if isinstance(row, dict) and (row.get("meal_id") or row.get("id"))
        ]
        self._turn.searches.append(kept)

    # ─── Reading ─────────────────────────────────────────────────────────────

    def did(self, kind: str, *, target: str | None = None) -> bool:
        """True when this turn really performed ``kind``.

        Only effects that landed count, so a failed RPC the session did not
        mock cannot license a claim that it worked.
        """
        wanted = str(target).strip() if target is not None else None
        for effect in self._turn.effects:
            if effect.kind != kind or not effect.landed:
                continue
            if wanted is not None and effect.target != wanted:
                continue
            return True
        return False

    def attempted(self, kind: str) -> bool:
        """True when ``kind`` was tried this turn, landed or not."""
        return any(effect.kind == kind for effect in self._turn.effects)

    def targets(self, kind: str) -> list[str]:
        """Targets of the landed effects of ``kind``, in order."""
        return [
            effect.target
            for effect in self._turn.effects
            if effect.kind == kind and effect.landed and effect.target
        ]

    def searched(self) -> bool:
        """True when any ``search_meals`` ran this turn."""
        return bool(self._turn.searches)

    def search_rows(self) -> Optional[list[dict[str, Any]]]:
        """Hits a ``show_meal`` this turn may open, or None if no search ran.

        The newest non-empty result wins: a narrowing search that returns
        nothing must not retract the hits found a moment earlier (live
        2026-08-30, ``search(cuisine=مشويات)`` → 0 then ``search()`` → 1214).
        An empty list means every search this turn came back empty, which is
        what lets ``show_meal`` refuse a stale id instead of guessing.
        """
        if not self._turn.searches:
            return None
        for rows in reversed(self._turn.searches):
            if rows:
                return list(rows)
        return []

    # ─── Diagnostics ─────────────────────────────────────────────────────────

    def snapshot(self) -> dict[str, Any]:
        """Compact view for logs; effects in the order they happened."""
        return {
            "turn": self._turn.key,
            "effects": [
                {
                    "kind": e.kind,
                    "target": e.target,
                    "ok": e.ok,
                    "mocked": e.mocked,
                }
                for e in self._turn.effects
            ],
            "searches": [len(rows) for rows in self._turn.searches],
        }


def user_turn_key(chat_ctx: Any) -> str:
    """Identify the user message a generation is answering.

    The id of the newest user item. Falls back to a digest of its text for
    chat contexts that carry no ids (tests, and the relabelled opening turn),
    and to an empty string when there is no user turn yet — the greeting, where
    an empty record is the right answer.
    """
    items = getattr(chat_ctx, "items", None)
    if items is None and isinstance(chat_ctx, (list, tuple)):
        items = chat_ctx
    for item in reversed(list(items or [])):
        role = getattr(item, "role", None)
        if role is None and isinstance(item, dict):
            role = item.get("role")
        if role != "user":
            continue
        ident = getattr(item, "id", None) or (
            item.get("id") if isinstance(item, dict) else None
        )
        if ident:
            return str(ident)
        content = getattr(item, "content", None)
        if content is None and isinstance(item, dict):
            content = item.get("content")
        text = " ".join(str(c) for c in content) if isinstance(content, list) else str(content or "")
        return f"text:{hash(text)}" if text else ""
    return ""
