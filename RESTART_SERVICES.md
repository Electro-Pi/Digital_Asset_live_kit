# Systemd Services

Four units make up the deployment on this host. All of them live in
`/etc/systemd/system/` and are installed from this repo (and from the
`TTS-Endpoint-api` checkout next to it).

| Unit | What it is | Listens on | Public at |
| --- | --- | --- | --- |
| `coda-agent.service` | LiveKit voice agent (`agent.py`) | `2953` (health only) | — (talks out to LiveKit Cloud) |
| `coda-next-frontend.service` | Public Next.js site | `127.0.0.1:8090` | `/` |
| `coda-tts.service` | TTS gateway (`TTS-Endpoint-api`) | `127.0.0.1:8011` | `/api/tts/` |
| `coda-admin-frontend.service` | Admin dashboard | `127.0.0.1:8092` | `/admin` |

`systemd/digital-asset-agent.service` and `systemd/digital-asset-frontend.service`
are the older `/home/ai2` layout and are **not installed**. `digital-asset-agent`
is the same LiveKit worker as `coda-agent`; running both registers two workers
against one project and jobs go to whichever answers first.

## Install and enable

Run once after cloning, or after changing any unit file:

```bash
cd /home/bassem/Coda/Digital_Asset_live_kit
sudo install -m 0644 systemd/coda-agent.service systemd/coda-next-frontend.service \
  systemd/coda-admin-frontend.service /etc/systemd/system/
sudo install -m 0644 ../TTS-Endpoint-api/deploy/coda-tts.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now coda-agent coda-next-frontend coda-tts
```

`coda-admin-frontend` is deliberately left out of that last line — see
`ADMIN_DEPLOYMENT.md`.

## Before the first start

The units run as `bassem`, the account that owns the checkout. Build as that
same user: a `sudo npm ci` leaves root-owned files in `node_modules/` and
`.next/` that the service then cannot read.

```bash
# Agent dependencies and its model files (turn detector, VAD).
cd /home/bassem/Coda/Digital_Asset_live_kit
uv sync
set -a && . ./.env.local && set +a
.venv/bin/python agent.py download-files

# Public frontend.
cd frontend/coda-frontend/coda-next
PATH=/opt/coda-node22/bin:$PATH npm ci
PATH=/opt/coda-node22/bin:$PATH npm run build

# TTS gateway.
cd /home/bassem/Coda/TTS-Endpoint-api
uv venv .venv --python 3.12
uv pip install --python .venv/bin/python -r requirements.txt
```

This host has no `/usr/bin/node`. Node 22 lives at `/opt/coda-node22/bin`, and
both frontend units invoke it by absolute path.

## Port 2953 is already in use

An older agent process is still running and receiving LiveKit jobs. Confirm it
is the old agent before killing it:

```bash
sudo fuser -v 2953/tcp
sudo fuser -TERM -k 2953/tcp
sudo systemctl restart coda-agent.service
```

## Restart

```bash
sudo systemctl restart coda-agent coda-next-frontend coda-tts
```

After frontend code changes, rebuild before restarting — `next start` serves
whatever is in `.next/`, so a restart alone changes nothing:

```bash
cd /home/bassem/Coda/Digital_Asset_live_kit/frontend/coda-frontend/coda-next
PATH=/opt/coda-node22/bin:$PATH npm run build
sudo systemctl restart coda-next-frontend.service
```

`NEXT_PUBLIC_*` values are inlined at build time. Changing one in `.env.local`
needs a rebuild, not just a restart.

`./deploy.sh` does the pull, install, build and restart in one pass.

## Status and logs

```bash
sudo systemctl status coda-agent coda-next-frontend coda-tts --no-pager
sudo journalctl -u coda-agent -f
curl http://127.0.0.1:8090/                                  # frontend
curl http://127.0.0.1:8011/healthz                           # gateway
curl http://127.0.0.1:2953/                                  # agent health

# Through nginx, before DNS points here:
curl -H 'Host: www.coda.eracoda.ai' http://127.0.0.1/api/tts/healthz
```
