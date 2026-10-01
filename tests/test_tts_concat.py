import asyncio
import unittest

import aiohttp
from aiohttp import web
import numpy as np

from src.tts_concat import PCMConditioner, prosody_text
from src.speech_streaming import ConcatPhraseTokenizer
from src.plugins.voxcpm_tts_plugin import VoxCPMTTSPlugin, trim_trailing_artifacts


class ConcatTests(unittest.IsolatedAsyncioTestCase):
    async def test_phrase_boundaries_independent_of_llm_tokens(self):
        text = 'هلا، معك سلمى من لقمة. سعر الوجبة هو 12.50 ريال، تبي نضيف معها صوص؟'
        tokenizer = ConcatPhraseTokenizer()
        expected = tokenizer.tokenize(text)
        self.assertEqual(len(expected), 2)
        self.assertEqual(' '.join(expected), text)
        self.assertIn('12.50', expected[1])
        for size in (1, 3, len(text)):
            stream = tokenizer.stream()
            for offset in range(0, len(text), size):
                stream.push_text(text[offset:offset + size])
            stream.end_input()
            self.assertEqual([event.token async for event in stream], expected)

    async def test_sentence_streams_before_eof_and_preserves_short_tail(self):
        stream = ConcatPhraseTokenizer().stream()
        sentence = 'أدور لك على الوجبات، حسب ذوقك؛ وأشرح لك: السعر والمكونات. '
        stream.push_text(sentence)
        self.assertEqual((await asyncio.wait_for(anext(stream), 0.5)).token, sentence.strip())
        stream.push_text('وش تختار؟')
        stream.end_input()
        self.assertEqual([event.token async for event in stream], ['وش تختار؟'])

    def test_continuation_punctuation_keeps_one_generation(self):
        text = 'أدور لك على الوجبات، حسب ذوقك... وأشرح لك: السعر؛ والمكونات؟'
        self.assertEqual(ConcatPhraseTokenizer().tokenize(text), [text])

    async def test_pcm_stream_releases_audio_before_http_eof(self):
        release = asyncio.Event()
        received = []
        pcm = (np.sin(np.arange(48000) * 2 * np.pi * 200 / 48000) * 8000).astype('<i2').tobytes()

        async def speech(request):
            received.append(await request.json())
            response = web.StreamResponse(headers={'Content-Type': 'audio/pcm'})
            await response.prepare(request)
            await response.write(pcm[:70001])  # odd boundary is intentional
            await release.wait()
            await response.write(pcm[70001:])
            await response.write_eof()
            return response

        app = web.Application()
        app.router.add_post('/v1/audio/speech', speech)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, '127.0.0.1', 0)
        await site.start()
        try:
            async with aiohttp.ClientSession() as session:
                plugin = VoxCPMTTSPlugin(server_url=f'http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}', http_session=session, phrase_streaming=True)
                async with plugin.synthesize('هلا معك سلمى من لقمة،') as stream:
                    try:
                        first = await asyncio.wait_for(anext(stream), 2)
                        self.assertGreater(len(first.frame.data), 0)
                        self.assertFalse(release.is_set())
                    finally:
                        release.set()
                    rest = [item async for item in stream]
                    self.assertTrue(rest[-1].is_final)
                self.assertEqual(received[0]['response_format'], 'pcm')
                self.assertTrue(received[0]['stream'])
                self.assertTrue(received[0]['input'].endswith('،'))
        finally:
            release.set()
            await runner.cleanup()

    def test_conditioner_is_bounded_and_chunk_independent(self):
        pcm = (np.sin(np.arange(48000) * 0.03) * 10000).astype('<i2').tobytes()
        reference = PCMConditioner()
        expected = reference.push(pcm) + reference.flush()
        for size in (2, 334, 8192):
            processor = PCMConditioner()
            out = []
            for offset in range(0, len(pcm), size):
                out.append(processor.push(pcm[offset:offset + size]))
            out.append(processor.flush())
            self.assertEqual(b''.join(out), expected)
        self.assertEqual(len(expected), len(pcm))
        short = PCMConditioner()
        self.assertEqual(len(short.push(pcm[:1000])), 1000)
        self.assertEqual(short.flush(), b'')

    def test_quiet_opening_does_not_boost_later_words(self):
        # Both phrases have identical loud words after different openings.
        # The old 240ms gain estimate made those words up to 4x different.
        loud = np.tile(np.array([-32768, -12000, 0, 12000, 32767], dtype='<i2'), 3000).tobytes()
        for onset in (0, 100, 12000):
            opening = np.full(11520, onset, dtype='<i2').tobytes()
            conditioner = PCMConditioner()
            out = conditioner.push(opening + loud) + conditioner.flush()
            self.assertEqual(out[len(opening):], loud)
            self.assertEqual(out[960:len(opening)], opening[960:])
        with self.assertRaises(ValueError):
            PCMConditioner().push(b'\x01')

    def test_numbers_and_pause_top_up(self):
        self.assertEqual(prosody_text('السعر 12.50 ريال, مناسب?'), 'السعر 12.50 ريال، مناسب؟')
        self.assertIn('1,000', prosody_text('السعر 1,000 ريال'))
        self.assertTrue(prosody_text('تبيها؟...').endswith('؟'))
        pcm = np.concatenate([np.full(4800, 1000, dtype='<i2'), np.zeros(4800, dtype='<i2')]).tobytes()
        out = trim_trailing_artifacts(pcm, pause_ms=340)
        self.assertEqual(len(out) // 2, 4800 + 16320)
