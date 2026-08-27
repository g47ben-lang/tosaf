#!/usr/bin/env bash
# One-shot provisioning for an Ubuntu VM (Oracle Cloud Always Free, etc.).
# Installs ffmpeg + python deps, sets up the venv, and installs the systemd
# service. Run from the repo root:  bash deploy/setup-oracle.sh
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_DIR"

echo "==> Installing system packages (ffmpeg, python venv)"
sudo apt-get update -y
sudo apt-get install -y python3-venv python3-pip ffmpeg

echo "==> Creating virtualenv and installing Python deps"
python3 -m venv .venv
./.venv/bin/pip install --upgrade pip
./.venv/bin/pip install -r requirements.txt

echo "==> Installing systemd service"
sudo cp deploy/savebridge.service /etc/systemd/system/savebridge.service
# point the unit at this checkout and the current user
sudo sed -i "s#/home/ubuntu/tosaf#${REPO_DIR}#g" /etc/systemd/system/savebridge.service
sudo sed -i "s#^User=.*#User=$(whoami)#" /etc/systemd/system/savebridge.service
sudo systemctl daemon-reload
sudo systemctl enable --now savebridge

echo
echo "Server is running on 127.0.0.1:8723 (behind Caddy for https)."
echo "Next: install Caddy, edit deploy/Caddyfile with your domain, and run it."
echo "Update yt-dlp any time with:  ./.venv/bin/pip install -U yt-dlp"
