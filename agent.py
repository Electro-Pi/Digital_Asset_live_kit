from dotenv import load_dotenv

from livekit import agents, rtc
from livekit.agents import AgentServer, AgentSession, Agent, room_io
from livekit.plugins import noise_cancellation, silero
from livekit.plugins.turn_detector.multilingual import MultilingualModel
from livekit.plugins import groq, openai
from livekit import rtc
from livekit.agents import ModelSettings, Agent
from typing import AsyncIterable
from livekit.agents import function_tool, Agent, RunContext
from typing import Any
from livekit.agents import AgentSession, inference

from src.plugins.custom_f5_tts_plugin import F5FastAPIPlugin

from src.plugins.tts_f5_plugin import F5TTSPlugin

from src.prompts.agent_prompt import system_prompt


load_dotenv(".env.local")

import re



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
        """Look up weather information for a given location.

        Args:
            location: The location to look up weather information for.
        """

        return {"weather": "sunny", "temperature_f": 70}



server = AgentServer()


@server.rtc_session()
async def my_agent(ctx: agents.JobContext):
    session = AgentSession(
        # stt=groq.STT(
        #     model="whisper-large-v3-turbo",
        #     language="ar",
        # ),
        stt=openai.STT(
            base_url="http://211.21.50.85:57008/v1",
            model="cohere-transcribe-03-2026",
            api_key="no-key-needed",
            language="ar",
        ),
        # llm=groq.LLM(model="openai/gpt-oss-20b"),
        llm=openai.LLM(
            base_url="http://8.213.50.155:8001/v1",
            model="gemma4",
            api_key="needed",
            verbosity = "high",
        ),
        # tts=openai.TTS(
        #     base_url="http://211.21.50.85:57010/v1",
        #     model="orpheus",
        #     voice="tara",
        #     api_key="no-key-needed",
        # ),
        tts=F5FastAPIPlugin(
            server_url="http://211.21.50.85:57010",  # Change to your FastAPI server URL
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
    )

    await session.start(
        room=ctx.room,
        agent=Assistant(),
        room_options=room_io.RoomOptions(
            audio_input=room_io.AudioInputOptions(
                noise_cancellation=lambda params: noise_cancellation.BVCTelephony()
                if params.participant.kind == rtc.ParticipantKind.PARTICIPANT_KIND_SIP
                else noise_cancellation.BVC(),
            ),
        ),
    )

    await session.generate_reply(
        instructions="Greet the user, and you must use the tool to tell the user the weather in Egypt",
    )


if __name__ == "__main__":
    agents.cli.run_app(server)



