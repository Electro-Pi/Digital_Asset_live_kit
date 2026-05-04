import os
import time
from dotenv import load_dotenv
from loguru import logger

from google import genai
from qdrant_client import QdrantClient

from livekit import agents, rtc
from livekit.agents import (
    Agent,
    AgentServer,
    AgentSession,
    function_tool,
    room_io,
    AgentStateChangedEvent,
    MetricsCollectedEvent,
    ToolError,
)
from livekit.plugins import noise_cancellation, silero, openai
from livekit.plugins.turn_detector.multilingual import MultilingualModel

from src.config.omnivoice_najdi_tts import najdi_omnivoice_speech_extra_body
from src.plugins.openai_stt_with_temperature import STTWithTemperature
from src.plugins.openai_tts_with_extra_body import OpenAITTSWithSpeechExtraBody

# =============================
# Load ENV
# =============================
load_dotenv(".env.local")

# =============================
# Clients
# =============================
genai_client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

qdrant_client = QdrantClient(
    url=os.getenv("QDRANT_URL"),
    api_key=os.getenv("QDRANT_API_KEY")
)

COLLECTION_NAME = "sera_knowledge_base"


@function_tool
def search_sera_knowledge_base(query: str) -> str:
    """
    Search SERA knowledge base for electricity regulations, complaints, and billing info.
    Use this tool whenever the question requires factual or official information.
    """
    try:
        # 1. Embedding
        result = genai_client.models.embed_content(
            model="gemini-embedding-2-preview",
            contents=query,
            config={"output_dimensionality": 768}
        )

        query_vector = result.embeddings[0].values

        # 2. Search
        search_result = qdrant_client.query_points(
            collection_name=COLLECTION_NAME,
            query=query_vector,
            using="dense",
            limit=3,
            with_payload=True
        )

        # 3. Format results (VERY IMPORTANT)
        chunks = [
            point.payload.get("content", "")
            for point in search_result.points
        ]

        if not chunks:
            return "لا توجد معلومات متاحة في قاعدة البيانات."

        formatted = "\n\n".join(
            [f"نتيجة {i+1}:\n{text}" for i, text in enumerate(chunks)]
        )

        return formatted

    except Exception as e:
        raise ToolError(f"RAG search failed: {str(e)}")


# =============================
# System Prompt
# =============================
sera_system_prompt = """
أنتِ سيرا (Sera)، المساعدة الافتراضية الرسمية للهيئة السعودية لتنظيم الكهرباء (SERA).

مهمتك الأساسية هي مساعدة المستفيدين في:
- اللوائح والتنظيمات
- الفواتير
- الشكاوى
- إجراءات الكهرباء

القواعد:
1. تحدثي باللهجة السعودية النجدية.
2. كوني دافئة واحترافية.
3. لا تؤلفي معلومات أبداً.
4. إذا كان السؤال يتطلب معلومات دقيقة أو رسمية → يجب استخدام أداة search_sera_knowledge_base أولاً.
5. بعد استخدام الأداة، اعتمدي فقط على النتائج.
6. لا تذكري أبداً أنك ذكاء اصطناعي.
"""

# =============================
# Agent
# =============================
class Assistant(Agent):
    def __init__(self) -> None:
        super().__init__(
            instructions=sera_system_prompt,
            tools=[search_sera_knowledge_base],  # ✅ ربط الـ RAG
        )


# =============================
# Server
# =============================
server = AgentServer()


@server.rtc_session()
async def my_agent(ctx: agents.JobContext):
    session = AgentSession(
        # =============================
        # STT
        # =============================
        stt=STTWithTemperature(
            base_url="http://8.213.50.155:8002/v1",
            model="/home/ai/vllm-sera/models/cohere-transcribe",
            api_key="no-key-needed",
            language="ar",
            temperature=0.0,
        ),

        # =============================
        # LLM
        # =============================
        llm=openai.LLM(
            base_url="http://8.213.50.155:8001/v1",
            model="gemma4",
            api_key="needed",
        ),

        # =============================
        # TTS
        # =============================
        tts=OpenAITTSWithSpeechExtraBody(
            speech_extra_body=najdi_omnivoice_speech_extra_body(),
            model="tts-1",
            voice="clone:voice_ronn",
            base_url="http://127.0.0.1:8188/v1",
            api_key="no-key-needed",
            response_format="pcm",
            speed=1.3,
        ),

        # =============================
        # Audio Processing
        # =============================
        vad=silero.VAD.load(),
        turn_detection=MultilingualModel(),
    )

    last_eou_metrics = None
    collected_metrics = []

    # =============================
    # Metrics
    # =============================
    @session.on("metrics_collected")
    def _on_metrics_collected(event: MetricsCollectedEvent):
        nonlocal last_eou_metrics

        if getattr(event.metrics, "type", None) == "eou_metrics":
            last_eou_metrics = event.metrics

        collected_metrics.append(event.metrics)
        logger.info(f"Metrics collected: {event.metrics}")

    async def log_usage():
        logger.info("Session ended.")
        logger.info(f"Total metrics events: {len(collected_metrics)}")

    ctx.add_shutdown_callback(log_usage)

    # =============================
    # State Tracking
    # =============================
    @session.on("agent_state_changed")
    def _on_agent_state_changed(event: AgentStateChangedEvent):
        logger.info(f"Agent state: {event.new_state}")

        if event.new_state == "speaking" and last_eou_metrics:
            try:
                elapsed = time.time() - last_eou_metrics.timestamp
                logger.info(f"EOU latency: {elapsed:.2f}s")
            except Exception as e:
                logger.warning(f"Latency calc failed: {e}")

    # =============================
    # Start Session
    # =============================
    await session.start(
        room=ctx.room,
        agent=Assistant(),
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

    # =============================
    # First Message
    # =============================
    await session.generate_reply(
        instructions="ابدئي بالترحيب بالمستخدم واسأليه كيف تقدري تساعدينه"
    )


# =============================
# Run
# =============================
if __name__ == "__main__":
    agents.cli.run_app(server)