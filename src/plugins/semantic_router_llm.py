"""
Drop-in replacement for a single `openai.LLM(...)` in AgentSession.

Routes each turn to one of two warm SGLang endpoints on the same Blackwell GPU:
  - LIGHT (granite-4.1-3b): casual/routine chit-chat, no tools, short utterances.
  - HEAVY (granite-4.1-8b): CRM/tool-calling turns and long/complex utterances.
    Runs with --speculative-algorithm ngram on the SGLang side for fast
    structured JSON output against your FastMCP tools.

Knowledge queries never reach this class at all — they're already caught by
`on_user_turn_completed` (semantic cache / SERA backend fast-path) before the
LLM step runs, so this router only ever sees chit-chat and CRM/tool turns.

NOTE: `livekit.agents.llm.LLM` / `ChatContext` internals shift between
agents versions. Verify `chat()`'s exact kwargs (`tools` vs `fnc_ctx`,
`ChatContext.items` vs `.messages`) against your installed version before
running — the routing logic itself is version-independent.
"""

from __future__ import annotations

from loguru import logger
from livekit.agents import llm
from livekit.plugins import openai as lk_openai

from src.plugins.crm_detector import is_crm_action


class SemanticRouterLLM(llm.LLM):
    def __init__(
        self,
        *,
        light_base_url: str,
        heavy_base_url: str,
        light_model: str = "ibm-granite/granite-4.1-3b",
        heavy_model: str = "ibm-granite/granite-4.1-8b",
        api_key: str = "no-key-needed",
        light_temperature: float = 0.3,
        heavy_temperature: float = 0.1,
        light_max_tokens: int = 256,
        heavy_max_tokens: int = 512,
        long_utterance_words: int = 40,
    ) -> None:
        super().__init__()

        self._light_name = light_model
        self._heavy_name = heavy_model
        self._long_utterance_words = long_utterance_words

        self._light = lk_openai.LLM(
            base_url=light_base_url,
            model=light_model,
            api_key=api_key,
            temperature=light_temperature,
            max_completion_tokens=light_max_tokens,
        )
        self._heavy = lk_openai.LLM(
            base_url=heavy_base_url,
            model=heavy_model,
            api_key=api_key,
            temperature=heavy_temperature,
            max_completion_tokens=heavy_max_tokens,
        )

    def _last_user_text(self, chat_ctx: llm.ChatContext) -> str:
        for item in reversed(chat_ctx.items):
            if getattr(item, "role", None) == "user":
                return getattr(item, "text_content", "") or ""
        return ""

    def _route(self, chat_ctx: llm.ChatContext, tools) -> str:
        message = self._last_user_text(chat_ctx)

        if tools:
            return "heavy"
        if is_crm_action(message):
            return "heavy"
        if len(message.split()) > self._long_utterance_words:
            return "heavy"
        return "light"

    def chat(
        self,
        *,
        chat_ctx: llm.ChatContext,
        tools: list | None = None,
        tool_choice=None,
        **kwargs,
    ) -> llm.LLMStream:
        route = self._route(chat_ctx, tools)
        target = self._heavy if route == "heavy" else self._light
        name = self._heavy_name if route == "heavy" else self._light_name
        logger.info(f"[SemanticRouter] -> {route.upper()} ({name})")

        return target.chat(
            chat_ctx=chat_ctx,
            tools=tools,
            tool_choice=tool_choice,
            **kwargs,
        )

    async def aclose(self) -> None:
        await self._light.aclose()
        await self._heavy.aclose()