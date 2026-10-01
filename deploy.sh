#!/bin/bash
set -e

APP_DIR="/home/Digital_Asset_live_kit/Digital_Asset_live_kit"

cd "$APP_DIR"

echo "⏳ Pulling latest code..."
/usr/bin/git restore .
/usr/bin/git pull origin main

echo "⏳ Installing dependencies..."
/usr/bin/npm install

echo "⏳ Restarting service..."
/usr/bin/systemctl restart digital-asset-agent.service

echo "✅ Deployment complete"
#test
