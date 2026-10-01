# Systemd Services (jahez-project)

Two independent units. They are **not** wired to each other — the frontend is a
standalone mockup and does not call the agent.

| Unit                    | What                     | Port                          |
| ----------------------- | ------------------------ | ----------------------------- |
| `jahez-agent.service`   | LiveKit voice agent      | `6700` (local health/debug)   |
| `jahez-frontend.service`| Next.js frontend         | `6701` (loopback only)        |
| nginx `jahez-frontend`  | public TLS entry point   | `6702`                        |

Public URL: <https://demo.testdevelopment.top:6702>

The agent is a LiveKit **worker**: it dials out to `LIVEKIT_URL` and is not
reachable from the internet. Port `6700` is only its local health endpoint.

## Install and enable

Run once after cloning or changing either unit file:

```bash
cd /home/ubuntu/projects/jahez-project/coda-jahez-agent-backend
sudo cp systemd/jahez-agent.service systemd/jahez-frontend.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now jahez-agent.service jahez-frontend.service
```

nginx vhost (already installed):

```bash
sudo cp systemd/jahez-frontend.nginx.conf /etc/nginx/sites-available/jahez-frontend.conf
sudo ln -sfn /etc/nginx/sites-available/jahez-frontend.conf /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
```

## First-time setup

Backend dependencies and the VAD / turn-detector model files:

```bash
cd /home/ubuntu/projects/jahez-project/coda-jahez-agent-backend
uv sync
.venv/bin/python agent.py download-files
```

Frontend production build (required before the first start):

```bash
cd /home/ubuntu/projects/jahez-project/jahez-frontend
npm ci
npm run build
```

## Port conflicts

`6700` must be unique per agent on this host — `2952` belongs to the unrelated
`digital-asset-agent.service`. If systemd reports the port is taken, confirm the
listener is a stale agent of *this* deployment before killing it:

```bash
sudo fuser -v 6700/tcp
sudo fuser -TERM -k 6700/tcp
sudo systemctl restart jahez-agent.service
```

## Restart

```bash
sudo systemctl restart jahez-agent.service jahez-frontend.service
```

After frontend code changes, rebuild before restarting:

```bash
cd /home/ubuntu/projects/jahez-project/jahez-frontend
npm run build
sudo systemctl restart jahez-frontend.service
```

## Status and logs

```bash
sudo systemctl status jahez-agent.service jahez-frontend.service --no-pager
sudo journalctl -u jahez-agent.service -f
sudo journalctl -u jahez-frontend.service -f
curl -I https://demo.testdevelopment.top:6702/
```

A healthy agent logs `HTTP server listening on :6700` followed by
`registered worker ... url=wss://...`.
