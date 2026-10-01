"""Where the wait went between the customer finishing a sentence and سلمى starting.

The pieces were already in the log, one metric per line, interleaved with
per-second VAD noise and never added up. Worse, the arithmetic only worked for
a turn with a single LLM call: a turn that runs tools does a full LLM round
trip per step, and nothing counted those. The 2026-09-21 «سلطة» turn spent
7.4s between the transcript and the first TTS byte, and four of those were
round trips spent re-searching, which no log line named.

So this measures the shape of the turn, not just its components:

    stopped speaking
      │  end_of_utterance_delay   VAD silence wait + transcription (LiveKit)
      ├─ transcript final ────────── the clock below starts here
      │  llm[1]                    round trip that asked for a tool
      │  tools[1]                  the tools running
      │  llm[2] … tools[n]         one pair per extra step
      │  llm[final]                the round trip that produced speech
      │  tts_ttfb                  first byte of audio
      └─ she starts speaking

``llm`` and ``tools`` are measured here with a monotonic clock; the rest are
durations LiveKit reports. Everything is per turn, reset when a new final
transcript arrives, and summarised in two greppable lines ("Turn latency") when
playback starts.

Diagnostics only: nothing here changes what the agent does, and every entry
point swallows its own errors so a bookkeeping slip cannot end a call.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional

from loguru import logger


def _ms(seconds: float | None) -> str:
    """Seconds as a short fixed-width number, for a scannable column."""
    if seconds is None:
        return "-"
    return f"{seconds:.2f}"


@dataclass
class Step:
    """One LLM round trip, and the tools it asked for afterwards."""

    llm: float = 0.0
    ttft: Optional[float] = None
    tools: list[str] = field(default_factory=list)
    tool_seconds: float = 0.0
    spoke: bool = False


@dataclass
class Turn:
    started: float = 0.0
    chars: int = 0
    eou_delay: Optional[float] = None
    transcription_delay: Optional[float] = None
    stt_infer: Optional[float] = None
    tts_ttfb: Optional[float] = None
    tts_chars: int = 0
    steps: list[Step] = field(default_factory=list)
    llm_open: Optional[float] = None
    last_llm_end: Optional[float] = None
    reported: bool = False


class TurnLatency:
    """Per-turn latency budget, written from the agent's own event handlers."""

    def __init__(self) -> None:
        self._turn = Turn()

    # ─── Turn boundary ───────────────────────────────────────────────────────

    def begin(self, chars: int = 0) -> None:
        """A final transcript arrived: the reply clock starts now."""
        self._turn = Turn(started=time.monotonic(), chars=int(chars or 0))

    # ─── Marks the agent takes itself ────────────────────────────────────────

    def llm_begin(self) -> None:
        self._turn.llm_open = time.monotonic()

    def llm_end(self, *, tools: list[str] | None = None, spoke: bool = False) -> None:
        """Close the round trip, and bill the gap since the last one to tools.

        The gap between two LLM calls of one turn *is* the tool execution, so
        it needs no per-tool instrumentation to attribute.
        """
        turn = self._turn
        now = time.monotonic()
        opened = turn.llm_open
        if opened is None:
            return
        step = Step(llm=now - opened, tools=list(tools or []), spoke=bool(spoke))
        if turn.last_llm_end is not None and turn.steps:
            turn.steps[-1].tool_seconds = max(0.0, opened - turn.last_llm_end)
        turn.steps.append(step)
        turn.llm_open = None
        turn.last_llm_end = now

    def note_ttft(self, ttft: float | None) -> None:
        """Attach the LLM's own first-token time to the newest round trip."""
        if ttft is None or not self._turn.steps:
            return
        if self._turn.steps[-1].ttft is None:
            self._turn.steps[-1].ttft = float(ttft)

    # ─── Numbers LiveKit reports ─────────────────────────────────────────────

    def note_eou(self, *, delay: float | None, transcription: float | None) -> None:
        self._turn.eou_delay = delay
        self._turn.transcription_delay = transcription

    def note_stt(self, duration: float | None) -> None:
        self._turn.stt_infer = duration

    def note_tts(self, *, ttfb: float | None, chars: int = 0) -> None:
        """First TTS response of the turn is the one that gates her voice."""
        if self._turn.tts_ttfb is None:
            self._turn.tts_ttfb = ttfb
            self._turn.tts_chars = int(chars or 0)

    # ─── Reporting ───────────────────────────────────────────────────────────

    def summary(self) -> dict[str, float | int | None]:
        """The budget as numbers, for the log line and for the simulator."""
        turn = self._turn
        after_transcript = (time.monotonic() - turn.started) if turn.started else 0.0
        llm_total = sum(s.llm for s in turn.steps)
        tool_total = sum(s.tool_seconds for s in turn.steps)
        return {
            # What the phases below do not account for: TTS handoff, playout
            # scheduling, and our own text post-processing.
            "other": max(
                0.0, after_transcript - llm_total - tool_total - (turn.tts_ttfb or 0.0)
            ),
            "total": after_transcript + (turn.eou_delay or 0.0),
            "eou": turn.eou_delay,
            "transcription": turn.transcription_delay,
            "stt_infer": turn.stt_infer,
            "llm": llm_total,
            "tools": tool_total,
            "tts_ttfb": turn.tts_ttfb,
            "steps": len(turn.steps),
            "tool_steps": sum(1 for s in turn.steps if s.tools),
            "said_chars": turn.tts_chars,
            "heard_chars": turn.chars,
        }

    def report(self) -> None:
        """Log the budget. Called when playback starts; only fires once."""
        turn = self._turn
        if turn.reported or not turn.started:
            return
        turn.reported = True

        b = self.summary()
        llm_total = float(b["llm"] or 0.0)
        tool_total = float(b["tools"] or 0.0)
        overhead = float(b["other"] or 0.0)
        total = float(b["total"] or 0.0)
        tool_steps = int(b["tool_steps"] or 0)

        logger.info(
            "Turn latency total={}s | eou={} (transcription={} stt_infer={}) | "
            "llm={} steps={} tool_steps={} | tools={} | tts_ttfb={} | other={} | "
            "said_chars={} heard_chars={}",
            _ms(total),
            _ms(turn.eou_delay),
            _ms(turn.transcription_delay),
            _ms(turn.stt_infer),
            _ms(llm_total),
            b["steps"],
            tool_steps,
            _ms(tool_total),
            _ms(turn.tts_ttfb),
            _ms(overhead),
            b["said_chars"],
            b["heard_chars"],
        )
        if turn.steps:
            logger.info(
                "Turn latency steps: {}",
                " → ".join(
                    "llm {}{}{}".format(
                        _ms(s.llm),
                        f" (ttft {_ms(s.ttft)})" if s.ttft is not None else "",
                        f" + tools {_ms(s.tool_seconds)} {','.join(s.tools)}"
                        if s.tools
                        else (" + speech" if s.spoke else ""),
                    )
                    for s in turn.steps
                ),
            )
        slowest = self._slowest(llm_total, tool_total, overhead)
        if slowest:
            logger.info("Turn latency dominated by {}", slowest)

    def _slowest(self, llm_total: float, tool_total: float, overhead: float) -> str:
        """Name the biggest phase, so a regression is obvious without maths."""
        turn = self._turn
        parts = {
            "eou+asr (VAD silence and transcription)": turn.eou_delay or 0.0,
            "llm round trips": llm_total,
            "tool execution": tool_total,
            "tts first byte": turn.tts_ttfb or 0.0,
            "other (tts handoff, playout, post-processing)": overhead,
        }
        name, value = max(parts.items(), key=lambda kv: kv[1])
        if value <= 0:
            return ""
        extra = ""
        tool_steps = sum(1 for s in turn.steps if s.tools)
        if name == "llm round trips" and tool_steps:
            extra = (
                f"; {tool_steps} of {len(turn.steps)} round trips only asked for tools, "
                "so each one is pure added wait"
            )
        return f"{name} at {_ms(value)}s{extra}"
