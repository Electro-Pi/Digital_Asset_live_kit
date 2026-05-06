# MuseTalk worker (Python 3.10)

Separate virtual environment from the LiveKit **agent** (`../pyproject.toml`, Python 3.12+). This package vendors [MuseTalk](https://github.com/TMElyralab/MuseTalk) under `upstream/` and runs GPU inference without touching the agent venv.

## Prerequisites

- **Python 3.10** (see `.python-version`)
- **CUDA**-capable GPU (MuseTalk uses PyTorch fp16 on GPU)
- **ffmpeg** on `PATH`
- **OpenMMLab** (mmcv / mmdet / mmpose) installed into the same venv as below

## One-time setup

```bash
cd musetalk_worker

# Create venv (uv or python -m venv)
uv venv --python 3.10 .venv
source .venv/bin/activate

# PyTorch 2.0.1 + CUDA 11.8 wheels (matches upstream MuseTalk README)
uv pip install torch==2.0.1 torchvision==0.15.2 torchaudio==2.0.2 \
  --index-url https://download.pytorch.org/whl/cu118

# Worker package + pinned ML deps
uv pip install -e .

# MMLab stack (required by upstream DWPose / preprocessing)
pip install -U openmim
mim install mmengine "mmcv==2.0.1" "mmdet==3.1.0" "mmpose==1.1.0"
```

Copy `.env.example` to `.env` and set **LIVEKIT_URL**, **LIVEKIT_API_KEY**, and **LIVEKIT_API_SECRET** (same values as the agent’s `../.env.local`).

### Download weights (~4 GB)

```bash
python download_models.py
python download_models.py --check
```

### Bake an avatar identity

From a video (25 fps recommended) or a portrait image:

```bash
python prepare_avatar.py --name sample --source upstream/data/video/sun.mp4
# or
python prepare_avatar.py --name boss --source /path/to/face.jpg --force
```

Output: `avatars/<name>/` (`meta.json`, `latents.pt`, `coords.pkl`, `full_imgs/`, `mask/`, …).

If `upstream/data/video/` has no sample MP4, clone MuseTalk once and copy `data/video/*.mp4` into `upstream/data/video/`, or point `--source` at any clear talking-head clip.

## Run the dispatcher + worker

The **agent** process (in the parent directory) calls `POST /launch` on the dispatcher; the dispatcher spawns `worker.py` per room.

```bash
source .venv/bin/activate
export $(grep -v '^#' .env | xargs)   # or use direnv
python worker_dispatcher.py
```

Logs for each room’s worker are written to `../worker-<room_name>.log`.

## Smoke-test inference (no LiveKit)

```bash
python musetalk_engine.py --avatar avatars/sample --audio ../speech.wav --benchmark
python musetalk_engine.py --avatar avatars/sample --audio ../speech.wav --out /tmp/smoke.mp4
```

## Layout

| Path | Role |
|------|------|
| `upstream/musetalk/` | Vendored MuseTalk package |
| `models/` | Downloaded checkpoints (gitignored) |
| `avatars/` | Baked identities (gitignored) |
| `worker_dispatcher.py` | FastAPI supervisor (`POST /launch`, `POST /stop`, `GET /status`) |
| `worker.py` | Joins room as `musetalk-avatar`, `AvatarRunner` + MuseTalk |
| `musetalk_engine.py` | Streaming UNet/VAE/Whisper loop |

## Environment variables

See `.env.example`. The worker subprocess receives **AVATAR_ROOM**, **AGENT_IDENTITY**, and **AVATAR_NAME** from the dispatcher; LiveKit credentials are inherited from the environment.

## Agent integration

From the repo root, `agent.py` (when **ENABLE_MUSETALK** is not `0`) posts to **MUSETALK_DISPATCHER_URL** (default `http://127.0.0.1:7980`), waits for participant `musetalk-avatar`, then sets `session.output.audio` to `DataStreamAudioOutput` so TTS PCM is sent to the worker. Clients should subscribe to the avatar participant’s **video + audio** tracks; the participant attribute `lk.publish_on_behalf` points at the agent identity.
