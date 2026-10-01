"""The latency budget has to add up, and has to name the tool loop.

Replays the shape of the 2026-09-21 «سلطة» turn: four LLM round trips that only
asked for tools, then a fifth that produced speech. The old logging showed one
"Time since last EOU" total and five unattributed llm_metrics lines, so the
four wasted round trips were invisible.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.turn_latency import TurnLatency  # noqa: E402


class _Captured:
    """Collects the lines TurnLatency logs, without a real sink."""

    def __init__(self) -> None:
        self.lines: list[str] = []

    def info(self, template, *args):
        rendered = template
        for a in args:
            rendered = rendered.replace("{}", str(a), 1)
        self.lines.append(rendered)

    # TurnLatency only ever logs at info/warning.
    warning = info

    def text(self) -> str:
        return "\n".join(self.lines)


class BudgetTests(unittest.TestCase):
    def setUp(self):
        self.log = _Captured()
        import src.turn_latency as mod

        self._real = mod.logger
        mod.logger = self.log

    def tearDown(self):
        import src.turn_latency as mod

        mod.logger = self._real

    def _tool_loop_turn(self) -> TurnLatency:
        """Four tool-only round trips, then one that speaks."""
        lat = TurnLatency()
        lat.begin(chars=25)
        lat.note_eou(delay=1.66, transcription=1.65)
        lat.note_stt(1.20)
        for tool in ("search_meals", "search_meals", "search_meals", "show_meal"):
            lat.llm_begin()
            lat.llm_end(tools=[tool], spoke=False)
        lat.llm_begin()
        lat.llm_end(tools=[], spoke=True)
        lat.note_tts(ttfb=0.56, chars=97)
        return lat

    def test_every_phase_is_named(self):
        self._tool_loop_turn().report()
        line = self.log.text()
        for phase in ("total=", "eou=", "stt_infer=", "llm=", "tools=", "tts_ttfb=", "other="):
            self.assertIn(phase, line, f"{phase} missing from the budget")

    def test_the_wasted_round_trips_are_counted(self):
        self._tool_loop_turn().report()
        line = self.log.text()
        self.assertIn("steps=5", line, "five LLM round trips ran")
        self.assertIn("tool_steps=4", line, "four of them only asked for tools")

    def test_the_step_detail_names_each_tool_in_order(self):
        self._tool_loop_turn().report()
        steps = [l for l in self.log.lines if l.startswith("Turn latency steps")]
        self.assertTrue(steps, "a step breakdown is logged")
        detail = steps[0]
        self.assertEqual(detail.count("search_meals"), 3)
        self.assertIn("show_meal", detail)
        self.assertIn("speech", detail, "the final round trip is marked as the speaking one")

    def test_the_total_includes_the_wait_before_the_transcript(self):
        lat = TurnLatency()
        lat.begin(chars=10)
        lat.note_eou(delay=1.66, transcription=1.65)
        lat.report()
        total = float(self.log.text().split("total=")[1].split("s")[0])
        self.assertGreaterEqual(total, 1.66, "eou wait belongs in the total")

    def test_the_dominant_phase_is_called_out(self):
        self._tool_loop_turn().report()
        self.assertIn("dominated by", self.log.text())

    def test_tool_time_is_the_gap_between_round_trips(self):
        """No per-tool timer needed: the gap between calls *is* the tool time."""
        lat = TurnLatency()
        lat.begin()
        lat.llm_begin()
        lat.llm_end(tools=["search_meals"])
        # Tools run here, between the two round trips.
        busy = 0.0
        import time

        end = time.monotonic() + 0.05
        while time.monotonic() < end:
            busy += 1
        lat.llm_begin()
        lat.llm_end(tools=[], spoke=True)
        lat.report()
        tools = float(self.log.text().split("| tools=")[1].split(" ")[0])
        self.assertGreaterEqual(tools, 0.04, "the gap was billed to tools")

    def test_a_report_fires_once(self):
        lat = self._tool_loop_turn()
        lat.report()
        first = len(self.log.lines)
        lat.report()
        self.assertEqual(len(self.log.lines), first, "a second report is ignored")

    def test_a_turn_that_never_started_reports_nothing(self):
        TurnLatency().report()
        self.assertEqual(self.log.lines, [])

    def test_a_new_transcript_resets_the_budget(self):
        lat = self._tool_loop_turn()
        lat.begin(chars=5)
        lat.note_eou(delay=0.4, transcription=0.4)
        lat.llm_begin()
        lat.llm_end(tools=[], spoke=True)
        lat.report()
        self.assertIn("steps=1", self.log.text(), "the previous turn's steps are gone")

    def test_an_unopened_round_trip_is_ignored(self):
        """llm_end without llm_begin must not invent a step or crash."""
        lat = TurnLatency()
        lat.begin()
        lat.llm_end(tools=["x"])
        lat.report()
        self.assertIn("steps=0", self.log.text())

    def test_a_failed_round_trip_does_not_poison_the_next(self):
        lat = TurnLatency()
        lat.begin()
        lat.llm_begin()
        lat.llm_end()  # the except path in llm_node
        lat.llm_begin()
        lat.llm_end(tools=[], spoke=True)
        lat.report()
        self.assertIn("steps=2", self.log.text())

    def test_missing_metrics_render_as_dashes_not_crashes(self):
        lat = TurnLatency()
        lat.begin()
        lat.llm_begin()
        lat.llm_end(tools=[], spoke=True)
        lat.report()
        self.assertIn("eou=-", self.log.text())

    def test_the_first_tts_response_is_the_one_that_gates_her_voice(self):
        lat = TurnLatency()
        lat.begin()
        lat.note_tts(ttfb=0.56, chars=97)
        lat.note_tts(ttfb=9.99, chars=40)
        lat.report()
        self.assertIn("tts_ttfb=0.56", self.log.text())


if __name__ == "__main__":
    unittest.main(verbosity=2)
