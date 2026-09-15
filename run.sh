#!/usr/bin/env bash
# Start the Quick Quote web app.
set -euo pipefail
cd "$(dirname "$0")"
[ -d .venv ] || python3 -m venv .venv
./.venv/bin/pip install -q -r requirements.txt
exec ./.venv/bin/python -m uvicorn quickquote.api.app:app \
  --app-dir backend --host "${HOST:-127.0.0.1}" --port "${PORT:-8000}" "$@"
