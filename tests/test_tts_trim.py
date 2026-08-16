"""Trailing-breath / ghost-word trim for VoxCPM output."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.plugins.voxcpm_tts_plugin import (  # noqa: E402
    SERVER_SAMPLE_RATE,
    trim_trailing_artifacts,
)


def _tone(ms: int, hz: float = 200.0, amp: int = 8000) -> np.ndarray:
    n = int(SERVER_SAMPLE_RATE * ms / 1000)
    t = np.arange(n) / SERVER_SAMPLE_RATE
    return (np.sin(2 * np.pi * hz * t) * amp).astype(np.int16)


def _silence(ms: int) -> np.ndarray:
    return np.zeros(int(SERVER_SAMPLE_RATE * ms / 1000), dtype=np.int16)


def _ms(pcm: bytes) -> float:
    return (len(pcm) / 2) / SERVER_SAMPLE_RATE * 1000


def main() -> None:
    speech = np.concatenate([_tone(250), _silence(180)])
    trimmed = trim_trailing_artifacts(speech.tobytes())
    assert _ms(trimmed) < _ms(speech.tobytes()) - 80, (
        f"breath tail not cut: {_ms(trimmed):.0f}ms of {_ms(speech.tobytes()):.0f}ms"
    )

    ghost = np.concatenate([_tone(220), _silence(120), _tone(280, hz=160)])
    cut = trim_trailing_artifacts(ghost.tobytes())
    assert _ms(cut) < 350, f"ghost word not cut: {_ms(cut):.0f}ms"

    # Inhale after the last syllable is quieter than the speech. Cut the
    # gasp, but a full-energy last syllable must survive (the previous
    # always-clip ate «اليوم» off the greeting).
    inhale = np.concatenate([
        _tone(250, amp=8000),
        _tone(180, hz=90, amp=2000),
    ])
    no_gasp = trim_trailing_artifacts(inhale.tobytes())
    assert _ms(no_gasp) < 300, f"inhale not cut: {_ms(no_gasp):.0f}ms of {_ms(inhale.tobytes()):.0f}ms"

    intact = trim_trailing_artifacts(_tone(400, amp=8000).tobytes())
    assert _ms(intact) > 340, f"last syllable clipped: {_ms(intact):.0f}ms of 400ms"

    print("PASS trailing breath trim")
    print("PASS ghost-word-after-pause trim")
    print("PASS mid-energy inhale trim")
    print("PASS last syllable kept")


if __name__ == "__main__":
    main()
