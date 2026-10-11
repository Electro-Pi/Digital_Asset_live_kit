import asyncio
import unittest

import numpy as np

from src.plugins.tts_prosody import gap_after, smooth_pcm, speech_chunks
from src.plugins.voxcpm_tts_plugin import SERVER_SAMPLE_RATE, VoxCPMTTSPlugin


class ProsodyTests(unittest.TestCase):
    def test_arabic_boundaries_and_numbers(self):
        self.assertEqual(
            speech_chunks('أَهلاً، القيمة 3.5، الموعد 12:30. جاهز؟'),
            ['أَهلاً،', 'القيمة 3.5،', 'الموعد 12:30.', 'جاهز؟'],
        )
        self.assertEqual(speech_chunks('أهلا, كيف الحال? تمام...'), ['أهلا،', 'كيف الحال؟', 'تمام…'])
        self.assertEqual(speech_chunks('   '), [])
        self.assertEqual(gap_after('جاهز؟”'), 420)

    def test_long_phrase_preserves_words(self):
        words = ['مَرْحَبا'] * 80
        chunks = speech_chunks(' '.join(words))
        self.assertTrue(all(len(c) <= 181 for c in chunks))
        self.assertEqual(' '.join(c.rstrip('،.') for c in chunks).split(), words)

    def test_audio_trim_level_and_fade(self):
        rate = 48000
        tone = 0.3 * np.sin(2 * np.pi * 200 * np.arange(rate // 2) / rate)
        audio = np.concatenate([np.zeros(rate // 4), tone, np.zeros(rate // 4)])
        pcm = (audio * 32767).astype('<i2').tobytes()
        result = np.frombuffer(smooth_pcm(pcm, rate), dtype='<i2') / 32768
        self.assertAlmostEqual(len(result) / rate, 0.58, places=2)
        self.assertLessEqual(np.abs(result).max(), 0.97)
        self.assertAlmostEqual(np.sqrt(np.mean(result ** 2)), 10 ** (-23 / 20), places=3)
        self.assertEqual(result[0], 0)
        self.assertEqual(result[-1], 0)
        self.assertEqual(smooth_pcm(bytes(100), rate), b'')
        self.assertEqual(smooth_pcm(b'', rate), b'')

    def test_livekit_requests_and_pcm_pauses(self):
        async def run():
            rate = SERVER_SAMPLE_RATE
            tone = (4000 * np.sin(2 * np.pi * 200 * np.arange(rate // 5) / rate)).astype('<i2').tobytes()
            requests = []

            class Response:
                status = 200
                headers = {}

                @property
                def content(self):
                    return self

                async def iter_chunked(self, size):
                    # Deliberately split a PCM sample across network packets.
                    yield tone[:101]
                    yield tone[101:]

                async def __aenter__(self):
                    return self

                async def __aexit__(self, *args):
                    pass

            class Session:
                def post(self, url, **kwargs):
                    requests.append(kwargs['json'])
                    return Response()

            plugin = VoxCPMTTSPlugin(http_session=Session(), seed=1234)
            async with plugin.synthesize('مرحبا، كيف الحال؟') as stream:
                frames = [event.frame async for event in stream]
            self.assertEqual([r['input'] for r in requests], ['مرحبا،', 'كيف الحال؟'])
            self.assertTrue(all(r['voice'] == 'nouf' and r['seed'] == 1234 for r in requests))
            actual = b''.join(bytes(frame.data) for frame in frames)
            phrase = smooth_pcm(tone, rate)
            expected = phrase + bytes(rate * 180 // 1000 * 2) + phrase + bytes(rate * 420 // 1000 * 2)
            # LiveKit pads the final audio frame to its frame size.
            self.assertEqual(actual[:len(expected)], expected)
            self.assertFalse(any(actual[len(expected):]))
            await plugin.aclose()

        asyncio.run(run())


if __name__ == '__main__':
    unittest.main()
