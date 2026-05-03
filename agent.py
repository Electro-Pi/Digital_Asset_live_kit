from dotenv import load_dotenv
from typing import Any

import time
from loguru import logger

from livekit import agents, rtc
from livekit.agents import (
    Agent,
    AgentServer,
    AgentSession,
    RunContext,
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
from src.prompts.agent_prompt import system_prompt


load_dotenv(".env.local")


MENU = {
    "بيتزا مارغريتا": 35,
    "بيتزا بيبروني": 42,
    "برجر لحم": 30,
    "برجر دجاج": 28,
    "باستا ألفريدو": 38,
    "سيزر سالاد": 25,
    "موهيتو": 18,
    "عصير برتقال": 12,
    "ماء": 5,
}


class Assistant(Agent):
    def __init__(self) -> None:
        super().__init__(
            instructions=system_prompt,
        )

    @function_tool()
    async def calculate_order(
        self,
        context: RunContext,
        item_name: str,
        quantity: int = 1,
    ) -> dict[str, Any]:
        """
        Calculate order total price and estimated delivery time.

        Args:
            item_name: Food or drink name from restaurant menu
            quantity: Number of items ordered
        """

        if item_name not in MENU:
            raise ToolError(f"Item not found: {item_name}")

        unit_price = MENU[item_name]
        total_price = unit_price * quantity

        # simple mock delivery estimate
        delivery_time = "ثلاثين إلى خمسة وأربعين دقيقة"

        return {
            "item_name": item_name,
            "quantity": quantity,
            "total_price_sar": total_price,
            "delivery_time": delivery_time,
        }


server = AgentServer()


@server.rtc_session()
async def my_agent(ctx: agents.JobContext):
    session = AgentSession(
        stt=STTWithTemperature(
            base_url="http://8.213.50.155:8002/v1",
            model="/home/ai/vllm-sera/models/cohere-transcribe",
            api_key="no-key-needed",
            language="ar",
            temperature=0.0,
        ),

        llm=openai.LLM(
            base_url="http://8.213.50.155:8001/v1",
            model="gemma4",
            api_key="needed",
        ),
        # OpenAI TTS: "tts-1"/"tts-1-hd" use binary PCM; other model names use SSE parsing.
        # Najdi tuning: src/config/omnivoice_najdi_tts.py (OmniVoice ``ars`` + generation_*).
        tts=OpenAITTSWithSpeechExtraBody(
            speech_extra_body=najdi_omnivoice_speech_extra_body(),
            model="tts-1",
            voice="clone:voice_ronn",
            base_url="http://127.0.0.1:8188/v1",
            api_key="no-key-needed",
            response_format="pcm",
            speed=1.3,
        ),

        vad=silero.VAD.load(),
        turn_detection=MultilingualModel(),
    )

    last_eou_metrics = None
    collected_metrics = []

    @session.on("metrics_collected")
    def _on_metrics_collected(event: MetricsCollectedEvent):
        nonlocal last_eou_metrics

        if getattr(event.metrics, "type", None) == "eou_metrics":
            last_eou_metrics = event.metrics

        collected_metrics.append(event.metrics)
        logger.info(f"Metrics collected: {event.metrics}")

    async def log_usage():
        logger.info("Session ended.")
        logger.info(f"Total metrics events collected: {len(collected_metrics)}")

        for metric in collected_metrics:
            logger.info(metric)

    ctx.add_shutdown_callback(log_usage)

    @session.on("agent_state_changed")
    def _on_agent_state_changed(event: AgentStateChangedEvent):
        logger.info(f"Agent state changed: {event.new_state}")

        if event.new_state == "speaking" and last_eou_metrics:
            try:
                elapsed_time = time.time() - last_eou_metrics.timestamp
                logger.info(
                    f"Time since last EOU: {elapsed_time:.2f} seconds"
                )
            except Exception as e:
                logger.warning(f"Could not calculate EOU latency: {e}")

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

    await session.generate_reply(
        instructions=(
            "ابدأ بالترحيب بالعميل في مطعم فينيرو، "
            "واسأله بشكل طبيعي: وش تحب تطلب اليوم؟"
        )
    )


if __name__ == "__main__":
    agents.cli.run_app(server)