#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

MUSETALK="$ROOT/musetalk_worker"
AGENT_VENV="$ROOT/.venv"
MT_VENV="$MUSETALK/.venv"

if [[ ! -x "$MT_VENV/bin/python" ]]; then
  echo "Missing MuseTalk venv: $MT_VENV (see musetalk_worker/README.md)." >&2
  exit 1
fi
if [[ ! -x "$AGENT_VENV/bin/python" ]]; then
  echo "Missing agent venv: $AGENT_VENV" >&2
  exit 1
fi

SAMPLE_VIDEO="$MUSETALK/upstream/data/video/sun.mp4"
if [[ ! -f "$MUSETALK/avatars/sample/meta.json" ]]; then
  if [[ -f "$SAMPLE_VIDEO" ]]; then
    echo "Baking sample avatar (first run) ..."
    (cd "$MUSETALK" && "$MT_VENV/bin/python" prepare_avatar.py \
        --name sample --source "$SAMPLE_VIDEO")
  else
    echo "WARN: No avatars/sample — add a video at $SAMPLE_VIDEO or run prepare_avatar.py manually." >&2
  fi
fi

# Preload MuseTalk weights into VRAM before any join (see worker_dispatcher.py docstring).
export MUSETALK_GPU_STANDBY="${MUSETALK_GPU_STANDBY:-1}"

nohup "$MT_VENV/bin/python" "$MUSETALK/worker_dispatcher.py" \
  >>"$ROOT/dispatcher.log" 2>&1 &
echo $! >"$ROOT/dispatcher.pid"

sleep 2

nohup env PYTHONUNBUFFERED=1 "$AGENT_VENV/bin/python" "$ROOT/agent.py" dev \
  >>"$ROOT/agent-dev.log" 2>&1 &
echo $! >"$ROOT/agent.pid"

echo "Dispatcher PID $(cat "$ROOT/dispatcher.pid"), agent PID $(cat "$ROOT/agent.pid")"
echo "Logs: tail -f \"$ROOT/dispatcher.log\" \"$ROOT/agent-dev.log\""
