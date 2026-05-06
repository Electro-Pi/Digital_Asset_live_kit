"""Sentence tokenizer that buffers all streamed text into one TTS request.

OpenAI TTS is registered with ``streaming=False``, so LiveKit wraps it in a
``StreamAdapter`` that defaults to BlingFire sentence splitting. Each sentence
triggers a separate ``synthesize()`` call (~1s+ TTFB per chunk), which sounds
like stuttering or lag between phrases — especially for Arabic.

This tokenizer ignores mid-stream ``flush()`` and only emits a single segment
on ``end_input()``, so one assistant reply segment maps to one TTS round-trip.
"""

from __future__ import annotations

from livekit.agents.tokenize.tokenizer import SentenceStream, SentenceTokenizer, TokenData


class _WholeUtteranceStream(SentenceStream):
    def __init__(self) -> None:
        super().__init__()
        self._buf = ""

    def push_text(self, text: str) -> None:
        self._check_not_closed()
        self._buf += text

    def flush(self) -> None:
        # StreamAdapter calls flush on LLM FlushSentinel; keep buffering so we
        # do not start a new HTTP TTS until the segment is complete.
        pass

    def end_input(self) -> None:
        self._check_not_closed()
        t = self._buf.strip()
        self._buf = ""
        if t:
            self._event_ch.send_nowait(TokenData(token=t))
        self._event_ch.close()

    async def aclose(self) -> None:
        self._event_ch.close()


class WholeUtteranceSentenceTokenizer(SentenceTokenizer):
    def tokenize(self, text: str, *, language: str | None = None) -> list[str]:
        s = text.strip()
        return [s] if s else []

    def stream(self, *, language: str | None = None) -> SentenceStream:
        return _WholeUtteranceStream()
