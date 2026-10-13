"""Einstiegspunkt: python -m jarvis

Normalerweise startest du Jarvis über start.py (bzw. "Jarvis starten.bat"),
denn nur dann klappen automatische Neustarts nach Selbstverbesserungen.

Optionen:
  --text                  Nur Tastatur und Text, kein Mikrofon und keine Sprachausgabe
  --selbsttest            Prüft, ob Jarvis startfähig ist (wird nach jeder Änderung ausgeführt)
  --einrichtung [NAME]    Zeigt den Einrichtungsstand (ohne geheime Werte) und prüft gesetzte Werte
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import tempfile


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass

    parser = argparse.ArgumentParser(prog="jarvis", description="J.A.R.V.I.S. Sprachassistent")
    parser.add_argument("--text", action="store_true", help="nur Textmodus (kein Mikrofon, keine Sprache)")
    parser.add_argument("--selbsttest", action="store_true", help="Selbsttest ausführen")
    parser.add_argument("--einrichtung", nargs="?", const="", default=None, metavar="FUNKTION",
                        help="Einrichtungsstand anzeigen und prüfen")
    args = parser.parse_args()

    if args.selbsttest:
        # Wie eine frische, unkonfigurierte Installation: leere Daten, leerer Einrichtungs-Speicher.
        tmp = tempfile.mkdtemp(prefix="jarvis_selbsttest_")
        os.environ["JARVIS_SELBSTTEST"] = "1"
        os.environ["JARVIS_DATA_DIR"] = os.path.join(tmp, "data")
        os.environ["JARVIS_EINRICHTUNG_DATEI"] = os.path.join(tmp, "einrichtung.json")
        from jarvis.selftest import run_selftest

        return run_selftest()

    if args.einrichtung is not None:
        from jarvis.selftest import einrichtung_anzeigen

        return einrichtung_anzeigen(args.einrichtung)

    from jarvis.app import Jarvis
    from jarvis.config import load_config

    try:
        return asyncio.run(Jarvis(load_config(), text_only=args.text).run())
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
