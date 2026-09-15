#!/usr/bin/env bash
# Start the Quick Quote web app.
#
# Works on macOS, Linux and Windows (Git Bash / MSYS2). Windows lays a virtual
# environment out as .venv/Scripts/python.exe; everywhere else it is
# .venv/bin/python. This script resolves whichever one is present rather than
# assuming either.
set -euo pipefail
cd "$(dirname "$0")"

MIN_PYTHON="3.11"
VENV_PY=""

# Locate the interpreter inside .venv, whichever layout created it.
detect_venv_python() {
  local candidate
  for candidate in .venv/bin/python .venv/bin/python3 \
                   .venv/Scripts/python.exe .venv/Scripts/python; do
    if [ -e "$candidate" ]; then
      VENV_PY="$candidate"
      return 0
    fi
  done
  return 1
}

# True when the given command is a real Python new enough to run this project.
# The version check also filters out the Microsoft Store stub that Windows
# installs as "python", which exists on PATH but is not an interpreter.
python_is_usable() {
  "$@" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' \
    >/dev/null 2>&1
}

# Find a host interpreter to build the virtual environment with. Windows
# usually has "python" or the "py" launcher rather than "python3".
find_host_python() {
  local candidate
  for candidate in python3 python; do
    if command -v "$candidate" >/dev/null 2>&1 && python_is_usable "$candidate"; then
      HOST_PYTHON=("$candidate")
      return 0
    fi
  done
  if command -v py >/dev/null 2>&1 && python_is_usable py -3; then
    HOST_PYTHON=(py -3)
    return 0
  fi
  return 1
}

if ! detect_venv_python; then
  if ! find_host_python; then
    echo "Quick Quote needs Python ${MIN_PYTHON} or newer, and none was found." >&2
    echo >&2
    echo "Tried: python3, python, py -3" >&2
    echo "Install it from https://www.python.org/downloads/ (on Windows, tick" >&2
    echo "\"Add python.exe to PATH\"), then run this script again." >&2
    exit 1
  fi

  echo "Creating virtual environment in .venv ..."
  # Removed first so an interrupted earlier attempt cannot leave a half-built
  # environment that looks present but has no interpreter.
  rm -rf .venv
  "${HOST_PYTHON[@]}" -m venv .venv

  if ! detect_venv_python; then
    echo "Could not create a virtual environment in .venv." >&2
    echo "On Debian/Ubuntu this usually means python3-venv is not installed:" >&2
    echo "  sudo apt install python3-venv" >&2
    exit 1
  fi
fi

# "python -m pip" rather than the pip executable: the module is always present
# in a virtual environment, and this avoids a second path that differs by
# platform.
"$VENV_PY" -m pip install -q --upgrade pip
"$VENV_PY" -m pip install -q -r requirements.txt

echo "Quick Quote is starting on http://${HOST:-127.0.0.1}:${PORT:-8000}"
exec "$VENV_PY" -m uvicorn quickquote.api.app:app \
  --app-dir backend --host "${HOST:-127.0.0.1}" --port "${PORT:-8000}" "$@"
