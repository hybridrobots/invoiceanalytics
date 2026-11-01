#!/usr/bin/env bash
# Invoice Intelligence — one-command launcher for Debian/Ubuntu (PEP 668 safe)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

VENV="$ROOT/.venv"
PYTHON_BIN="${PYTHON_BIN:-python3}"
REQ_HASH_FILE="$VENV/.requirements.sha256"

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "Error: $PYTHON_BIN not found. Install with: sudo apt install python3 python3-venv python3-full" >&2
  exit 1
fi

if ! "$PYTHON_BIN" -c "import venv" 2>/dev/null; then
  echo "Error: python3-venv is missing. Install with: sudo apt install python3-venv python3-full" >&2
  exit 1
fi

if [[ ! -x "$VENV/bin/python" ]]; then
  echo "Creating virtual environment in .venv …"
  "$PYTHON_BIN" -m venv "$VENV"
fi

need_install=0
if [[ ! -f "$REQ_HASH_FILE" ]]; then
  need_install=1
elif command -v sha256sum >/dev/null 2>&1; then
  current="$(sha256sum "$ROOT/requirements.txt" | awk '{print $1}')"
  stored="$(cat "$REQ_HASH_FILE" 2>/dev/null || true)"
  if [[ "$current" != "$stored" ]]; then
    need_install=1
  fi
else
  need_install=1
fi

if ! "$VENV/bin/python" -c "import flask" 2>/dev/null; then
  need_install=1
fi

if [[ "$need_install" -eq 1 ]]; then
  echo "Installing dependencies into .venv …"
  "$VENV/bin/python" -m pip install --upgrade pip
  "$VENV/bin/python" -m pip install -r "$ROOT/requirements.txt"
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$ROOT/requirements.txt" | awk '{print $1}' > "$REQ_HASH_FILE"
  else
    date -u +%s > "$REQ_HASH_FILE"
  fi
  echo "Dependencies ready."
fi

echo "Starting Invoice Intelligence …"
exec "$VENV/bin/python" "$ROOT/run.py" "$@"
