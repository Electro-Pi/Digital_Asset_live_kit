"""
MuseTalk LiveKit avatar worker.

Joins a LiveKit room as a second participant and publishes a lip-synced
video+audio stream driven by the agent's TTS audio (received via DataStream).

Usage (called by worker_dispatcher.py):
    LIVEKIT_URL=wss://... LIVEKIT_API_KEY=API... LIVEKIT_API_SECRET=...
    AVATAR_ROOM=room-name AGENT_IDENTITY=agent-identity AVATAR_NAME=sample
    python worker.py

Environment variables:
    LIVEKIT_URL           LiveKit server WebSocket URL
    LIVEKIT_API_KEY       LiveKit API key
    LIVEKIT_API_SECRET    LiveKit API secret
    AVATAR_ROOM           Room name to join (omit when MUSETALK_ASSIGN_VIA_STDIN=1)
    AGENT_IDENTITY        Identity of the agent participant (omit when stdin assign)
    AVATAR_NAME           Name of baked avatar dir under avatars/ (default: sample)
    MUSETALK_ASSIGN_VIA_STDIN  If true: print MUSETALK_STANDBY_READY then read JSON line
                          {"room","agent_identity","avatar"} from stdin (dispatcher GPU standby).
    MUSETALK_STANDBY_AVATAR    Avatar used for initial warmup in stdin mode (default: AVATAR_NAME or sample)
    MUSETALK_BATCH_SIZE   UNet batch size (default: 1)
    CUDA_VISIBLE_DEVICES  GPU index (default: 0)
    MUSETALK_VIDEO_FPS    Output stream fps (default: 30). Must divide 16000 evenly for
                          lip-synced PCM; e.g. 30 → 32 fps. Common: 25, 32, 40, 50.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import AsyncIterator

WORKER_DIR = Path(__file__).parent.resolve()
UPSTREAM_DIR = WORKER_DIR / "upstream"
sys.path.insert(0, str(UPSTREAM_DIR))

from dotenv import load_dotenv
from loguru import logger

load_dotenv(WORKER_DIR / ".env")
load_dotenv(WORKER_DIR.parent / ".env.local", override=False)

from livekit import api, rtc
from livekit.agents.voice.avatar import (
    AvatarOptions,
    AvatarRunner,
    AudioSegmentEnd,
    DataStreamAudioReceiver,
    VideoGenerator,
)

from musetalk_engine import MuseTalkEngine

AVATAR_IDENTITY = "musetalk-avatar"


class MuseTalkVideoGenerator(VideoGenerator):
    """
    Bridges LiveKit's VideoGenerator ABC to the MuseTalkEngine.

    AvatarRunner calls:
      - push_audio(frame) to deliver TTS audio from the DataStream
      - __aiter__() to read back video+audio frames for publishing
      - clear_buffer() on interruption
    """

    def __init__(self, engine: MuseTalkEngine) -> None:
        self._engine = engine
        self._audio_queue: asyncio.Queue[rtc.AudioFrame] = asyncio.Queue()
        self._segment_end_queue: asyncio.Queue[AudioSegmentEnd] = asyncio.Queue()

    async def push_audio(self, frame: rtc.AudioFrame | AudioSegmentEnd) -> None:
        if isinstance(frame, AudioSegmentEnd):
            await self._engine.flush()
            await self._segment_end_queue.put(AudioSegmentEnd())
            return
        await self._audio_queue.put(frame)

    async def clear_buffer(self) -> None:
        while not self._audio_queue.empty():
            try:
                self._audio_queue.get_nowait()
            except asyncio.QueueEmpty:
                break
        while not self._segment_end_queue.empty():
            try:
                self._segment_end_queue.get_nowait()
            except asyncio.QueueEmpty:
                break
        await self._engine.reset()
        logger.debug("MuseTalk buffer cleared (interruption)")

    async def __aiter__(self) -> AsyncIterator[rtc.VideoFrame | rtc.AudioFrame | AudioSegmentEnd]:
        pump_task = asyncio.create_task(self._audio_pump())
        stream_it = self._engine.stream_frames().__aiter__()
        try:
            while True:
                while True:
                    try:
                        yield self._segment_end_queue.get_nowait()
                    except asyncio.QueueEmpty:
                        break
                bgr, pcm_40ms = await stream_it.__anext__()

                h, w = bgr.shape[:2]
                rgb = bgr[:, :, ::-1].copy()
                yield rtc.VideoFrame(
                    width=w,
                    height=h,
                    type=rtc.VideoBufferType.RGB24,
                    data=rgb.tobytes(),
                )
                yield rtc.AudioFrame(
                    data=pcm_40ms,
                    sample_rate=MuseTalkEngine.SAMPLE_RATE,
                    num_channels=1,
                    samples_per_channel=(MuseTalkEngine.SAMPLE_RATE // self._engine._fps),
                )
        finally:
            pump_task.cancel()
            try:
                await pump_task
            except asyncio.CancelledError:
                pass

    async def _audio_pump(self) -> None:
        """Forward audio frames from the LiveKit DataStream into the engine."""
        while True:
            frame = await self._audio_queue.get()
            await self._engine.push_audio(bytes(frame.data), frame.sample_rate)


def _frame_size(engine: MuseTalkEngine) -> tuple[int, int]:
    if engine._frame_list_cycle:
        h, w = engine._frame_list_cycle[0].shape[:2]
        return w, h
    return 256, 256


async def run_worker() -> None:
    url = os.environ["LIVEKIT_URL"]
    api_key = os.environ["LIVEKIT_API_KEY"]
    api_secret = os.environ["LIVEKIT_API_SECRET"]
    assign_via_stdin = os.getenv("MUSETALK_ASSIGN_VIA_STDIN", "").lower() in (
        "1",
        "true",
        "yes",
    )
    if assign_via_stdin:
        room_name = None
        agent_identity = None
        # Preload avatar baked assets for GPU warmup; launch JSON may switch avatar later.
        avatar_name = os.getenv(
            "MUSETALK_STANDBY_AVATAR", os.getenv("AVATAR_NAME", "sample")
        )
    else:
        room_name = os.environ["AVATAR_ROOM"]
        agent_identity = os.environ["AGENT_IDENTITY"]
        avatar_name = os.environ.get("AVATAR_NAME", "sample")

    # Standby handshake uses stdout for a single line; MuseTalk prints loading progress
    # to stdout unless we redirect during warmup (otherwise the dispatcher reads garbage).
    dispatcher_stdout = sys.stdout if assign_via_stdin else None
    if assign_via_stdin:
        sys.stdout = sys.stderr

    batch_size = int(os.environ.get("MUSETALK_BATCH_SIZE", "1"))
    # Defaulting to 30 typically coerces to 32fps (16kHz alignment), which requires
    # <31ms/frame end-to-end. Our runtime logs show inference often exceeds that,
    # causing accumulated lag and audible stutter. A safer default is 16fps.
    video_fps = int(os.environ.get("MUSETALK_VIDEO_FPS", "20"))

    avatar_dir = WORKER_DIR / "avatars" / avatar_name
    if not (avatar_dir / "meta.json").exists():
        logger.error(
            f"Avatar '{avatar_name}' not found at {avatar_dir}. Run prepare_avatar.py first."
        )
        sys.exit(1)

    logger.info(f"Loading MuseTalk engine (avatar={avatar_name}, batch_size={batch_size}) ...")
    engine = MuseTalkEngine(avatar_dir=avatar_dir, batch_size=batch_size, fps=video_fps)
    stream_fps = float(engine._fps)
    if int(stream_fps) != video_fps:
        logger.info(f"MUSETALK_VIDEO_FPS={video_fps} adjusted to {stream_fps} for 16 kHz A/V alignment")
    await engine.warmup()
    logger.info("Engine ready.")

    if assign_via_stdin:
        assert dispatcher_stdout is not None
        print("MUSETALK_STANDBY_READY", file=dispatcher_stdout, flush=True)
        line = await asyncio.to_thread(sys.stdin.readline)
        if not line.strip():
            logger.error("Standby worker received no room assignment on stdin — exiting.")
            sys.exit(1)
        try:
            cfg = json.loads(line)
        except json.JSONDecodeError as e:
            logger.error(f"Invalid assignment JSON on stdin: {e}")
            sys.exit(1)
        room_name = cfg["room"]
        agent_identity = cfg["agent_identity"]
        req_avatar = cfg.get("avatar", avatar_name)
        if req_avatar != avatar_name:
            avatar_name = req_avatar
            avatar_dir = WORKER_DIR / "avatars" / avatar_name
            if not (avatar_dir / "meta.json").exists():
                logger.error(
                    f"Avatar '{avatar_name}' not found at {avatar_dir}. Run prepare_avatar.py first."
                )
                sys.exit(1)
            engine._avatar_dir = avatar_dir
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, engine._load_avatar)
            await loop.run_in_executor(None, engine._dummy_forward)
            logger.info(f"Standby switched to avatar={avatar_name!r}")
        logger.info(
            f"Assignment received: room={room_name!r} agent_identity={agent_identity!r}"
        )

    assert room_name is not None and agent_identity is not None

    token = (
        api.AccessToken(api_key=api_key, api_secret=api_secret)
        .with_identity(AVATAR_IDENTITY)
        .with_name("Avatar")
        .with_grants(
            api.VideoGrants(
                room=room_name,
                room_join=True,
                can_publish=True,
                can_subscribe=True,
                can_publish_data=True,
            )
        )
        .with_attributes({"lk.publish_on_behalf": agent_identity})
        .to_jwt()
    )

    room = rtc.Room()

    @room.on("disconnected")
    def _on_disconnect(reason: object) -> None:
        logger.warning(f"Room disconnected: {reason}")

    logger.info(f"Connecting to room '{room_name}' as '{AVATAR_IDENTITY}' ...")
    await room.connect(url, token)
    logger.info("Connected.")

    vw, vh = _frame_size(engine)
    video_gen = MuseTalkVideoGenerator(engine=engine)
    audio_recv = DataStreamAudioReceiver(
        room=room,
        sender_identity=agent_identity,
    )

    runner = AvatarRunner(
        room=room,
        audio_recv=audio_recv,
        video_gen=video_gen,
        options=AvatarOptions(
            video_width=vw,
            video_height=vh,
            video_fps=stream_fps,
            audio_sample_rate=MuseTalkEngine.SAMPLE_RATE,
            audio_channels=1,
        ),
        _queue_size_ms=600,
    )

    logger.info("Starting AvatarRunner ...")
    await runner.start()
    await runner.wait_for_complete()
    await runner.aclose()
    await room.disconnect()
    logger.info("Worker exiting cleanly.")


if __name__ == "__main__":
    asyncio.run(run_worker())
