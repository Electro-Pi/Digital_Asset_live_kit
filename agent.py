import os
import time
from dotenv import load_dotenv
from loguru import logger

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
from src.plugins.custom_f5_tts_plugin import F5FastAPIPlugin
from src.prompts.agent_prompt import sera_system_prompt
from src.tools.rag_tools import search_sera_knowledge_base

# =============================
# Load ENV
# =============================
load_dotenv(".env.local")

# =============================
# Agent
# =============================
class Assistant(Agent):
    def __init__(self) -> None:
        super().__init__(
            instructions=sera_system_prompt,
            tools=[search_sera_knowledge_base],
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
        tts=F5FastAPIPlugin(
            server_url="http://211.21.50.85:57023",
            sample_rate=24000,
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