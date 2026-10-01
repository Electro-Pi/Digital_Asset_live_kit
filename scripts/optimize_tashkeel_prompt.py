"""Optimise سلمى's tashkeel guide with GEPA against live conversations.

The TTS reads the short vowels it is given, so the words she writes must be
diacritized on every letter but the last, with the vowels a Saudi speaker
actually says. Only ``TASHKEEL_GUIDE`` in ``src/prompts/agent_prompt.py`` is
rewritten; the conversations are the sales optimiser's scenarios, run the
same way (real ``Assistant``, real tools, Coda-V2).

    set -a; source .env.local; set +a
    .venv-optim/bin/python scripts/optimize_tashkeel_prompt.py baseline --old-prompt <old agent_prompt.py>
    .venv-optim/bin/python scripts/optimize_tashkeel_prompt.py optimize --budget 150
    .venv-optim/bin/python scripts/optimize_tashkeel_prompt.py baseline --guide logs/tashkeel-optim/run-…/best_guide.md

How a reply is scored (per conversation, over all her replies):
- coverage: the share of letters that need a mark and have one
  (``src.tashkeel_audit``: the last letter, long vowels and the ل of «ال»
  are not counted);
- clean finals: no mark on a word's last letter;
- vowels: a Gemini judge lists words whose marks make the TTS say something
  a Saudi would not, with the right spelling; score 1 − wrong / words;
- behaviour: the sales scenario's hard checks, so a guide that makes her
  stop using tools or selling cannot win.
The judge's corrections are the feedback GEPA's reflection model reads.

Same venv and gateway rules as ``optimize_sales_prompt.py``: ``.venv-optim``,
``--parallel`` ≤ 8, a 503 is retried, not scored.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

import optimize_sales_prompt as sales  # noqa: E402  (imports livekit before dspy)
import gepa  # noqa: E402
from gepa.core.adapter import EvaluationBatch  # noqa: E402

from src.llm_backend import GEMINI_BASE_URL  # noqa: E402
from src.prompts import agent_prompt  # noqa: E402
from src.spoken_lexicon import clean_for_speech  # noqa: E402
from src.tashkeel_audit import _letters, audit  # noqa: E402

COMPONENT = "tashkeel_guide"
OUT_DIR = sales.ROOT / "logs" / "tashkeel-optim"

# Varied replies: questions, pitches with prices and names, cart and totals,
# apologies, small talk. Training and validation are disjoint.
TRAIN_IDS = [
    "hungry_open", "discover_then_recommend", "meal_deal_pitch", "group_dinner", "price_objection",
    "pair_after_add", "complaint", "is_it_good",
]
VAL_IDS = [
    "recommend_with_taste", "answers_all_at_once", "you_choose", "meal_deal_accept", "in_a_hurry",
    "think_about_it", "healthy_work_lunch", "two_noes_stop",
]


# The seed guide's reference words are settled house spellings. Taken from
# the seed, not the candidate, so a candidate cannot whitelist its own words.
HOUSE_FORMS = "، ".join(
    w.strip() for line in agent_prompt.TASHKEEL_GUIDE.split("كلمات تتكرر")[1].split("ردود كاملة")[0].splitlines()
    if line.startswith("- ") for w in line[2:].split("،") if w.strip()
)

JUDGE_PROMPT = """You check the Arabic diacritics (tashkeel) a Saudi voice agent wrote, because a TTS engine reads the marks literally.

The rule she follows: every letter of a word carries its mark EXCEPT the last letter, which is always bare. The target pronunciation is plain, everyday white Saudi speech (what any Saudi hears as normal), not Modern Standard Arabic.

For each reply below, list the words whose marks would make the TTS say the word wrongly:
- a wrong short vowel, sukun or shadda on an interior letter (e.g. «مَحَبُوب» for مَحْبُوب, «فَهَمْت» for فِهِمْت, «الرَّقَم» is fine but «أَدَلَّلَك» should be أَدَلِّلَك);
- MSA vowels where Saudis say it differently (e.g. «هَذِهِ» or «شَيْء» instead of «هَذِي», «شَي»);
- vowels that make an impossible or different word.
Accept any pronunciation real Saudi speakers use (Najdi, Hijazi or general), accept both «تَبِي» and «تِبِي», accept syncope such as «يِنَاسْبَك» or «يِنَاسِبَك» or «ثَمَانْيَة». These house forms are correct, never flag them: «دِجَاج» (dijāj), «أَبْشِر», «لَقِيت», «أَثَبِّت», «أَرَشِّح», «أَعْتَمِدْهَا», «أَحْفَظْهَا», «شَاوِرْمَا», number words written as the tool spells them («سِتَّة عَشَر», «أَحَد عَشَر»), and every word in this list: {house}. Do NOT flag: missing marks, a bare last letter, a mark on the last letter, Latin words, or the spelling of a dish or restaurant name. Only flag what a Saudi listener would hear as mispronounced.

Replies:
{replies}

Answer with JSON only: {{"wrong": [{{"reply": <reply number>, "word": "<the word as written>", "correct": "<the same word with the right marks, last letter bare>", "why": "<a few words in English>"}}]}}"""


class TashkeelJudge:
    def __init__(self, model: str) -> None:
        from openai import AsyncOpenAI

        self.client = AsyncOpenAI(base_url=GEMINI_BASE_URL, api_key=os.environ["GEMINI_API_KEY"])
        self.model = model

    async def wrong_words(self, replies: list[str]) -> Optional[list[dict[str, Any]]]:
        """The judge's list of mispronounced words, or None if it could not answer."""
        numbered = "\n".join(f"{i + 1}. {r}" for i, r in enumerate(replies) if r.strip())
        if not numbered:
            return []
        prompt = JUDGE_PROMPT.format(replies=numbered, house=HOUSE_FORMS)
        for attempt in range(3):
            try:
                resp = await self.client.chat.completions.create(
                    model=self.model,
                    messages=[{"role": "user", "content": prompt}],
                    reasoning_effort="low",
                    response_format={"type": "json_object"},
                )
                data = json.loads(resp.choices[0].message.content or "{}")
                return [w for w in data.get("wrong", []) if isinstance(w, dict) and _real_flag(w)]
            except Exception:  # noqa: BLE001
                await asyncio.sleep(3 * (attempt + 1))
        return None


def _real_flag(flag: dict[str, Any]) -> bool:
    """A judge flag about an interior vowel of the same word.

    Dropped: a correction that changes the letters (a different word, not a
    different reading) or only the last letter's mark (the code strips it).
    """
    written, correct = _letters(str(flag.get("word") or "")), _letters(str(flag.get("correct") or ""))
    if not written or [c for c, _ in written] != [c for c, _ in correct]:
        return False
    return written[:-1] != correct[:-1]


@dataclass
class Graded:
    scenario: str
    score: float
    coverage: float
    finals: float
    vowels: float
    checks: float
    words: int
    bare: list[str]
    partial: list[str]
    final_marked: list[str]
    wrong: list[dict[str, Any]]
    replies: list[str]
    heard: list[str]
    failed: list[str]
    transcript: str


async def grade(scenario: sales.Scenario, call: sales.Call, judge: TashkeelJudge) -> Graded:
    if call.error or not call.turns:
        return Graded(scenario.id, 0.0, 0, 0, 0, 0, 0, [], [], [], [], [], [], [f"conversation failed: {call.error}"],
                      sales.render(call))
    replies = [t["reply"] for t in call.turns]
    found = audit("\n".join(replies))
    wrong = await judge.wrong_words(replies)
    vowels = 0.5 if wrong is None else max(0.0, 1 - len(wrong) / max(1, found.words))
    hard = [c(call) for c in scenario.checks]
    checks = sum(ok for ok, _ in hard) / len(hard) if hard else 1.0
    score = 0.35 * found.coverage + 0.05 * found.clean_finals + 0.4 * vowels + 0.2 * checks
    return Graded(
        scenario.id, round(score, 4), round(found.coverage, 4), round(found.clean_finals, 4), round(vowels, 4),
        round(checks, 4), found.words, found.bare_words, found.partial_words, found.final_marked,
        wrong or [], replies, [clean_for_speech(r) for r in replies],
        [d for ok, d in hard if not ok], sales.render(call),
    )


async def evaluate_prompt(system_prompt: str, scenarios: list[sales.Scenario], *, backend, judge, parallel: int) -> list[Graded]:
    gate = asyncio.Semaphore(parallel)

    async def one(s: sales.Scenario) -> Graded:
        async with gate:
            call = await sales.converse_retrying(system_prompt, s.lines, backend)
        return await grade(s, call, judge)

    return list(await asyncio.gather(*(one(s) for s in scenarios)))


def feedback(g: Graded) -> str:
    parts = [f"Score {g.score:.2f} (coverage {g.coverage:.2f}, bare last letters {g.finals:.2f}, "
             f"vowels {g.vowels:.2f}, behaviour {g.checks:.2f}; {g.words} words)."]
    if g.bare:
        parts.append("Words with NO marks at all: " + "، ".join(dict.fromkeys(g.bare)) + ".")
    if g.partial:
        parts.append("Words missing some marks: " + "، ".join(dict.fromkeys(g.partial)) + ".")
    if g.final_marked:
        parts.append("Marks on the LAST letter (must be bare): " + "، ".join(dict.fromkeys(g.final_marked)) + ".")
    if g.wrong:
        parts.append("Wrong vowels (written → correct): " + "; ".join(
            f"{w.get('word')} → {w.get('correct')} ({w.get('why', '')})" for w in g.wrong) + ".")
    if g.failed:
        parts.append("Behaviour checks failed: " + "; ".join(g.failed) + ".")
    return " ".join(parts)


class TashkeelAdapter:
    """GEPA ↔ live conversations: a candidate is a tashkeel guide."""

    def __init__(self, *, backend, judge: TashkeelJudge, parallel: int, log_path: Path) -> None:
        self.backend = backend
        self.judge = judge
        self.parallel = parallel
        self.log_path = log_path
        self.propose_new_texts = None

    def evaluate(self, batch, candidate: dict[str, str], capture_traces: bool = False) -> EvaluationBatch:
        prompt = agent_prompt.build_system_prompt(tashkeel_guide=candidate[COMPONENT])
        started = time.time()
        graded = asyncio.run(evaluate_prompt(prompt, batch, backend=self.backend, judge=self.judge, parallel=self.parallel))
        with self.log_path.open("a") as log:
            log.write(json.dumps({
                "t": round(time.time() - started, 1),
                "guide_hash": hash(candidate[COMPONENT]) & 0xFFFFFFFF,
                "scores": {g.scenario: g.score for g in graded},
            }, ensure_ascii=False) + "\n")
        return EvaluationBatch(
            outputs=[g.transcript for g in graded],
            scores=[g.score for g in graded],
            trajectories=graded if capture_traces else None,
        )

    def make_reflective_dataset(self, candidate, eval_batch: EvaluationBatch, components_to_update):
        records = []
        for g in eval_batch.trajectories or []:
            records.append({
                "Inputs": {"scenario": g.scenario, "caller_lines": " | ".join(sales.BY_ID[g.scenario].lines)},
                "Generated Outputs": "\n".join(f"{i + 1}. {r}" for i, r in enumerate(g.replies)),
                "Feedback": feedback(g),
            })
        return {COMPONENT: records}


REFLECTION_TEMPLATE = """You are improving one section of the system prompt of «سلمى», a Saudi voice sales agent. Her replies go to a TTS engine that reads Arabic diacritics literally, so every word must carry tashkeel on every letter EXCEPT the last (which stays bare), with the vowels of plain everyday Saudi speech. The model writing her replies is a mid-size LLM; the rest of her prompt (tools, selling, dialect) is fixed and not shown. You may only rewrite the TASHKEEL section below.

Current TASHKEEL section:
```
<curr_param>
```

Her replies written with it, and what was wrong with their tashkeel:
```
<side_info>
```

Write an improved TASHKEEL section that fixes these failures while keeping what works.
Hard constraints:
- In Arabic, addressed to her as «أنتِ», starting with the heading «# التشكيل».
- Keep the core rule: marks on every letter except the last; the last letter bare (no sukun, vowel or tanween); a final ة written bare.
- Every Arabic example word or sentence you write must itself be fully and correctly diacritized by that rule, in plain Saudi pronunciation. A wrong example teaches a wrong word.
- Keep the reference word list, fix any entry the judge showed is wrong, and add the words she actually got wrong or left bare above (with correct marks).
- At most about 550 words. A shorter, sharper rule beats a longer one; this model stalls and skips tools when the prompt grows.
- Do not change what she says, only how it is diacritized: no new phrases, bans or selling rules.
- Only mention tool fields already in the current section (`name_say_ar`, `price_say_ar`).

Return only the new TASHKEEL section inside one ``` block."""


def summary(graded: list[Graded]) -> dict[str, Any]:
    n = len(graded) or 1
    words = sum(g.words for g in graded) or 1
    heard = audit("\n".join(h for g in graded for h in g.heard))
    return {
        "mean": round(sum(g.score for g in graded) / n, 4),
        "coverage": round(sum(g.coverage * g.words for g in graded) / words, 4),
        "bare_word_rate": round(sum(len(g.bare) for g in graded) / words, 4),
        "final_marked_rate": round(sum(len(g.final_marked) for g in graded) / words, 4),
        "wrong_vowel_rate": round(sum(len(g.wrong) for g in graded) / words, 4),
        "tts_coverage": round(heard.coverage, 4),
        "behaviour": round(sum(g.checks for g in graded) / n, 4),
        "per_scenario": {g.scenario: g.score for g in graded},
    }


def cmd_baseline(args) -> None:
    backend, judge = sales.student_backend(args.student), TashkeelJudge(args.judge_model)
    prompts: dict[str, str] = {}
    if args.old_prompt:
        prompts["old"] = sales.old_prompt_text(args.old_prompt)
    prompts["current"] = agent_prompt.SALMA_SYSTEM_PROMPT
    if args.guide:
        prompts["candidate"] = agent_prompt.build_system_prompt(tashkeel_guide=Path(args.guide).read_text())
    ids = args.only or (TRAIN_IDS + VAL_IDS)
    scenarios = [sales.BY_ID[i] for i in ids]
    report: dict[str, Any] = {}
    for name, prompt in prompts.items():
        flat = [g for _ in range(args.repeats)
                for g in asyncio.run(evaluate_prompt(prompt, scenarios, backend=backend, judge=judge, parallel=args.parallel))]
        report[name] = summary(flat)
        with (OUT_DIR / f"baseline-{name}.jsonl").open("w") as f:
            for g in flat:
                f.write(json.dumps(g.__dict__, ensure_ascii=False) + "\n")
        print(f"{name}: {json.dumps({k: v for k, v in report[name].items() if k != 'per_scenario'}, ensure_ascii=False)}", flush=True)
    (OUT_DIR / "baseline.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))


def cmd_optimize(args) -> None:
    backend, judge = sales.student_backend(args.student), TashkeelJudge(args.judge_model)
    train = [sales.BY_ID[i] for i in TRAIN_IDS]
    val = [sales.BY_ID[i] for i in VAL_IDS]
    run_dir = OUT_DIR / time.strftime("run-%Y%m%d-%H%M%S")
    run_dir.mkdir(parents=True, exist_ok=True)
    adapter = TashkeelAdapter(backend=backend, judge=judge, parallel=args.parallel, log_path=run_dir / "evals.jsonl")
    result = gepa.optimize(
        seed_candidate={COMPONENT: agent_prompt.TASHKEEL_GUIDE.strip()},
        trainset=train,
        valset=val,
        adapter=adapter,
        reflection_lm=sales.reflection_lm(args.reflection_model),
        reflection_prompt_template=REFLECTION_TEMPLATE,
        reflection_minibatch_size=args.minibatch,
        max_metric_calls=args.budget,
        run_dir=str(run_dir / "gepa"),
        raise_on_exception=False,
        skip_perfect_score=False,
        seed=args.seed,
        display_progress_bar=False,
    )
    best = result.best_candidate[COMPONENT]
    (run_dir / "best_guide.md").write_text(best + "\n")
    scores = list(result.val_aggregate_scores)
    report = {
        "student": backend.model,
        "seed_val_score": scores[0] if scores else None,
        "best_val_score": scores[result.best_idx] if scores else None,
        "candidates": len(result.candidates),
        "val_scores": scores,
    }
    (run_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    for i, cand in enumerate(result.candidates):
        (run_dir / f"candidate-{i}.md").write_text(cand[COMPONENT] + "\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"best guide: {run_dir / 'best_guide.md'}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--student", choices=["coda", "gemini"], default="coda")
    parser.add_argument("--judge-model", default=os.environ.get("GEMINI_MODEL", "gemini-3.8-flash"))
    parser.add_argument("--reflection-model", default=os.environ.get("GEMINI_MODEL", "gemini-3.8-flash"))
    parser.add_argument("--parallel", type=int, default=6)
    sub = parser.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("baseline", help="score the current prompt, an older one, and optionally a candidate guide")
    b.add_argument("--repeats", type=int, default=1)
    b.add_argument("--guide", help="a tashkeel guide file to score as 'candidate'")
    b.add_argument("--old-prompt", default="", help="an earlier agent_prompt.py to score as 'old'")
    b.add_argument("--only", nargs="*", help="scenario ids")
    o = sub.add_parser("optimize", help="run GEPA on the tashkeel guide")
    o.add_argument("--budget", type=int, default=150, help="max scenario rollouts")
    o.add_argument("--minibatch", type=int, default=4)
    o.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()
    if args.parallel > 8:
        parser.error("--parallel above 8 overloads the shared Coda gateway")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    {"baseline": cmd_baseline, "optimize": cmd_optimize}[args.cmd](args)


if __name__ == "__main__":
    main()
