from dotenv import load_dotenv
from typing import Any

import time
from loguru import logger

import httpx

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
    metrics,
)
from livekit.plugins import noise_cancellation, silero, openai
from livekit.plugins.turn_detector.multilingual import MultilingualModel

from src.plugins.custom_f5_tts_plugin import F5FastAPIPlugin
from src.prompts.agent_prompt import system_prompt


load_dotenv(".env.local")


class Assistant(Agent):
    def __init__(self) -> None:
        super().__init__(
            instructions=system_prompt,
        )



    @function_tool()
    async def lookup_weather(
        self,
        context: RunContext,
        location: str,
    ) -> dict[str, Any]:
        """
        Look up weather information for a given location.

        Args:
            location: The location to look up weather information for.
        """
        
        # await context.session.say("هشفهولك حالا.")

        async with httpx.AsyncClient(timeout=4.0) as client:

            # ---------------------------
            # 1. Geocoding
            # ---------------------------
            geo_response = await client.get(
                "https://geocoding-api.open-meteo.com/v1/search",
                params={
                    "name": location,
                    "count": 1,
                    "language": "en",
                },
            )

            if geo_response.status_code != 200:
                raise ToolError(f"Geocoding API failed for: {location}")

            geo_data = geo_response.json()

            results = geo_data.get("results")
            if not results:
                raise ToolError(f"Could not find location: {location}")

            lat = results[0]["latitude"]
            lon = results[0]["longitude"]
            place_name = results[0]["name"]

            # ---------------------------
            # 2. Weather API
            # ---------------------------
            weather_response = await client.get(
                "https://api.open-meteo.com/v1/forecast",
                params={
                    "latitude": lat,
                    "longitude": lon,
                    "current": ["temperature_2m", "weathercode"],
                    "temperature_unit": "celsius",
                },
            )

            if weather_response.status_code != 200:
                raise ToolError(f"Weather API failed for: {location}")

            weather_data = weather_response.json()

            current = weather_data.get("current", {})

            if not current:
                raise ToolError("No current weather data returned")

        return {
            "location": place_name,
            "summary": f"{current.get('temperature_2m')}°C",
        }


server = AgentServer()


@server.rtc_session()
async def my_agent(ctx: agents.JobContext):
    session = AgentSession(
        # stt=groq.STT(
        #     model="whisper-large-v3-turbo",
        #     language="ar",
        # ),
        stt=openai.STT(
            base_url="http://211.21.50.85:57019/v1",
            model="stt_ar_fastconformer_hybrid_large_pcd_v1.0",
            api_key="no-key-needed",
            language="ar",
        ),
        # llm=groq.LLM(model="openai/gpt-oss-20b"),
        llm=openai.LLM(
            base_url="http://8.213.50.155:8001/v1",
            model="gemma4",
            api_key="needed",
        ),
        # tts=openai.TTS(
        #     base_url="http://211.21.50.85:57010/v1",
        #     model="orpheus",
        #     voice="tara",
        #     api_key="no-key-needed",
        # ),
        tts=F5FastAPIPlugin(
            server_url="http://211.21.50.85:57023",  # Change to your FastAPI server URL
            sample_rate=24000,
        ),
        # tts=groq.TTS(model="canopylabs/orpheus-arabic-saudi", voice="fahad"),
        # tts=F5TTSPlugin(
        #     server_url="http://45.81.32.2:11096/",
        #     model_name="f5_tts",
        #     reference_audio_path="speech.wav",
        #     reference_text="جصتي تبدا من ايام ما كنا نزور بيت خوالي",
        #     sample_rate=24000,
        # ),
        vad=silero.VAD.load(),
        turn_detection=MultilingualModel(),
        # preemptive_generation=True
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
            "Greet the user, and you must use the tool "
            "to tell the user the weather in Egypt"
        )
    )


if __name__ == "__main__":
    agents.cli.run_app(server)




    