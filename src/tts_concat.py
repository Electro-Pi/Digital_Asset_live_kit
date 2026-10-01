"""Phrase boundaries and bounded PCM conditioning inspired by the concat demo."""
from __future__ import annotations

import re

import numpy as np

GAP_MS = {"؟": 420, "!": 400, ".": 340, "…": 300, "؛": 260, ":": 240, "،": 180}


def prosody_text(text: str) -> str:
    text = " ".join(text.split())
    # Keep number punctuation (12.50, 1,000) intact.
    text = re.sub(r"(?<!\d),(?!\d)|(?<=\d),(?!\d)|(?<!\d),(?=\d)", "،", text)
    text = text.replace(";", "؛").replace("?", "؟")
    text = re.sub(r"\.{2,}|…+", "…", text)
    text = re.sub(r"([،؛؟!.:…])\s*\1+", r"\1", text)
    text = re.sub(r"\s+([،؛؟!.:…])", r"\1", text)
    text = re.sub(r"([،؛؟!:…])(?=\S)", r"\1 ", text)
    text = re.sub(r"\.(?=[^\s.\d])|(?<!\d)\.(?=\d)", ". ", text)
    # Terminal ellipses previously caused clipped words on this deployment.
    text = re.sub(r"([؟!])?\s*…$", lambda m: m.group(1) or ".", text)
    if text and text[-1] not in GAP_MS:
        text += "."
    return text


class PCMConditioner:
    """Fade the first 10ms without estimating or changing phrase gain.

    Quiet openings must not amplify later words. Output gain and peak limiting
    belong to the plugin's single fixed output stage. Input is mono PCM16.
    """

    def __init__(self, rate: int = 48000):
        self.rate = rate
        self._position = 0

    def push(self, pcm: bytes) -> bytes:
        if len(pcm) % 2:
            raise ValueError("PCM16 requires complete samples")
        fade = max(1, int(self.rate * 0.01))
        if self._position >= fade:
            return pcm
        samples = np.frombuffer(pcm, dtype="<i2").astype(np.float64)
        ramp = np.minimum(1, (np.arange(len(samples)) + self._position) / fade)
        samples *= ramp
        self._position += len(samples)
        return np.rint(samples).astype("<i2").tobytes()

    def flush(self) -> bytes:
        return b""
