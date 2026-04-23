from dotenv import load_dotenv


from livekit import agents, rtc
from livekit.agents import AgentServer, AgentSession, Agent, room_io
from livekit.plugins import noise_cancellation
from livekit.plugins.turn_detector.multilingual import MultilingualModel
from livekit.plugins import openai
from livekit.agents import function_tool, Agent, RunContext
from typing import Any
from livekit.plugins.silero import VAD

import asyncio
import io
import wave
import base64
import aiohttp
import json

from src.plugins.custom_f5_tts_plugin import F5FastAPIPlugin
from src.prompts.agent_prompt import system_prompt


load_dotenv(".env.local")

server = AgentServer()

# History for Gemma4
chat_history = [{"role": "system", "content": system_prompt}]


def encode_audio_base64(frames: list[rtc.AudioFrame]) -> str:
    """Takes a list of LiveKit AudioFrames, converts to a standard 16kHz WAV byte array, and base64 encodes it."""
    # Assuming frames are 16-bit PCM. We need to create a WAV file in memory.
    if not frames:
        return ""

    # Calculate total samples and check sampling rate
    sample_rate = frames[0].sample_rate
    num_channels = frames[0].num_channels

    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(num_channels)
        wav_file.setsampwidth(2)  # 16-bit PCM = 2 bytes
        wav_file.setframerate(sample_rate)
        # join all raw frame byte data
        for f in frames:
            wav_file.writeframes(f.data)

    # Return base64 string
    return base64.b64encode(buffer.getvalue()).decode("utf-8")


async def stream_audio_to_gemma(base64_audio: str, ctx: agents.JobContext):
    """Sends the audio chunk to Cloudflare gemma4 HTTP endpoint, gets text response, and synthesizes."""

    # Sanitize previous history: gemma4 only allows 1 audio per prompt
    for msg in chat_history:
        if isinstance(msg.get("content"), list):
            # Replace old audio blocks with a text placeholder
            msg["content"] = "User provided audio input."

    # Format message payload adhering to OpenAI Multimodal schema
    message = {
        "role": "user",
        "content": [
            {
                "type": "input_audio",
                "input_audio": {"data": base64_audio, "format": "wav"},
            }
        ],
    }
    chat_history.append(message)

    # Make HTTP POST
    print("Calling Gemma4 via HTTP...")
    url = "https://ranch-shirt-ceremony-rail.trycloudflare.com/v1/chat/completions"
    headers = {"Content-Type": "application/json", "Authorization": "Bearer needed"}
    payload = {
        "model": "gemma4",
        "messages": chat_history,
        "max_tokens": 512,
        "stream": False,  # Set to False to receive a single JSON response instead of SSE
    }

    full_response = ""
    async with aiohttp.ClientSession() as session:
        async with session.post(url, headers=headers, json=payload) as resp:
            if resp.status != 200:
                print(f"Server Error {resp.status}: {await resp.text()}")
                chat_history.pop()  # remove failed message
                return

            # Since standard OpenAI format returns JSON
            data = await resp.json()
            full_response = data["choices"][0]["message"]["content"]

            # Append assistant response to history
            chat_history.append({"role": "assistant", "content": full_response})

    if full_response.strip():
        print(f"Gemma4 Response: {full_response}")
        # Send text back to frontend via data channel
        print("Sending text to frontend...")
        payload = full_response.encode("utf-8")
        await ctx.room.local_participant.publish_data(payload, reliable=True)


@server.rtc_session()
async def my_agent(ctx: agents.JobContext):
    print("Initializing agent...")

    # We will use Silero VAD to detect speech
    vad = VAD.load()

    import collections

    async def process_track(track: rtc.RemoteAudioTrack):
        print("Subscribed to user audio track")
        audio_stream = rtc.AudioStream(track)
        vad_stream = vad.stream()

        audio_buffer = []
        is_speaking = False
        pre_buffer = collections.deque(maxlen=100)  # half a second of pre-roll buffer

        async def _push_frames():
            async for event in audio_stream:
                vad_stream.push_frame(event.frame)
                if is_speaking:
                    audio_buffer.append(event.frame)
                else:
                    pre_buffer.append(event.frame)

        async def _read_vad():
            nonlocal is_speaking, audio_buffer
            async for vad_evt in vad_stream:
                if vad_evt.type == agents.vad.VADEventType.START_OF_SPEECH:
                    is_speaking = True
                    audio_buffer = list(pre_buffer)
                    pre_buffer.clear()
                    print("Speech started...")
                elif vad_evt.type == agents.vad.VADEventType.END_OF_SPEECH:
                    is_speaking = False
                    print("Speech ended, processing audio chunk...")
                    if audio_buffer:
                        base64_audio = encode_audio_base64(audio_buffer)
                        asyncio.create_task(stream_audio_to_gemma(base64_audio, ctx))
                    audio_buffer = []

        await asyncio.gather(_push_frames(), _read_vad())

    # Listen to remote tracks (Register BEFORE connecting!)
    @ctx.room.on("track_subscribed")
    def on_track_subscribed(
        track: rtc.Track,
        publication: rtc.RemoteTrackPublication,
        participant: rtc.RemoteParticipant,
    ):
        if track.kind == rtc.TrackKind.KIND_AUDIO:
            asyncio.create_task(process_track(track))

    await ctx.connect()
    print("Agent joined room.")

    # Catch any tracks that were already subscribed during connection
    for participant in ctx.room.remote_participants.values():
        for publication in participant.track_publications.values():
            if publication.track and publication.track.kind == rtc.TrackKind.KIND_AUDIO:
                asyncio.create_task(process_track(publication.track))

    # Keep alive
    try:
        await asyncio.Event().wait()
    except asyncio.CancelledError:
        pass


if __name__ == "__main__":
    agents.cli.run_app(server)
