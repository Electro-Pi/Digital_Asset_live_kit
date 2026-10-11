"""Phrase boundaries and PCM joins adapted from tts_mulitple_concat.py."""
import re

import numpy as np

GAP_MS = {"؟": 420, "!": 400, ".": 340, "…": 300, "؛": 260, ":": 240, "،": 180}
TERMINALS = "".join(GAP_MS)


def speech_chunks(text: str, max_chars: int = 180) -> list[str]:
    """Keep punctuation and vowels; split long unpunctuated phrases at words."""
    text = " ".join(text.split()).translate(str.maketrans({",": "،", ";": "؛", "?": "؟"}))
    text = re.sub(r"\.{2,}", "…", text)
    text = re.sub(r"\s+([،؛؟!.:…])", r"\1", text)
    # Decimal points and times (3.5 / 12:30) belong to their number.
    boundary = r"(?<!\d)[.:]|[.:](?!\d)|[،؛؟!…]"
    chunks = []
    start = 0
    for match in re.finditer(rf"(?:{boundary})+[\"'»”)]*", text):
        chunks.append(text[start:match.end()].strip())
        start = match.end()
    chunks.append(text[start:].strip())
    result = []
    for chunk in chunks:
        while len(chunk) > max_chars:
            cut = chunk.rfind(" ", 0, max_chars + 1)
            if cut <= 0:
                break  # Never break a word, number, or its diacritics.
            result.append(chunk[:cut].rstrip() + "،")
            chunk = chunk[cut:].strip()
        if chunk and any(c.isalnum() for c in chunk):
            if chunk.rstrip('\"\'»”)')[-1:] not in TERMINALS:
                chunk += "."
            result.append(chunk)
    return result


def gap_after(text: str) -> int:
    return GAP_MS.get(text.rstrip().rstrip('\"\'»”)')[-1:], 320)


def smooth_pcm(pcm: bytes, rate: int) -> bytes:
    """Trim with 40 ms padding, level to -23 dBFS, and fade 10 ms edges."""
    x = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0
    if not len(x) or not np.any(x):
        return b""
    hop = max(1, rate // 100)
    # Include the final partial frame so a short ending is never discarded.
    frames = np.pad(x, (0, (-len(x)) % hop)).reshape(-1, hop)
    energy = np.sqrt(np.mean(frames ** 2, axis=1))
    loud = np.flatnonzero(energy >= energy.max() * 10 ** (-45 / 20))
    pad = int(rate * 0.040)
    x = x[max(0, loud[0] * hop - pad):min(len(x), (loud[-1] + 1) * hop + pad)]
    rms = float(np.sqrt(np.mean(x ** 2)))
    gain = min(10 ** (-23 / 20) / max(rms, 1e-12), 0.97 / max(float(np.abs(x).max()), 1e-12))
    x *= gain
    n = min(int(rate * 0.010), len(x) // 2)
    if n:
        ramp = np.linspace(0, 1, n, dtype=np.float32)
        x[:n] *= ramp
        x[-n:] *= ramp[::-1]
    return np.clip(x * 32767, -32768, 32767).astype("<i2").tobytes()
