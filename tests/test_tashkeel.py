"""Every word the TTS hears is diacritized on every letter but the last.

The model is asked for that in ``TASHKEEL_GUIDE``; these tests hold the parts
code controls to the same rule: the lines code speaks word for word, the
example lines she copies from the prompt, the speech filter's lexicon, and
the spoken prices tools hand her.
"""

from __future__ import annotations

import re

import pytest

from src import flow_say
from src.catalog import sar_words
from src.prompts import agent_prompt
from src.spoken_lexicon import clean_for_speech
from src.tashkeel_audit import audit, missing_marks


@pytest.mark.parametrize("word,missing", [
    ("تَمَام", 0), ("دِجَاج", 0), ("دِجاج", 1),        # the letter before ا needs its fatha
    ("صُوص", 0), ("فِيه", 0), ("الْحِين", 0),          # long و / ي after their vowel
    ("الطَّلَب", 0), ("اليَوْم", 0), ("هَالشَّي", 0),   # the ل of «ال» and Saudi «هال»
    ("بِاثْنَيْن", 0), ("إيش", 2), ("ريال", 2),
])
def test_what_counts_as_a_missing_mark(word, missing):
    assert missing_marks(word)[1] == missing


def test_a_mark_on_the_last_letter_is_reported():
    assert audit("رِيَالْ").final_marked == ["رِيَالْ"]
    assert audit("رِيَال").final_marked == []


def _literals(module_source: str) -> list[str]:
    return [s for s in re.findall(r'"([^"\n]*[ء-ي][^"\n]*)"', module_source)]


def test_lines_code_speaks_are_fully_marked():
    import inspect

    for line in _literals(inspect.getsource(flow_say)):
        found = audit(line)
        assert not found.missing and not found.final_marked, (line, found.bare_words + found.partial_words, found.final_marked)


def _her_examples() -> list[str]:
    """Quoted lines in the prompts that carry tashkeel: her example speech."""
    text = agent_prompt.SALMA_SYSTEM_PROMPT + agent_prompt.checkout_instructions()
    # The guide shows the wrong forms («رِيَالْ»، «رِيَالُ») on purpose, and a
    # caller's words («اختاري أنتِ») are quoted as he says them.
    return [q for q in re.findall(r"«([^»]+)»", text)
            if audit(q).coverage > 0.5 and q not in {"رِيَالْ", "رِيَالُ"}]


def test_her_example_lines_are_fully_marked():
    examples = _her_examples()
    assert len(examples) > 20
    for q in examples:
        found = audit(q)
        assert not found.missing and not found.final_marked, (q, found.bare_words + found.partial_words)


def test_the_guide_is_in_both_prompts_and_asks_for_a_bare_last_letter():
    guide = agent_prompt.TASHKEEL_GUIDE.strip()
    assert guide in agent_prompt.SALMA_SYSTEM_PROMPT and guide in agent_prompt.checkout_instructions()
    assert "آخر حرف في الكلمة بدون أي حركة" in guide
    # The old rule asked for a sukun on every last letter, against the TTS rule.
    assert "سكون تام وإلزامي على آخر حرف" not in agent_prompt.SALMA_SYSTEM_PROMPT


@pytest.mark.parametrize("table", ["SPOKEN_PHRASES", "WORD_FIXES", "NUMBER_WORDS", "NUMBER_TYPOS", "ROMAN_STRAYS", "DIALECT_RESPELL"])
def test_the_speech_lexicon_is_fully_marked(table):
    """A half-marked entry overwrites the model's full marks (live: «وَعِشْرِين» → «وَعِشْرين»)."""
    import src.spoken_lexicon as lexicon

    for value in getattr(lexicon, table).values():
        if re.search("[ء-ي]", value):
            assert not audit(value).missing, (table, value)


@pytest.mark.parametrize("value", [1, 2, 11, 12, 16, 21, 49, 100, 112, 164, 200, 234, 999, 1250, 3500])
def test_a_price_copied_bare_from_a_tool_is_heard_fully_marked(value):
    heard = clean_for_speech(sar_words(value))
    found = audit(heard)
    assert not found.missing, (value, heard, found.bare_words + found.partial_words)


def test_water_is_not_read_as_a_hundred():
    assert "مِيَاه" in clean_for_speech("تبي مياه معدنية؟")
    assert "مِيَّهْ" in clean_for_speech("بمية ريال")
