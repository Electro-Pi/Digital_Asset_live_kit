import asyncio
import logging
import time

import numpy as np
import requests
import soundfile as sf

from livekit.agents import tts
from livekit.agents.types import DEFAULT_API_CONNECT_OPTIONS


logger = logging.getLogger(__name__)


class F5TTSPlugin(tts.TTS):
    def __init__(
        self,
        server_url="http://localhost:8000",
        model_name="f5_tts",
        reference_audio_path="",
        reference_text="Some call me nature, others call me mother nature.",
        sample_rate=24000,
        chunk_duration_ms=20,
    ):
        super().__init__(
            capabilities=tts.TTSCapabilities(streaming=False),
            sample_rate=sample_rate,
            num_channels=1,
        )

        self.server_url = server_url.rstrip("/")
        self.model_name = model_name
        self.reference_text = reference_text
        self.chunk_duration_ms = chunk_duration_ms
        self._session = requests.Session()

        self.reference_waveform = None
        self._reference_inputs = None
        if reference_audio_path:
            self.reference_waveform, sr = self._load_audio(reference_audio_path)
            assert sr == 24000, "Sample rate must be 24000"
            self._reference_inputs = self._prepare_reference_inputs(
                self.reference_waveform
            )

    def synthesize(
        self, text: str, *, conn_options=DEFAULT_API_CONNECT_OPTIONS
    ) -> tts.ChunkedStream:
        return F5TTSChunkedStream(
            tts=self,
            input_text=text,
            conn_options=conn_options,
        )

    @property
    def name(self):
        return "F5-TTS"

    # 🔥 Your helper functions (adapted inside class)

    def _load_audio(self, wav_path, target_sample_rate=24000):
        waveform, sample_rate = sf.read(wav_path)

        if sample_rate != target_sample_rate:
            from scipy.signal import resample

            waveform = resample(
                waveform, int(len(waveform) * (target_sample_rate / sample_rate))
            )

        return waveform.astype(np.float32), target_sample_rate

    def _prepare_reference_inputs(self, waveform):
        waveform = waveform.astype(np.float32)
        lengths = np.array([[len(waveform)]], dtype=np.int32)
        waveform = waveform.reshape(1, -1)

        return [
            {
                "name": "reference_wav",
                "shape": waveform.shape,
                "datatype": "FP32",
                "data": waveform.tolist(),
            },
            {
                "name": "reference_wav_len",
                "shape": lengths.shape,
                "datatype": "INT32",
                "data": lengths.tolist(),
            },
            {
                "name": "reference_text",
                "shape": [1, 1],
                "datatype": "BYTES",
                "data": [self.reference_text],
            },
        ]

    def _prepare_request(self, waveform, target_text):
        reference_inputs = self._reference_inputs
        if reference_inputs is None:
            reference_inputs = self._prepare_reference_inputs(waveform)

        return {
            "inputs": [
                *reference_inputs,
                {
                    "name": "target_text",
                    "shape": [1, 1],
                    "datatype": "BYTES",
                    "data": [target_text],
                },
            ]
        }


class F5TTSChunkedStream(tts.ChunkedStream):
    def __init__(self, *, tts: F5TTSPlugin, input_text: str, conn_options):
        super().__init__(tts=tts, input_text=input_text, conn_options=conn_options)
        self._tts: F5TTSPlugin = tts
        self._audio_generated = False

    def _do_request(self, url, data):
        response = self._tts._session.post(
            url,
            headers={"Content-Type": "application/json"},
            json=data,
            params={"request_id": "0"},
            timeout=120,
        )
        response.raise_for_status()
        return response.json()

    def _emit_audio_chunks(self, output_emitter: tts.AudioEmitter, audio_bytes: bytes):
        bytes_per_sample = 2
        frame_bytes = max(
            bytes_per_sample,
            int(self._tts.sample_rate * self._tts.chunk_duration_ms / 1000)
            * bytes_per_sample,
        )

        for start in range(0, len(audio_bytes), frame_bytes):
            output_emitter.push(audio_bytes[start : start + frame_bytes])

    async def _run(self, output_emitter: tts.AudioEmitter):
        if self._audio_generated:
            return

        try:
            started_at = time.perf_counter()
            data = self._tts._prepare_request(
                self._tts.reference_waveform,
                self.input_text,
            )
            prepared_at = time.perf_counter()

            url = f"{self._tts.server_url}/v2/models/{self._tts.model_name}/infer"
            result = await asyncio.to_thread(self._do_request, url, data)
            response_at = time.perf_counter()

            audio = result["outputs"][0]["data"]
            audio = np.array(audio, dtype=np.float32)
            audio = np.clip(audio, -1.0, 1.0)
            audio_int16 = (audio * 32767).astype(np.int16)
            audio_bytes = audio_int16.tobytes()
            decoded_at = time.perf_counter()

            output_emitter.initialize(
                request_id=str(id(self)),
                sample_rate=self._tts.sample_rate,
                num_channels=1,
                mime_type="audio/pcm",
            )

            self._emit_audio_chunks(output_emitter, audio_bytes)
            output_emitter.flush()
            finished_at = time.perf_counter()

            logger.info(
                "F5TTS timings prepare=%.3fs request=%.3fs decode=%.3fs emit=%.3fs total=%.3fs text_chars=%d audio_seconds=%.3fs",
                prepared_at - started_at,
                response_at - prepared_at,
                decoded_at - response_at,
                finished_at - decoded_at,
                finished_at - started_at,
                len(self.input_text),
                len(audio) / self._tts.sample_rate,
            )

            self._audio_generated = True

        except Exception as e:
            raise RuntimeError(f"F5TTS synthesis failed: {str(e)}")

        finally:
            try:
                output_emitter.end_input()
            except Exception:
                pass
