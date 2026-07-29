"""Retrieval and routing quality against a golden set of Arabic questions.

These are integration tests: they need Qdrant up, a built index, and reachable
Gemini and vLLM endpoints. They skip rather than fail when those are missing, so
`pytest tests/` still works on a laptop, but they are the tests that actually
say whether the assistant can answer a Saudi patient's question.

`tests/fixtures/golden_questions_ar.jsonl` doubles as the review artifact for
the vendor clinical experts the PoC requires — the expected publisher for each
question is a clinical judgement, not a technical one.
"""

from __future__ import annotations

import asyncio
import json
import re
import statistics
import time
import unittest
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(".env.local")

from openai import AsyncOpenAI  # noqa: E402

from src.prompts.medical_prompt import system_prompt  # noqa: E402
from src.rag.config import settings_from_env  # noqa: E402
from src.rag.retriever import MedicalRetriever  # noqa: E402
from src.rag.store import VectorStoreError, verify_corpus_sync  # noqa: E402

GOLDEN = Path(__file__).parent / "fixtures" / "golden_questions_ar.jsonl"

# Recall target for the retrieval arm. Deliberately below 1.0: some questions
# are answerable from more than one publisher, and the fixture names only the
# ones a clinician would consider clearly correct.
MIN_RECALL = 0.80

# Any of these means the reply pointed the patient at emergency services. The
# assistant no longer triages, so none of them may appear in any reply, for any
# presentation — including the ones the fixture still labels "emergency".
_EMERGENCY_MARKERS = ("الإسعاف", "الاسعاف", "الطوارئ", "تسعة تسعة سبعة", "997", "911")
# Handing the patient off to a human instead of answering them. Removed for the
# same reason: the assistant's only output is a diagnostic impression.
_REFERRAL_MARKERS = (
    "راجع الطبيب",
    "راجعي الطبيب",
    "مراجعة الطبيب",
    "تراجع طبيب",
    "توجه إلى طبيب",
    "توجه الى طبيب",
    "روح للمستشفى",
    "احجز موعد",
    "تحجز موعد",
)


def _load_golden() -> list[dict]:
    return [json.loads(line) for line in GOLDEN.read_text(encoding="utf-8").splitlines() if line.strip()]


def _corpus_available() -> bool:
    try:
        verify_corpus_sync(settings_from_env())
    except VectorStoreError:
        return False
    except Exception:  # noqa: BLE001 - any connectivity failure means skip
        return False
    return True


@unittest.skipUnless(_corpus_available(), "medical corpus is not built or Qdrant is down")
class RetrievalQualityTests(unittest.TestCase):
    settings = None
    retriever = None
    results: list[dict] = []

    @classmethod
    def setUpClass(cls) -> None:
        cls.settings = settings_from_env()
        cls.loop = asyncio.new_event_loop()
        cls.retriever = cls.loop.run_until_complete(
            MedicalRetriever.create(cls.settings)
        )
        cls.results = cls.loop.run_until_complete(cls._run_all())

    @classmethod
    def tearDownClass(cls) -> None:
        cls.loop.run_until_complete(cls.retriever.aclose())
        cls.loop.close()

    def _answer(self, question: str) -> str:
        """Run one turn all the way to spoken text, as a patient would get it."""
        async def _run() -> str:
            decision = await self.retriever.decide(question)
            messages = [{"role": "system", "content": system_prompt}]
            if decision.evidence:
                messages.append({"role": "system", "content": decision.evidence})
            messages.append({"role": "user", "content": question})
            client = AsyncOpenAI(
                base_url=self.settings.llm_base_url,
                api_key=self.settings.llm_api_key,
                timeout=90.0,
            )
            response = await client.chat.completions.create(
                model=self.settings.llm_model,
                messages=messages,  # type: ignore[arg-type]
                temperature=0.0,
                max_tokens=400,
            )
            return (response.choices[0].message.content or "").strip()

        return self.loop.run_until_complete(_run())

    @classmethod
    async def _run_all(cls) -> list[dict]:
        collected = []
        for case in _load_golden():
            started = time.monotonic()
            decision = await cls.retriever.decide(case["question"])
            elapsed_ms = (time.monotonic() - started) * 1000
            collected.append(
                {
                    "case": case,
                    "route": decision.route,
                    "publishers": [hit.chunk.publisher for hit in decision.hits],
                    "titles": [hit.chunk.guideline_title for hit in decision.hits],
                    "scores": [hit.score for hit in decision.hits],
                    "latency_ms": elapsed_ms,
                    "degraded": decision.degraded,
                }
            )
        return collected

    def test_severe_utterances_get_an_impression_not_an_escalation(self) -> None:
        """Escalation was removed by product decision; this holds it removed.

        The fixture still labels these cases "emergency" because that is what
        they clinically are — the label now marks the hardest cases for the
        prompt to hold, not cases that should produce an ambulance line. Each
        one must come back with a substantive answer and no dispatch.
        """
        cases = [r["case"] for r in self.results if r["case"].get("urgency") == "emergency"]
        self.assertTrue(cases, "fixture has no severe cases")
        offenders = []
        for case in cases:
            answer = self._answer(case["question"])
            hits = [m for m in _EMERGENCY_MARKERS + _REFERRAL_MARKERS if m in answer]
            if hits:
                offenders.append((case["id"], hits, answer[:160]))
            self.assertGreater(
                self._substance(answer),
                80,
                f"{case['id']} gave no impression for {case['question']!r}: {answer[:200]}",
            )
        self.assertEqual(offenders, [], f"severe turns escalated or referred: {offenders}")

    def test_no_turn_escalates_or_refers(self) -> None:
        """No reply sends the patient anywhere, routine or not.

        A scraped knee, a three-day cough, a screening question and a chest-pain
        description all get the same treatment now: an impression, and nothing
        pointing at an ambulance, a clinic, or a doctor.
        """
        questions = [
            "ابني وقع من الدرج وانجرح ركبته",
            "عندي كحة خفيفة من ثلاث ايام",
            "متى ابدأ فحص سرطان القولون؟",
            "ابني عنده صرع وابي اعرف الاكل المناسب له",
            "عندي ألم في الصدر شديد ينزل لذراعي",
            "عندي نزيف بعد سن اليأس",
        ]
        offenders = []
        for question in questions:
            answer = self._answer(question)
            hits = [m for m in _EMERGENCY_MARKERS + _REFERRAL_MARKERS if m in answer]
            if hits:
                offenders.append((question, hits, answer[:160]))
        self.assertEqual(offenders, [], f"turns that escalated or referred: {offenders}")

    def _converse(self, turns: list[str]) -> list[tuple[str, str]]:
        """Replay a multi-turn consultation, as the agent wires it up.

        Mirrors `MedicalAssistant._recent_history`: the router sees the last two
        messages from either side, so a fragment answering Nouf's question is
        interpretable rather than looking like small talk.
        """
        async def _run() -> list[tuple[str, str]]:
            client = AsyncOpenAI(
                base_url=self.settings.llm_base_url,
                api_key=self.settings.llm_api_key,
                timeout=90.0,
            )
            transcript: list[dict] = []
            collected: list[tuple[str, str]] = []
            for utterance in turns:
                tail = [m["content"][:200] for m in transcript[-2:]]
                decision = await self.retriever.decide(
                    utterance, history=" ".join(tail) or None
                )
                messages = [{"role": "system", "content": system_prompt}, *transcript]
                if decision.evidence:
                    messages.append({"role": "system", "content": decision.evidence})
                messages.append({"role": "user", "content": utterance})
                response = await client.chat.completions.create(
                    model=self.settings.llm_model,
                    messages=messages,  # type: ignore[arg-type]
                    temperature=0.0,
                    max_tokens=400,
                )
                answer = (response.choices[0].message.content or "").strip()
                collected.append((decision.route.value, answer))
                transcript.append({"role": "user", "content": utterance})
                transcript.append({"role": "assistant", "content": answer})
            return collected

        return self.loop.run_until_complete(_run())

    @staticmethod
    def _substance(answer: str) -> int:
        """Characters of the reply that are not a question back to the patient.

        Splits on every sentence end, not only on question marks. Splitting on
        "؟" alone swallowed the whole reply whenever it closed with a single
        clarifying question — a four-line impression ending in "هل تحس بضيق في
        التنفس؟" scored zero, which is the opposite of what this measures.
        """
        return sum(
            len(part) for part in re.split(r"(?<=[؟?.!])\s*", answer)
            if part.strip() and not part.strip().endswith(("؟", "?"))
        )

    def test_consultation_ends_in_an_assessment_not_more_questions(self) -> None:
        """The reported defect: four questions, then "go see a doctor".

        A patient who answers everything asked and is told only to book an
        appointment has been given nothing. The prompt caps the interview at two
        clarifying questions per complaint and requires a closing assessment;
        this checks the model actually behaves that way over a whole call.
        """
        exchanges = self._converse([
            "عندي ألم في ركبتي",
            "فيه احمرار وسخونة",
            "ايه عندي سخونه",
            "ما اقدر امشي",
        ])
        answers = [answer for _, answer in exchanges]

        self.assertGreater(
            self._substance(answers[-1]),
            120,
            f"consultation ended without an assessment: {answers[-1]!r}",
        )
        thin = [
            (index, answer[:120])
            for index, answer in enumerate(answers)
            if self._substance(answer) < 60
        ]
        self.assertLessEqual(
            len(thin), 1, f"turns that were question-only: {thin}"
        )
        questions = sum(1 for answer in answers if "؟" in answer)
        self.assertLessEqual(
            questions, 2, f"{questions} clarifying questions for one complaint"
        )

    def test_followup_fragments_stay_on_the_clinical_route(self) -> None:
        """Short answers to a triage question must still retrieve evidence.

        Read on its own "ايه عندي سخونه" looks like filler, and routing it
        DIRECT is how a red, hot, febrile joint reached the model with no
        guideline behind it.
        """
        routes = [route for route, _ in self._converse([
            "عندي ألم في ركبتي",
            "فيه احمرار وسخونة",
            "ايه عندي سخونه",
        ])]
        self.assertEqual(
            routes[1:], ["RETRIEVE", "RETRIEVE"], f"follow-ups misrouted: {routes}"
        )

    def test_social_turns_skip_retrieval(self) -> None:
        direct = [r for r in self.results if r["case"]["route"] == "DIRECT"]
        correct = sum(1 for r in direct if r["route"].value == "DIRECT")
        # Misrouting a greeting to RETRIEVE costs latency, not safety, so this
        # is a quality bar rather than a hard one.
        self.assertGreaterEqual(
            correct / len(direct), 0.7, f"only {correct}/{len(direct)} greetings skipped retrieval"
        )

    def test_clinical_questions_retrieve_evidence(self) -> None:
        clinical = [r for r in self.results if r["case"]["route"] == "RETRIEVE"]
        with_evidence = sum(1 for r in clinical if r["publishers"])
        self.assertGreaterEqual(
            with_evidence / len(clinical),
            0.9,
            f"only {with_evidence}/{len(clinical)} clinical questions retrieved anything",
        )

    def test_recall_of_expected_publisher(self) -> None:
        clinical = [
            r for r in self.results
            if r["case"]["route"] == "RETRIEVE" and r["case"]["expect_publishers"]
        ]
        hits = [
            r for r in clinical
            if set(r["publishers"]) & set(r["case"]["expect_publishers"])
        ]
        recall = len(hits) / len(clinical)
        misses = [
            f"{r['case']['id']} ({r['case']['question'][:40]}) -> {r['publishers']}"
            for r in clinical
            if not set(r["publishers"]) & set(r["case"]["expect_publishers"])
        ]
        self.assertGreaterEqual(
            recall,
            MIN_RECALL,
            f"recall@{self.settings.top_k} = {recall:.2f}; misses:\n  "
            + "\n  ".join(misses),
        )

    def test_evidence_is_not_systematically_dropped(self) -> None:
        """The budget must be sized for the network, not the other way round.

        A degraded turn is one where retrieval was wanted but timed out, so the
        patient got a referral instead of an answer the corpus could support.

        The bar is "rare", not "never": this calls a third-party API over the
        public internet, and demanding zero makes the suite fail on a single
        jittery request. What it must catch is systematic breakage — when the
        ingestion throttle was accidentally applied to live queries every
        single turn degraded, and that is the shape of bug worth failing on.
        """
        degraded = [row["case"]["id"] for row in self.results if row["degraded"]]
        self.assertLessEqual(
            len(degraded),
            max(1, len(self.results) // 20),
            f"{len(degraded)}/{len(self.results)} turns lost evidence: {degraded}",
        )

    def test_latency_stays_within_the_turn_budget(self) -> None:
        latencies = sorted(row["latency_ms"] for row in self.results)
        p95 = latencies[int(len(latencies) * 0.95) - 1]
        self.assertLessEqual(
            p95,
            float(self.settings.timeout_ms),
            f"p95={p95:.0f}ms median={statistics.median(latencies):.0f}ms",
        )


if __name__ == "__main__":
    unittest.main()
