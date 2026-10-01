"""Measure tashkeel quality in سلمى's replies against Arabic phonotactics.

Only unambiguous violations are counted, so the number means something:

* **initial sukun** — an Arabic word cannot begin with a vowelless consonant.
  "سْتْرِبْس" is unpronounceable as written, and the TTS makes a mess of it.
* **double sukun** — two vowelless consonants in a row mid-word is equally
  impossible; it is where "مِقْرْمِش" goes wrong.
* **final mark** — إعراب on the last letter, the formal-register giveaway the
  prompt has always banned.
* **bare word** — a fully undiacritized word, i.e. the rule was skipped.

Coverage of interior letters is reported but not judged: ا/و/ي are long vowels
and legitimately carry nothing, so "missing" marks there are not errors.

Run:  .venv/bin/python tests/tashkeel_audit.py
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

TASHKEEL = set(range(0x064B, 0x0653))
SUKUN = 0x0652
LONG_VOWELS = set("اويىآأإ")
WORD_RE = re.compile(r"[ؠ-ْ]+")


def _is_letter(ch: str) -> bool:
    return 0x0620 <= ord(ch) <= 0x064A


def audit_word(word: str) -> list[str]:
    """Unambiguous tashkeel violations in one word."""
    problems: list[str] = []
    letters = [(i, c) for i, c in enumerate(word) if _is_letter(c)]
    if not letters:
        return problems

    marks: dict[int, set[int]] = {}
    for pos, (idx, _) in enumerate(letters):
        following = set()
        j = idx + 1
        while j < len(word) and ord(word[j]) in TASHKEEL:
            following.add(ord(word[j]))
            j += 1
        marks[pos] = following

    if len(letters) > 1 and not any(marks[p] for p in marks):
        problems.append("bare word")
        return problems

    if SUKUN in marks[0]:
        problems.append("initial sukun")
    for pos in range(len(letters) - 1):
        if SUKUN in marks[pos] and SUKUN in marks[pos + 1]:
            problems.append("double sukun")
            break
    if marks[len(letters) - 1]:
        problems.append("final mark")
    return problems


def audit_text(text: str) -> tuple[int, Counter, list[tuple[str, str]]]:
    words = WORD_RE.findall(text)
    counts: Counter = Counter()
    offenders: list[tuple[str, str]] = []
    for word in words:
        for problem in audit_word(word):
            counts[problem] += 1
            offenders.append((word, problem))
    return len(words), counts, offenders


TURNS = [
    ["ابغى برجر دجاج مقرمش"],
    ["عندكم ستربس؟", "طيب ابغى شي مقلي ومقرمش"],
    ["ابغى بيتزا وكولا", "وش عندكم حلا؟"],
    ["ابغى مشاوي ومندي وكبسة", "وش الفرق بينهم؟"],
    ["ابغى شي صحي فيه سلطة وكينوا"],
    ["ابغى سوشي وميلك شيك"],
]


async def main() -> None:
    import tests.test_luqma_flow as h

    total_words = 0
    totals: Counter = Counter()
    totals_after: Counter = Counter()
    all_offenders: list[tuple[str, str]] = []
    replies: list[str] = []

    for turns in TURNS:
        ui = h.FakeUI()
        assistant = h.Assistant(instructions="placeholder", ui=ui)  # type: ignore[arg-type]
        tools, dispatch = h.openai_tools(assistant)
        from src import catalog
        from src.prompts.agent_prompt import compose_instructions

        messages = [
            {"role": "system", "content": compose_instructions(
                catalog_index=catalog.catalog_index())}
        ]
        import httpx

        async with httpx.AsyncClient() as client:
            for turn in turns:
                messages.append({"role": "user", "content": turn})
                for _ in range(5):
                    msg = await h.chat(client, messages, tools)
                    messages.append(msg)
                    calls = msg.get("tool_calls") or []
                    if not calls:
                        break
                    for call in calls:
                        fn = call["function"]["name"]
                        args = json.loads(call["function"]["arguments"] or "{}")
                        try:
                            out = await dispatch[fn](None, **args)
                        except Exception as e:
                            out = {"error": str(e)}
                        messages.append({
                            "role": "tool", "tool_call_id": call["id"],
                            "content": json.dumps(out, ensure_ascii=False),
                        })
                reply = (msg.get("content") or "").strip()
                replies.append(reply)
                from src.catalog import clean_for_speech
                spoken = clean_for_speech(reply)
                words, counts, offenders = audit_text(reply)
                _, counts_after, offenders_after = audit_text(spoken)
                total_words += words
                totals.update(counts)
                totals_after.update(counts_after)
                all_offenders += offenders
                print(f"  {reply}")
                if offenders:
                    print(f"    raw ⚠ {offenders}")
                if offenders_after:
                    print(f"    SPOKEN ⚠ {offenders_after}")

    print("\n" + "=" * 70)
    print(f"words audited        : {total_words}")
    for problem in ("initial sukun", "double sukun", "final mark", "bare word"):
        n = totals[problem]
        print(f"{problem:<21}: {n:>3}  ({n / max(total_words, 1):.1%})")
    bad = sum(totals.values())
    print(f"{'TOTAL raw (model)':<21}: {bad:>3}  ({bad / max(total_words, 1):.1%})")
    bad_after = sum(totals_after.values())
    print(f"{'TOTAL after filter':<21}: {bad_after:>3}  ({bad_after / max(total_words, 1):.1%})")
    worst = Counter(w for w, _ in all_offenders).most_common(8)
    print(f"most frequent offenders: {worst}")


if __name__ == "__main__":
    asyncio.run(main())
