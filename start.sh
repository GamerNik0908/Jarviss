#!/usr/bin/env sh
# Startet Jarvis unter Linux/macOS.
cd "$(dirname "$0")" || exit 1
exec python3 start.py "$@"
