"""Foreground-audio checks used before sending a VAD segment to ASR.

Neural VAD answers "does this resemble speech?" but does not ensure that the
speech is prominent over the local noise floor.  This second gate rejects
steady noise and short transients that occasionally fool VAD.
"""

from __future__ import annotations

from dataclasses import dataclass
import math

import numpy as np

from livekit import rtc


@dataclass(frozen=True)
class ForegroundSpeechAnalysis:
    accepted: bool
    reason: str
    duration: float
    foreground_duration: float
    noise_rms: float
    foreground_rms: float
    snr_db: float


def analyze_foreground_speech(
    frame: rtc.AudioFrame,
    *,
    window_ms: int = 20,
    min_foreground_rms: float = 0.015,
    min_snr_db: float = 8.0,
    min_foreground_duration: float = 0.20,
) -> ForegroundSpeechAnalysis:
    """Return whether a VAD segment contains sustained foreground audio.

    The lower RMS percentile estimates the segment's local noise floor.  A
    valid segment must contain enough 20 ms windows that are both above an
    absolute level and sufficiently stronger than that floor.
    """

    if frame.sample_rate <= 0 or frame.num_channels <= 0:
        return ForegroundSpeechAnalysis(False, "invalid_audio", 0, 0, 0, 0, 0)

    samples = np.asarray(frame.data, dtype=np.int16).astype(np.float32)
    if frame.num_channels > 1:
        samples = samples.reshape(-1, frame.num_channels).mean(axis=1)
    samples /= 32768.0

    duration = len(samples) / frame.sample_rate
    window_samples = max(1, frame.sample_rate * window_ms // 1000)
    complete_samples = len(samples) // window_samples * window_samples
    if complete_samples == 0:
        return ForegroundSpeechAnalysis(
            False, "too_short", duration, 0, 0, 0, 0
        )

    windows = samples[:complete_samples].reshape(-1, window_samples)
    rms = np.sqrt(np.mean(windows * windows, axis=1))

    # VAD segments contain prefix/trailing padding, which provides a useful
    # local baseline. Keep a small floor so digital silence cannot produce an
    # infinite SNR.
    noise_rms = max(float(np.percentile(rms, 20)), 1e-4)
    foreground_rms = float(np.percentile(rms, 90))
    snr_db = 20.0 * math.log10(max(foreground_rms, 1e-8) / noise_rms)

    required_rms = max(
        min_foreground_rms,
        noise_rms * (10.0 ** (min_snr_db / 20.0)),
    )
    foreground_duration = float(np.count_nonzero(rms >= required_rms) * window_ms / 1000)

    if foreground_rms < min_foreground_rms:
        reason = "below_minimum_level"
    elif snr_db < min_snr_db:
        reason = "insufficient_snr"
    elif foreground_duration < min_foreground_duration:
        reason = "foreground_too_short"
    else:
        reason = "accepted"

    return ForegroundSpeechAnalysis(
        accepted=reason == "accepted",
        reason=reason,
        duration=duration,
        foreground_duration=foreground_duration,
        noise_rms=noise_rms,
        foreground_rms=foreground_rms,
        snr_db=snr_db,
    )
