"""TTS each string in a list, in order, then save one smoothly concatenated audio file.

Talks to the OpenAI-compatible Coda speech gateway configured in .env.
The SDK appends /audio/speech, so base_url must include /v1
(TTS_SERVER_URL is the gateway root without /v1).

Smoothness comes from two sides:
  * text side  - punctuation tells the model where to breathe, slow down and
    lift intonation, so each chunk is spoken with a finished cadence.
  * audio side - chunks are silence-trimmed, level-matched, edge-faded and
    joined with a pause whose length follows the punctuation that ended the line.
"""

import io
import os
import re
import wave
from datetime import datetime
from pathlib import Path

import numpy as np
from dotenv import load_dotenv
from openai import OpenAI
# from clean import tts_safe_text

load_dotenv()

# --- join tuning -----------------------------------------------------------
SILENCE_DB = -45.0      # frames this far under the chunk peak count as silence
KEEP_PAD_MS = 40        # breath left in place after trimming
EDGE_FADE_MS = 10       # ramp on each chunk edge, kills seam clicks
TARGET_DBFS = -23.0     # per-chunk RMS target, kills volume jumps
PEAK_CEILING = 0.97     # final safety limit

# Pause after a chunk, picked from the punctuation it ends with. A full stop
# needs more air than a comma; a question needs more than a full stop.
GAP_MS = {
    "؟": 420,
    "!": 400,
    ".": 340,
    "…": 300,
    "؛": 260,
    ":": 240,
    "،": 180,
}
GAP_DEFAULT_MS = 320

# "…" is a pause hint for the model. If the voice reads it out loud or chokes
# on it, switch to "comma" (replace with ،) or "dots" (replace with ...).
ELLIPSIS_MODE = "keep"

tts_root = os.environ["TTS_SERVER_URL"].rstrip("/")
base_url = tts_root if tts_root.endswith("/v1") else f"{tts_root}/v1"

client = OpenAI(
    api_key=os.environ.get("SPEECH_GATEWAY_API_KEY") or os.environ["LLM_API_KEY"],
    base_url=base_url
)

# Punctuated for delivery, not for grammar: a comma at every breath point,
# "…" where the thought should hang, "؟" kept last so the line lifts.
texts = [
    "الطَّبَرِي أَلَّفَ كِتابه المَشهور جامِع البَيان في تَفسير القُرآن، وَهُو مِن أَعظَم وَأَوثَق كُتُب التَّفسير في تاريخنا.",
]

TERMINALS = ".؟!…؛:،"


def prosody_text(text: str) -> str:
    """Clean the text so its punctuation actually lands as prosody.

    Latin marks are swapped for the Arabic forms the model was trained on,
    spacing is fixed (a space before a comma reads as a stray token), and a
    missing terminal mark is added so the line does not end mid-breath.
    """
    t = " ".join(text.split())
    t = t.replace(",", "،").replace(";", "؛").replace("?", "؟")
    t = re.sub(r"\.\s*\.[.\s]*", "…", t)             # any dot run is one ellipsis
    t = re.sub(r"([،؛؟!.:…])[\s]*\1+", r"\1", t)     # no repeated marks

    if ELLIPSIS_MODE == "comma":
        t = t.replace("…", "،")
    elif ELLIPSIS_MODE == "dots":
        t = t.replace("…", "...")

    t = re.sub(r"\s+([،؛؟!.:…])", r"\1", t)          # no space before a mark
    t = re.sub(r"([،؛؟!:…])(?=\S)", r"\1 ", t)       # one space after a mark
    t = re.sub(r"\.(?=[^\s.])", ". ", t)             # same for ".", not inside "..."
    t = t.strip()

    if t and t[-1] not in TERMINALS:
        t += "."
    return t


def gap_after(text: str) -> int:
    """Pause length for the seam that follows this line."""
    end = text.rstrip()[-1:] if text.strip() else ""
    return GAP_MS.get(end, GAP_DEFAULT_MS)


texts = [prosody_text(t) for t in texts]
# texts = [tts_safe_text(t) for t in texts]


def wav_to_float(data: bytes) -> tuple[np.ndarray, int, int]:
    """Decode a wav blob to float32 samples shaped (frames, channels)."""
    with wave.open(io.BytesIO(data), "rb") as src:
        rate = src.getframerate()
        channels = src.getnchannels()
        width = src.getsampwidth()
        raw = src.readframes(src.getnframes())

    if width == 2:
        x = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    elif width == 4:
        x = np.frombuffer(raw, dtype="<i4").astype(np.float32) / 2147483648.0
    elif width == 1:
        x = (np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
    else:
        raise ValueError(f"unsupported sample width: {width}")

    return x.reshape(-1, channels), rate, channels


def trim_silence(x: np.ndarray, rate: int) -> np.ndarray:
    """Cut the chunk's own lead-in/tail silence so gaps become uniform."""
    hop = max(1, rate // 100)                       # 10 ms frames
    mono = x.mean(axis=1)
    n = (len(mono) // hop) * hop
    if n == 0:
        return x
    frames = mono[:n].reshape(-1, hop)
    energy = np.sqrt((frames ** 2).mean(axis=1) + 1e-12)
    peak = energy.max()
    if peak <= 0:
        return x[:0]
    loud = np.flatnonzero(energy >= peak * (10 ** (SILENCE_DB / 20)))
    if loud.size == 0:
        return x[:0]

    pad = int(rate * KEEP_PAD_MS / 1000)
    start = max(0, loud[0] * hop - pad)
    end = min(len(x), (loud[-1] + 1) * hop + pad)
    return x[start:end]


def fade_edges(x: np.ndarray, rate: int) -> np.ndarray:
    """Short in/out ramps so a seam never lands on a nonzero sample."""
    n = int(rate * EDGE_FADE_MS / 1000)
    if n <= 0 or len(x) < 2 * n:
        return x
    ramp = np.linspace(0.0, 1.0, n, dtype=np.float32)[:, None]
    x = x.copy()
    x[:n] *= ramp
    x[-n:] *= ramp[::-1]
    return x


def match_level(x: np.ndarray) -> np.ndarray:
    """Pull each chunk to the same RMS so loudness does not step between lines."""
    rms = float(np.sqrt((x ** 2).mean() + 1e-12))
    if rms <= 0:
        return x
    gain = (10 ** (TARGET_DBFS / 20)) / rms
    gain = min(gain, PEAK_CEILING / max(float(np.abs(x).max()), 1e-9))
    return x * gain


def concat_wavs(wav_chunks: list[bytes], out_path: Path,
                gaps_ms: list[int] | None = None) -> None:
    """Join chunks, inserting gaps_ms[i] of silence after chunk i."""
    pieces: list[np.ndarray] = []
    gaps: list[int] = []
    rate = channels = None

    for i, data in enumerate(wav_chunks):
        x, r, ch = wav_to_float(data)
        if rate is None:
            rate, channels = r, ch
        elif (r, ch) != (rate, channels):
            raise ValueError(f"format mismatch: {(r, ch)} vs {(rate, channels)}")

        x = trim_silence(x, r)
        if len(x) == 0:
            continue
        pieces.append(fade_edges(match_level(x), r))
        gaps.append(gaps_ms[i] if gaps_ms else GAP_DEFAULT_MS)

    if not pieces:
        raise ValueError("no audio to write")

    out_parts = [pieces[0]]
    for gap_ms, piece in zip(gaps, pieces[1:]):
        out_parts.append(np.zeros((int(rate * gap_ms / 1000), channels), dtype=np.float32))
        out_parts.append(piece)
    out = np.concatenate(out_parts)

    peak = float(np.abs(out).max())
    if peak > PEAK_CEILING:
        out *= PEAK_CEILING / peak

    pcm = np.clip(out * 32767.0, -32768, 32767).astype("<i2")
    with wave.open(str(out_path), "wb") as dst:
        dst.setnchannels(channels)
        dst.setsampwidth(2)
        dst.setframerate(rate)
        dst.writeframes(pcm.tobytes())

    print(f"joined {len(pieces)} chunks -> {len(out) / rate:.2f}s @ {rate} Hz")


chunks = []
for i, text in enumerate(texts, 1):
    response = client.audio.speech.create(
        model=os.environ.get("TTS_MODEL", "Coda-V2"),
        voice=os.environ.get("TTS_VOICE", "nouf"),
        input=text,
        response_format="wav",
    )
    data = response.content
    chunks.append(data)
    print(f"[{i}/{len(texts)}] got {len(data)} bytes, gap after {gap_after(text)} ms")

stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
out_path = Path(f"speech_concat_{stamp}.wav")
concat_wavs(chunks, out_path, [gap_after(t) for t in texts])
print(f"Wrote {out_path.resolve()} ({out_path.stat().st_size} bytes)")
