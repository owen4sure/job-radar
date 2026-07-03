#!/usr/bin/env bash
# One-command start: venv → deps → server. Reads .env if present.
set -e
cd "$(dirname "$0")"
[ -f .env ] && export $(grep -v '^#' .env | xargs) || echo "No .env — copy .env.example → .env and set OPENAI_API_KEY"
[ -d .venv ] || python3 -m venv .venv
./.venv/bin/pip install -q -r requirements.txt
echo "→ http://127.0.0.1:${PORT:-8080}"
exec ./.venv/bin/uvicorn app:app --host 0.0.0.0 --port "${PORT:-8080}"
