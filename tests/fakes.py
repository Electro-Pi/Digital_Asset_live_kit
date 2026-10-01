"""Test doubles: a fake browser, a tool context, and a scripted LLM.

``FakeUI`` subclasses the real ``UIBridge`` and replaces only its transport,
so the bridge's own code (including effect recording in the turn ledger) runs
unchanged. It simulates the Alawaly Real Estate frontend: navigation, project
display, contact-form filling, and UI-state queries.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Optional

from livekit.agents import llm
from livekit.agents.llm.llm import FunctionToolCall
from livekit.agents.types import DEFAULT_API_CONNECT_OPTIONS, NOT_GIVEN

from src.session_state import SessionState
from src.ui_bridge import UIBridge, UIResult
from src.voice_agent.base import Shop


class FakeUI(UIBridge):
    """Minimal Alawaly frontend stub.

    Handles ``alawaly.*`` RPC methods and the legacy ``luqma.*`` fallbacks so
    that ``UIBridge.navigate()``'s retry logic works in tests.
    """

    def __init__(self, *, fail: Iterable[str] = ()) -> None:
        super().__init__(room=None, identity=None)
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.path: str = "/"
        self.title: str = ""
        self.current_project_id: Optional[str] = None
        self.form: dict[str, Any] = {}
        self.fail = set(fail)

    @property
    def available(self) -> bool:
        return True

    def methods(self) -> list[str]:
        return [m for m, _ in self.calls]

    async def _invoke(self, method: str, payload: dict[str, Any]) -> UIResult:
        self.calls.append((method, payload))
        if method in self.fail:
            return UIResult(ok=False, reason="rpc_error_timeout")

        # ── Navigation ──────────────────────────────────────────────────────
        if method in ("alawaly.navigate", "luqma.navigate"):
            self.path = payload.get("path", self.path)
            self.title = payload.get("title", "")
            return UIResult(ok=True, data={"path": self.path})

        # ── Show project card ────────────────────────────────────────────────
        if method == "alawaly.showProject":
            self.current_project_id = payload.get("projectId")
            return UIResult(ok=True, data={"projectId": self.current_project_id})

        # ── Contact / interest form ──────────────────────────────────────────
        if method == "alawaly.fillContact":
            self.form.update(payload)
            return UIResult(ok=True, data={})

        # ── UI state query ───────────────────────────────────────────────────
        if method in ("alawaly.getUiState", "luqma.getUiState"):
            return UIResult(ok=True, data={
                "path": self.path,
                "title": self.title,
                "projectId": self.current_project_id,
            })

        # Unknown method — return success so tests don't break on new methods.
        return UIResult(ok=True, data={})


class FakeSession:
    def __init__(self) -> None:
        self.said: list[str] = []

    def say(self, text: str, **_: Any) -> None:
        self.said.append(text)


class FakeContext:
    """What a tool receives as ``context``: a session to speak through, and
    a record of whether the tool made itself uninterruptible."""

    def __init__(self, session: Optional[FakeSession] = None) -> None:
        self.session = session or FakeSession()
        self.uninterruptible = 0

    def disallow_interruptions(self) -> None:
        self.uninterruptible += 1


def make_shop(**ui_kwargs: Any) -> Shop:
    return Shop(ui=FakeUI(**ui_kwargs), state=SessionState(session_id="test-session"), hour=13)


# ─── Scripted LLM ────────────────────────────────────────────────────────────


@dataclass
class Step:
    """One LLM response: some text, some tool calls, or both."""

    text: str = ""
    calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)


Policy = Callable[[llm.ChatContext, list[Any]], Step]


class ScriptedLLM(llm.LLM):
    """An LLM whose every response comes from ``policy(chat_ctx, tools)``."""

    def __init__(self, policy: Policy) -> None:
        super().__init__()
        self.policy = policy
        self.requests: list[llm.ChatContext] = []

    @property
    def model(self) -> str:
        return "scripted"

    @property
    def provider(self) -> str:
        return "test"

    def chat(self, *, chat_ctx, tools=None, conn_options=DEFAULT_API_CONNECT_OPTIONS,
             parallel_tool_calls=NOT_GIVEN, tool_choice=NOT_GIVEN, extra_kwargs=NOT_GIVEN):
        self.requests.append(chat_ctx)
        return _ScriptedStream(self, chat_ctx=chat_ctx, tools=tools or [], conn_options=conn_options)


class _ScriptedStream(llm.LLMStream):
    async def _run(self) -> None:
        step = self._llm.policy(self._chat_ctx, self._tools)
        chunk_id = f"chunk-{uuid.uuid4().hex[:6]}"
        if step.text:
            # Two pieces, so streaming code sees a split reply.
            half = max(1, len(step.text) // 2)
            for piece in (step.text[:half], step.text[half:]):
                self._event_ch.send_nowait(llm.ChatChunk(id=chunk_id, delta=llm.ChoiceDelta(role="assistant", content=piece)))
        if step.calls:
            self._event_ch.send_nowait(
                llm.ChatChunk(
                    id=chunk_id,
                    delta=llm.ChoiceDelta(
                        role="assistant",
                        tool_calls=[
                            FunctionToolCall(name=name, arguments=json.dumps(args, ensure_ascii=False), call_id=f"call_{uuid.uuid4().hex[:8]}")
                            for name, args in step.calls
                        ],
                    ),
                )
            )


def last_tool_output(chat_ctx: llm.ChatContext, name: str) -> dict[str, Any]:
    """The newest output of tool ``name`` in a chat context, parsed."""
    from src.chat_view import parse_tool_output

    calls = {item.call_id: item.name for item in chat_ctx.items if item.type == "function_call"}
    for item in reversed(chat_ctx.items):
        if item.type == "function_call_output" and calls.get(item.call_id) == name:
            return parse_tool_output(item.output)
    return {}


def last_user(chat_ctx: llm.ChatContext) -> str:
    from src.chat_view import last_user_text

    return last_user_text(chat_ctx)


def tool_ran_this_turn(chat_ctx: llm.ChatContext, name: str) -> bool:
    from src.chat_view import tool_called_since_last_user

    return tool_called_since_last_user(chat_ctx, name)
