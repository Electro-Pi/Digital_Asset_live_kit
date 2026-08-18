from contextlib import AsyncExitStack
# from fastmcp import Client
from dotenv import load_dotenv
from typing import Any, AsyncIterable
import uuid
import time
import httpx
import re
from loguru import logger
load_dotenv(".env.local", override=True)

from livekit import agents, rtc
from livekit.agents import (
    Agent,
    AgentServer,
    AgentSession,
    ModelSettings,
    RunContext,
    StopResponse,
    function_tool,
    room_io,
    AgentStateChangedEvent,
    ConversationItemAddedEvent,
    MetricsCollectedEvent,
    ToolError,
    BackgroundAudioPlayer,
    AudioConfig,
    BuiltinAudioClip,
)
from livekit.agents.llm import ChatMessage
from livekit.agents.voice.turn import TurnHandlingOptions
from src.plugins.semantic_cache import sera_cache
from src.plugins.crm_detector import is_crm_action, is_greeting, is_knowledge_query
from livekit.plugins import noise_cancellation, silero, openai, groq, cartesia

from livekit.agents.stt import StreamAdapter as STTStreamAdapter
from livekit.agents.tts import StreamAdapter
from src.config.model_endpoints import (
    LLM_API_KEY,
    LLM_BASE_URL,
    LLM_MODEL,
    STT_API_KEY,
    STT_BASE_URL,
    STT_MODEL,
    TTS_MODEL,
    TTS_SEED,
    TTS_SERVER_URL,
    TTS_VOICE,
)
from src.plugins.cohere_asr_streaming_stt import CohereASRStreamingSTT
from src.plugins.voxcpm_tts_plugin import VoxCPMTTSPlugin
from src.prompts.agent_prompt import system_prompt
from src.plugins.higgs_tts import HiggsTTS
from src.utils.user_text import extract_user_message
from src.utils.speech_format import (
    format_for_speech,
    finalize_prosody,
    format_stream_for_speech,
    is_detail_followup,
    softly_shorten_response,
)

_RAG_MISS_DEFAULT = "عذراً، ما لقيت هالمعلومة في قاعدة المعرفة حالياً، ممكن توضح سؤالك؟"

# Intent-specific fallbacks when RAG has no direct answer (smarter than generic miss).
_RAG_MISS_BY_INTENT: tuple[tuple[tuple[str, ...], str], ...] = (
    (
        ("رقم", "خدمة العملاء", "مركز الاتصال", "اتصال", "هاتف", "تواصل"),
        "ما عندي رقم خدمة عملاء محدد في قاعدة المعرفة حالياً، "
        "تقدر تزور موقع الهيئة الإلكتروني أو تتواصل مع مقدم الخدمة مباشرة.",
    ),
    (
        ("حالة", "طلب", "متابعة", "تتبع"),
        "ما لقيت إجراء واضح لمتابعة حالة الطلب في قاعدة المعرفة، "
        "جرب تدخل برقم الطلب في بوابة الهيئة أو تواصل مع مقدم الخدمة.",
    ),
    (
        ("رسوم", "تكلفة", "سعر", "مجاني", "مقابل"),
        "ما ظهر عندي معلومة عن رسوم أو تكلفة لهذا الإجراء في قاعدة المعرفة حالياً.",
    ),
    (
        ("رفض", "رفضوا", "مرفوض", "اعتراض"),
        "ما عندي إجراء محدد لاعتراض على رفض الشكوى في قاعدة المعرفة حالياً، "
        "تقدر توضح لي وضع شكواك بالتفصيل؟",
    ),
    (
        ("افتح", "جديدة", "تقديم", "ارفع", "سجل"),
        "ما لقيت خطوات واضحة لفتح أو تقديم شكوى جديدة في قاعدة المعرفة حالياً.",
    ),
    (
        ("اتابع", "متابعة", "رقم الشكوى"),
        "ما لقيت طريقة محددة لمتابعة الشكوى في قاعدة المعرفة، "
        "جرب تدخل برقم الشكوى في موقع الهيئة.",
    ),
    (
        ("تقييم", "رضا", "قياس"),
        "ما عندي معلومة عن تقييم الخدمة أو الشكوى في قاعدة المعرفة حالياً.",
    ),
    (
        ("الغي", "إلغاء", "الغاء", "احذف"),
        "ما لقيت إجراء إلغاء أو تعديل في قاعدة المعرفة حالياً، وضح لي نوع الطلب اللي تبغى تلغيه.",
    ),
    (
        ("فاتور", "دفع", "سداد", "مبلغ"),
        "ما ظهر عندي تفاصيل عن الفواتير أو السداد لهذا السؤال في قاعدة المعرفة حالياً.",
    ),
    (
        ("تعريف", "تعرفة", "اسعار", "أسعار", "تكلفة الكهرب"),
        "ما لقيت معلومة عن التعريفة أو الأسعار لهذا السؤال في قاعدة المعرفة حالياً.",
    ),
    (
        ("شمس", "طاقة شمس", "كهروضوئ", "لوح"),
        "ما عندي تفاصيل عن الطاقة الشمسية لهذا السؤال في قاعدة المعرفة حالياً.",
    ),
    (
        ("حرية", "معلومات"),
        "ما لقيت إجراء حرية المعلومات لهذا السؤال في قاعدة المعرفة حالياً.",
    ),
)


def _rag_miss_reply(query: str) -> str:
    q = (query or "").strip()
    if not q:
        return _RAG_MISS_DEFAULT
    for signals, reply in _RAG_MISS_BY_INTENT:
        if any(s in q for s in signals):
            return reply
    return _RAG_MISS_DEFAULT


def _is_rag_miss_reply(text: str) -> bool:
    t = (text or "").strip()
    if not t:
        return True
    if t == _RAG_MISS_DEFAULT:
        return True
    return any(t == reply for _, reply in _RAG_MISS_BY_INTENT)

_HUMANIZE_SYSTEM = """\
أنت سيرا، المساعدة الصوتية الرسمية للهيئة السعودية لتنظيم الكهرباء.
مهمتك: أعد صياغة الإجابة التالية المستخرجة من قاعدة المعرفة بأسلوب إنساني ودافئ وطبيعي باللهجة السعودية الخليجية، كأنك تشرح لشخص أمامك.

قواعد صارمة:
- لا تضيف معلومات جديدة أبداً، فقط أعد الصياغة.
- استخدم جُمَلاً قصيرة واضحة مع وقفات طبيعية (فاصلة أو نقطة).
- ابدأ مباشرةً بالإجابة بدون أي تحية أو مقدمة.
- احتفظ بالتشكيل الموجود في النص الأصلي ولا تحذفه.
- لا تستخدم ترقيم أو نقاط أو قوائم — فقط نثر متصل.
- لا تضف خاتمة إنشائية. اختم عند اكتمال المعلومة فقط.
- أجب باللغة العربية فقط.
- اجعل الرد مختصرًا نسبيًا: من 3 إلى 5 جمل كحد أقصى.
- ابتعد عن الإطالة والتكرار.
- الرد يجب أن لا يتجاوز 95 كلمة.
"""


def _intent_guard_ok(query: str, response: str) -> bool:
    q = (query or "").strip()
    r = (response or "").strip()
    if not q or not r:
        return False

    def _is_phone_query(text: str) -> bool:
        if "خدمة العملاء" in text or "مركز الاتصال" in text or "مركز اتصال" in text:
            return True
        return "رقم" in text and any(
            s in text for s in ("العملاء", "مركز", "اتصال", "هاتف", "تواصل", "اتصل")
        )

    # Keep strict guards for intents that frequently retrieve wrong chunks.
    if _is_phone_query(q):
        return (
            ("خدمة العملاء" in r or "مركز الاتصال" in r or "مركز اتصال" in r)
            and ("19944" in r or "800" in r or re.search(r"[0-9٠-٩]{3,}", r) is not None)
        )
    if ("حالة" in q and "طلب" in q) or ("متابعة" in q and "طلب" in q):
        return any(s in r for s in ("حالة الطلب", "رقم الطلب", "متابعة", "تتبع", "الاستعلام", "بوابة"))
    if ("افتح" in q or "جديدة" in q or "تقديم" in q) and ("شكوى" in q or "الشكوى" in q):
        if "إغلاق" in r or "اغلاق" in r:
            return False
        return any(s in r for s in ("تقديم الشكوى", "تقديم", "فتح", "خدمة تقديم"))
    if ("اتابع" in q or "متابعة" in q) and ("شكوى" in q or "الشكوى" in q):
        return any(s in r for s in ("متابعة", "رقم الشكوى", "الاستعلام"))
    if "تقييم" in q and ("خدمة" in q or "الشكوى" in q):
        return any(s in r for s in ("تقييم", "رضا", "قياس"))

    checks: list[tuple[tuple[str, ...], tuple[str, ...]]] = [
        (
            ("خدمة العملاء", "مركز الاتصال", "اتصال", "تواصل", "هاتف"),
            ("خدمة العملاء", "مركز الاتصال", "اتصال", "هاتف", "واتساب", "بريد", "19944"),
        ),
        (("رسوم", "تكلفة", "تكاليف", "سعر", "اسعار", "أسعار", "تعريفة", "تعرفة"), ("رسوم", "تكلفة", "تكاليف", "مقابل", "ريال", "مجان", "تعريفة", "تعرفة", "سكن", "كهرب", "استهلاك", "خدمات", "منشآت")),
        (("حالة الطلب", "حالة", "متابعة"), ("حالة", "متابعة", "رقم الطلب", "الطلب")),
        (("رفض", "مرفوض"), ("اعتراض", "تصعيد", "إعادة", "رفض")),
        (("الغي", "إلغاء"), ("إلغاء", "تعديل", "حذف", "إيقاف")),
    ]
    for q_terms, must_have in checks:
        if any(t in q for t in q_terms):
            if "خدمة العملاء" in q_terms and not _is_phone_query(q):
                continue
            return any(t in r for t in must_have)
    return True

def _strip_chatty_edges(text: str) -> str:
    out = (text or "").strip()
    if not out:
        return out

    leading = (
        "أوضح لك،",
        "اوضح لك،",
        "طبعًا،",
        "طبعاً،",
        "طبعا،",
        "أكيد،",
        "حياك،",
        "هلا،",
        "هلا بك،",
        "بكل سرور،",
        "خلني أوضح لك،",
    )
    trailing = (
        "أي استفسار ثاني أنا هنا.",
        "اي استفسار ثاني انا هنا.",
        "إن شاء الله تستفيد.",
        "إن شاء الله تكون الصورة واضحة.",
        "إذا حاب أكمل لك التفاصيل، أبشر.",
        "إذا تحتاج تفصيل أكثر، حاضر.",
    )

    for phrase in leading:
        if out.startswith(phrase):
            out = out[len(phrase):].strip()
            break

    for phrase in trailing:
        if out.endswith(phrase):
            out = out[: -len(phrase)].rstrip(" ،.")
            break

    return out


async def _humanize_rag(raw: str) -> str:
    """Rewrite a raw RAG chunk into warm, natural Saudi Arabic speech via LLM."""
    base = LLM_BASE_URL.rstrip("/")
    if not base.endswith("/v1"):
        base = f"{base}/v1"
    url = f"{base}/chat/completions"
    payload = {
        "model": LLM_MODEL,
        "temperature": 0.25,
        "max_tokens": 220,
        "messages": [
            {"role": "system", "content": _HUMANIZE_SYSTEM},
            {"role": "user", "content": raw},
        ],
    }
    headers = {"Authorization": f"Bearer {LLM_API_KEY}"}
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(12.0, connect=5.0)) as client:
            resp = await client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            text = resp.json()["choices"][0]["message"]["content"].strip()
            if len(text) > 30:
                logger.info("RAG humanized: {} → {} chars", len(raw), len(text))
                return text
    except Exception as exc:
        logger.warning("RAG humanize failed ({}), using cleaned original", exc)
    return raw


class Assistant(Agent):

    def __init__(
        self,
        session_id: str,
        background_audio: BackgroundAudioPlayer,
    ) -> None:

        super().__init__(
            instructions=system_prompt,
        )

        self.session_id = session_id
        self.background_audio = background_audio
        self._last_user_message = ""

    def _remember_user_message(self, message: str) -> str:
        cleaned = extract_user_message(message)
        if cleaned:
            self._last_user_message = cleaned
        return cleaned

    def _latest_user_message(self, session: AgentSession) -> str:
        if self._last_user_message:
            return self._last_user_message

        history = getattr(session, "history", None)
        if history is None:
            return ""

        for item in reversed(history.items):
            if isinstance(item, ChatMessage) and item.role == "user":
                cleaned = extract_user_message(item.text_content or "")
                if cleaned:
                    return cleaned
        return ""

    def _unwrap_tts(self):
        activity = self._get_activity_or_raise()
        wrapped = activity.tts
        while isinstance(wrapped, StreamAdapter):
            wrapped = wrapped._wrapped_tts
        return wrapped

    async def _resolve_rag_message(self, message: str) -> str:
        """Answer from Qdrant RAG only (read-only, no MCP backend)."""
        message = extract_user_message(message)
        if not message:
            return ""

        rag_answer = sera_cache.get_cached_response(message)
        if not rag_answer:
            logger.info(f"RAG miss for '{message[:60]}'")
            return _rag_miss_reply(message)

        # Clean the raw chunk first, then humanize via LLM for natural delivery.
        cleaned = format_for_speech(rag_answer)
        if not cleaned:
            return _rag_miss_reply(message)
        if not _intent_guard_ok(message, cleaned):
            logger.warning(
                "RAG hit rejected by intent guard | query='{}' | preview='{}'",
                message[:80],
                cleaned[:120],
            )
            return _rag_miss_reply(message)

        # Short KB snippets are already speakable — humanize often over-compresses them.
        if len(cleaned) <= 150:
            spoken = softly_shorten_response(
                finalize_prosody(cleaned),
                max_chars=max(120, len(cleaned)),
                max_sentences=4,
            )
        else:
            humanized = await _humanize_rag(cleaned)
            humanized = _strip_chatty_edges(humanized)
            if len(humanized) < max(60, int(len(cleaned) * 0.45)):
                humanized = cleaned

            # Keep the spoken answer complete, but slightly shorter than raw RAG text.
            target_chars = max(140, int(len(cleaned) * 0.82))
            spoken = softly_shorten_response(
                finalize_prosody(humanized),
                max_chars=target_chars,
                max_sentences=4,
            )
            # Guardrail: never expand beyond the source chunk.
            if len(spoken) >= len(cleaned):
                spoken = softly_shorten_response(
                    cleaned,
                    max_chars=max(120, int(len(cleaned) * 0.9)),
                    max_sentences=4,
                )

        logger.info(
            f"RAG answer ({len(spoken)} chars): "
            f"{spoken[:120]}...{spoken[-60:] if len(spoken) > 180 else ''}"
        )
        return spoken
        
    def trim_history(self, session: AgentSession, max_turns: int = 5):
        logger.info(f"Current Chat Context Length: {len(session.chat_ctx.messages)} messages")
        messages = session.chat_ctx.messages
        if len(messages) > (max_turns * 2):
            # Keep the system prompt (index 0) and the last max_turns*2 messages
            session.chat_ctx.messages = [messages[0]] + messages[-(max_turns * 2):]

    async def on_user_turn_completed(self, turn_ctx, new_message) -> None:
        """Route knowledge/CRM queries to Qdrant RAG — skip LLM hallucinations."""
        message = self._remember_user_message(new_message.text_content)
        if not message or is_greeting(message):
            return

        if not is_knowledge_query(message) and not is_crm_action(message):
            return

        spoken = await self._resolve_rag_message(message)
        if not spoken:
            return

        logger.info(
            f"RAG fast-path for '{message[:60]}' — speaking {len(spoken)} chars, skipping LLM."
        )
        await self.session.interrupt()
        self.session.say(spoken, allow_interruptions=False)
        raise StopResponse()
    
    async def tts_node(
        self,
        text: AsyncIterable[str],
        model_settings: ModelSettings,
    ) -> AsyncIterable[rtc.AudioFrame]:
        """One TTS call for the full cleaned text — no sentence splitting."""
        activity = self._get_activity_or_raise()
        base_tts = self._unwrap_tts()
        conn_options = activity.session.conn_options.tts_conn_options

        buffer = ""
        async for chunk in format_stream_for_speech(text):
            buffer += chunk

        if not buffer:
            return

        async with base_tts.synthesize(buffer, conn_options=conn_options) as tts_stream:
            async for audio in tts_stream:
                yield audio.frame

    @function_tool()
    async def sera_chat(self, context: RunContext, message: str) -> str:
        try:
            latest_user = self._latest_user_message(context.session)
            if latest_user:
                message = latest_user
            else:
                message = extract_user_message(message)

            logger.info(f"sera_chat query (user turn): '{message}'")
            clean_response = await self._resolve_rag_message(message)
            if clean_response and not _is_rag_miss_reply(clean_response):
                logger.info(f"Speaking RAG answer directly ({len(clean_response)} chars).")
                context.session.say(clean_response, allow_interruptions=False)
            raise StopResponse()

            # --- MCP backend disabled; RAG-only mode ---
            # crm_intent = is_crm_action(message)
            # if not crm_intent:
            #     ...
            # result = await self.mcp_client.call_tool(
            #     name="sera_chat",
            #     arguments={"message": message, "session_id": self.session_id},
            # )

        except StopResponse:
            raise
        except Exception as e:
            error_msg = f"Error resolving RAG answer: {str(e)}"
            logger.error(error_msg)
            return error_msg

server = AgentServer(
    num_idle_processes=1,
    initialize_process_timeout=120.0,
    job_memory_warn_mb=3000,
)


@server.rtc_session()
async def my_agent(ctx: agents.JobContext):
    exit_stack = AsyncExitStack()
    background_audio = None

    try:
        # --- MCP backend disabled; RAG-only mode ---
        # url = "http://8.213.50.155:8081/mcp"
        # logger.info(f"Connecting to remote FastMCP server at {url}...")
        # mcp_client = await exit_stack.enter_async_context(Client(url))
        # logger.info("Remote FastMCP connection established successfully.")
        logger.info("MCP disabled — answering from Qdrant RAG only.")

        missing_keys = {"", "no-key-needed", "REPLACE_WITH_LLM_API_KEY"}
        if LLM_API_KEY in missing_keys:
            logger.error(
                "LLM_API_KEY is missing/placeholder — LLM and STT will return 401. "
                "Set LLM_API_KEY (and STT_API_KEY if different) in .env.local."
            )
        if STT_API_KEY in missing_keys | {"REPLACE_WITH_STT_API_KEY"}:
            logger.error(
                "STT_API_KEY is missing/placeholder — speech recognition will fail with 401."
            )

        session = AgentSession(
            stt=STTStreamAdapter(
                stt=CohereASRStreamingSTT(
                    base_url=STT_BASE_URL,
                    model=STT_MODEL,
                    api_key=STT_API_KEY,
                ),
                vad=silero.VAD.load(),
            ),
            llm=openai.LLM(
                base_url=LLM_BASE_URL,
                model=LLM_MODEL,
                api_key=LLM_API_KEY,
                temperature=0.1,
                max_completion_tokens=400,
            ),
            tts=VoxCPMTTSPlugin(
                server_url=TTS_SERVER_URL,
                model=TTS_MODEL,
                voice=TTS_VOICE,
                seed=TTS_SEED,
            ),
            vad=silero.VAD.load(),
            turn_handling=TurnHandlingOptions(
                turn_detection="vad",
                endpointing={"min_delay": 0.35, "max_delay": 2.0},
                interruption={"min_duration": 0.8, "min_words": 2},
                preemptive_generation={"enabled": False},
            ),
            aec_warmup_duration=4.0,
            tts_text_transforms=["filter_markdown", "filter_emoji"],
        )

        background_audio = BackgroundAudioPlayer(
            # Office ambience decode can fail on some builds; keep typing only.
            ambient_sound=None,
            stream_timeout_ms=1000,
        )

        last_eou_metrics = None
        collected_metrics = []

        @session.on("metrics_collected")
        def _on_metrics_collected(event: MetricsCollectedEvent):
            nonlocal last_eou_metrics
            if getattr(event.metrics, "type", None) == "eou_metrics":
                last_eou_metrics = event.metrics
            collected_metrics.append(event.metrics)

        # 2. UPDATE SHUTDOWN CALLBACK 
        async def log_usage_and_cleanup():
            logger.info("Session ended.")
            logger.info(f"Total metrics events collected: {len(collected_metrics)}")
            await exit_stack.aclose()

        ctx.add_shutdown_callback(log_usage_and_cleanup)

        @session.on("agent_state_changed")
        def _on_agent_state_changed(event: AgentStateChangedEvent):
            logger.info(f"Agent state changed: {event.new_state}")
            if event.new_state == "speaking" and last_eou_metrics:
                try:
                    elapsed_time = time.time() - last_eou_metrics.timestamp
                    logger.info(f"Time since last EOU: {elapsed_time:.2f} seconds")
                except Exception as e:
                    logger.warning(f"Could not calculate EOU latency: {e}")

        # Generate a completely unique, pure Python string for this phone call
        call_session_id = str(uuid.uuid4())
        
        

        # 3. PASS THE CLIENT AND SESSION ID TO THE ASSISTANT
        assistant = Assistant(
            session_id=call_session_id,
            background_audio=background_audio,
        )

        @session.on("conversation_item_added")
        def _on_conversation_item(event: ConversationItemAddedEvent):
            if isinstance(event.item, ChatMessage) and event.item.role == "user":
                assistant._remember_user_message(event.item.text_content or "")

        await session.start(
            room=ctx.room,
            agent=assistant,
            room_options=room_io.RoomOptions(
                audio_input=room_io.AudioInputOptions(
                    noise_cancellation=lambda params:
                        noise_cancellation.BVCTelephony()
                        if params.participant.kind
                        == rtc.ParticipantKind.PARTICIPANT_KIND_SIP
                        else noise_cancellation.BVC()
                ),
            ),
        )
        
        
        await background_audio.start(
            room=ctx.room,
            agent_session=session,
        )

        await session.say(
            "هلا والله، معك سيرا المساعدة الرسمية للهيئة السعودية لتنظيم الكهرباء، كيف أقدر أخدمك اليوم؟",
            allow_interruptions=True,
        )
        
        
    except Exception as e:
        logger.error(f"Failed to start agent session: {e}")
        if background_audio:
            await background_audio.aclose()
        await exit_stack.aclose()


if __name__ == "__main__":
    agents.cli.run_app(server)