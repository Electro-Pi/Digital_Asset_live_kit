"""Incremental phrase boundaries for Arabic speech and streamed LLM replies."""

from __future__ import annotations

import re
from collections.abc import Callable

from livekit.agents import tokenize
from livekit.agents.tokenize.tokenizer import SentenceStream, TokenData

from src.spoken_lexicon import strip_speech_artifacts


# Require a following separator while streaming: a final '.' may still become
# '...' or a decimal when the next LLM token arrives. Keep punctuation intact.
_BOUNDARY = re.compile(r"[.!?؟۔…]+(?=\s)")
_UNSAFE = re.compile(
    r"[<>{}\[\]|`\\]|\b(?:call\s*:|thought|analysis|commentary|final|channel|assistant|system)\b",
    re.IGNORECASE,
)


class PhraseBuffer:
    def __init__(self, min_length: int = 32, continuation_min_length: int | None = None) -> None:
        self.first_min_length = max(1, min_length)
        self.min_length = self.first_min_length
        self.continuation_min_length = max(self.min_length, continuation_min_length or self.min_length)
        self.text = ""

    def push(self, text: str) -> list[str]:
        self.text += text
        parts: list[str] = []
        start = 0
        for match in _BOUNDARY.finditer(self.text):
            end = match.end()
            phrase = self.text[start:end].strip()
            if len(phrase) >= self.min_length:
                parts.append(phrase)
                start = end
                self.min_length = self.continuation_min_length
        self.text = self.text[start:]
        return parts

    def flush(self) -> list[str]:
        tail = self.text.strip()
        self.text = ""
        self.min_length = self.first_min_length
        return [tail] if tail else []


class SpeechPhraseTokenizer(tokenize.SentenceTokenizer):
    """Start quickly, then batch later phrases to preserve voice continuity.

    Reinitializing VoxCPM at every ellipsis restarts its delivery. Once the
    first phrase is playing, accumulate more context for subsequent requests.
    EOF always releases a short remainder, with no timer or extra waiting.
    """

    def __init__(self, min_length: int = 32, continuation_min_length: int = 160) -> None:
        self.min_length = min_length
        self.continuation_min_length = continuation_min_length

    def tokenize(self, text: str, *, language: str | None = None) -> list[str]:
        buf = PhraseBuffer(self.min_length, self.continuation_min_length)
        return buf.push(text) + buf.flush()

    def stream(self, *, language: str | None = None) -> SentenceStream:
        return _PhraseStream(PhraseBuffer(self.min_length, self.continuation_min_length))


class WholeReplyTokenizer(tokenize.SentenceTokenizer):
    """Hand the TTS the complete reply as a single request.

    Every request is a separate VoxCPM generation, so splitting a reply into
    phrases put a stop, a silence and a restart of her pitch and rhythm between
    them — heard live as cuts and stutter. One request per reply has no joins.
    The cost is waiting for the LLM to finish the reply before synthesis starts
    (measured 2026-09-16: 0.46 s median, 1.26 s p90 from first token to done).
    """

    def tokenize(self, text: str, *, language: str | None = None) -> list[str]:
        text = text.strip()
        return [text] if text else []

    def stream(self, *, language: str | None = None) -> SentenceStream:
        return _PhraseStream(_WholeReplyBuffer())


class ConcatPhraseTokenizer(tokenize.SentenceTokenizer):
    """Stream sentences without restarting synthesis at each comma."""

    def tokenize(self, text: str, *, language: str | None = None) -> list[str]:
        buffer = _ConcatBuffer()
        return buffer.push(text) + buffer.flush()

    def stream(self, *, language: str | None = None) -> SentenceStream:
        return _PhraseStream(_ConcatBuffer())


class _ConcatBuffer:
    # Wait for whitespace so decimal points and multi-token punctuation remain
    # intact. Very short openers stay with the next clause for voice context.
    boundary = re.compile(r"(?:[؟?!۔]+|(?<!\.)\.(?!\.))(?=\s)")

    def __init__(self):
        self.text = ""

    def push(self, text: str) -> list[str]:
        self.text += text
        phrases = []
        start = 0
        for match in self.boundary.finditer(self.text):
            phrase = self.text[start:match.end()].strip()
            if len(phrase.split()) >= 4:
                phrases.append(phrase)
                start = match.end()
        self.text = self.text[start:]
        return phrases

    def flush(self) -> list[str]:
        text, self.text = self.text.strip(), ""
        return [text] if text else []


class _WholeReplyBuffer:
    def __init__(self) -> None:
        self.text = ""

    def push(self, text: str) -> list[str]:
        self.text += text
        return []

    def flush(self) -> list[str]:
        tail = self.text.strip()
        self.text = ""
        return [tail] if tail else []


class _PhraseStream(SentenceStream):
    def __init__(self, buffer: PhraseBuffer | _WholeReplyBuffer | _ConcatBuffer) -> None:
        super().__init__()
        self._buffer = buffer

    def push_text(self, text: str) -> None:
        self._check_not_closed()
        for phrase in self._buffer.push(text):
            self._event_ch.send_nowait(TokenData(token=phrase))

    def flush(self) -> None:
        self._check_not_closed()
        for phrase in self._buffer.flush():
            self._event_ch.send_nowait(TokenData(token=phrase))

    def end_input(self) -> None:
        self.flush()
        self._do_close()

    async def aclose(self) -> None:
        self._do_close()


class GuardedReplyStream:
    """Release only validated complete phrases; defer suspicious tails to EOF.

    The caller retains the entire response for the existing tool recovery
    pass. Once deferred, nothing else from this generation is released early.
    """

    def __init__(self, validator: Callable[[str], bool], min_length: int = 32) -> None:
        self.buffer = PhraseBuffer(min_length)
        self.validator = validator
        self.spoken = ""
        self.deferred = False

    def push(self, text: str) -> list[str]:
        if self.deferred:
            return []
        if _UNSAFE.search(self.buffer.text + text):
            self.deferred = True
            return []
        out: list[str] = []
        for phrase in self.buffer.push(text):
            cleaned = strip_speech_artifacts(phrase)
            candidate = " ".join(filter(None, (self.spoken, cleaned)))
            if not cleaned or not self.validator(candidate):
                self.deferred = True
                break
            self.spoken = candidate
            # The separator lets the TTS tokenizer emit this phrase now.
            out.append(cleaned + " ")
        return out

    def remaining(self, validated_text: str) -> str:
        text = " ".join(validated_text.split())
        if not self.spoken:
            return text
        if text.startswith(self.spoken):
            return text[len(self.spoken):].lstrip()
        # A later whole-response correction must not repeat already-spoken text.
        return ""
