import asyncio
import os
import time
from collections.abc import AsyncGenerator, AsyncIterable
from typing import Any

import httpx
from dotenv import load_dotenv
from loguru import logger

from livekit import agents, api as lk_api, rtc
from livekit.agents.job import DEFAULT_PARTICIPANT_KINDS
from livekit.agents.types import ATTRIBUTE_PUBLISH_ON_BEHALF
from livekit.agents import (
    Agent,
    AgentServer,
    AgentSession,
    ModelSettings,
    RunContext,
    function_tool,
    llm,
    room_io,
    tts,
    utils,
    AgentStateChangedEvent,
    MetricsCollectedEvent,
    ToolError,
)
from livekit.plugins import noise_cancellation, silero, openai
from livekit.plugins.turn_detector.multilingual import MultilingualModel
from livekit.agents.voice.avatar import DataStreamAudioOutput
from livekit.protocol.models import ParticipantInfo
from livekit.protocol.room import ListParticipantsRequest, RoomParticipantIdentity

from src.config.omnivoice_najdi_tts import najdi_omnivoice_speech_extra_body
from src.plugins.openai_stt_with_temperature import STTWithTemperature
from src.plugins.openai_tts_with_extra_body import OpenAITTSWithSpeechExtraBody
from src.plugins.whole_utterance_sentence_tokenizer import WholeUtteranceSentenceTokenizer
from src.prompts.agent_prompt import system_prompt


load_dotenv(".env.local")

MUSETALK_AVATAR_IDENTITY = "musetalk-avatar"
MUSETALK_DISPATCHER_URL = os.getenv(
    "MUSETALK_DISPATCHER_URL", "http://127.0.0.1:7980"
).rstrip("/")

GREETING_INSTRUCTIONS = (
    "ابدأ بالترحيب بالعميل في مطعم فينيرو، "
    "واسأله بشكل طبيعي: وش تحب تطلب اليوم؟"
)

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

    async def tts_node(
        self,
        text: AsyncIterable[str],
        model_settings: ModelSettings,
    ) -> AsyncGenerator[rtc.AudioFrame, None]:
        """Single TTS HTTP call per segment (default BlingFire splits = multi-second gaps)."""
        activity = self._get_activity_or_raise()
        if activity.tts is None:
            raise RuntimeError(
                "`tts_node` called but no TTS node is available. If audio output is not needed, "
                "disable it using `session.output.set_audio_enabled(False)`."
            )

        wrapped_tts = activity.tts
        if not activity.tts.capabilities.streaming:
            wrapped_tts = tts.StreamAdapter(
                tts=wrapped_tts,
                sentence_tokenizer=WholeUtteranceSentenceTokenizer(),
            )

        conn_options = activity.session.conn_options.tts_conn_options
        async with wrapped_tts.stream(conn_options=conn_options) as stream:
            async def _forward_input() -> None:
                async for chunk in text:
                    stream.push_text(chunk)

                stream.end_input()

            forward_task = asyncio.create_task(_forward_input())
            try:
                async for ev in stream:
                    yield ev.frame
            finally:
                await utils.aio.cancel_and_wait(forward_task)

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


server = AgentServer(
    # Dev default is 0 idle processes, which delays the first job while a worker
    # subprocess is forked. Keep one warmed process so the first room join is fast.
    num_idle_processes=int(os.getenv("LIVEKIT_AGENT_IDLE_PROCESSES", "1")),
)
AGENT_NAME = os.getenv("LIVEKIT_AGENT_NAME", "musetalk-agent")


async def _dedupe_stale_agents_room_api(
    room_name: str,
    keep_participant_identity: str,
    registered_agent_name: str,
) -> None:
    """Evict duplicate cloud agent participants and collapse duplicate dispatches.

    Two ``agent-AJ_*`` jobs in one room break MuseTalk (``lk.clear_buffer`` RPC
    timeouts) because the avatar worker is tied to a single agent identity.
    """
    try:
        lk = lk_api.LiveKitAPI()
    except ValueError as e:
        logger.debug(f"LiveKitAPI unavailable for room dedupe: {e}")
        return

    try:
        lp = await lk.room.list_participants(ListParticipantsRequest(room=room_name))
        for p in lp.participants:
            if p.identity == keep_participant_identity:
                continue
            if p.identity == MUSETALK_AVATAR_IDENTITY:
                continue
            if p.kind != ParticipantInfo.Kind.AGENT:
                continue
            try:
                await lk.room.remove_participant(
                    RoomParticipantIdentity(room=room_name, identity=p.identity)
                )
                logger.warning(
                    f"Removed duplicate/stale agent participant {p.identity!r} from room {room_name!r}"
                )
            except Exception as e:
                logger.warning(
                    f"remove_participant failed for {p.identity!r} in {room_name!r}: {e}"
                )

        dispatches = await lk.agent_dispatch.list_dispatch(room_name)
        ours = [d for d in dispatches if d.agent_name == registered_agent_name]
        if len(ours) <= 1:
            return

        def _sort_key(d):
            created = int(d.state.created_at) if d.state and d.state.created_at else 0
            return (created, d.id)

        for dup in sorted(ours, key=_sort_key)[1:]:
            try:
                await lk.agent_dispatch.delete_dispatch(dup.id, room_name)
                logger.warning(
                    f"Deleted duplicate agent dispatch {dup.id!r} in room {room_name!r}"
                )
            except Exception as e:
                logger.warning(f"delete_dispatch {dup.id!r} failed: {e}")
    except Exception as e:
        logger.warning(f"room admin dedupe skipped: {e}")
    finally:
        await lk.aclose()


@server.rtc_session(agent_name=AGENT_NAME)
async def my_agent(ctx: agents.JobContext):
    muse_talk_enabled = os.getenv("ENABLE_MUSETALK", "1").lower() not in (
        "0",
        "false",
        "no",
    )
    musetalk_worker_running = False
    use_musetalk_datastream = False
    # Use the *actual* RTC room name (viewer room), not the job id / job room alias.
    # We'll set this after connect().
    _musetalk_room_name: str | None = None

    if not ctx.room.isconnected():
        await ctx.connect()
    await _dedupe_stale_agents_room_api(
        ctx.room.name,
        ctx.room.local_participant.identity,
        AGENT_NAME,
    )

    async def _dispatcher_launch(room_name: str, agent_identity: str) -> None:
        async with httpx.AsyncClient() as client:
            r = await client.post(
                f"{MUSETALK_DISPATCHER_URL}/launch",
                json={
                    "room": room_name,
                    "agent_identity": agent_identity,
                    "avatar": os.getenv("AVATAR_NAME", "sample"),
                },
                timeout=60.0,
            )
            r.raise_for_status()

    async def _dispatcher_stop(room_name: str) -> None:
        try:
            async with httpx.AsyncClient() as client:
                await client.post(
                    f"{MUSETALK_DISPATCHER_URL}/stop",
                    json={"room": room_name},
                    timeout=10.0,
                )
        except Exception as e:
            logger.warning(f"MuseTalk dispatcher /stop failed: {e}")

    async def _wait_musetalk_avatar(timeout: float = 180.0) -> None:
        for p in ctx.room.remote_participants.values():
            if p.identity == MUSETALK_AVATAR_IDENTITY:
                return
        fut: asyncio.Future[None] = asyncio.get_running_loop().create_future()

        def _on_participant_connected(p: rtc.RemoteParticipant) -> None:
            if p.identity == MUSETALK_AVATAR_IDENTITY and not fut.done():
                fut.set_result(None)

        ctx.room.on("participant_connected", _on_participant_connected)
        try:
            await asyncio.wait_for(fut, timeout=timeout)
        finally:
            ctx.room.off("participant_connected", _on_participant_connected)

    if muse_talk_enabled:
        try:
            # rtc.Room is not connected at entrypoint; connect before reading
            # local_participant or waiting for musetalk-avatar. AgentSession.start()
            # also schedules connect(); a second call is a no-op.
            if not ctx.room.isconnected():
                await ctx.connect()

            _musetalk_room_name = ctx.room.name
            await _dispatcher_launch(
                _musetalk_room_name,
                ctx.room.local_participant.identity,
            )
            await _wait_musetalk_avatar()
            use_musetalk_datastream = True
        except Exception:
            logger.exception(
                "MuseTalk setup failed — continuing with default agent audio output"
            )
            if _musetalk_room_name:
                await _dispatcher_stop(_musetalk_room_name)

    session_kwargs: dict = {
        "stt": STTWithTemperature(
            base_url="http://8.213.50.155:8002/v1",
            model="/home/ai/vllm-sera/models/cohere-transcribe",
            api_key="no-key-needed",
            language="ar",
            temperature=0.0,
        ),
        "llm": openai.LLM(
            base_url="http://8.213.50.155:8001/v1",
            model="gemma4",
            api_key="needed",
        ),
        # OpenAI TTS: "tts-1"/"tts-1-hd" use binary PCM; other model names use SSE parsing.
        # Najdi tuning: src/config/omnivoice_najdi_tts.py (OmniVoice ``ars`` + generation_*).
        "tts": OpenAITTSWithSpeechExtraBody(
            speech_extra_body=najdi_omnivoice_speech_extra_body(),
            model="tts-1",
            voice="clone:voice_ronn",
            base_url="http://127.0.0.1:8188/v1",
            api_key="no-key-needed",
            response_format="pcm",
            speed=1.3,
        ),
        "vad": silero.VAD.load(),
        "turn_detection": MultilingualModel(),
    }
    if use_musetalk_datastream:
        # DataStreamAudioOutput cannot pause; default resume_false_interruption
        # causes LiveKit to warn and can yield choppy playback.
        session_kwargs["turn_handling"] = {
            "interruption": {"resume_false_interruption": False},
        }

    session = AgentSession(**session_kwargs)
    assistant_agent = Assistant()

    if session.tts is not None:
        session.tts.prewarm()

    if use_musetalk_datastream:
        session.output.audio = DataStreamAudioOutput(
            room=ctx.room,
            destination_identity=MUSETALK_AVATAR_IDENTITY,
            sample_rate=None,
        )
        musetalk_worker_running = True
        logger.info(
            "MuseTalk avatar: TTS audio redirected via DataStream to "
            f"{MUSETALK_AVATAR_IDENTITY}"
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

    async def _shutdown_musetalk() -> None:
        if musetalk_worker_running and _musetalk_room_name:
            await _dispatcher_stop(_musetalk_room_name)

    ctx.add_shutdown_callback(_shutdown_musetalk)

    await session.start(
        room=ctx.room,
        agent=assistant_agent,
        room_options=room_io.RoomOptions(
            # Keep the agent (and MuseTalk worker) alive across viewer "Rejoin".
            # Otherwise the session closes immediately when the browser disconnects,
            # leaving no remote audio/video publishers to subscribe to after reconnect.
            close_on_disconnect=False,
            audio_input=room_io.AudioInputOptions(
                noise_cancellation=lambda params:
                    noise_cancellation.BVCTelephony()
                    if params.participant.kind
                    == rtc.ParticipantKind.PARTICIPANT_KIND_SIP
                    else noise_cancellation.BVC()
            ),
        ),
    )

    # RoomIO only awaits the *first* user participant once in its init task. With
    # close_on_disconnect=False, when the viewer leaves and a new identity joins,
    # we must explicitly re-target mic/STT to the new participant or audio stays
    # bound to the disconnected viewer (silent rejoin).
    def _is_user_viewer(p: rtc.RemoteParticipant) -> bool:
        if p.identity == MUSETALK_AVATAR_IDENTITY:
            return False
        if (
            p.attributes.get(ATTRIBUTE_PUBLISH_ON_BEHALF)
            == ctx.room.local_participant.identity
        ):
            return False
        return p.kind in DEFAULT_PARTICIPANT_KINDS

    # When the human viewer disconnects and joins again, restart the flow from
    # the beginning (clear LLM history + new welcome), while keeping the same
    # agent job / MuseTalk worker (close_on_disconnect=False).
    _viewer_left_room = False
    _rejoin_greeting_task: asyncio.Task[None] | None = None

    def _on_user_viewer_disconnected(p: rtc.RemoteParticipant) -> None:
        nonlocal _viewer_left_room
        if not _is_user_viewer(p):
            return
        _viewer_left_room = True

    def _on_viewer_rejoin_for_room_io(p: rtc.RemoteParticipant) -> None:
        nonlocal _viewer_left_room, _rejoin_greeting_task
        if not _is_user_viewer(p):
            return
        try:
            session.room_io.set_participant(p.identity)
        except Exception:
            logger.exception(
                "Failed to re-link RoomIO to participant after (re)connect",
                extra={"identity": p.identity},
            )
        if not _viewer_left_room:
            return
        _viewer_left_room = False

        async def _conversation_fresh_start_for_rejoin() -> None:
            try:
                await asyncio.wait_for(session.interrupt(force=True), timeout=8.0)
            except Exception:
                pass
            try:
                session.clear_user_turn()
            except Exception:
                pass
            try:
                await assistant_agent.update_chat_ctx(llm.ChatContext.empty())
            except Exception:
                logger.exception("rejoin: failed to reset chat context")
            try:
                await session.generate_reply(instructions=GREETING_INSTRUCTIONS)
            except Exception:
                logger.exception("rejoin: failed to deliver greeting")

        if _rejoin_greeting_task is not None and not _rejoin_greeting_task.done():
            _rejoin_greeting_task.cancel()
        _rejoin_greeting_task = asyncio.create_task(_conversation_fresh_start_for_rejoin())

    ctx.room.on("participant_connected", _on_viewer_rejoin_for_room_io)
    ctx.room.on("participant_disconnected", _on_user_viewer_disconnected)

    async def _off_viewer_rejoin_hook() -> None:
        ctx.room.off("participant_connected", _on_viewer_rejoin_for_room_io)
        ctx.room.off("participant_disconnected", _on_user_viewer_disconnected)
        if _rejoin_greeting_task is not None and not _rejoin_greeting_task.done():
            _rejoin_greeting_task.cancel()
            try:
                await _rejoin_greeting_task
            except asyncio.CancelledError:
                pass

    ctx.add_shutdown_callback(_off_viewer_rejoin_hook)

    await session.generate_reply(instructions=GREETING_INSTRUCTIONS)


if __name__ == "__main__":
    agents.cli.run_app(server)