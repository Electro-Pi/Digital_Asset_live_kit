"""LEAP navigator metadata, location validation, and LiveKit UI messages.

The browser owns the calibrated corridor graph.  The voice agent is responsible
only for resolving the two places named in conversation and publishing their
canonical IDs.  Keeping that boundary here prevents the LLM from inventing a
booth or turn-by-turn geometry that is not present in the frontend map.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Literal

from livekit import rtc

from src.arabic_text import normalize_arabic_speech_codes, strip_tashkeel


NAVIGATOR_EXPERIENCE = "leap-navigator"
NAVIGATOR_ROUTE_TOPIC = "navigator.route"
NAVIGATOR_NARRATION_TOPIC = "navigator.route.narration"

MAX_NARRATION_PAYLOAD_BYTES = 14_000
MAX_NARRATION_STEPS = 30
MAX_NARRATION_INSTRUCTION_CHARS = 600
MAX_NARRATION_NOTE_CHARS = 300
MAX_NARRATION_TEXT_CHARS = 8_000

NavigatorLanguage = Literal["en", "ar"]


@dataclass(frozen=True)
class NavigatorSession:
    language: NavigatorLanguage
    start_id: str | None = None
    destination_id: str | None = None


@dataclass(frozen=True)
class NavigatorLocation:
    id: str
    name: str
    name_ar: str
    aliases: tuple[str, ...]
    booth_code: str | None = None


@dataclass(frozen=True)
class NavigatorNarrationStep:
    id: str
    instruction: str
    note: str | None = None


@dataclass(frozen=True)
class NavigatorRouteNarration:
    language: NavigatorLanguage
    start: NavigatorLocation
    destination: NavigatorLocation
    distance_meters: int
    duration_minutes: int
    steps: tuple[NavigatorNarrationStep, ...]


# Both groups come from the same generated PDF catalog consumed by the browser.
# This keeps the voice resolver and map on one canonical set of exact IDs.
def _load_pdf_locations() -> tuple[
    tuple[NavigatorLocation, ...],
    tuple[NavigatorLocation, ...],
]:
    """Load named public places and assigned booths from the supplied PDF."""
    catalog_path = (
        Path(__file__).resolve().parents[1]
        / "navigator-frontend"
        / "src"
        / "data"
        / "pdfPlaces.json"
    )
    try:
        payload = json.loads(catalog_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise RuntimeError(f"Could not load navigator PDF catalog: {catalog_path}") from error
    if (
        not isinstance(payload, dict)
        or not isinstance(payload.get("publicPlaces"), list)
        or not isinstance(payload.get("places"), list)
    ):
        raise RuntimeError("Navigator PDF catalog has an invalid top-level structure.")

    def parse_group(key: str, count_key: str) -> tuple[NavigatorLocation, ...]:
        locations: list[NavigatorLocation] = []
        for index, raw_location in enumerate(payload[key]):
            if not isinstance(raw_location, dict):
                raise RuntimeError(
                    f"Navigator PDF catalog {key}[{index}] is not an object."
                )
            location_id = raw_location.get("id")
            name = raw_location.get("name")
            name_ar = raw_location.get("nameAr")
            raw_aliases = raw_location.get("aliases")
            booth_code = raw_location.get("boothCode")
            if (
                not isinstance(location_id, str)
                or not location_id
                or not isinstance(name, str)
                or not name
                or not isinstance(name_ar, str)
                or not name_ar
                or not isinstance(raw_aliases, list)
                or not all(isinstance(alias, str) and alias for alias in raw_aliases)
                or (booth_code is not None and (not isinstance(booth_code, str) or not booth_code))
            ):
                raise RuntimeError(f"Navigator PDF catalog {key}[{index}] is invalid.")
            locations.append(
                NavigatorLocation(
                    location_id,
                    name,
                    name_ar,
                    tuple(raw_aliases),
                    booth_code,
                )
            )
        if payload.get(count_key) != len(locations):
            raise RuntimeError(
                f"Navigator PDF catalog {count_key} does not match {key}."
            )
        return tuple(locations)

    public_locations = parse_group("publicPlaces", "publicPlaceCount")
    booth_locations = parse_group("places", "assignedBoothCount")
    all_ids = [location.id for location in (*public_locations, *booth_locations)]
    if len(all_ids) != len(set(all_ids)):
        raise RuntimeError("Navigator PDF catalog location IDs must be unique.")
    return public_locations, booth_locations


_PUBLIC_NAVIGATOR_LOCATIONS, _BOOTH_NAVIGATOR_LOCATIONS = _load_pdf_locations()
NAVIGATOR_LOCATIONS: tuple[NavigatorLocation, ...] = (
    *_PUBLIC_NAVIGATOR_LOCATIONS,
    *_BOOTH_NAVIGATOR_LOCATIONS,
)


class NavigationLocationError(ValueError):
    """A spoken location cannot be mapped safely to one canonical place."""


class NavigationNarrationError(ValueError):
    """A frontend narration packet violates the trusted route contract."""


def _normalize_label(value: str) -> str:
    if not isinstance(value, str):
        return ""
    value = strip_tashkeel(unicodedata.normalize("NFKC", value)).casefold()
    value = value.translate(
        str.maketrans(
            {
                "أ": "ا",
                "إ": "ا",
                "آ": "ا",
                "ٱ": "ا",
                "ى": "ي",
                "ؤ": "و",
                "ئ": "ي",
                "ة": "ه",
                "ـ": "",
            }
        )
    )
    # IDs use hyphens while people say spaces. Treat separators and punctuation
    # uniformly, but deliberately do not do substring or fuzzy matching.
    value = re.sub(r"[-_/]+", " ", value)
    value = re.sub(r"[^\w\u0600-\u06ff]+", " ", value, flags=re.UNICODE)
    # Common spoken descriptions should resolve to the wording printed on the
    # floor plan. These are meaning-preserving Arabic variants, not fuzzy place
    # guesses: e.g. visitors naturally say "غرفة الصلاة الشرقية" for the PDF's
    # "المصلى الشرقي".
    value = re.sub(r"\bغرفه الصلاه\b", "المصلي", value)
    value = re.sub(r"\bالشرقيه\b", "الشرقي", value)
    value = re.sub(r"\bالغربيه\b", "الغربي", value)
    return " ".join(value.split())


def _build_location_index() -> dict[str, tuple[NavigatorLocation, ...]]:
    entries: dict[str, list[NavigatorLocation]] = {}

    def add(label: str, location: NavigatorLocation) -> None:
        normalized = _normalize_label(label)
        if normalized and location not in entries.setdefault(normalized, []):
            entries[normalized].append(location)

    # Canonical IDs and full printed names always take precedence over short
    # fragments extracted from wrapped PDF labels.
    for location in NAVIGATOR_LOCATIONS:
        for label in (location.id, location.name, location.name_ar):
            add(label, location)

    # A short alias must never dilute an exact canonical ID or printed name.
    # This keeps "registration" mapped to REGISTRATION (CP2), while genuine
    # printed-name duplicates such as T2 remain correctly ambiguous.
    canonical_labels = set(entries)
    for location in _PUBLIC_NAVIGATOR_LOCATIONS:
        for label in location.aliases:
            if _normalize_label(label) in canonical_labels:
                continue
            add(label, location)

    reserved_labels = set(entries)
    pdf_aliases: dict[str, list[NavigatorLocation]] = {}
    for location in _BOOTH_NAVIGATOR_LOCATIONS:
        for label in location.aliases:
            normalized = _normalize_label(label)
            if not normalized or normalized in reserved_labels:
                continue
            if location not in pdf_aliases.setdefault(normalized, []):
                pdf_aliases[normalized].append(location)
    for normalized, locations in pdf_aliases.items():
        entries[normalized] = locations
    return {label: tuple(locations) for label, locations in entries.items()}


_LOCATION_INDEX = _build_location_index()
_LOCATION_BY_ID = {location.id: location for location in NAVIGATOR_LOCATIONS}

_SPOKEN_FILLER_WORDS = {
    # Common English route-request words. Removing these lets the deterministic
    # resolver see the actual place inside a natural utterance.
    "a", "am", "at", "can", "could", "directions", "for", "from", "get",
    "go", "going", "help", "i", "like", "map", "me", "navigate", "need",
    "please", "route", "show", "take", "the", "to", "toward", "towards",
    "want", "where", "would",
    # Saudi/Modern Standard Arabic equivalents after _normalize_label().
    "ابي", "ابغي", "ابغى", "انا", "اريد", "اروح", "اذهب", "الى",
    "الان", "الحين", "الطريق", "المسار", "المكان", "بي", "تقدر",
    "حاليا", "خذني", "روح", "عند", "علي", "عن", "في", "لو", "ممكن",
    "من", "واقف", "ودي", "ودني", "وين",
}

_ARABIC_PHONETIC = {
    "ا": "a", "ب": "b", "ت": "t", "ث": "s", "ج": "g", "ح": "h",
    "خ": "kh", "د": "d", "ذ": "z", "ر": "r", "ز": "z", "س": "s",
    "ش": "sh", "ص": "s", "ض": "d", "ط": "t", "ظ": "z", "ع": "a",
    "غ": "gh", "ف": "f", "ق": "k", "ك": "k", "ل": "l", "م": "m",
    "ن": "n", "ه": "h", "و": "w", "ي": "y", "ء": "",
}

_PHONETIC_LEGAL_WORDS = {
    "arabia", "arabian", "branch", "co", "company", "corporation", "group",
    "limited", "ltd", "office", "saudi", "system", "systems", "technologies",
    "technology",
}


def _spoken_query(value: str) -> str:
    """Remove conversational wrappers without changing catalog name tokens."""
    tokens = _normalize_label(value).split()
    meaningful = [token for token in tokens if token not in _SPOKEN_FILLER_WORDS]
    return " ".join(meaningful) or " ".join(tokens)


def _phonetic_key(value: str) -> str:
    """Return a conservative consonant key for English names spoken in Arabic.

    The Arabic STT correctly writes ordinary Arabic but often transliterates an
    English exhibitor (for example, Microsoft -> مايكروسوفت). Comparing a small
    consonant key bridges those two scripts without asking the LLM to invent a
    catalog entry.
    """
    words: list[str] = []
    for raw_token in _normalize_label(value).split():
        if raw_token in _PHONETIC_LEGAL_WORDS:
            continue
        if any("\u0600" <= character <= "\u06ff" for character in raw_token):
            token = "".join(_ARABIC_PHONETIC.get(character, "") for character in raw_token)
        else:
            token = raw_token
        token = token.replace("ph", "f").replace("tion", "shn").replace("sh", "s")
        token = token.replace("ch", "k").replace("gh", "g").replace("j", "g")
        token = re.sub(r"c(?=[eiy])", "s", token)
        token = token.replace("c", "k").replace("q", "k").replace("x", "ks")
        token = re.sub(r"[aeiouyw]", "", token)
        token = re.sub(r"[^a-z0-9]", "", token)
        if token:
            words.append(token)
    return "".join(words)


def _location_search_labels() -> tuple[tuple[str, tuple[NavigatorLocation, ...]], ...]:
    return tuple(
        (label, matches)
        for label, matches in _LOCATION_INDEX.items()
        # Tiny layout fragments are not useful for tolerant speech matching.
        if len(label) >= 3
    )


_LOCATION_SEARCH_LABELS = _location_search_labels()


def _ambiguous_location_error(
    value: str,
    role: str,
    matches: tuple[NavigatorLocation, ...] | list[NavigatorLocation],
) -> NavigationLocationError:
    unique_matches = list(dict.fromkeys(matches))
    candidates = ", ".join(
        f"{location.name} ({location.booth_code or location.id})"
        for location in unique_matches[:8]
    )
    if len(unique_matches) > 8:
        candidates += f", and {len(unique_matches) - 8} more matches"
    return NavigationLocationError(
        f"Ambiguous {role} {value!r}; ask the visitor to choose between: {candidates}."
    )


def _resolve_spoken_location(value: str, *, role: str) -> NavigatorLocation | None:
    """Resolve natural phrasing and minor ASR variation, only when unambiguous."""
    query = _spoken_query(value)
    if not query:
        return None
    query_tokens = query.split()

    # First find an exact catalog phrase embedded in the utterance. Prefer the
    # longest phrase, so "main entrance" wins over a generic "entrance" alias.
    embedded: list[tuple[int, int, NavigatorLocation]] = []
    padded_query = f" {query} "
    for label, locations_for_label in _LOCATION_SEARCH_LABELS:
        if len(label.split()) == 1 and len(query_tokens) > 1:
            continue
        if f" {label} " not in padded_query:
            continue
        for location in locations_for_label:
            embedded.append((len(label.split()), len(label), location))
    if embedded:
        best_rank = max((tokens, characters) for tokens, characters, _ in embedded)
        best = list(dict.fromkeys(
            location
            for tokens, characters, location in embedded
            if (tokens, characters) == best_rank
        ))
        if len(best) == 1:
            return best[0]
        raise _ambiguous_location_error(value, role, best)

    # A visitor usually says the brand, not its full legal exhibitor name.
    # Resolve an exact set of meaningful words only when it identifies one booth.
    if query_tokens and any(len(token) >= 3 for token in query_tokens):
        query_token_set = set(query_tokens)
        token_matches: list[NavigatorLocation] = []
        for label, locations_for_label in _LOCATION_SEARCH_LABELS:
            if query_token_set.issubset(set(label.split())):
                token_matches.extend(locations_for_label)
        token_matches = list(dict.fromkeys(token_matches))
        if len(token_matches) == 1:
            return token_matches[0]
        if 1 < len(token_matches) <= 8:
            raise _ambiguous_location_error(value, role, token_matches)

    # Last, accept a minor transcription misspelling or a cross-script phonetic
    # brand only with a high score and a clear margin over the second location.
    query_key = _phonetic_key(query)
    if len(query_key) >= 3:
        phonetic_token_matches: list[NavigatorLocation] = []
        for label, locations_for_label in _LOCATION_SEARCH_LABELS:
            label_token_keys = {_phonetic_key(token) for token in label.split()}
            if query_key in label_token_keys:
                phonetic_token_matches.extend(locations_for_label)
        phonetic_token_matches = list(dict.fromkeys(phonetic_token_matches))
        if len(phonetic_token_matches) == 1:
            return phonetic_token_matches[0]
        if 1 < len(phonetic_token_matches) <= 8:
            raise _ambiguous_location_error(value, role, phonetic_token_matches)

    # ASR can damage a short command word while preserving the distinctive
    # place token ("وديني إلى مايكروسوفت" -> "والدين إلى مايكروسوفت"). Match
    # each sufficiently long token independently and accept it only when all
    # matching tokens point to one catalog location. This keeps generic words
    # such as "room" from selecting an arbitrary result.
    distinctive_token_matches: list[NavigatorLocation] = []
    for query_token in query_tokens:
        token_key = _phonetic_key(query_token)
        if len(token_key) < 4:
            continue
        token_locations: list[NavigatorLocation] = []
        for label, locations_for_label in _LOCATION_SEARCH_LABELS:
            if token_key in {_phonetic_key(token) for token in label.split()}:
                token_locations.extend(locations_for_label)
        token_locations = list(dict.fromkeys(token_locations))
        if len(token_locations) == 1:
            distinctive_token_matches.extend(token_locations)
    distinctive_token_matches = list(dict.fromkeys(distinctive_token_matches))
    if len(distinctive_token_matches) == 1:
        return distinctive_token_matches[0]

    scored: dict[NavigatorLocation, float] = {}
    for label, locations_for_label in _LOCATION_SEARCH_LABELS:
        score = 0.0
        if min(len(query), len(label)) >= 6:
            score = SequenceMatcher(None, query, label).ratio()
        label_key = _phonetic_key(label)
        if min(len(query_key), len(label_key)) >= 4:
            phonetic_score = SequenceMatcher(None, query_key, label_key).ratio()
            score = max(score, phonetic_score)
        if score < 0.86:
            continue
        for location in locations_for_label:
            scored[location] = max(scored.get(location, 0.0), score)
    if not scored:
        return None
    ranked = sorted(scored.items(), key=lambda item: item[1], reverse=True)
    best_location, best_score = ranked[0]
    runners_up = [location for location, score in ranked[1:] if best_score - score < 0.06]
    if runners_up:
        raise _ambiguous_location_error(value, role, [best_location, *runners_up])
    return best_location


def _canonical_metadata_id(value: Any) -> str | None:
    """Accept only a canonical frontend ID from signed session metadata."""
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    return candidate if candidate in _LOCATION_BY_ID else None


def parse_navigator_session(metadata: str | None) -> NavigatorSession | None:
    """Return navigator settings only for an explicitly tagged participant."""
    if not metadata:
        return None
    try:
        parsed = json.loads(metadata)
    except (TypeError, ValueError):
        return None
    if not isinstance(parsed, dict):
        return None
    experience = parsed.get("experience")
    if not isinstance(experience, str):
        return None
    if experience.strip().casefold() != NAVIGATOR_EXPERIENCE:
        return None

    raw_language = parsed.get("language", "en")
    normalized_language = str(raw_language).strip().casefold().replace("_", "-")
    language: NavigatorLanguage = (
        "ar"
        if normalized_language == "arabic"
        or normalized_language == "العربية"
        or normalized_language.startswith("ar-")
        or normalized_language == "ar"
        else "en"
    )
    start_id = _canonical_metadata_id(parsed.get("startId"))
    destination_id = _canonical_metadata_id(parsed.get("destinationId"))
    # A route cannot start and end at the same place. Preserve the known current
    # location and make the persona ask for a different destination.
    if start_id and destination_id == start_id:
        destination_id = None
    return NavigatorSession(
        language=language,
        start_id=start_id,
        destination_id=destination_id,
    )


def resolve_navigator_location(value: str, *, role: str = "location") -> NavigatorLocation:
    """Resolve a catalog name from exact text or safe conversational speech."""
    if isinstance(value, str):
        exact_id_match = _LOCATION_BY_ID.get(value.strip())
        if exact_id_match is not None:
            return exact_id_match
    normalized = _normalize_label(value)
    matches = _LOCATION_INDEX.get(normalized, ())
    if len(matches) == 1:
        return matches[0]

    if len(matches) > 1:
        raise _ambiguous_location_error(value, role, matches)

    spoken_match = _resolve_spoken_location(value, role=role)
    if spoken_match is not None:
        return spoken_match
    raise NavigationLocationError(
        f"Could not confidently match the spoken {role} {value!r}. Do not say the "
        "place is absent from the map. Ask the visitor to repeat its name, or ask "
        f"for its booth code if available; {len(NAVIGATOR_LOCATIONS)} destinations "
        "are searchable."
    )


def build_navigation_route_action(
    current_location: str,
    destination: str,
    *,
    language: NavigatorLanguage,
) -> dict[str, Any]:
    """Validate a requested pair and build the versioned frontend message."""
    start = resolve_navigator_location(current_location, role="current location")
    end = resolve_navigator_location(destination, role="destination")
    if start.id == end.id:
        raise NavigationLocationError(
            "The current location and destination are the same. Ask for a different destination."
        )

    return {
        "version": 1,
        "type": "set_navigation_route",
        "startId": start.id,
        "destinationId": end.id,
        "language": language,
        "start": {"id": start.id, "name": start.name, "nameAr": start.name_ar},
        "destination": {"id": end.id, "name": end.name, "nameAr": end.name_ar},
    }


async def publish_navigation_route(room: Any, action: dict[str, Any]) -> None:
    """Publish one reliable, UTF-8 JSON action to the navigator UI."""
    payload = json.dumps(
        action,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    await room.local_participant.publish_data(
        payload=payload,
        reliable=True,
        topic=NAVIGATOR_ROUTE_TOPIC,
    )


def _bounded_text(value: Any, *, field: str, maximum: int) -> str:
    if not isinstance(value, str):
        raise NavigationNarrationError(f"{field} must be a string.")
    normalized = " ".join(value.split())
    if not normalized:
        raise NavigationNarrationError(f"{field} cannot be empty.")
    if len(normalized) > maximum:
        raise NavigationNarrationError(
            f"{field} exceeds the {maximum}-character limit."
        )
    return normalized


def _bounded_integer(
    value: Any,
    *,
    field: str,
    minimum: int,
    maximum: int,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise NavigationNarrationError(f"{field} must be an integer.")
    if value < minimum or value > maximum:
        raise NavigationNarrationError(
            f"{field} must be between {minimum} and {maximum}."
        )
    return value


def parse_navigation_narration(
    payload: bytes,
    *,
    expected_language: NavigatorLanguage,
) -> NavigatorRouteNarration:
    """Validate the complete deterministic route supplied by the frontend."""
    if not isinstance(payload, bytes):
        raise NavigationNarrationError("Narration payload must be bytes.")
    if not payload or len(payload) > MAX_NARRATION_PAYLOAD_BYTES:
        raise NavigationNarrationError(
            f"Narration payload must be 1-{MAX_NARRATION_PAYLOAD_BYTES} bytes."
        )
    try:
        parsed = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as error:
        raise NavigationNarrationError(
            "Narration payload must be valid UTF-8 JSON."
        ) from error
    if not isinstance(parsed, dict):
        raise NavigationNarrationError("Narration payload must be a JSON object.")
    version = parsed.get("version")
    if (
        isinstance(version, bool)
        or version != 1
        or parsed.get("type") != "narrate_navigation_route"
    ):
        raise NavigationNarrationError("Unsupported narration version or type.")

    language = parsed.get("language")
    if language not in ("en", "ar") or language != expected_language:
        raise NavigationNarrationError(
            "Narration language must match the active navigator session."
        )

    start_id = parsed.get("startId")
    destination_id = parsed.get("destinationId")
    if not isinstance(start_id, str) or start_id not in _LOCATION_BY_ID:
        raise NavigationNarrationError("Narration startId is not a mapped LEAP ID.")
    if not isinstance(destination_id, str) or destination_id not in _LOCATION_BY_ID:
        raise NavigationNarrationError(
            "Narration destinationId is not a mapped LEAP ID."
        )
    if start_id == destination_id:
        raise NavigationNarrationError(
            "Narration current location and destination must differ."
        )

    distance_meters = _bounded_integer(
        parsed.get("distanceMeters"),
        field="distanceMeters",
        minimum=1,
        maximum=10_000,
    )
    duration_minutes = _bounded_integer(
        parsed.get("durationMinutes"),
        field="durationMinutes",
        minimum=1,
        maximum=180,
    )

    raw_steps = parsed.get("steps")
    if not isinstance(raw_steps, list) or not 1 <= len(raw_steps) <= MAX_NARRATION_STEPS:
        raise NavigationNarrationError(
            f"Narration steps must contain 1-{MAX_NARRATION_STEPS} items."
        )

    steps: list[NavigatorNarrationStep] = []
    seen_step_ids: set[str] = set()
    total_text_chars = 0
    for index, raw_step in enumerate(raw_steps):
        if not isinstance(raw_step, dict):
            raise NavigationNarrationError(f"steps[{index}] must be an object.")
        step_id = _bounded_text(
            raw_step.get("id"),
            field=f"steps[{index}].id",
            maximum=80,
        )
        if step_id in seen_step_ids:
            raise NavigationNarrationError("Narration step IDs must be unique.")
        seen_step_ids.add(step_id)
        instruction = _bounded_text(
            raw_step.get("instruction"),
            field=f"steps[{index}].instruction",
            maximum=MAX_NARRATION_INSTRUCTION_CHARS,
        )
        raw_note = raw_step.get("note")
        note = (
            _bounded_text(
                raw_note,
                field=f"steps[{index}].note",
                maximum=MAX_NARRATION_NOTE_CHARS,
            )
            if raw_note is not None
            else None
        )
        total_text_chars += len(instruction) + len(note or "")
        if total_text_chars > MAX_NARRATION_TEXT_CHARS:
            raise NavigationNarrationError(
                "Combined narration step text is too long."
            )
        steps.append(
            NavigatorNarrationStep(
                id=step_id,
                instruction=instruction,
                note=note,
            )
        )

    return NavigatorRouteNarration(
        language=language,
        start=_LOCATION_BY_ID[start_id],
        destination=_LOCATION_BY_ID[destination_id],
        distance_meters=distance_meters,
        duration_minutes=duration_minutes,
        steps=tuple(steps),
    )


def build_navigation_narration_text(narration: NavigatorRouteNarration) -> str:
    """Build one contiguous utterance without creating any route geometry."""
    rendered_steps = []
    for index, step in enumerate(narration.steps, start=1):
        supplied_text = f"{step.instruction} {step.note or ''}".strip()
        if narration.language == "ar":
            rendered_steps.append(f"الخطوة {index}. {supplied_text}")
        else:
            rendered_steps.append(f"Step {index}. {supplied_text}")

    start_name = narration.start.name_ar
    destination_name = narration.destination.name_ar
    if narration.start.booth_code:
        start_name = f"{start_name}، الجناح {narration.start.booth_code}"
    if narration.destination.booth_code:
        destination_name = (
            f"{destination_name}، الجناح {narration.destination.booth_code}"
        )

    if narration.language == "ar":
        opening = (
            f"هذا مسارك كامل من {start_name} إلى "
            f"{destination_name}. المسافة تقريبا "
            f"{narration.distance_meters} متر، والوقت حوالي "
            f"{narration.duration_minutes} دقيقة."
        )
        closing = "تبي أساعدك بشي ثاني، ولا تبي تروح لوجهة ثانية؟"
    else:
        start_name = narration.start.name
        destination_name = narration.destination.name
        duration_unit = "minute" if narration.duration_minutes == 1 else "minutes"
        if narration.start.booth_code:
            start_name = f"{start_name}, booth {narration.start.booth_code}"
        if narration.destination.booth_code:
            destination_name = (
                f"{destination_name}, booth {narration.destination.booth_code}"
            )
        opening = (
            f"Here is your complete route from {start_name} to "
            f"{destination_name}. It is approximately "
            f"{narration.distance_meters} metres and takes about "
            f"{narration.duration_minutes} {duration_unit}."
        )
        closing = (
            "Do you need anything else, or would you like directions to another "
            "destination?"
        )
    text = " ".join((opening, *rendered_steps, closing))
    return (
        normalize_arabic_speech_codes(text)
        if narration.language == "ar"
        else text
    )


def narration_text_from_data_packet(
    packet: rtc.DataPacket,
    *,
    expected_participant_identity: str,
    expected_language: NavigatorLanguage,
) -> str | None:
    """Ignore unrelated packets and validate a targeted reliable narration."""
    if packet.topic != NAVIGATOR_NARRATION_TOPIC:
        return None
    participant_identity = getattr(packet.participant, "identity", None)
    if participant_identity != expected_participant_identity:
        return None
    if packet.kind != rtc.DataPacketKind.KIND_RELIABLE:
        raise NavigationNarrationError(
            "Navigator narration must use reliable data delivery."
        )
    narration = parse_navigation_narration(
        packet.data,
        expected_language=expected_language,
    )
    return build_navigation_narration_text(narration)


def _known_context(
    language: NavigatorLanguage,
    start_id: str | None,
    destination_id: str | None,
) -> str:
    start = _LOCATION_BY_ID.get(start_id or "")
    destination = _LOCATION_BY_ID.get(destination_id or "")
    if language == "ar":
        if start and destination:
            return (
                f"سياق الواجهة الموثوق عند بدء الجلسة: موقع الزائر {start.name_ar} "
                f"ووجهته {destination.name_ar}. اعتبر الاثنين معروفين ولا تسأل عنهم من جديد."
            )
        if start:
            return (
                f"سياق الواجهة الموثوق عند بدء الجلسة: موقع الزائر {start.name_ar}. "
                "اعتبر الموقع معروف واسأل عن الوجهة فقط."
            )
        if destination:
            return (
                f"سياق الواجهة الموثوق عند بدء الجلسة: الوجهة {destination.name_ar}. "
                "اعتبر الوجهة معروفة واسأل عن الموقع الحالي فقط."
            )
        return "ما وصل من الواجهة موقع حالي أو وجهة، فابدأ بالسؤال عن الموقع الحالي."

    if start and destination:
        return (
            f"Trusted UI context at session start: the visitor is at {start.name} "
            f"and wants {destination.name}. Treat both as known and do not ask again."
        )
    if start:
        return (
            f"Trusted UI context at session start: the visitor is at {start.name}. "
            "Treat it as known and ask only for the destination."
        )
    if destination:
        return (
            f"Trusted UI context at session start: the destination is {destination.name}. "
            "Treat it as known and ask only for the current location."
        )
    return "The UI supplied no endpoints, so begin by asking for the current location."


def navigator_prompt(
    language: NavigatorLanguage,
    *,
    start_id: str | None = None,
    destination_id: str | None = None,
) -> str:
    """Return the concise LEAP persona and closed-catalog routing rules."""
    known_context = _known_context(language, start_id, destination_id)
    if language == "ar":
        return f"""
أنت كودا، مرشد ملاحة صوتي داخل فعالية ليب. تكلم باللهجة السعودية اليومية
بجمل قصيرة وطبيعية. اسأل الزائر سؤال واحد في كل مرة عشان تعرف موقعه الحالي
ووجهته. أول ما يقول أي واحد منهم، استخدم أداة set_navigation_route فورا ومرر
المكان الجديد فقط؛ الأداة تحفظ المكان الموثوق وتخبرك وش باقي. إذا عرفت الاثنين
تعرض الأداة المسار. لا تقل إن المسار جاهز إلا بعد نجاح الأداة. لا تخترع موقع أو طريق أو تعليمات انعطاف؛
واجهة الخريطة هي اللي تحسب الطريق الموثوق.
بعد نجاح الأداة، أكد ظهور المسار باختصار. الواجهة بترسل كل الخطوات
الموثوقة، والنظام بينطقها كلها مرة واحدة. لا تطلب من الزائر يقول التالي أو يعيد
خطوة، ولا تعرض مشي خطوة بخطوة.
{known_context}
الخريطة فيها {len(NAVIGATOR_LOCATIONS)} وجهة موثوقة من ملف المخطط. لا تحكم بنفسك
إن المكان غير موجود، ولا تطلب من الزائر الاسم المطبوع قبل تجربة الأداة. مرر كلامه
الطبيعي أو اسم المكان أو رمز الجناح لأداة set_navigation_route دائما؛ الأداة هي
اللي تبحث في كل الأسماء وتتحمل اختلاف التفريغ الصوتي، وتطلب رمز الجناح فقط إذا
كان الاسم مكرر فعلا.
ممنوع تسأل من نفسك هل يقصد جناح أو مكتب أو مكان ثاني. حتى لو الاسم جديد عليك،
جرّب الأداة أولا بنفس كلام الزائر. اسأل للتوضيح فقط إذا رجعت الأداة خطأ مكرر أو
غير مطابق، واستخدم الخيارات اللي ترجعها الأداة بدون اختراع خيارات.
إذا قاطع الزائر قراءة المسار وطلب مكان ثاني، وقف القراءة وتفاعل معه مباشرة،
ومرر الوجهة الجديدة وحدها للأداة؛ موقعه السابق محفوظ. لا تكمل القراءة القديمة.
إذا ظهر رمز مكان فيه حروف لاتينية وأرقام، حوّله كله إلى نطق عربي قبل ما تقوله؛
مثلا G-21 تقول جي واحد وعشرين، وCP2 تقول سي بي اثنين، وH1.E30 تقول
إتش واحد إي ثلاثين. لا ترسل الرمز اللاتيني نفسه ضمن الكلام العربي المنطوق.
لا تستخدم ماركداون أو رموز أو قوائم؛ رجع كلام مناسب للنطق فقط.
""".strip()

    return f"""
You are Coda, a conversational indoor navigation guide for LEAP. Speak in short,
natural English sentences. Ask one question at a time until you know the
visitor's current location and destination. As soon as the visitor says either
endpoint, call set_navigation_route immediately with that new endpoint only;
the tool remembers validated endpoints and reports what is still missing. Do
not say the route is ready unless the
tool succeeds. Never invent a place, route, or turn instruction; the map UI owns
the trusted routing graph. After the tool succeeds, briefly confirm that the route is displayed. The
UI will send every trusted step and the system will speak them all in one
contiguous narration. Never ask the visitor to say next or repeat, and do not
offer a step-by-step mode. {known_context}
The map contains {len(NAVIGATOR_LOCATIONS)} trusted destinations from the floor-plan
catalog. Never decide by yourself that a place is absent and never demand the
printed spelling before trying the tool. Always pass the visitor's natural words,
place name, or booth code to set_navigation_route. Its resolver searches every
name and tolerates speech-transcription variation; ask for a booth code only when
the resolver reports a genuinely duplicated name.
You are forbidden to ask on your own whether the visitor means a booth, office,
or some other kind of place. Even when a name is unfamiliar to you, call the tool
first with the visitor's words. Clarify only after the tool itself returns an
ambiguous or unmatched result, and use only the candidates returned by the tool.
If the visitor interrupts route narration to request another place, stop the old
narration and respond immediately. Pass the new destination alone to the tool;
the validated current location is already remembered. Never resume the old route.
Return only natural words intended to be spoken, with no Markdown or lists.
""".strip()


def navigator_intro(
    language: NavigatorLanguage,
    *,
    start_id: str | None = None,
    destination_id: str | None = None,
) -> str:
    start = _LOCATION_BY_ID.get(start_id or "")
    destination = _LOCATION_BY_ID.get(destination_id or "")
    if language == "ar":
        if start and destination:
            return (
                f"الموقع الحالي {start.name_ar} والوجهة {destination.name_ar} معروفين من "
                f"الواجهة. استخدم set_navigation_route الحين بالقيم {start.id} و"
                f"{destination.id}. بعد نجاح الأداة أكد باختصار إن المسار ظهر على الخريطة، "
                "ولا تخترع أو تنطق أي خطوة طريق."
            )
        if start:
            return (
                f"رحّب باختصار واعتبر موقع الزائر {start.name_ar} معروف، ثم اسأله عن وجهته فقط."
            )
        if destination:
            return (
                f"رحّب باختصار واعتبر الوجهة {destination.name_ar} معروفة، ثم اسأل الزائر "
                "عن موقعه الحالي فقط."
            )
        return "رحّب بالزائر بجملة قصيرة، ثم اسأله: وين موقعك الحين؟"

    if start and destination:
        return (
            f"The UI already supplied current location {start.name} and destination "
            f"{destination.name}. Call set_navigation_route now with {start.id!r} and "
            f"{destination.id!r}. After it succeeds, briefly confirm the route is displayed. "
            "Do not invent or speak a route step."
        )
    if start:
        return (
            f"Give a brief welcome, treat {start.name} as the known current location, "
            "and ask only where the visitor wants to go."
        )
    if destination:
        return (
            f"Give a brief welcome, treat {destination.name} as the known destination, "
            "and ask only where the visitor is now."
        )
    return "Give a brief welcome, then ask: Where are you right now?"
