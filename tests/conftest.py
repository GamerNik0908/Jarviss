"""Gemeinsame Test-Einstellungen.

GESCHÜTZTE DATEI (Sicherheitsnetz): Tests laufen isoliert (leerer Daten-Ordner, leerer Einrichtungs-Speicher)
und offline (Verbindungen nach außen sind gesperrt). So kann kein Test echte Nachrichten verschicken oder
echte Zugangsdaten benutzen.
"""

import os
import tempfile

_tmp = tempfile.mkdtemp(prefix="jarvis_tests_")
os.environ.setdefault("JARVIS_SELBSTTEST", "1")
os.environ.setdefault("JARVIS_DATA_DIR", os.path.join(_tmp, "data"))
os.environ.setdefault("JARVIS_EINRICHTUNG_DATEI", os.path.join(_tmp, "einrichtung.json"))

from jarvis.selftest import netz_sperren  # noqa: E402 – erst nach dem Setzen der Umgebung importieren

netz_sperren()
