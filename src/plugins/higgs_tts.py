import httpx
import json
import base64
import re
import struct
import uuid
from pathlib import Path
from loguru import logger
from livekit.agents import tts, APIConnectOptions

SAMPLE_RATE = 24000
NUM_CHANNELS = 1

# Default to the local Windows data folder
_REF_AUDIO_PATH = "data/sera_reference.wav"
_REF_TEXT = "هلا والله، أنا سيرا، مساعدتك الذكية من الهيئة السعودية لتنظيم الكهرباء."

# Regex to catch hallucinated bracket tags
_BRACKET_TAG = re.compile(r'\[[^\[\]]{1,40}\]')

def _clean_text(text: str) -> str:
    if not text:
        return ""
    text = _BRACKET_TAG.sub('', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text

def _strip_wav_header(data: bytes) -> bytes:
    if len(data) < 12 or data[0:4] != b"RIFF" or data[8:12] != b"WAVE":
        return data
    pos = 12
    while pos + 8 <= len(data):
        chunk_id = data[pos:pos + 4]
        chunk_size = struct.unpack("<I", data[pos + 4:pos + 8])[0]
        body_start = pos + 8
        if chunk_id == b"data":
            return data[body_start:body_start + chunk_size]
        pos = body_start + chunk_size + (chunk_size % 2)
    return data[44:] if len(data) > 44 else b""

def _to_data_url(path_or_url: str) -> str:
    if path_or_url.startswith(("http://", "https://", "data:")):
        return path_or_url
    
    path = Path(path_or_url)
    if not path.exists():
        raise FileNotFoundError(f"Reference audio file not found at: {path.absolute()}")
        
    with open(path, "rb") as f:
        encoded = base64.b64encode(f.read()).decode("utf-8")
        
    return f"data:audio/wav;base64,{encoded}"


class HiggsTTS(tts.TTS):
    def __init__(
        self,
        *,
        base_url: str,
        model: str = "bosonai/higgs-audio-v3-tts-4b",
        reference_audio_path: str = _REF_AUDIO_PATH,
        reference_text: str = _REF_TEXT,
        api_key: str = "no-key",
        temperature: float = 0.0001,
        top_k: int = 1,
        top_p: float = 1.0,
        seed: int = 42,
        max_new_tokens: int = 2048,
        connect_timeout: float = 5.0,
        stream_timeout: float = 30.0,
    ):
        super().__init__(
            capabilities=tts.TTSCapabilities(streaming=False),
            sample_rate=SAMPLE_RATE,
            num_channels=NUM_CHANNELS,
        )
        
        base_url = base_url.rstrip("/")
        if base_url.endswith("/v1"):
            base_url = base_url[:-3]
        self._base_url = base_url

        self._model = model
        self._api_key = api_key
        
        self._ref_audio_data_url = _to_data_url(reference_audio_path)
        self._ref_text = reference_text.strip()
        
        self._temperature = temperature
        self._top_k = top_k
        self._top_p = top_p
        self._seed = seed
        self._max_new_tokens = max_new_tokens
        self._connect_timeout = connect_timeout
        self._stream_timeout = stream_timeout

        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(
                connect=self._connect_timeout,
                read=self._stream_timeout,
                write=10.0,
                pool=self._stream_timeout,
            ),
            limits=httpx.Limits(
                max_connections=300,
                max_keepalive_connections=200,
                keepalive_expiry=30,
            ),
            http2=False,
        )

    async def aclose(self) -> None:
        await self._client.aclose()
        await super().aclose()

    def synthesize(self, text: str, *, conn_options: APIConnectOptions) -> tts.ChunkedStream:
        return HiggsChunkedStream(tts=self, input_text=text, conn_options=conn_options)


class HiggsChunkedStream(tts.ChunkedStream):
    def __init__(self, *, tts: HiggsTTS, input_text: str, conn_options: APIConnectOptions):
        super().__init__(tts=tts, input_text=input_text, conn_options=conn_options)
        self._higgs: HiggsTTS = tts

    async def _run(self, output_emitter: tts.AudioEmitter) -> None:
        request_id = getattr(self._conn_options, "request_id", None) or str(uuid.uuid4())

        output_emitter.initialize(
            request_id=request_id,
            sample_rate=SAMPLE_RATE,
            num_channels=NUM_CHANNELS,
            mime_type="audio/pcm",
        )

        text = _clean_text(self.input_text)
        logger.debug(f"HiggsTTS input text: {text!r}")
        
        if not text:
            output_emitter.flush()
            return

        payload = {
            "input": text,
            "model": self._higgs._model,
            "temperature": self._higgs._temperature,
            "top_k": self._higgs._top_k,
            "top_p": self._higgs._top_p,
            "seed": self._higgs._seed,
            "max_new_tokens": self._higgs._max_new_tokens,
            "stream": True,
            "response_format": "pcm",  # <-- REQUIRED TO TRIGGER SGLANG BINARY STREAMING
            "references": [{
                "audio_path": self._higgs._ref_audio_data_url,
                "text": self._higgs._ref_text,
            }],
        }

        audio_buffer = bytearray()
        bytes_per_sample = 2 * NUM_CHANNELS

        async with self._higgs._client.stream(
            "POST",
            f"{self._higgs._base_url}/v1/audio/speech",
            json=payload,
            headers={"Authorization": f"Bearer {self._higgs._api_key}"},
        ) as resp:
            
            # Read the explicit error body if 400 Bad Request happens
            if resp.status_code >= 400:
                body = await resp.aread()
                raise RuntimeError(
                    f"HiggsTTS server returned {resp.status_code}: {body.decode(errors='replace')}"
                )
            
            first_chunk = True
            
            # Since response_format="pcm", SGLang streams raw binary audio, not JSON
            async for chunk in resp.aiter_bytes():
                if not chunk:
                    continue
                
                audio_buffer.extend(chunk)

                if first_chunk:
                    if len(audio_buffer) < 44:
                        continue
                    
                    stripped_bytes = _strip_wav_header(bytes(audio_buffer))
                    audio_buffer = bytearray(stripped_bytes)
                    first_chunk = False

                valid_length = len(audio_buffer) - (len(audio_buffer) % bytes_per_sample)

                if valid_length > 0:
                    frame_data = bytes(audio_buffer[:valid_length])
                    audio_buffer = audio_buffer[valid_length:]
                    output_emitter.push(frame_data)

        output_emitter.flush()