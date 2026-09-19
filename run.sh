#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"
if [[ -x "$ROOT/.venv/bin/python" ]]; then
  exec "$ROOT/.venv/bin/python" -m uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8787}" "$@"
fi
exec python3 -m uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8787}" "$@"
