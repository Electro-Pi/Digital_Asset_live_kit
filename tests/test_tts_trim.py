"""Speech-preserving endings and PCM streaming regressions. Run directly."""
from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path

import aiohttp
from aiohttp import web
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.plugins.voxcpm_tts_plugin import (
    FADE_MS,
    HOLD_TAIL_MS,
    SERVER_SAMPLE_RATE as RATE,
    TRAILING_SILENCE_MS,
    VoxCPMTTSPlugin,
    apply_output_gain,
    _TailHoldback,
    _WSOLAStretcher,
    settle_final_ellipsis,
    trim_trailing_artifacts,
)
from src.spoken_lexicon import clean_for_speech, finalize_prosody


def tone(ms: int, amp: int = 8000) -> np.ndarray:
    t = np.arange(int(RATE * ms / 1000)) / RATE
    return (np.sin(2 * np.pi * 200 * t) * amp).astype('<i2')


def silence(ms: int) -> np.ndarray:
    return np.zeros(int(RATE * ms / 1000), dtype='<i2')


class EndingsTest(unittest.TestCase):
    def test_style_cannot_escape_control_prefix(self):
        for style in ("warm)read this", "(warm)", "（warm）"):
            with self.assertRaisesRegex(ValueError, "parentheses"):
                VoxCPMTTSPlugin(style=style)

    def test_startup_buffers_audio_and_flushes_short_replies_without_loss(self):
        hold = _TailHoldback(RATE, startup_ms=240)
        first = tone(200).tobytes()
        second = tone(200).tobytes()
        self.assertEqual(hold.push(first), b'')
        ready = hold.push(second)
        self.assertGreaterEqual(len(ready), RATE * 240 // 1000 * 2)
        self.assertEqual(ready + hold.flush(RATE), trim_trailing_artifacts(first + second))
        short = _TailHoldback(RATE, startup_ms=240)
        self.assertEqual(short.push(first), b'')
        self.assertEqual(short.flush(RATE), trim_trailing_artifacts(first))

    def test_volume_boost_preserves_timing_pitch_and_silence(self):
        source = np.concatenate([tone(300), silence(120)])
        boosted = np.frombuffer(apply_output_gain(source.tobytes(), 4.0), dtype='<i2')
        self.assertEqual(len(boosted), len(source))
        np.testing.assert_array_equal(boosted == 0, source == 0)
        np.testing.assert_array_equal(np.signbit(boosted), np.signbit(source))
        measured_db = 20 * np.log10(np.linalg.norm(boosted.astype(float)) / np.linalg.norm(source.astype(float)))
        self.assertAlmostEqual(measured_db, 4.0, places=2)

    def test_volume_limiter_is_bounded_and_chunk_independent(self):
        source = np.array([-32768, -30000, -12000, 0, 12000, 30000, 32767], dtype='<i2')
        out = apply_output_gain(source.tobytes(), 12.0)
        values = np.frombuffer(out, dtype='<i2').astype(int)
        self.assertTrue(np.all(np.diff(values) >= 0))
        self.assertTrue(np.all(np.abs(values) <= 32768))
        chunks = b''.join(apply_output_gain(source[i:i+1].tobytes(), 12.0) for i in range(len(source)))
        self.assertEqual(out, chunks)
        self.assertEqual(apply_output_gain(source.tobytes(), 0.0), source.tobytes())

    def test_real_word_after_pause_survives(self):
        pcm = np.concatenate([tone(220), silence(120), tone(280)]).tobytes()
        out = trim_trailing_artifacts(pcm)
        fade_bytes = int(RATE * FADE_MS / 1000) * 2
        self.assertEqual(out[:len(pcm) - fade_bytes], pcm[:-fade_bytes])
        self.assertGreaterEqual(len(out), len(pcm))

    def test_quiet_final_consonant_survives(self):
        for amplitude in (2000, 400, 40):
            with self.subTest(amplitude=amplitude):
                pcm = np.concatenate([tone(250), tone(180, amplitude), silence(150)]).tobytes()
                self.assertEqual(trim_trailing_artifacts(pcm), pcm)

    def test_fade_and_minimum_pause(self):
        pcm = np.full(int(RATE * 0.2), -32768, dtype='<i2').tobytes()
        out = np.frombuffer(trim_trailing_artifacts(pcm), dtype='<i2')
        end = len(pcm) // 2
        self.assertEqual(out[0], -32768)
        self.assertEqual(out[end - 1], 0)
        self.assertTrue(np.all(out[end:] == 0))
        self.assertEqual(len(out) - end, int(RATE * TRAILING_SILENCE_MS / 1000))

    def test_existing_pause_is_not_doubled(self):
        for pause_ms in (60, 120, 400):
            pcm = np.concatenate([tone(250), silence(pause_ms)]).tobytes()
            out = trim_trailing_artifacts(pcm)
            self.assertAlmostEqual(len(out) / 2 / RATE * 1000, 250 + max(pause_ms, TRAILING_SILENCE_MS), delta=1)

    def test_short_silent_and_empty_audio(self):
        self.assertEqual(trim_trailing_artifacts(b''), b'')
        self.assertEqual(trim_trailing_artifacts(silence(200).tobytes()), silence(200).tobytes())
        self.assertGreaterEqual(len(trim_trailing_artifacts(tone(2).tobytes())), len(tone(2).tobytes()))
        with self.assertRaises(ValueError):
            trim_trailing_artifacts(b'\x01')

    def test_holdback_is_bounded_and_chunk_independent(self):
        pcm = np.concatenate([tone(220), silence(120), tone(280, 400), silence(60)]).tobytes()
        for size in (1, 333, 8192, len(pcm)):
            hold = _TailHoldback(RATE)
            parts = []
            for i in range(0, len(pcm), size):
                parts.append(hold.push(pcm[i:i + size]))
                self.assertLessEqual(len(hold._buf), int(RATE * HOLD_TAIL_MS / 1000) * 2 + 1)
            self.assertTrue(any(parts))
            parts.append(hold.flush(RATE))
            self.assertEqual(b''.join(parts), trim_trailing_artifacts(pcm))
            self.assertEqual(hold.flush(RATE), b'')

    def test_sentence_punctuation(self):
        for raw, expected in (
            ('أبشر، أضفت الطلب. تبي شي بارد؟', 'أبشر، أضفت الطلب. تبي شي بارد؟'),
            ('أبشر...', 'أبشر...'),
            ('أبشر…', 'أبشر...'),
            ('أبشر... أضفت الطلب... تبي شي بارد؟...', 'أبشر... أضفت الطلب... تبي شي بارد؟...'),
            ('أبشر؛', 'أبشر.'),
            ('السعر 12.5 ريال', 'السعر 12.5 ريال.'),
        ):
            self.assertEqual(finalize_prosody(raw), expected)


class StreamingTest(unittest.IsolatedAsyncioTestCase):
    async def test_http_chunks_and_stretcher_flush_reach_livekit(self):
        pcm = np.concatenate([tone(500), silence(120), tone(180, 400)]).tobytes()
        requests = []

        async def speech(request):
            requests.append(await request.json())
            response = web.StreamResponse(headers={'Content-Type': 'audio/pcm'})
            await response.prepare(request)
            for i in range(0, len(pcm), 333):
                await response.write(pcm[i:i + 333])
                await asyncio.sleep(0)
            await response.write_eof()
            return response

        app = web.Application()
        app.router.add_post('/v1/audio/speech', speech)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, '127.0.0.1', 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        try:
            async with aiohttp.ClientSession() as session:
                for speed, gain_db, style in ((1.0, 0.0, None), (1.0, 4.0, None), (0.9, 4.0, None), (1.1, 4.0, None), (1.0, 0.0, "Warm, conversational, natural emphasis")):
                    with self.subTest(speed=speed, gain_db=gain_db):
                        text = 'أبشر... أضفت الطلب... تبي شي بارد؟...'
                        plugin = VoxCPMTTSPlugin(server_url=f'http://127.0.0.1:{port}', http_session=session, speed=speed, gain_db=gain_db, style=style, text_filter=clean_for_speech)
                        async with plugin.synthesize(text) as stream:
                            frames = [event async for event in stream]
                        self.assertTrue(frames[-1].is_final)
                        actual = b''.join(bytes(event.frame.data) for event in frames)
                        if speed == 1.0:
                            expected = trim_trailing_artifacts(pcm)
                        else:
                            stretcher = _WSOLAStretcher(sample_rate=RATE, speed=speed)
                            expected = trim_trailing_artifacts(stretcher.push(pcm) + stretcher.flush())
                        expected = apply_output_gain(expected, gain_db)
                        # LiveKit pads the last frame to its 20ms boundary.
                        self.assertEqual(actual[:len(expected)], expected)
                        self.assertLess(len(actual) - len(expected), int(RATE * 0.02) * 2)
                        self.assertFalse(any(actual[len(expected):]))
                        self.assertTrue(requests[-1]['stream'])
                        self.assertNotIn('seed', requests[-1])
                        # Inner «...» pauses reach the server; the final one is
                        # settled, because it made the server chop the last word.
                        self.assertEqual(requests[-1]['input'], (f'({style})' if style else '') + settle_final_ellipsis(clean_for_speech(text)))
                        self.assertEqual(requests[-1]['input'].count('...'), 2)
                        self.assertTrue(requests[-1]['input'].endswith('؟'))
        finally:
            await runner.cleanup()


if __name__ == '__main__':
    unittest.main()
