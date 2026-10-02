#!/bin/bash
set -e

APP_DIR="/home/ubuntu/projects/Digital_Asset_live_kit"

cd "$APP_DIR"

echo "⏳ Pulling latest code..."
/usr/bin/git restore .
/usr/bin/git pull origin alawaly-voice-agent

echo "⏳ Installing dependencies..."
uv sync --active
source .venv/bin/activate
python agent.py download-files

echo "⏳ Restarting service..."
sudo /usr/bin/systemctl restart alawaly-agent.service

echo "✅ Deployment complete"
