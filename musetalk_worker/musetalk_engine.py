"""
MuseTalk streaming inference engine.

Wraps the MuseTalk v1.5 pipeline as an async generator:
  - Push 16 kHz mono int16 PCM audio chunks in via push_audio()
  - Read (rgb_bgr_frame: np.ndarray, pcm_frame_bytes) pairs from stream_frames()
  - Call reset() on interruption
  - Call flush() when an audio segment ends

The engine keeps models loaded across calls — instantiate once and reuse.

CLI smoke-test:
    python musetalk_engine.py --avatar avatars/sample --audio ../speech.wav --out /tmp/smoke.mp4
    python musetalk_engine.py --avatar avatars/sample --audio ../speech.wav --benchmark
"""
from __future__ import annotations

import argparse
import asyncio
import glob
import logging
import os
import pickle
import sys
import threading
import time
from pathlib import Path
from typing import AsyncIterator

import cv2
import numpy as np
import torch

WORKER_DIR = Path(__file__).parent.resolve()
UPSTREAM_DIR = WORKER_DIR / "upstream"
sys.path.insert(0, str(UPSTREAM_DIR))

_logger = logging.getLogger(__name__)


class MuseTalkEngine:
    """
    Real-time MuseTalk inference engine.

    Loads all models once (call warmup()) then processes audio chunks
    on demand. Thread-safe for a single producer + single consumer.
    """

    SAMPLE_RATE = 16000
    # MuseTalk / baked avatars are 25 fps; streaming can use other rates if they
    # divide SAMPLE_RATE evenly (otherwise int16 frames drift from wall clock).
    MODEL_FPS = 25
    MAX_STREAM_FPS = 50
    # Whisper feature frame stride: 50 feature frames per second
    WHISPER_FPS = 50
    # Context window around each frame (±2 Whisper frames on each side)
    AUDIO_PAD_LEFT = 2
    AUDIO_PAD_RIGHT = 2
    AUDIO_FEAT_LEN = 2 * (AUDIO_PAD_LEFT + AUDIO_PAD_RIGHT + 1)  # 10

    @classmethod
    def _coerce_stream_fps(cls, requested: int) -> int:
        """Pick a stream fps that divides SAMPLE_RATE so each video frame maps to an
        exact int16 PCM slice (avoids A/V drift). Values like 30 are snapped to the
        nearest valid rate (e.g. 30 → 32 at 16 kHz)."""
        sr = cls.SAMPLE_RATE
        if requested <= 0:
            return cls.MODEL_FPS
        valid = [f for f in range(1, cls.MAX_STREAM_FPS + 1) if sr % f == 0]
        if not valid:
            return cls.MODEL_FPS
        target = min(requested, cls.MAX_STREAM_FPS)
        best = min(valid, key=lambda f: (abs(f - target), -f))
        if best != requested:
            _logger.warning(
                "MuseTalk stream fps adjusted from %s to %s (%s Hz PCM requires an "
                "integer sample count per frame; max stream fps is %s).",
                requested,
                best,
                sr,
                cls.MAX_STREAM_FPS,
            )
        return best

    def __init__(
        self,
        avatar_dir: Path,
        device: str = "cuda",
        batch_size: int = 1,
        extra_margin: int = 10,
        parsing_mode: str = "jaw",
        fps: int | None = None,
    ) -> None:
        self._avatar_dir = Path(avatar_dir)
        self._device = torch.device(device if torch.cuda.is_available() else "cpu")
        self._batch_size = batch_size
        self._extra_margin = extra_margin
        self._parsing_mode = parsing_mode
        req = int(fps) if fps is not None else self.MODEL_FPS
        self._fps = self._coerce_stream_fps(req)

        # Set CWD so upstream relative model paths resolve correctly
        os.chdir(WORKER_DIR)

        # These are set in warmup()
        self._vae = None
        self._unet = None
        self._pe = None
        self._whisper = None
        self._audio_processor = None
        self._fp = None
        self._timesteps = None
        self._weight_dtype = None

        # Avatar cache (loaded in _load_avatar)
        self._coord_list_cycle: list = []
        self._frame_list_cycle: list = []
        self._latent_list_cycle: list = []
        self._mask_list_cycle: list = []
        self._mask_coords_list_cycle: list = []
        self._n_cycle = 0

        # Streaming state
        self._audio_buffer = bytearray()         # raw 16kHz int16 mono PCM
        self._whisper_features: list = []        # pre-computed per-half-frame features
        self._frame_idx = 0                      # current cycle position
        self._is_speaking = False
        self._silent_frames = 0
        self._reset_event = asyncio.Event()
        self._flush_event = asyncio.Event()
        self._lock = asyncio.Lock()
        self._last_whisper_extract_ts: float = 0.0
        self._extract_task: asyncio.Task[None] | None = None
        # Serializes GPU access between UNet inference and Whisper extraction
        self._gpu_lock = threading.Lock()
        # Per-stage timing accumulators (seconds)
        self._t_executor_overhead: list[float] = []
        self._t_unet: list[float] = []
        self._t_vae: list[float] = []
        self._t_blend: list[float] = []
        self._t_frame_total: list[float] = []
        self._timing_log_interval = 10  # print stats every N inferred frames
        # Stateful 24 kHz → 16 kHz (OpenAI TTS default). Per-chunk librosa causes clicks.
        self._rs24_x = np.array([], dtype=np.float32)
        self._rs24_pos: float = 0.0

        # Derived per-fps constants (instance-level)
        self._samples_per_frame = self.SAMPLE_RATE // self._fps
        self._whisper_idx_multiplier = self.WHISPER_FPS / float(self._fps)

    # ------------------------------------------------------------------ #
    #  Public API                                                          #
    # ------------------------------------------------------------------ #

    async def warmup(self) -> None:
        """Load all models into GPU memory and run a dummy forward pass."""
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._load_models)
        await loop.run_in_executor(None, self._load_avatar)
        await loop.run_in_executor(None, self._dummy_forward)

    async def push_audio(self, pcm_bytes: bytes, sample_rate: int) -> None:
        """
        Buffer incoming audio. Resamples to 16 kHz if needed.
        pcm_bytes: raw int16 little-endian mono PCM.
        """
        async with self._lock:
            if sample_rate != self.SAMPLE_RATE:
                pcm_bytes = self._resample_to_16k_mono(pcm_bytes, sample_rate)
            self._audio_buffer.extend(pcm_bytes)
            self._is_speaking = True
            self._silent_frames = 0
        # Don't block the audio ingest path on Whisper extraction; that can take
        # 100s of ms and causes audio starvation + choppy playback.
        self._maybe_schedule_extract()

    async def flush(self) -> None:
        """Signal that the current audio segment has ended."""
        self._flush_event.set()

    async def reset(self) -> None:
        """Interrupt current generation (e.g. user started speaking)."""
        async with self._lock:
            self._audio_buffer.clear()
            self._whisper_features.clear()
            self._is_speaking = False
            self._last_whisper_extract_ts = 0.0
            self._rs24_x = np.array([], dtype=np.float32)
            self._rs24_pos = 0.0
            if self._extract_task is not None and not self._extract_task.done():
                self._extract_task.cancel()
            self._extract_task = None
        self._reset_event.set()
        self._flush_event.clear()

    async def stream_frames(self) -> AsyncIterator[tuple[np.ndarray, bytes]]:
        """
        Yield (bgr_frame, pcm_frame_bytes) at self._fps.
        bgr_frame: numpy array shape (H, W, 3) uint8 BGR (OpenCV convention).
        pcm_frame_bytes: (SAMPLE_RATE // fps) int16 mono samples @ 16 kHz as raw bytes.
        """
        loop = asyncio.get_event_loop()
        t0 = time.monotonic()
        pending_bgr: np.ndarray | None = None
        while True:
            if self._reset_event.is_set():
                self._reset_event.clear()
                pending_bgr = None
                continue

            frame_idx = self._frame_idx % self._n_cycle
            features = self._get_features_for_frame(self._frame_idx)
            if features is None and self._is_speaking:
                # Avoid publishing "resting" frames while audio is already flowing.
                # That looks like lip desync. Briefly wait for Whisper extraction to
                # produce features, but don't block forever.
                self._maybe_schedule_extract()
                # Short wait: better to publish a resting frame briefly than
                # stall the producer and cause "behind schedule" gaps.
                wait_deadline = time.monotonic() + 0.020
                while (
                    self._is_speaking
                    and not self._reset_event.is_set()
                    and not self._whisper_features
                    and time.monotonic() < wait_deadline
                ):
                    await asyncio.sleep(0.002)
                features = self._get_features_for_frame(self._frame_idx)

            if features is not None:
                t_before_exec = time.monotonic()
                # GPU-only: UNet + VAE decode → raw face crop
                gpu_data = await loop.run_in_executor(None, self._infer_frame_gpu, frame_idx, features)
                self._t_executor_overhead.append(time.monotonic() - t_before_exec)
                self._silent_frames = 0
            else:
                gpu_data = None
                self._silent_frames += 1

            # Start CPU blend for this frame in background — it overlaps with next
            # frame's GPU inference, collapsing critical path to UNet+VAE only.
            if gpu_data is not None:
                blend_fut = loop.run_in_executor(None, self._apply_blend, gpu_data)
            else:
                blend_fut = None

            # Yield previous frame's fully-blended result while current blend runs
            if pending_bgr is not None:
                bgr = pending_bgr
            else:
                bgr = self._frame_list_cycle[frame_idx]

            pcm = await self.take_audio_frame_pcm()
            self._frame_idx += 1
            yield bgr, pcm

            # Now wait for current frame's blend to finish (ready for next iteration)
            if blend_fut is not None:
                pending_bgr = await blend_fut
            else:
                pending_bgr = self._frame_list_cycle[frame_idx % self._n_cycle]

            # No producer-side pacing: AvatarRunner has its own bounded queue
            # (see _queue_size_ms in worker.py) which paces output and provides
            # back-pressure. Letting the producer run free means fast frames
            # build a small reserve that absorbs occasional GPU spikes —
            # eliminating audio gaps from p95 latency outliers.
            await asyncio.sleep(0)

    # ------------------------------------------------------------------ #
    #  Internal helpers                                                    #
    # ------------------------------------------------------------------ #

    def _load_models(self) -> None:
        from transformers import WhisperModel
        from musetalk.utils.utils import load_all_model
        from musetalk.utils.audio_processor import AudioProcessor
        from musetalk.utils.face_parsing import FaceParsing

        models_dir = WORKER_DIR / "models"
        self._vae, self._unet, self._pe = load_all_model(
            unet_model_path=str(models_dir / "musetalkV15" / "unet.pth"),
            vae_type="sd-vae",
            unet_config=str(models_dir / "musetalkV15" / "musetalk.json"),
            device=self._device,
        )
        self._weight_dtype = torch.float16
        self._vae.vae = self._vae.vae.half().to(self._device)
        self._unet.model = self._unet.model.half().to(self._device)
        self._pe = self._pe.half().to(self._device)

        self._whisper = (WhisperModel
                         .from_pretrained(str(WORKER_DIR / "models" / "whisper"))
                         .to(device=self._device, dtype=self._weight_dtype)
                         .eval())
        self._whisper.requires_grad_(False)

        self._audio_processor = AudioProcessor(
            feature_extractor_path=str(WORKER_DIR / "models" / "whisper")
        )
        self._fp = FaceParsing(left_cheek_width=90, right_cheek_width=90)
        self._timesteps = torch.tensor([0], device=self._device)

    def _load_avatar(self) -> None:
        import torch as _t
        d = self._avatar_dir
        self._latent_list_cycle = _t.load(str(d / "latents.pt"), map_location="cpu")
        with open(d / "coords.pkl", "rb") as f:
            self._coord_list_cycle = pickle.load(f)
        with open(d / "mask_coords.pkl", "rb") as f:
            self._mask_coords_list_cycle = pickle.load(f)
        frame_files = sorted(glob.glob(str(d / "full_imgs" / "*.[jpJP][pnPN]*[gG]")))
        self._frame_list_cycle = [cv2.imread(fp) for fp in frame_files]
        mask_files = sorted(glob.glob(str(d / "mask" / "*.[jpJP][pnPN]*[gG]")))
        self._mask_list_cycle = [cv2.imread(fp) for fp in mask_files]
        self._n_cycle = len(self._frame_list_cycle)

    def _dummy_forward(self) -> None:
        """Warm up CUDA kernels."""
        dummy_latent = self._latent_list_cycle[0].to(device=self._device, dtype=self._weight_dtype)
        if dummy_latent.dim() == 3:
            dummy_latent = dummy_latent.unsqueeze(0)
        dummy_audio = torch.zeros(1, 50, 384, device=self._device, dtype=self._weight_dtype)
        audio_feat = self._pe(dummy_audio)
        out = self._unet.model(dummy_latent, self._timesteps, encoder_hidden_states=audio_feat).sample
        self._vae.decode_latents(out.to(dtype=self._vae.vae.dtype))
        if torch.cuda.is_available():
            torch.cuda.synchronize()
        _logger.info("Warmup complete.")

    def _maybe_schedule_extract(self) -> None:
        """Pre-extract Whisper features when buffer has ≥200 ms of audio.

        Re-running Whisper on every small push_audio() replaces feature rows
        mid-utterance and stalls the GPU — throttle unless the runway is low.
        """
        if self._extract_task is not None and not self._extract_task.done():
            return

        min_samples = int(self.SAMPLE_RATE * 0.2)  # 200 ms = 3200 samples = 6400 bytes int16
        if len(self._audio_buffer) < min_samples * 2:
            return

        now = time.monotonic()
        min_interval = 0.32
        low_runway = len(self._whisper_features) < int(self._fps * 0.5)  # <~0.5 s of lip frames left
        if not low_runway and (now - self._last_whisper_extract_ts) < min_interval:
            return

        self._last_whisper_extract_ts = now
        self._extract_task = asyncio.create_task(self._extract_whisper_features())

    async def _extract_whisper_features(self) -> None:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._extract_whisper_features_sync)

    def _extract_whisper_features_sync(self) -> None:
        """Extract Whisper encoder hidden states for the buffered audio."""
        with self._lock_sync():
            audio_bytes = bytes(self._audio_buffer)
        audio_np = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32) / 32768.0
        # AudioProcessor.get_audio_feature requires a filesystem path (librosa.load)
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp_path = tmp.name
        try:
            import soundfile as sf

            sf.write(tmp_path, audio_np, self.SAMPLE_RATE, subtype="PCM_16")
            features_list, librosa_len = self._audio_processor.get_audio_feature(
                tmp_path, weight_dtype=self._weight_dtype
            )
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

        if features_list is None:
            return
        # No GPU lock: see _infer_frame_gpu. We accept brief UNet contention
        # rather than block the producer for the full Whisper duration.
        whisper_chunks = self._audio_processor.get_whisper_chunk(
                features_list,
                self._device,
                self._weight_dtype,
                self._whisper,
                librosa_len,
                fps=self._fps,
                audio_padding_length_left=self.AUDIO_PAD_LEFT,
                audio_padding_length_right=self.AUDIO_PAD_RIGHT,
            )
        # One row per output video frame: [T, seq, 384]
        self._whisper_features = [whisper_chunks[i] for i in range(whisper_chunks.shape[0])]

    def _get_features_for_frame(self, frame_idx: int) -> torch.Tensor | None:
        if not self._whisper_features or not self._is_speaking:
            return None
        # Pop the front feature (aligned to current frame)
        feat = self._whisper_features.pop(0) if self._whisper_features else None
        if feat is None:
            return None
        return feat.to(device=self._device, dtype=self._weight_dtype)

    def _blend_fast(
        self,
        ori_frame: np.ndarray,
        face: np.ndarray,
        bbox: tuple,
        mask_bgr: np.ndarray,
        crop_box: tuple,
    ) -> np.ndarray:
        """NumPy/OpenCV replacement for get_image_blending — ~10x faster than PIL path."""
        x1, y1, x2, y2 = bbox
        cx1, cy1, cx2, cy2 = crop_box

        result = ori_frame.copy()

        # Paste resized face into crop region at its offset
        fx, fy = x1 - cx1, y1 - cy1
        result[cy1:cy2, cx1:cx2][fy : fy + (y2 - y1), fx : fx + (x2 - x1)] = face

        # Build float alpha mask from precomputed BGR mask image
        if mask_bgr.ndim == 3:
            mask_gray = cv2.cvtColor(mask_bgr, cv2.COLOR_BGR2GRAY)
        else:
            mask_gray = mask_bgr
        alpha = mask_gray.astype(np.float32)[:, :, np.newaxis] / 255.0

        # Alpha-blend the crop region against the original
        crop_orig = ori_frame[cy1:cy2, cx1:cx2].astype(np.float32)
        crop_new  = result[cy1:cy2, cx1:cx2].astype(np.float32)
        result[cy1:cy2, cx1:cx2] = np.clip(
            crop_new * alpha + crop_orig * (1.0 - alpha), 0, 255
        ).astype(np.uint8)

        return result

    @torch.no_grad()
    def _infer_frame_gpu(self, frame_idx: int, audio_feature: torch.Tensor) -> tuple:
        """GPU-only: UNet + VAE decode. Returns raw data for CPU blend step."""
        t0 = time.monotonic()

        af = audio_feature
        if af.dim() == 2:
            af = af.unsqueeze(0)
        audio_feat = self._pe(af)

        latent = self._latent_list_cycle[frame_idx % self._n_cycle]
        latent = latent.to(device=self._device, dtype=self._weight_dtype)
        if latent.dim() == 3:
            latent = latent.unsqueeze(0)

        # No GPU lock here: serializing UNet against Whisper extraction caused
        # hard 300ms stalls (entire Whisper run) that drained the downstream
        # buffer. Brief UNet contention spikes (~60ms) are absorbed by the
        # 600ms AvatarRunner queue and are far less disruptive than long stalls.
        t_unet = time.monotonic()
        pred_latent = self._unet.model(
            latent, self._timesteps, encoder_hidden_states=audio_feat
        ).sample
        if torch.cuda.is_available():
            torch.cuda.synchronize()
        self._t_unet.append(time.monotonic() - t_unet)

        pred_latent = pred_latent.to(dtype=self._vae.vae.dtype)

        t_vae = time.monotonic()
        recon = self._vae.decode_latents(pred_latent)
        if torch.cuda.is_available():
            torch.cuda.synchronize()
        self._t_vae.append(time.monotonic() - t_vae)

        if isinstance(recon, np.ndarray):
            res_frame = recon[0] if recon.ndim == 4 else recon
        elif isinstance(recon, (list, tuple)):
            res_frame = recon[0]
        else:
            res_frame = recon[0] if recon.ndim == 4 else recon
        if isinstance(res_frame, torch.Tensor):
            res_frame = res_frame.cpu().numpy()

        self._t_frame_total.append(time.monotonic() - t0)
        self._maybe_log_timing()

        ori_frame = self._frame_list_cycle[frame_idx % self._n_cycle]
        bbox = self._coord_list_cycle[frame_idx % self._n_cycle]
        mask = self._mask_list_cycle[frame_idx % self._n_cycle]
        crop_box = self._mask_coords_list_cycle[frame_idx % self._n_cycle]
        return (ori_frame, res_frame, bbox, mask, crop_box)

    def _apply_blend(self, gpu_data: tuple) -> np.ndarray:
        """CPU blend step — runs concurrently with next frame's GPU inference."""
        ori_frame, res_frame, bbox, mask, crop_box = gpu_data
        x1, y1, x2, y2 = bbox
        t_blend = time.monotonic()
        try:
            res_frame = cv2.resize(res_frame.astype(np.uint8), (x2 - x1, y2 - y1))
        except Exception:
            return ori_frame.copy()
        result = self._blend_fast(ori_frame, res_frame, bbox, mask, crop_box)
        self._t_blend.append(time.monotonic() - t_blend)
        return result

    def _maybe_log_timing(self) -> None:
        n = len(self._t_frame_total)
        if n < self._timing_log_interval or n % self._timing_log_interval != 0:
            return

        def stats(samples: list[float]) -> str:
            if not samples:
                return "n/a"
            arr = sorted(samples[-self._timing_log_interval:])
            p50 = arr[len(arr) // 2] * 1000
            p95 = arr[int(len(arr) * 0.95)] * 1000
            return f"p50={p50:.1f}ms  p95={p95:.1f}ms"

        budget_ms = 1000.0 / self._fps
        overhead = [
            e - f for e, f in zip(
                self._t_executor_overhead[-self._timing_log_interval:],
                self._t_frame_total[-self._timing_log_interval:],
            )
        ] if self._t_executor_overhead else []

        print(
            f"\n── MuseTalk per-frame timing (last {self._timing_log_interval} inferred frames,"
            f" budget={budget_ms:.0f}ms) ──\n"
            f"  total (incl. executor):  {stats(self._t_frame_total)}\n"
            f"  UNet forward:            {stats(self._t_unet)}\n"
            f"  VAE decode:              {stats(self._t_vae)}\n"
            f"  CPU blend+resize:        {stats(self._t_blend)}\n"
            f"  executor overhead:       p50={((sum(overhead)/len(overhead)*1000) if overhead else 0.0):.1f}ms",
            flush=True,
        )

    def _resample_to_16k_mono(self, pcm_bytes: bytes, sample_rate: int) -> bytes:
        """Convert to 16 kHz mono int16. OpenAI TTS uses 24 kHz — use a stateful path (no per-chunk librosa)."""
        if sample_rate == self.SAMPLE_RATE:
            return pcm_bytes
        if sample_rate == 24000:
            return self._resample_24k_linear_stream(pcm_bytes)
        import librosa

        audio_f32 = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32) / 32768.0
        resampled = librosa.resample(
            audio_f32, orig_sr=sample_rate, target_sr=self.SAMPLE_RATE
        )
        return (resampled * 32767.0).astype(np.int16).tobytes()

    def _resample_24k_linear_stream(self, pcm_bytes: bytes) -> bytes:
        """24 kHz → 16 kHz linear interpolation with sample carry (avoids chunk-edge clicks)."""
        x = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32) / 32768.0
        self._rs24_x = np.concatenate([self._rs24_x, x]) if self._rs24_x.size else x.copy()
        out_list: list[float] = []
        step = 24000 / self.SAMPLE_RATE  # 1.5 input samples per output sample
        while self._rs24_pos + 1.0 < float(len(self._rs24_x)):
            i0 = int(self._rs24_pos)
            frac = self._rs24_pos - float(i0)
            a0 = float(self._rs24_x[i0])
            a1 = float(self._rs24_x[i0 + 1])
            v = a0 * (1.0 - frac) + a1 * frac
            out_list.append(v)
            self._rs24_pos += step
        if not out_list:
            return b""
        consumed = int(self._rs24_pos)
        if consumed > 0:
            self._rs24_x = self._rs24_x[consumed:]
            self._rs24_pos -= float(consumed)
        arr = np.clip(np.asarray(out_list, dtype=np.float32), -1.0, 1.0)
        return (arr * 32767.0).astype(np.int16).tobytes()

    async def take_audio_frame_pcm(self) -> bytes:
        """Pop one video tick of 16 kHz mono PCM; pad immediately if short.

        Long waits here stall the whole pipeline and cause downstream
        "behind schedule" warnings. Cap wait to ~one frame budget; the
        AvatarRunner queue absorbs any brief zero-pad, and TTS bursts
        refill the buffer faster than realtime so backpressure self-heals.
        """
        n_bytes = self._samples_per_frame * 2
        # Wait only if buffer is fully empty AND we're actively speaking.
        if self._is_speaking:
            deadline = time.monotonic() + 0.020
            while time.monotonic() < deadline:
                async with self._lock:
                    if len(self._audio_buffer) >= n_bytes:
                        break
                await asyncio.sleep(0.002)
        async with self._lock:
            if len(self._audio_buffer) >= n_bytes:
                chunk = bytes(self._audio_buffer[:n_bytes])
                del self._audio_buffer[:n_bytes]
                return chunk
            available = bytes(self._audio_buffer)
            self._audio_buffer.clear()
            return available + bytes(n_bytes - len(available))

    def _lock_sync(self):
        """Simple context manager for sync code (can't await in executor)."""
        import threading
        if not hasattr(self, "_sync_lock"):
            self._sync_lock = threading.Lock()
        return self._sync_lock


# ------------------------------------------------------------------ #
#  CLI smoke-test / benchmark                                         #
# ------------------------------------------------------------------ #

async def _run_smoke(avatar_dir: Path, audio_path: str, out_path: str | None, benchmark: bool) -> None:
    import soundfile as sf

    engine = MuseTalkEngine(avatar_dir=avatar_dir, batch_size=1)
    print("Warming up models ...")
    t0 = time.perf_counter()
    await engine.warmup()
    print(f"  warmup: {time.perf_counter() - t0:.1f}s")

    audio_data, sr = sf.read(audio_path, dtype="int16", always_2d=False)
    if audio_data.ndim > 1:
        audio_data = audio_data[:, 0]

    # Push all audio in one go
    await engine.push_audio(audio_data.tobytes(), sr)
    await engine.flush()

    duration_s = len(audio_data) / sr
    n_frames = int(duration_s * engine._fps)
    print(f"Audio: {duration_s:.1f}s → {n_frames} frames target")

    frames_bgr = []
    audio_chunks = []
    t_start = time.perf_counter()
    async for bgr, pcm in engine.stream_frames():
        frames_bgr.append(bgr)
        audio_chunks.append(pcm)
        if len(frames_bgr) >= n_frames:
            break
    elapsed = time.perf_counter() - t_start

    fps_achieved = len(frames_bgr) / elapsed
    print(f"\n{'BENCHMARK' if benchmark else 'SMOKE TEST'} RESULT")
    print(f"  frames: {len(frames_bgr)}  time: {elapsed:.2f}s  fps: {fps_achieved:.1f}")
    print(
        f"  {'✅ REAL-TIME' if fps_achieved >= engine._fps * 0.95 else '❌ BELOW TARGET FPS — lower resolution or batch'}"
    )

    if out_path and not benchmark:
        import soundfile as sf
        import subprocess, tempfile

        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        h, w = frames_bgr[0].shape[:2]
        vid_tmp = out_path + ".tmp.mp4"
        writer = cv2.VideoWriter(vid_tmp, fourcc, float(engine._fps), (w, h))
        for f in frames_bgr:
            writer.write(f)
        writer.release()

        raw_audio = b"".join(audio_chunks)
        audio_np = np.frombuffer(raw_audio, dtype=np.int16)
        wav_tmp = out_path + ".tmp.wav"
        sf.write(wav_tmp, audio_np, engine.SAMPLE_RATE)

        subprocess.run([
            "ffmpeg", "-y", "-v", "warning",
            "-i", vid_tmp, "-i", wav_tmp,
            "-c:v", "libx264", "-c:a", "aac", out_path,
        ], check=True)
        os.remove(vid_tmp)
        os.remove(wav_tmp)
        print(f"  Output: {out_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="MuseTalk engine smoke-test")
    parser.add_argument("--avatar", required=True, help="Path to baked avatar dir (e.g. avatars/sample)")
    parser.add_argument("--audio", required=True, help="Input WAV file")
    parser.add_argument("--out", default=None, help="Output mp4 (omit for benchmark-only)")
    parser.add_argument("--benchmark", action="store_true", help="Print fps without writing video")
    args = parser.parse_args()
    asyncio.run(_run_smoke(Path(args.avatar), args.audio, args.out, args.benchmark))
