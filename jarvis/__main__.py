"""Einstiegspunkt: python -m jarvis

Normalerweise startest du Jarvis über start.py (bzw. "Jarvis starten.bat"),
denn nur dann klappen automatische Neustarts nach Selbstverbesserungen.

Optionen:
  --text         Nur Tastatur und Text, kein Mikrofon und keine Sprachausgabe
  --selbsttest   Prüft, ob Jarvis startfähig ist (wird nach jeder Änderung ausgeführt)
"""

from __future__ import annotations

import argparse
import asyncio
import sys


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass

    parser = argparse.ArgumentParser(prog="jarvis", description="J.A.R.V.I.S. Sprachassistent")
    parser.add_argument("--text", action="store_true", help="nur Textmodus (kein Mikrofon, keine Sprache)")
    parser.add_argument("--selbsttest", action="store_true", help="Selbsttest ausführen")
    args = parser.parse_args()

    if args.selbsttest:
        from jarvis.selftest import run_selftest

        return run_selftest()

    from jarvis.app import Jarvis
    from jarvis.config import load_config

    try:
        return asyncio.run(Jarvis(load_config(), text_only=args.text).run())
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
