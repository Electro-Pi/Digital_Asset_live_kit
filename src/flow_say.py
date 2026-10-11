"""The few lines سلمى says word for word.

Most of what she says is phrased by the model from tool facts. The lines here
are the exceptions, each for a reason:

* **Greeting**: the same opening line on every call.
* **Order summary and confirmation**: spoken exactly (``say_exact``) because
  they carry the items and the total the caller is agreeing to pay.
* **Guardrail replies**: fixed and safe (``src.guardrails``).

Every repeated line has variants, picked in rotation per call
(``SessionState.pick_variant``) so a caller never hears the same sentence
twice in a row. Nothing here names a restaurant or a dish.
"""

from __future__ import annotations

import hashlib
from typing import Any, Iterable

# The opening, spoken word for word on every call (the owner's wording).
# It names her: the caller has just been picked up by a voice with no face and
# no screen. Tashkeel on every letter but the last of each word.
GREETING_SAY_AR = "حَيَّاك اللَّه، مَعَاك سَلْمَى مِن لُقْمَة. إِيش أَقْدَر أَسَاعِدَك فِيه الْيَوْم؟"


ABUSE_LINES: dict[str, tuple[str, ...]] = {
    "jailbreak": (
        "مَا أَقْدَر أَسَوِّي هَذَا، بَس أَقْدَر أَسَاعِدَك تِكَمِّل طَلَبَك عَادِي. تَبِي تِكَمِّل؟",
        "هَذَا مُو شَي أَقْدَر أَسَوِّيه، خَلْنَا نِكَمِّل طَلَبَك. وِش تَبِي؟",
    ),
    "prompt": (
        "هَذَا مُو شَي أَقْدَر أَشَارِكَه، بَس تِقْدَر تِسْأَلْنِي أَي شَي عَن الطَّلَب.",
        "مَا أَقْدَر أَشَارِك هَالشَّي، بَس أَبْشِر بِأَي شَي يِخُص طَلَبَك.",
    ),
    "internal": (
        "هَذِي مَعْلُومَات مَا أَقْدَر أَشَارِكْهَا، بَس أَقْدَر أَسَاعِدَك تِطْلُب أَكْلَك.",
        "مَا أَقْدَر أَعْطِيك مَعْلُومَات عَن طَلَبَات ثَانِيَة، خَلْنَا نِكَمِّل طَلَبَك.",
    ),
    "discount": (
        "مَا أَقْدَر أَسَوِّي خُصُومَات مِن عِنْدِي، الْأَسْعَار هِي نَفْسَهَا الْمُعْتَمَدَة. تَبِي نِكَمِّل بِهَالسِّعْر؟",
        "الْخُصُومَات مُو بِيَدِي، بَس أَقْدَر أَدَوِّر لَك عَلَى عَرْض يِنَاسْبَك. تَبِي؟",
    ),
    "free": (
        "مَا أَقْدَر أَسَوِّي هَذَا، كُل صِنْف بِسِعْرَه الْمُسَجَّل. تَبِي نِكَمِّل الطَّلَب؟",
        "كُل صِنْف لَه سِعْرَه، مَا أَقْدَر أَخَلِّيه بِبَلَاش. نِكَمِّل؟",
    ),
}

UNCLEAR_LINES: tuple[str, ...] = (
    "مَعْذِرَة، مَا فِهِمْت عَلَيْك زِين، تِقْدَر تِعِيدْهَا؟",
    "مَا وِصَلْنِي كَلَامَك زِين، عِيدَه لِي لَو سَمَحْت.",
    "مَعْلِيش، قُول لِي وِش تَبِي الْحِين وَأَكَمِّل لَك الطَّلَب.",
)

ORDER_CONFIRMED_CLOSES: tuple[str, ...] = (
    "يِعْطِيك الْعَافِيَة، وَنِشُوفَك قَرِيب!",
    "تِشَرَّفْنَا فِيك، وَبِالْعَافِيَة!",
    "يَا هَلَا، وَنِسْتَنَّاك مَرَّة ثَانِيَة!",
)


def _stable_index(*parts: str, mod: int) -> int:
    """Deterministic selection — same inputs -> same index, always.

    Never backed by ``random`` or wall-clock time, so a retried/recovered turn
    can never land on a different pick than the first attempt.
    """
    if mod <= 0:
        return 0
    key = "|".join(str(p) for p in parts).encode("utf-8")
    digest = hashlib.sha256(key).hexdigest()
    return int(digest[:8], 16) % mod


def order_summary_say_ar(*, lines: Iterable[str], total_say: str) -> str:
    listed = "، ".join(str(x).strip() for x in lines if str(x).strip())
    total = str(total_say or "").strip()
    if not listed:
        return f"الْمَجْمُوع {total}. أَثَبِّت الطَّلَب؟" if total else "أَثَبِّت الطَّلَب؟"
    if total:
        return f"خَلْنَا نِرَاجِع: {listed}، الْمَجْمُوع {total}. أَثَبِّت الطَّلَب؟"
    return f"خَلْنَا نِرَاجِع: {listed}. أَثَبِّت الطَّلَب؟"


def summary_line_say_ar(*, name: str, qty_say: str, extras_say: str = "") -> str:
    dish = str(name or "").strip()
    qty = str(qty_say or "").strip()
    extras = str(extras_say or "").strip()
    if dish and qty and extras:
        return f"{dish} عَدَد {qty}، مَعْهَا {extras}"
    if dish and qty:
        return f"{dish} عَدَد {qty}"
    if dish and extras:
        return f"{dish}، مَعْهَا {extras}"
    return dish


def order_confirmed_say_ar(*, total_say: str = "", name: str = "", state: Any = None) -> str:
    who = str(name or "").strip()
    ya = f" يَا {who}" if who else ""
    total = str(total_say or "").strip()
    head = f"أَبْشِر{ya}، ثَبَّت طَلَبَك"
    if total:
        head += f"، وَالْمَجْمُوع {total}"
    close = (
        state.pick_variant("order_confirmed_close", ORDER_CONFIRMED_CLOSES)
        if state is not None and hasattr(state, "pick_variant")
        else ORDER_CONFIRMED_CLOSES[0]
    )
    return f"{head}. {close}"


def delivery_eta_say_ar(eta: str) -> str:
    when = str(eta or "").strip()
    if not when:
        return ""
    return f"بِيوَصْلَك خِلَال {when} إِن شَاء اللَّه."


def pickup_eta_say_ar(*, eta: str, branch: str) -> str:
    when = str(eta or "").strip()
    place = str(branch or "").strip()
    if when and place:
        return f"يِكُون جَاهِز لِلِاسْتِلَام خِلَال {when} مِن فَرْع {place}."
    if when:
        return f"يِكُون جَاهِز لِلِاسْتِلَام خِلَال {when}."
    if place:
        return f"يِكُون جَاهِز لِلِاسْتِلَام مِن فَرْع {place}."
    return ""
