"""
MuseTalk worker dispatcher — per-room subprocess supervisor.

Listens on 127.0.0.1:7980 (configurable via env).

Routes:
    POST /launch   {"room": "...", "agent_identity": "...", "avatar": "sample"}
                   → spawns worker.py, returns {"pid": int, "room": str}
    POST /stop     {"room": "..."}
                   → SIGTERMs the worker for that room
    GET  /status   → list of active workers {"workers": [{room, pid, started_at}]}

Optional GPU preload (first join avoids ~minute-long model load):
    MUSETALK_GPU_STANDBY=1   — on dispatcher startup, load MuseTalk weights into VRAM
                               and keep a standby subprocess until /launch assigns a room.
    MUSETALK_STANDBY_AVATAR  — baked avatar used for preload (default: AVATAR_NAME or sample).
                               If /launch requests another avatar, the standby worker reloads
                               avatar tensors only (much faster than cold start).

Restarts crashed workers automatically (exponential back-off, max 5 retries).

Run:
    python worker_dispatcher.py
    # or as a module:
    python -m worker_dispatcher
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

WORKER_DIR = Path(__file__).parent.resolve()

from dotenv import load_dotenv
load_dotenv(WORKER_DIR / ".env")
load_dotenv(WORKER_DIR.parent / ".env.local", override=False)

from fastapi import FastAPI, HTTPException
from loguru import logger
from pydantic import BaseModel
import uvicorn


# ------------------------------------------------------------------ #
#  State                                                               #
# ------------------------------------------------------------------ #


@dataclass
class WorkerEntry:
    room: str
    agent_identity: str
    avatar: str
    process: subprocess.Popen
    started_at: float
    retries: int = 0


_workers: dict[str, WorkerEntry] = {}  # room → WorkerEntry
_restart_lock = asyncio.Lock()

_standby_proc: subprocess.Popen | None = None
_standby_lock = asyncio.Lock()


def _terminate_worker_process(entry: WorkerEntry) -> None:
    if entry.process.poll() is not None:
        return
    entry.process.terminate()
    try:
        entry.process.wait(timeout=8)
    except subprocess.TimeoutExpired:
        entry.process.kill()
        try:
            entry.process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            pass


def _gpu_standby_enabled() -> bool:
    return os.getenv("MUSETALK_GPU_STANDBY", "").lower() in ("1", "true", "yes")


def _spawn_standby_worker_sync() -> subprocess.Popen | None:
    """Spawn worker.py in stdin-assignment mode; block until models are GPU-warm."""
    if not _gpu_standby_enabled():
        return None
    standby_avatar = os.getenv(
        "MUSETALK_STANDBY_AVATAR", os.getenv("AVATAR_NAME", "sample")
    )
    avatar_dir = WORKER_DIR / "avatars" / standby_avatar
    if not (avatar_dir / "meta.json").exists():
        logger.warning(
            f"MUSETALK_GPU_STANDBY disabled: avatar {standby_avatar!r} has no meta.json "
            f"under {avatar_dir}"
        )
        return None
    env = {
        **os.environ,
        "MUSETALK_ASSIGN_VIA_STDIN": "1",
        "MUSETALK_STANDBY_AVATAR": standby_avatar,
        "AVATAR_NAME": standby_avatar,
        "PYTHONUNBUFFERED": "1",
    }
    log_path = WORKER_DIR.parent / "worker-standby.log"
    log_file = open(log_path, "a")
    proc = subprocess.Popen(
        [sys.executable, str(WORKER_DIR / "worker.py")],
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=log_file,
        cwd=str(WORKER_DIR),
        bufsize=0,
    )
    line = proc.stdout.readline()
    if not line:
        logger.error(
            "Standby MuseTalk worker exited before signalling READY — check worker-standby.log"
        )
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
        return None
    token = line.strip().decode()
    if token != "MUSETALK_STANDBY_READY":
        logger.error(
            f"Standby worker unexpected stdout {line!r} (expected MUSETALK_STANDBY_READY)"
        )
        _terminate_worker_process(
            WorkerEntry(
                room="__standby__",
                agent_identity="",
                avatar=standby_avatar,
                process=proc,
                started_at=time.time(),
            )
        )
        return None
    logger.info(
        f"MuseTalk GPU standby ready (avatar={standby_avatar}, pid={proc.pid}, log={log_path})"
    )
    return proc


async def _replenish_standby() -> None:
    if not _gpu_standby_enabled():
        return
    async with _standby_lock:
        global _standby_proc
        if _standby_proc is not None and _standby_proc.poll() is None:
            return
        proc = await asyncio.to_thread(_spawn_standby_worker_sync)
        _standby_proc = proc


async def _shutdown_standby() -> None:
    global _standby_proc
    async with _standby_lock:
        if _standby_proc is None:
            return
        entry = WorkerEntry(
            room="__standby__",
            agent_identity="",
            avatar="",
            process=_standby_proc,
            started_at=0.0,
        )
        _standby_proc = None
    _terminate_worker_process(entry)


@asynccontextmanager
async def _lifespan(app: FastAPI):
    logger.info("Dispatcher starting ...")
    await _replenish_standby()
    yield
    logger.info("Dispatcher shutting down — terminating standby and workers ...")
    await _shutdown_standby()
    for entry in list(_workers.values()):
        _terminate_worker_process(entry)
    _workers.clear()


app = FastAPI(title="MuseTalk Worker Dispatcher", lifespan=_lifespan)

# ------------------------------------------------------------------ #
#  Models                                                              #
# ------------------------------------------------------------------ #

class LaunchRequest(BaseModel):
    room: str
    agent_identity: str
    avatar: str = "sample"


class StopRequest(BaseModel):
    room: str


# ------------------------------------------------------------------ #
#  Worker lifecycle                                                    #
# ------------------------------------------------------------------ #

def _spawn_worker(room: str, agent_identity: str, avatar: str) -> subprocess.Popen:
    env = {
        **os.environ,
        "AVATAR_ROOM": room,
        "AGENT_IDENTITY": agent_identity,
        "AVATAR_NAME": avatar,
        "PYTHONUNBUFFERED": "1",
    }
    log_path = WORKER_DIR.parent / f"worker-{room}.log"
    log_file = open(log_path, "a")
    proc = subprocess.Popen(
        [sys.executable, str(WORKER_DIR / "worker.py")],
        env=env,
        stdout=log_file,
        stderr=log_file,
        cwd=str(WORKER_DIR),
    )
    logger.info(f"Spawned worker pid={proc.pid} room={room} avatar={avatar} log={log_path}")
    return proc


async def _watch_worker(room: str) -> None:
    """Background task: restart worker if it crashes (up to 5 times)."""
    max_retries = 5
    base_delay = 2.0

    while True:
        await asyncio.sleep(2)
        async with _restart_lock:
            entry = _workers.get(room)
            if entry is None:
                return
            ret = entry.process.poll()
            if ret is None:
                continue  # still running
            logger.warning(f"Worker for room={room} exited with code {ret}")
            if entry.retries >= max_retries:
                logger.error(f"Worker for room={room} exceeded retry limit — giving up")
                del _workers[room]
                return
            delay = base_delay * (2 ** entry.retries)
            logger.info(f"Restarting worker for room={room} in {delay:.1f}s (attempt {entry.retries + 1})")
            await asyncio.sleep(delay)
            proc = _spawn_worker(room, entry.agent_identity, entry.avatar)
            _workers[room] = WorkerEntry(
                room=room,
                agent_identity=entry.agent_identity,
                avatar=entry.avatar,
                process=proc,
                started_at=time.time(),
                retries=entry.retries + 1,
            )


# ------------------------------------------------------------------ #
#  Routes                                                              #
# ------------------------------------------------------------------ #

@app.post("/launch")
async def launch(req: LaunchRequest):
    global _standby_proc
    async with _restart_lock:
        if req.room in _workers:
            entry = _workers[req.room]
            if entry.process.poll() is None:
                same_agent = entry.agent_identity == req.agent_identity
                same_avatar = entry.avatar == req.avatar
                if same_agent and same_avatar:
                    return {
                        "pid": entry.process.pid,
                        "room": req.room,
                        "status": "already_running",
                    }
                # New agent job / identity: worker must restart so DataStreamAudioReceiver
                # uses the correct AGENT_IDENTITY.
                logger.info(
                    f"Replacing MuseTalk worker for room={req.room!r}: "
                    f"agent {entry.agent_identity!r}->{req.agent_identity!r} or "
                    f"avatar {entry.avatar!r}->{req.avatar!r}"
                )
                _terminate_worker_process(entry)
                del _workers[req.room]
            else:
                del _workers[req.room]

        avatar_dir = WORKER_DIR / "avatars" / req.avatar
        if not (avatar_dir / "meta.json").exists():
            raise HTTPException(
                status_code=400,
                detail=f"Avatar '{req.avatar}' not found. Run prepare_avatar.py --name {req.avatar} first.",
            )

        proc: subprocess.Popen | None = None
        launch_status = "launched"
        async with _standby_lock:
            if (
                _standby_proc is not None
                and _standby_proc.poll() is None
                and _standby_proc.stdin is not None
            ):
                proc = _standby_proc
                _standby_proc = None

        if proc is not None:
            try:
                line = json.dumps(
                    {
                        "room": req.room,
                        "agent_identity": req.agent_identity,
                        "avatar": req.avatar,
                    },
                    ensure_ascii=False,
                )
                proc.stdin.write((line + "\n").encode("utf-8"))
                proc.stdin.flush()
                launch_status = "from_gpu_standby"
            except Exception:
                logger.exception(
                    "Assigning GPU standby worker failed — spawning cold worker",
                    extra={"room": req.room},
                )
                try:
                    proc.kill()
                except Exception:
                    pass
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    pass
                proc = _spawn_worker(req.room, req.agent_identity, req.avatar)

        if proc is None:
            proc = _spawn_worker(req.room, req.agent_identity, req.avatar)

        _workers[req.room] = WorkerEntry(
            room=req.room,
            agent_identity=req.agent_identity,
            avatar=req.avatar,
            process=proc,
            started_at=time.time(),
        )

    asyncio.create_task(_watch_worker(req.room))
    if _gpu_standby_enabled():
        asyncio.create_task(_replenish_standby())
    return {"pid": proc.pid, "room": req.room, "status": launch_status}


@app.post("/stop")
async def stop(req: StopRequest):
    async with _restart_lock:
        entry = _workers.pop(req.room, None)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"No worker running for room '{req.room}'")
    _terminate_worker_process(entry)
    logger.info(f"Stopped worker pid={entry.process.pid} room={req.room}")
    if _gpu_standby_enabled():
        asyncio.create_task(_replenish_standby())
    return {"stopped": True, "room": req.room}


@app.get("/status")
async def status():
    result = []
    for room, entry in list(_workers.items()):
        result.append({
            "room": room,
            "pid": entry.process.pid,
            "alive": entry.process.poll() is None,
            "avatar": entry.avatar,
            "agent_identity": entry.agent_identity,
            "started_at": entry.started_at,
            "retries": entry.retries,
        })
    return {"workers": result}


# ------------------------------------------------------------------ #
#  Entry point                                                         #
# ------------------------------------------------------------------ #

if __name__ == "__main__":
    host = os.environ.get("DISPATCHER_HOST", "127.0.0.1")
    port = int(os.environ.get("DISPATCHER_PORT", "7980"))
    logger.info(f"Starting dispatcher on {host}:{port}")
    uvicorn.run(app, host=host, port=port, log_level="info")
