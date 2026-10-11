"""Final تاء مربوطة is the one final letter that carries a mark: ``هْ``.

Every other word ends bare — a vowel there is إعراب and reads as a news
broadcast. A sukun on the pausal haa says the opposite: stop, no vowel.

Run: .venv/bin/python tests/test_tashkeel_pausal_taa.py
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.spoken_lexicon import clean_for_speech, sanitize_tashkeel  # noqa: E402

SUKUN = "ْ"


class PausalTaaTests(unittest.TestCase):
    def test_final_taa_marbuta_becomes_haa_with_sukun(self):
        self.assertEqual(sanitize_tashkeel("لُقْمَة"), "لُقْمَهْ")

    def test_every_taa_marbuta_in_a_phrase_is_marked(self):
        self.assertEqual(
            sanitize_tashkeel("وَجْبَة عائِلِيَّة"), "وَجْبَهْ عائِلِيَّهْ"
        )

    def test_a_root_final_haa_stays_bare(self):
        """وَجْه and فِيه end in a real consonant, not a pausal ة."""
        for word in ("وَجْه", "فِيه", "عَلَيْه"):
            self.assertEqual(sanitize_tashkeel(word), word, word)

    def test_no_other_final_letter_keeps_a_vowel(self):
        self.assertEqual(sanitize_tashkeel("رِيَالُ"), "رِيَال")

    def test_tanween_is_removed_and_the_taa_still_marked(self):
        self.assertEqual(sanitize_tashkeel("مَعْذِرَةً"), "مَعْذِرَهْ")

    def test_running_twice_changes_nothing(self):
        for text in (
            "لُقْمَة",
            "وَجْبَة عائِلِيَّة",
            "هَلا واللَّه، مَعَك سَلْمَى مِن لُقْمَة.",
            "وَجْه",
        ):
            once = sanitize_tashkeel(text)
            self.assertEqual(sanitize_tashkeel(once), once, text)

    def test_interior_tashkeel_is_preserved(self):
        """The rule only touches the final letter."""
        out = sanitize_tashkeel("سَلَطَة")
        self.assertIn("سَ", out)
        self.assertIn("لَ", out)


class FullSpeechPassTests(unittest.TestCase):
    def test_sentence_keeps_every_word_intact(self):
        out = clean_for_speech("هَلا واللَّه، مَعَك سَلْمَى مِن لُقْمَة.")
        self.assertIn("لُقْمَهْ", out)
        self.assertIn("سَلْمَى", out)

    def test_words_are_not_mangled_by_the_pausal_pass(self):
        """clean_for_speech maps the helper over every word; it must be safe."""
        out = clean_for_speech("دِجَاج مَشْوِي مَع أَرُز")
        for fragment in ("دِجَاج", "مَشْوِي"):
            self.assertIn(fragment, out)
        self.assertNotIn("دِجاهْ", out)

    def test_no_bare_taa_marbuta_survives_to_the_tts(self):
        out = clean_for_speech("وَجْبَة عائِلِيَّة تِكَفِّي ثَلاثَة أَشْخاص.")
        self.assertNotIn("ة", out)
        self.assertIn("هْ", out)

    def test_sukun_actually_reaches_the_output(self):
        self.assertIn("ه" + SUKUN, clean_for_speech("سَلَطَة"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
