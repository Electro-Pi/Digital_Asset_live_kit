# Systemd Services

The project includes systemd units for both application processes:

- `digital-asset-agent.service`: the LiveKit backend agent
- `digital-asset-frontend.service`: the production frontend on port `8090`

## Install and enable

Run once after cloning the project or changing either unit file:

```bash
cd /home/ai2/Digital_Asset_live_kit
sudo cp systemd/*.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now digital-asset-agent.service digital-asset-frontend.service
```

## Migrate from an older manually run agent

The backend uses port `2952`. If systemd reports that this port is already in
use, an older agent process is still running and receiving LiveKit jobs. Check
the process, stop it, then start the systemd service:

```bash
sudo fuser -v 2952/tcp
sudo fuser -TERM -k 2952/tcp
sudo systemctl restart digital-asset-agent.service
sudo systemctl status digital-asset-agent.service --no-pager
```

Run the stop command only after confirming that the listener on port `2952` is
the old Digital Asset agent.

Before the first frontend start, install dependencies and create the production
build:

```bash
cd /home/ai2/Digital_Asset_live_kit/frontend
npm ci
npm run build
```

## Restart

```bash
sudo systemctl restart digital-asset-agent.service
sudo systemctl restart digital-asset-frontend.service
```

Restart both at once:

```bash
sudo systemctl restart digital-asset-agent.service digital-asset-frontend.service
```

After frontend code changes, rebuild before restarting it:

```bash
cd /home/ai2/Digital_Asset_live_kit/frontend
npm run build
sudo systemctl restart digital-asset-frontend.service
```

## Status and logs

```bash
sudo systemctl status digital-asset-agent.service digital-asset-frontend.service --no-pager
sudo journalctl -u digital-asset-agent.service -f
sudo journalctl -u digital-asset-frontend.service -f
curl http://localhost:8090/healthz
```
