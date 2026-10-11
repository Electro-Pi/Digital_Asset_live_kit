"""Interior vowels and register words the TTS would otherwise read wrong.

sanitize_tashkeel only strips marks off the LAST letter of a word, so a wrong
vowel in the middle survives every pass and reaches the voice. Counted over 198
live assistant lines on 2026-09-16: شَيء 28, جِدّا 6, كِل 5, مَن 3, كَمَان 3,
كِلها 2, كِدَه 2, هَذِه 1, أُعَطِيك 1.

Run: .venv/bin/python tests/test_najdi_register.py
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.spoken_lexicon import clean_for_speech  # noqa: E402


class InteriorVowelTests(unittest.TestCase):
    def test_min_keeps_its_kasra(self):
        """«مَن كْرِيسْبِي» was read /man/ — a different word from /min/."""
        self.assertIn("مِن", clean_for_speech("بَرْجَر مَن كْرِيسْبِي"))

    def test_already_correct_min_is_left_alone(self):
        self.assertIn("مِن", clean_for_speech("مِن مَطْعَم سَاكُورَا"))

    def test_kul_keeps_its_damma(self):
        self.assertIn("كُل", clean_for_speech("يِجِي فِيه كِل الأنْواع"))

    def test_kullaha_gets_its_shadda(self):
        """Without the shadda the voice says /kilha/."""
        self.assertIn("كِلَّهَا", clean_for_speech("السُّعْرَات كِلها سِت مِئَة"))

    def test_atik_is_respelled(self):
        self.assertIn("أَعْطِيك", clean_for_speech("مَا رَاح أُعَطِيك إِلّا الزِّين"))


class RegisterTests(unittest.TestCase):
    def test_msa_shay_becomes_najdi(self):
        out = clean_for_speech("عِنْدِي لَك شَيء زِين")
        self.assertIn("شَي", out)
        self.assertNotIn("شَيء", out)

    def test_msa_hadhihi_becomes_najdi(self):
        self.assertIn("هَذِي", clean_for_speech("هَذِه هِيَ السَّلَّة"))

    def test_egyptian_kida_becomes_najdi(self):
        out = clean_for_speech("كِدَه تَمَام؟")
        self.assertIn("كِذَا", out)
        self.assertNotIn("كِدَه", out)

    def test_egyptian_kaman_becomes_najdi(self):
        out = clean_for_speech("كَمَان تَقْيِيمه عَالِي")
        self.assertIn("بَعَد", out)
        self.assertNotIn("كَمَان", out)


class PromptTests(unittest.TestCase):
    """The opener and collocation rules the register depends on."""

    def setUp(self):
        from src.prompts.agent_prompt import SALMA_SYSTEM_PROMPT

        self.prompt = SALMA_SYSTEM_PROMPT

    def test_prompt_forbids_one_opener_for_every_reply(self):
        """«أبشر» opened 53 of 198 live lines."""
        self.assertIn("ممنوع تبدئين كل رد بنفس الكلمة", self.prompt)

    def test_prompt_offers_alternative_openers(self):
        # The openers are written with tashkeel (she copies example phrases).
        bare = re.sub("[\u064b-\u0652]", "", self.prompt)
        for opener in ("لقيت لك", "شوف هذي", "معك حق"):
            self.assertIn(opener, bare)

    def test_prompt_bans_the_observed_nonsense_collocations(self):
        for phrase in ("اقتراح يجمل", "رايع للجوع", "تسرق الانتباه"):
            self.assertIn(phrase, self.prompt)

    def test_prompt_forbids_calling_herself_a_bot(self):
        self.assertIn("ممنوع تعرّفين نفسك كمساعدة آلية", self.prompt)


if __name__ == "__main__":
    unittest.main(verbosity=2)
