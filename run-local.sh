#!/usr/bin/env bash
# Start the SaveBridge server locally on 127.0.0.1:8723.
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -d .venv ]; then
	python3 -m venv .venv
	./.venv/bin/pip install --upgrade pip
	./.venv/bin/pip install -r requirements.txt
fi

if ! command -v ffmpeg >/dev/null 2>&1; then
	echo "WARNING: ffmpeg not found on PATH. Merging/mp3/3gp will fail until it is installed." >&2
fi

exec ./.venv/bin/uvicorn savebridge_server.server:app --host 127.0.0.1 --port 8723
