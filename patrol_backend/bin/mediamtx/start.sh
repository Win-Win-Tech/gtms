#!/usr/bin/env bash
# Start MediaMTX for GTMS CCTV live view (Phase 2)
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"
if pgrep -x mediamtx >/dev/null 2>&1; then
  echo "MediaMTX already running (pid $(pgrep -x mediamtx | head -1))"
  exit 0
fi
nohup ./mediamtx ./mediamtx.yml > mediamtx.log 2>&1 &
echo "Started MediaMTX pid $! — HLS :8888 API :9997"
echo "Log: $DIR/mediamtx.log"
