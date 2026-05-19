import asyncio
import wave

from loguru import logger
from livekit import rtc

SAMPLE_RATE = 16000
CHANNELS = 1
FRAME_DURATION = 0.02  # 20ms
SAMPLES_PER_CHANNEL = int(SAMPLE_RATE * FRAME_DURATION)


async def publish_background_noise(room: rtc.Room):
    try:
        logger.info("Starting background noise publisher...")

        source = rtc.AudioSource(
            SAMPLE_RATE,
            CHANNELS,
        )

        track = rtc.LocalAudioTrack.create_audio_track(
            "office-background-noise",
            source,
        )

        publish_options = rtc.TrackPublishOptions()
        publish_options.source = rtc.TrackSource.SOURCE_MICROPHONE

        publication = await room.local_participant.publish_track(
            track,
            publish_options,
        )

        logger.info(
            f"Background noise track published successfully: {publication.sid}"
        )

        wav_file_path = "office_low.wav"

        wf = wave.open(wav_file_path, "rb")

        logger.info(f"Streaming background audio from: {wav_file_path}")

        while True:
            pcm_data = wf.readframes(SAMPLES_PER_CHANNEL)

            if len(pcm_data) == 0:
                logger.info("Looping background audio...")
                wf.rewind()
                continue

            frame = rtc.AudioFrame(
                data=pcm_data,
                sample_rate=SAMPLE_RATE,
                num_channels=CHANNELS,
                samples_per_channel=SAMPLES_PER_CHANNEL,
            )

            await source.capture_frame(frame)

            await asyncio.sleep(FRAME_DURATION)

    except Exception as e:
        logger.exception(f"Background noise publisher crashed: {e}")
        
        
# ffmpeg -i office.mp3 \
# -ar 16000 \
# -ac 1 \
# -c:a pcm_s16le \
# -filter:a "volume=0.05" \
# office_low.wav