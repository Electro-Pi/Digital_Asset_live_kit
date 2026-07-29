"""Routes each patient utterance before any retrieval work happens.

Most turns in a real consultation are not clinical questions — greetings,
"na'am", "shukran", "can you repeat that". Embedding and searching on those
wastes a Gemini call and ~200ms of the patient's time for evidence the model
will not use. This classifier reuses the same gemma vLLM endpoint the agent
already talks to, so it adds no new infrastructure.
"""

from __future__ import annotations

import asyncio
from enum import Enum

from loguru import logger
from openai import AsyncOpenAI

from src.rag.config import RAGSettings


class QueryRoute(str, Enum):
    #: A clinical question that must be grounded in guideline evidence. This
    #: includes severe-sounding presentations: they get a diagnostic answer
    #: like everything else, and it still has to be evidence-backed.
    RETRIEVE = "RETRIEVE"
    #: Social, meta or conversational filler — no retrieval needed.
    DIRECT = "DIRECT"


_SYSTEM_PROMPT = """أنت مصنّف نصوص في نظام مساعد طبي صوتي. مهمتك تصنيف آخر رسالة من المريض إلى تصنيف واحد فقط.

التصنيفات:
RETRIEVE — أي كلام له علاقة بالصحة: أعراض، ألم، إصابة، مرض مزمن، دواء، فحص أو تطعيم، حمل، تغذية، نصيحة وقائية، سؤال عن مدى خطورة حالة، أو وصف حالة طارئة.
DIRECT — كلام غير طبي أو لا يحتاج مرجع: تحية، شكر، وداع، طلب إعادة أو توضيح لما قيل، سؤال عن المساعد نفسه، أو مزاح.

مهم: إذا كان السياق السابق سؤالاً طبياً من المساعد، فإن رد المريض عليه — حتى لو كان كلمة وحدة مثل نعم أو لا أو مدة زمنية — هو RETRIEVE، لأنه يكمل وصف الحالة.

أجب بكلمة واحدة فقط: RETRIEVE أو DIRECT. لا تكتب أي شرح."""

_FEW_SHOT: tuple[tuple[str, str], ...] = (
    ("السلام عليكم", "DIRECT"),
    ("مشكورة، الله يعافيك", "DIRECT"),
    ("ممكن تعيدين آخر جملة؟", "DIRECT"),
    ("انتي بشر ولا روبوت؟", "DIRECT"),
    ("عمري خمسين سنة ومعي سكري، متى المفروض أفحص العين؟", "RETRIEVE"),
    ("من متى أبدأ فحص سرطان القولون؟", "RETRIEVE"),
    ("عندي ألم في الركبة من أسبوع بعد ما لعبت كوره", "RETRIEVE"),
    ("ضغطي مئة وأربعين على تسعين، هل هذا عالي؟", "RETRIEVE"),
    ("أبوي طاح وما يرد علي وما يتنفس", "RETRIEVE"),
    ("ابني وقع من الدرج وانجرح ركبته", "RETRIEVE"),
    # Short answers to a triage question. Read alone these look like filler,
    # and routing them DIRECT is what left a red, hot joint ungrounded.
    ("[سياق سابق: هل في تورم أو احمرار حول المفصل؟]\nايه فيه احمرار وسخونة", "RETRIEVE"),
    ("[سياق سابق: من متى وأنت تحس بالكحة؟]\nمن ثلاث أيام", "RETRIEVE"),
    ("[سياق سابق: هل تحس بضيق في التنفس؟]\nلا", "RETRIEVE"),
    ("[سياق سابق: كيف أقدر أساعدك اليوم؟]\nمشكورة، ما أحتاج شي", "DIRECT"),
)


def _build_messages(utterance: str, history: str | None) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = [{"role": "system", "content": _SYSTEM_PROMPT}]
    for example, label in _FEW_SHOT:
        messages.append({"role": "user", "content": example})
        messages.append({"role": "assistant", "content": label})
    # A bare "and how often?" is only classifiable against what came before, so
    # the immediately preceding turn is included when there is one.
    content = utterance if not history else f"[سياق سابق: {history}]\n{utterance}"
    messages.append({"role": "user", "content": content})
    return messages


def _parse_label(raw: str) -> QueryRoute | None:
    text = (raw or "").strip().upper()
    for route in (QueryRoute.RETRIEVE, QueryRoute.DIRECT):
        if route.value in text:
            return route
    return None


class QueryClassifier:
    def __init__(self, settings: RAGSettings) -> None:
        self._settings = settings
        self._client = AsyncOpenAI(
            base_url=settings.llm_base_url,
            api_key=settings.llm_api_key,
            timeout=settings.classifier_timeout_s,
            max_retries=0,
        )

    async def classify(
        self, utterance: str, *, history: str | None = None
    ) -> QueryRoute:
        """Route one utterance to retrieval or straight to the model.

        This decides only whether to spend a retrieval on the turn. It carries
        no clinical weight: what to tell the patient is decided by the
        answering model under the system prompt.
        """
        if not utterance.strip():
            return QueryRoute.DIRECT

        try:
            response = await asyncio.wait_for(
                self._client.chat.completions.create(
                    model=self._settings.classifier_model,
                    messages=_build_messages(utterance, history),  # type: ignore[arg-type]
                    temperature=0.0,
                    max_tokens=self._settings.classifier_max_tokens,
                ),
                timeout=self._settings.classifier_timeout_s,
            )
        except Exception as exc:  # noqa: BLE001 - any failure falls back
            # Fail toward grounding: retrieving evidence the model ignores is
            # cheap, answering a clinical question without it is not.
            logger.warning(
                "Query classifier unavailable ({}); defaulting to RETRIEVE",
                type(exc).__name__,
            )
            return QueryRoute.RETRIEVE

        raw = (response.choices[0].message.content or "") if response.choices else ""
        route = _parse_label(raw)
        if route is None:
            logger.warning(
                "Query classifier returned an unparseable label (chars={}); "
                "defaulting to RETRIEVE",
                len(raw),
            )
            return QueryRoute.RETRIEVE
        return route
