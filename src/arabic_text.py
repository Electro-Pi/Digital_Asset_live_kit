"""Display-side Arabic text handling for the agent's published transcript.

The agent writes diacritized Arabic on purpose: the prompt asks for
word-internal tashkeel (bare word endings, so no i'rab) so the TTS voices the
vowels instead of guessing them, and those marks survive all the way into the
synthesis request — see ``normalize_arabic`` in
``src/plugins/voxcpm_tts_plugin.py``.

The marks are for the voice, not the reader. Ordinary Saudi prose is written
bare, and a fully vowelled transcript reads like a schoolbook, so the text
published to the room is stripped instead. Mirrors ``stripTashkeel`` in the
frontend's ``src/lib/arabic.ts``: the frontend strip stays as a second line of
defence for text from other sources, but doing it here is what makes the rule
hold for every client, not just this one.
"""

from __future__ import annotations

import re
import unicodedata

# Removed:
#   * U+064B-U+0652  — standard tashkeel (fatha…sukun, shadda, tanween)
#   * U+0670         — dagger alef; a real long /aː/ in هٰذا, but dropping it
#                      yields هذا, which is the ordinary bare spelling
#   * U+0640 tatweel — a typographic stretch, never phonetic
#   * U+0610-U+061A  — honorific annotations
#   * U+06D6-U+06ED  — Quranic recitation/annotation marks
_TASHKEEL_AND_MARKS = re.compile(
    "[\\u064b-\\u0652\\u0670\\u0640\\u0610-\\u061a\\u06d6-\\u06dc\\u06df-\\u06e8\\u06ea-\\u06ed]"
)


def strip_tashkeel(text: str) -> str:
    """Strip diacritics for display, preserving letter identity.

    The NFC pass is load-bearing. Hamza and maddah also exist as COMBINING marks
    (U+0653-U+0655), so decomposed text spells أ as ا+U+0654. Deleting that range
    silently rewrites letters — رئيس becomes رييس, مؤسسة becomes موسسة — so it
    stays out of the class above, and composing first turns any such sequence
    back into a single letter no rule here can touch.
    """
    return _TASHKEEL_AND_MARKS.sub("", unicodedata.normalize("NFC", text))


# ─── Dual-form markers ───────────────────────────────────────────────────────
# A reply is read on screen and spoken aloud, and those outputs need different
# forms for brand names, booth codes and numbers. The model writes both once as
# [[written|spoken]]; the transcript keeps the first and TTS keeps the second.
_OPEN = "[["
_CLOSE = "]]"
_SEP = "|"
_MAX_PENDING = 400

WRITTEN, SPOKEN = 0, 1


def resolve_dual_form(text: str, side: int) -> str:
    """Resolve every complete [[written|spoken]] marker in a finished string."""
    stream = DualFormStream(side)
    return stream.feed(text) + stream.flush()


class DualFormStream:
    """Resolve dual-form markers across streamed partial text deltas."""

    def __init__(self, side: int) -> None:
        self._side = side
        self._buffer = ""

    def feed(self, text: str) -> str:
        """Consume one delta, returning whatever is now unambiguous."""
        self._buffer += text
        out: list[str] = []
        while self._buffer:
            start = self._buffer.find(_OPEN)
            if start == -1:
                keep = 1 if self._buffer.endswith("[") else 0
                if keep:
                    out.append(self._buffer[:-1])
                    self._buffer = self._buffer[-1:]
                else:
                    out.append(self._buffer)
                    self._buffer = ""
                break
            end = self._buffer.find(_CLOSE, start + len(_OPEN))
            if end == -1:
                out.append(self._buffer[:start])
                self._buffer = self._buffer[start:]
                if len(self._buffer) > _MAX_PENDING:
                    out.append(self._release(self._buffer))
                    self._buffer = ""
                break
            out.append(self._buffer[:start])
            out.append(self._pick(self._buffer[start + len(_OPEN) : end]))
            self._buffer = self._buffer[end + len(_CLOSE) :]
        return "".join(out)

    def flush(self) -> str:
        """Release anything still held when the reply ends mid-marker."""
        pending, self._buffer = self._buffer, ""
        return self._release(pending)

    def _pick(self, inner: str) -> str:
        written, sep, spoken = inner.partition(_SEP)
        if not sep:
            return written
        return written if self._side == WRITTEN else spoken

    def _release(self, pending: str) -> str:
        """Salvage readable text from a marker that never closed."""
        if not pending.startswith(_OPEN):
            return pending
        return self._pick(pending[len(_OPEN) :])
