"""General tool-call validation for the agent harness.

This module holds the checks that apply to any call, before any tool runs:

* **Schema validation** — a call must name a real tool and carry arguments that
  fit that tool's generated JSON schema. ``gemma4`` invents argument names and
  sends numbers as words, so arguments are repaired where a repair is
  unambiguous and the call is dropped where it is not.
* **Loops and contradictions** — the same call twice in a turn runs once, and
  accepting an offer while searching for something else keeps only the search.

Checks that depend on the order's state (is the cart empty, is there an open
offer) live in the tools, which return an actionable error the model can read
and recover from. Nothing here inspects user text.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Optional, Sequence

from loguru import logger


# ─── Schemas ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ToolSpec:
    """The callable surface of one tool, as the model sees it."""

    name: str
    properties: Mapping[str, Any] = field(default_factory=dict)
    required: frozenset[str] = frozenset()

    def accepts(self, argument: str) -> bool:
        return argument in self.properties

    def json_type(self, argument: str) -> str:
        node = self.properties.get(argument) or {}
        declared = node.get("type")
        if isinstance(declared, str):
            return declared
        # Pydantic renders Optional[x] as anyOf; take the first concrete type.
        for branch in node.get("anyOf") or []:
            branch_type = branch.get("type")
            if isinstance(branch_type, str) and branch_type != "null":
                return branch_type
        return ""


def build_tool_specs(tools: Sequence[Any]) -> dict[str, ToolSpec]:
    """Derive specs from the live tool objects, so they cannot drift."""
    from livekit.agents.llm import utils as llm_utils

    specs: dict[str, ToolSpec] = {}
    for tool in tools or []:
        try:
            schema = llm_utils.build_legacy_openai_schema(tool, internally_tagged=False)
            fn = schema.get("function") or {}
            name = str(fn.get("name") or "").strip()
            if not name:
                continue
            params = fn.get("parameters") or {}
            specs[name] = ToolSpec(
                name=name,
                properties=params.get("properties") or {},
                required=frozenset(params.get("required") or ()),
            )
        except Exception as exc:  # noqa: BLE001 - a tool we cannot read is a tool we cannot check
            name = getattr(getattr(tool, "info", None), "name", "?")
            logger.warning("Could not read schema for tool {}: {}", name, exc)
    return specs


# ─── Argument repair ─────────────────────────────────────────────────────────


_TRUE = {"true", "yes", "1", "on"}
_FALSE = {"false", "no", "0", "off"}


# Coda-V2 sometimes serialises a string argument as leftovers of its own
# decoding: «', 'NULL'», «', NULL, 'NULL», «null». Live 2026-09-27: that
# reached offer_kind four times in a row and the turn ended mute.
_NULLISH = re.compile(r"[\s'\",\[\]()]*(?:(?:null|none|nil|undefined)[\s'\",\[\]()]*)*", re.IGNORECASE)


def _is_debris(value: str) -> bool:
    """Only quotes, commas, brackets and null words: no value at all."""
    return bool(value.strip()) and _NULLISH.fullmatch(value) is not None


def _leaks_other_arguments(value: str, own: str, names: Iterable[str]) -> bool:
    """«', NULL, dish_type: 'main', max_price: 30»: the rest of the call spilled into one string."""
    others = [n for n in names if n != own]
    return bool(others) and re.search(r"\b(?:%s)\s*:" % "|".join(map(re.escape, others)), value) is not None


def _coerce(value: Any, want: str) -> tuple[bool, Any]:
    """Return ``(ok, coerced)``. Only unambiguous conversions are allowed."""
    if not want:
        return True, value
    if want == "string":
        if isinstance(value, str):
            return True, value
        if isinstance(value, (int, float, bool)):
            return True, str(value)
        return False, value
    if want == "boolean":
        if isinstance(value, bool):
            return True, value
        if isinstance(value, str) and value.strip().lower() in _TRUE:
            return True, True
        if isinstance(value, str) and value.strip().lower() in _FALSE:
            return True, False
        if isinstance(value, (int, float)) and value in (0, 1):
            return True, bool(value)
        return False, value
    if want in {"integer", "number"}:
        if isinstance(value, bool):
            return False, value
        if isinstance(value, (int, float)):
            return True, int(value) if want == "integer" else float(value)
        if isinstance(value, str):
            text = value.strip().replace(",", "")
            try:
                number = float(text)
            except ValueError:
                return False, value
            return True, int(number) if want == "integer" else number
        return False, value
    if want == "array":
        if isinstance(value, list):
            return True, value
        if isinstance(value, str) and value.strip():
            return True, [value]
        return False, value
    if want == "object":
        return isinstance(value, dict), value
    return True, value


@dataclass
class ValidatedCall:
    name: str
    arguments: dict[str, Any]
    call_id: str


def validate_call(
    name: str,
    arguments: Any,
    specs: Mapping[str, ToolSpec],
    *,
    call_id: str = "",
    allowed: Optional[Iterable[str]] = None,
) -> tuple[Optional[ValidatedCall], str]:
    """Check one call against its schema.

    Returns ``(call, "")`` when it may run, or ``(None, reason)`` when it may
    not. Unknown arguments are dropped rather than failing the whole call —
    the model routinely adds a stray field to an otherwise correct call.

    ``allowed`` lists the tools this turn offers. A tool that is offered but
    whose schema could not be read is passed through unchecked: refusing a
    real tool because we failed to introspect it would be worse than not
    validating its arguments.
    """
    tool = str(name or "").strip()
    if not tool:
        return None, "missing tool name"
    spec = specs.get(tool)
    if spec is None:
        if allowed is not None and tool in set(allowed):
            parsed = arguments
            if isinstance(parsed, str):
                try:
                    parsed = json.loads(parsed)
                except (ValueError, TypeError):
                    return None, "arguments are not valid JSON"
            if parsed is None:
                parsed = {}
            if not isinstance(parsed, dict):
                return None, "arguments are not an object"
            logger.debug("No schema for offered tool {}; passing through", tool)
            return ValidatedCall(name=tool, arguments=parsed, call_id=call_id), ""
        return None, f"unknown tool {tool!r}"

    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except (ValueError, TypeError):
            return None, "arguments are not valid JSON"
    if arguments is None:
        arguments = {}
    if not isinstance(arguments, dict):
        return None, "arguments are not an object"

    clean: dict[str, Any] = {}
    for key, value in arguments.items():
        key = str(key)
        if not spec.accepts(key):
            logger.debug("Dropping unknown argument {}.{}", tool, key)
            continue
        want = spec.json_type(key)
        ok, coerced = _coerce(value, want)
        if ok and want == "string" and isinstance(coerced, str) and (
            _is_debris(coerced) or _leaks_other_arguments(coerced, key, spec.properties)
        ):
            logger.warning("Malformed string argument {}.{}={!r}", tool, key, coerced[:80])
            ok = False
        if not ok:
            if key in spec.required:
                return None, f"{key} has the wrong type for {tool}"
            logger.debug("Dropping uncoercible argument {}.{}", tool, key)
            continue
        clean[key] = coerced

    missing = sorted(spec.required - clean.keys())
    if missing:
        return None, f"{tool} is missing required {', '.join(missing)}"

    return ValidatedCall(name=tool, arguments=clean, call_id=call_id), ""


# ─── Turn assembly ───────────────────────────────────────────────────────────


def _args_signature(arguments: Optional[Mapping[str, Any]]) -> str:
    return json.dumps(dict(arguments or {}), sort_keys=True, ensure_ascii=False)


def _signature(call: ValidatedCall) -> tuple[str, str]:
    return call.name, _args_signature(call.arguments)


# Accepting the open offer and going to look for a different dish are
# contradictory requests. When one model step asks for both, the move away is
# the one the caller is owed, so the acceptance is dropped. A decline next to a
# search is not a contradiction: «لا، شف غيره» is exactly that.
_MOVES_AWAY = frozenset({"search_meals", "show_meal"})


def drop_contradictions(calls: list[ValidatedCall]) -> tuple[list[ValidatedCall], list[str]]:
    """Remove calls that a different call in the same step contradicts."""
    names = {call.name for call in calls}
    kept: list[ValidatedCall] = []
    refused: list[str] = []
    for call in calls:
        accepting = call.name == "respond_to_offer" and bool(call.arguments.get("accepted"))
        clash = _MOVES_AWAY & names if accepting else set()
        if clash:
            refused.append(f"{call.name}: contradicted by {', '.join(sorted(clash))} in the same turn")
            continue
        kept.append(call)
    return kept, refused


def plan_turn(
    calls: Iterable[tuple[str, Any, str]],
    specs: Mapping[str, ToolSpec],
    *,
    already_ran: Iterable[tuple[str, Optional[Mapping[str, Any]]]] = (),
    allowed: Optional[Iterable[str]] = None,
) -> tuple[list[ValidatedCall], list[str]]:
    """Validate a turn's calls and drop the ones this session cannot run.

    Returns the calls to execute plus a human-readable list of what was
    refused, for the log. Order is preserved; exact duplicates collapse.

    ``already_ran`` carries ``(name, arguments)`` for tools that already ran
    since the last user turn. A call identical to one of those is refused:
    the model re-issuing the same search after seeing its results is a loop,
    not a new request.
    """
    ran = {(str(name), _args_signature(args)) for name, args in (already_ran or ())}
    approved: list[ValidatedCall] = []
    seen: set[tuple[str, str]] = set()
    refused: list[str] = []

    for name, arguments, call_id in calls:
        call, reason = validate_call(
            name, arguments, specs, call_id=call_id, allowed=allowed
        )
        if call is None:
            refused.append(f"{name}: {reason}")
            continue
        signature = _signature(call)
        if signature in seen:
            refused.append(f"{call.name}: duplicate call in the same turn")
            continue
        if signature in ran:
            refused.append(f"{call.name}: an identical call already ran this turn")
            continue
        seen.add(signature)
        approved.append(call)

    approved, contradictions = drop_contradictions(approved)
    refused.extend(contradictions)
    return approved, refused
