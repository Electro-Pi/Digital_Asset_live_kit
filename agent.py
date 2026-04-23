import asyncio
from typing import Any

from dotenv import load_dotenv
from livekit import agents, rtc
from livekit.agents import (
    Agent,
    AgentServer,
    AgentSession,
    RunContext,
    function_tool,
    room_io,
)
from livekit.plugins import noise_cancellation, silero

from src.plugins.custom_f5_tts_plugin import F5FastAPIPlugin
from src.plugins.custom_gemma4_audio_plugin import Gemma4AudioLLM
from src.prompts.agent_prompt import system_prompt

load_dotenv(".env.local")


class Assistant(Agent):
    def __init__(self) -> None:
        super().__init__(instructions=system_prompt)

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
    await ctx.connect()

    vad = silero.VAD.load()
    gemma_llm = Gemma4AudioLLM(
        base_url="http://211.21.50.85:57008/v1",
        model="google/gemma-4-e2b-it",
        api_key="needed",
        system_prompt=system_prompt,
    )
    session = AgentSession(
        llm=gemma_llm,
        tts=F5FastAPIPlugin(
            server_url="http://211.21.50.85:57010",
            sample_rate=24000,
        ),
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
    gemma_llm.attach_room_audio(room=ctx.room, session=session, vad_model=vad)

    await session.generate_reply(
        instructions="Greet the user, and you must use the tool to tell the user the weather in Egypt",
    )

    try:
        await asyncio.Event().wait()
    except asyncio.CancelledError:
        pass
    finally:
        await session.aclose()


if __name__ == "__main__":
    agents.cli.run_app(server)
