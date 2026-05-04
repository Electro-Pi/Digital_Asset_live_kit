import aiohttp
import asyncio
import io
import wave
import logging
from typing import List
from livekit import rtc
from livekit.agents import stt

logger = logging.getLogger(__name__)

class CustomVLLMSTT(stt.STT):
    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str = "no-key",
        language: str = "ar",
        temperature: float = 0.0,
        task: str = "transcribe",
        prompt: str = "",
    ):
        # We tell LiveKit this STT waits for the full sentence before returning text
        super().__init__(
            capabilities=stt.STTCapabilities(streaming=False, interim_results=False)
        )
        self.base_url = base_url.rstrip("/")
        self.custom_model = model
        self.api_key = api_key
        self.language = language
        self.temperature = temperature
        self.task = task
        self.prompt = prompt

    async def _recognize_impl(self, buffer, *, language: str | None = None, **kwargs) -> stt.SpeechEvent:
        # 1. Convert LiveKit's AudioBuffer to a WAV file in memory
        wav_io = io.BytesIO()
        with wave.open(wav_io, "wb") as wav:
            wav.setnchannels(buffer.num_channels)
            wav.setsampwidth(2)  # 16-bit PCM
            wav.setframerate(buffer.sample_rate)
            wav.writeframes(buffer.data.tobytes())
        
        wav_bytes = wav_io.getvalue()

        # 2. Build our Custom HTTP Request
        async with aiohttp.ClientSession() as session:
            data = aiohttp.FormData()
            data.add_field('file', wav_bytes, filename='audio.wav', content_type='audio/wav')
            data.add_field('model', self.custom_model)
            data.add_field('language', language or self.language)
            data.add_field('temperature', str(self.temperature))
            data.add_field('task', self.task)
            
            if self.prompt:
                data.add_field('prompt', self.prompt)

            headers = {"Authorization": f"Bearer {self.api_key}"}
            url = f"{self.base_url}/audio/transcriptions"
            
            started_at = asyncio.get_event_loop().time()
            
            # 3. Send to vLLM Server
            async with session.post(url, data=data, headers=headers) as resp:
                resp.raise_for_status()
                result = await resp.json()
                text = result.get("text", "").strip()
                
                duration = asyncio.get_event_loop().time() - started_at
                logger.info(f"Custom STT Transcribed in {duration:.2f}s: '{text}'")
                
                # 4. Return the transcription back to the agent
                return stt.SpeechEvent(
                    type=stt.SpeechEventType.FINAL_TRANSCRIPT,
                    alternatives=[stt.SpeechData(text=text, language=language or self.language)]
                )

    def stream(self) -> "CustomVLLMStream":
        return CustomVLLMStream(self)


class CustomVLLMStream(stt.SpeechStream):
    def __init__(self, stt_instance: CustomVLLMSTT):
        super().__init__(stt=stt_instance)
        self._stt = stt_instance
        self._audio_frames: List[rtc.AudioFrame] = []

    def push_frame(self, frame: rtc.AudioFrame):
        # Collect audio frames sequentially while the user is speaking
        self._audio_frames.append(frame)

    async def flush(self):
        # When VAD detects the user stopped speaking, flush() is called to process the audio
        if not self._audio_frames:
            return

        try:
            # 1. Extract audio specs
            sample_rate = self._audio_frames[0].sample_rate
            num_channels = self._audio_frames[0].num_channels
            
            # 2. Merge all audio frames into raw bytes
            audio_data = bytearray()
            for frame in self._audio_frames:
                audio_data.extend(frame.data.tobytes())

            # 3. Convert raw bytes to a WAV file in memory
            wav_io = io.BytesIO()
            with wave.open(wav_io, "wb") as wav:
                wav.setnchannels(num_channels)
                wav.setsampwidth(2)  # 16-bit PCM (Standard)
                wav.setframerate(sample_rate)
                wav.writeframes(audio_data)
            
            wav_bytes = wav_io.getvalue()

            # 4. Build our Unrestricted Custom HTTP Request
            async with aiohttp.ClientSession() as session:
                data = aiohttp.FormData()
                data.add_field('file', wav_bytes, filename='audio.wav', content_type='audio/wav')
                data.add_field('model', self._stt.model)
                data.add_field('language', self._stt.language)
                
                # Here are your custom parameters!
                data.add_field('temperature', str(self._stt.temperature))
                data.add_field('task', self._stt.task)
                
                if self._stt.prompt:
                    data.add_field('prompt', self._stt.prompt)

                headers = {"Authorization": f"Bearer {self._stt.api_key}"}
                url = f"{self._stt.base_url}/audio/transcriptions"
                
                started_at = asyncio.get_event_loop().time()
                
                # 5. Send to vLLM Server
                async with session.post(url, data=data, headers=headers) as resp:
                    resp.raise_for_status()
                    result = await resp.json()
                    text = result.get("text", "").strip()
                    
                    duration = asyncio.get_event_loop().time() - started_at
                    logger.info(f"Custom STT Transcribed in {duration:.2f}s: '{text}'")
                    
                    if text:
                        # 6. Push the successful text back into the LiveKit Agent's brain
                        event = stt.SpeechEvent(
                            type=stt.SpeechEventType.FINAL_TRANSCRIPT,
                            alternatives=[stt.SpeechData(text=text, language=self._stt.language)]
                        )
                        self._event_ch.send_nowait(event)

        except Exception as e:
            logger.error(f"Custom STT failed: {e}")
        finally:
            # Always clear the audio buffer for the next sentence
            self._audio_frames = []

    async def aclose(self, wait: bool = True):
        if wait:
            await self.flush()