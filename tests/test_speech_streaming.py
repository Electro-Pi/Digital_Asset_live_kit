"""Phrase buffering and the guarded reply stream (src/speech_streaming.py).

The LLM node's use of them is tested in tests/test_llm_node.py.
"""
import asyncio
import unittest

from src.speech_streaming import GuardedReplyStream, PhraseBuffer, SpeechPhraseTokenizer, WholeReplyTokenizer


class PhraseTests(unittest.IsolatedAsyncioTestCase):
    async def test_first_phrase_without_eof_or_second_sentence(self):
        stream = SpeechPhraseTokenizer(1, continuation_min_length=1).stream()
        stream.push_text('هلا... ')
        self.assertEqual((await asyncio.wait_for(anext(stream), 0.5)).token, 'هلا...')
        stream.push_text('وش تبي؟... ')
        self.assertEqual((await asyncio.wait_for(anext(stream), 0.5)).token, 'وش تبي؟...')
        stream.push_text('باقي الكلام')
        stream.end_input()
        self.assertEqual([x.token async for x in stream], ['باقي الكلام'])
        await stream.aclose()

    async def test_fast_start_then_grouped_continuation_without_losing_words(self):
        first = 'أهلا وسهلا... معك سلمى من لقمة... '
        rest = 'إذا كنت جوعان ومستعجل... عندنا وجبات خفيفة ولذيذة... وش رأيك؟...'
        stream = SpeechPhraseTokenizer().stream()
        stream.push_text(first)
        self.assertEqual((await asyncio.wait_for(anext(stream), 0.5)).token, first.strip())
        pending = asyncio.create_task(anext(stream))
        stream.push_text(rest)
        await asyncio.sleep(0)
        self.assertFalse(pending.done())
        stream.end_input()
        self.assertEqual((await pending).token, rest)
        self.assertEqual([x async for x in stream], [])

    async def test_continuation_batches_independent_of_llm_chunk_size(self):
        text = ('هذه جملة كاملة ذات معنى للاختبار... ' * 16).strip()
        tokenizer = SpeechPhraseTokenizer()
        expected = tokenizer.tokenize(text)
        self.assertLessEqual(len(expected), 5)
        self.assertEqual(' '.join(expected), text)
        for size in (1, 7, len(text)):
            stream = tokenizer.stream()
            for offset in range(0, len(text), size):
                stream.push_text(text[offset:offset+size])
            stream.end_input()
            self.assertEqual([x.token async for x in stream], expected)

    async def test_token_boundaries_preserve_decimals_and_ellipses(self):
        text = 'السعر 12.50 ريال... تبيها؟... زين.'
        expected = ['السعر 12.50 ريال...', 'تبيها؟...', 'زين.']
        for size in (1, 2, 5, len(text)):
            buf = PhraseBuffer(1)
            actual = []
            for i in range(0, len(text), size):
                actual.extend(buf.push(text[i:i + size]))
            actual.extend(buf.flush())
            self.assertEqual(actual, expected)

    async def test_whole_reply_is_one_tts_request(self):
        """Each request is its own generation; a split was heard as a cut."""
        text = 'أَهْلًا بِك... أَنَا تَمَام، كُلِّي حَمَاس أَسَاعِدَك. وِش نَاوِي تِطْلُب اللَّيْلَهْ؟...'
        tokenizer = WholeReplyTokenizer()
        self.assertEqual(tokenizer.tokenize(text), [text])
        for size in (1, 7, len(text)):
            stream = tokenizer.stream()
            for i in range(0, len(text), size):
                stream.push_text(text[i:i + size])
            stream.end_input()
            self.assertEqual([x.token async for x in stream], [text])

    async def test_whole_reply_releases_nothing_before_the_reply_ends(self):
        stream = WholeReplyTokenizer().stream()
        stream.push_text('هَلا واللَّه، وَقْت العَشا زِين. ')
        pending = asyncio.create_task(anext(stream))
        await asyncio.sleep(0.05)
        self.assertFalse(pending.done())
        stream.push_text('وِش تِشْتَهي؟...')
        stream.end_input()
        self.assertEqual((await pending).token, 'هَلا واللَّه، وَقْت العَشا زِين. وِش تِشْتَهي؟...')
        self.assertEqual([x async for x in stream], [])

    async def test_empty_reply_sends_nothing(self):
        stream = WholeReplyTokenizer().stream()
        stream.push_text('   ')
        stream.end_input()
        self.assertEqual([x async for x in stream], [])

    async def test_split_leaked_tokens_are_deferred(self):
        for chunks in (
            ['ca', 'll:show_meal{reason: هلا... ', 'meal_id:x}'],
            ['<|ana', 'lysis|> سر... '],
            ['{"say_to_user_ar": "هلا... ', '"}'],
        ):
            gate = GuardedReplyStream(lambda _: True, 1)
            self.assertEqual([p for c in chunks for p in gate.push(c)], [])
            self.assertTrue(gate.deferred)
