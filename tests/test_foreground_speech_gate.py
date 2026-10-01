import unittest

import numpy as np

from livekit import rtc

from src.plugins.foreground_speech_gate import analyze_foreground_speech


SAMPLE_RATE = 16_000


def _audio_frame(samples: np.ndarray) -> rtc.AudioFrame:
    pcm = (np.clip(samples, -1.0, 1.0) * 32767).astype(np.int16)
    return rtc.AudioFrame(
        data=pcm.tobytes(),
        sample_rate=SAMPLE_RATE,
        num_channels=1,
        samples_per_channel=len(pcm),
    )


class ForegroundSpeechGateTests(unittest.TestCase):
    def test_rejects_steady_loud_noise(self) -> None:
        rng = np.random.default_rng(19)
        t = np.arange(SAMPLE_RATE * 2) / SAMPLE_RATE
        samples = (
            0.03 * np.sin(2 * np.pi * 120 * t)
            + 0.02 * rng.normal(size=len(t))
        )

        result = analyze_foreground_speech(_audio_frame(samples))

        self.assertFalse(result.accepted)
        self.assertEqual(result.reason, "insufficient_snr")

    def test_rejects_short_transient(self) -> None:
        t = np.arange(SAMPLE_RATE * 2) / SAMPLE_RATE
        samples = np.zeros_like(t)
        start = SAMPLE_RATE // 2
        length = SAMPLE_RATE // 10
        samples[start : start + length] = 0.2 * np.sin(
            2 * np.pi * 300 * t[:length]
        )

        result = analyze_foreground_speech(_audio_frame(samples))

        self.assertFalse(result.accepted)

    def test_accepts_sustained_foreground_audio(self) -> None:
        t = np.arange(SAMPLE_RATE * 2) / SAMPLE_RATE
        samples = np.zeros_like(t)
        start = SAMPLE_RATE // 2
        length = SAMPLE_RATE * 3 // 5
        voiced_t = t[:length]
        samples[start : start + length] = (
            0.08 * np.sin(2 * np.pi * 180 * voiced_t)
            + 0.035 * np.sin(2 * np.pi * 360 * voiced_t)
        )

        result = analyze_foreground_speech(_audio_frame(samples))

        self.assertTrue(result.accepted)
        self.assertGreaterEqual(result.foreground_duration, 0.20)


if __name__ == "__main__":
    unittest.main()
