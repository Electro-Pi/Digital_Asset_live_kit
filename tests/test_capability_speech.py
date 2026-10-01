import unittest

from src.spoken_lexicon import strip_speech_artifacts
from src.speech_claims import claims_stall


class CapabilitySpeechTests(unittest.TestCase):
    def test_capability_survives_both_filters(self):
        for text in (
            'أقدر أدور لك على الوجبات وأشرح لك الأسعار. تبي نبدا؟',
            'أَقْدَر أَشُوف لَك الخِيارات وأَساعِدَك تِعَدِّل طَلَبَك. آمُرْنِي وِش تِبِي؟',
            'أقدر أشيك لك على الخيارات.',
            'بإمكاني أشوف لك العروض.',
            'أَدَوِّر لَك أَحْسَن الوَجَبَات حَسَب مِزَاجَك ومِيزَانِيَّتَك وسُعْرَاتَك، وأَعْرِض لَك التَّفَاصِيل والأَسْعَار قَبْل تِخْتَار.',
            'فِي البِدَايَهْ، أَدَوِّر لَك عَلَى الوَجَبَات حَسَب ذُوقَك ومِيزَانِيَّتَك، سَوَاء تِبِي شَيء صِحِّي، أَوْ مَشَاوِي، أَوْ بَرْجَر.',
        ):
            with self.subTest(text=text):
                self.assertEqual(strip_speech_artifacts(text), text)
                self.assertFalse(claims_stall(text))

    def test_actual_stalls_remain_blocked(self):
        for text in (
            'لحظة أشوف لك العروض.',
            'أدور لك على الوجبات.',
            'أقدر أشوف لك الخيارات، لحظة أشوف لك العروض.',
            'أقدر أدور لك على الوجبات، ثوان وأرجع لك.',
            'لحظة أشوف لك الوجبات حسب ذوقك.',
            'أدور لك الوجبات حسب ذوقك، ثوان وأرجع لك.',
        ):
            with self.subTest(text=text):
                self.assertEqual(strip_speech_artifacts(text), '')
                self.assertTrue(claims_stall(text))

    def test_capability_does_not_exempt_following_stall_sentence(self):
        text = 'أقدر أدور لك على الوجبات. لحظة أشوف لك العروض. تبي نبدا؟'
        self.assertEqual(strip_speech_artifacts(text), 'أقدر أدور لك على الوجبات. تبي نبدا؟')
        self.assertTrue(claims_stall(text))
