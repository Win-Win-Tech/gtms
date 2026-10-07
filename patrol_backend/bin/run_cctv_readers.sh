#!/usr/bin/env bash
# Run the CCTV vehicle (ANPR) reader and face reader from one systemd service.
# Each reader runs only if its switch is on (ANPR_ENABLED / FACE_CCTV_ENABLED).
# If one reader stops, the other is stopped too and the script exits non-zero,
# so systemd (Restart=always) starts both again.
set -uo pipefail

APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$APP_DIR"

if [[ -z "${PYTHON:-}" ]]; then
  for candidate in "$APP_DIR/../gtms_venv/bin/python" "$APP_DIR/../venv/bin/python"; do
    if [[ -x "$candidate" ]]; then
      PYTHON="$candidate"
      break
    fi
  done
fi
if [[ -z "${PYTHON:-}" ]]; then
  echo "run_cctv_readers: python not found — set PYTHON=/path/to/venv/bin/python" >&2
  exit 1
fi

is_on() {
  case "${1,,}" in
    1 | true | yes) return 0 ;;
    *) return 1 ;;
  esac
}

pids=()
stop_all() {
  for pid in "${pids[@]}"; do
    kill -TERM "$pid" 2>/dev/null
  done
  wait
}
trap 'stop_all; exit 0' TERM INT

if is_on "${ANPR_ENABLED:-false}"; then
  "$PYTHON" manage.py run_anpr_reader &
  pids+=($!)
  echo "run_cctv_readers: vehicle reader started (pid $!)"
fi

if is_on "${FACE_CCTV_ENABLED:-false}"; then
  # Lower priority: when CPU is short, vehicle gate entry wins over face.
  nice -n "${FACE_CCTV_NICE:-10}" "$PYTHON" manage.py run_face_reader &
  pids+=($!)
  echo "run_cctv_readers: face reader started (pid $!)"
fi

if ((${#pids[@]} == 0)); then
  echo "run_cctv_readers: nothing to run — set ANPR_ENABLED=true and/or FACE_CCTV_ENABLED=true" >&2
  exit 1
fi

wait -n
code=$?
echo "run_cctv_readers: a reader stopped (exit $code) — stopping the other so systemd restarts both" >&2
stop_all
exit $((code == 0 ? 1 : code))
