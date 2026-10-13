"""Laufzeit-Protokoll (data/protokoll.log) und Fehlerliste (data/fehler.jsonl) – Geheimnisse sind geschwärzt.

Claude Code liest beides, wenn es eine Funktion reparieren soll. Eigene Funktionen loggen einfach mit
logging.getLogger(__name__) – alles unter "jarvis.*" landet hier.
"""

from __future__ import annotations

import asyncio
import errno
import json
import logging
import logging.handlers
import traceback
from datetime import datetime, timedelta
from typing import Any

from jarvis import config
from jarvis.einrichtung import FehlendeEinrichtung
from jarvis.sicherheit import schwaerzen

LOGGER = logging.getLogger("jarvis")
_NETZ_ERRNOS = {errno.ECONNREFUSED, errno.ECONNRESET, errno.ECONNABORTED, errno.EHOSTUNREACH,
                errno.ENETUNREACH, errno.ETIMEDOUT, getattr(errno, "EAI_NONAME", -2)}


def protokoll_datei():
    return config.DATA_DIR / "protokoll.log"


def fehler_datei():
    return config.DATA_DIR / "fehler.jsonl"


class _Schwaerzer(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = schwaerzen(record.getMessage())
        record.args = None
        if record.exc_info:
            record.exc_text = schwaerzen(logging.Formatter().formatException(record.exc_info))
            record.exc_info = None
        return True


def einrichten() -> None:
    """Schreibt ab jetzt alles von 'jarvis.*' (ab INFO) nach data/protokoll.log."""
    if any(getattr(h, "_jarvis", False) for h in LOGGER.handlers):
        return
    config.ensure_data_dir()
    handler = logging.handlers.RotatingFileHandler(protokoll_datei(), maxBytes=1_000_000, backupCount=1,
                                                   encoding="utf-8")
    handler._jarvis = True  # type: ignore[attr-defined]
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s", "%Y-%m-%d %H:%M:%S"))
    handler.addFilter(_Schwaerzer())
    LOGGER.addHandler(handler)
    LOGGER.setLevel(logging.INFO)
    LOGGER.propagate = False


def klassifizieren(exc: BaseException) -> str:
    """'einrichtung', 'verbindung' oder 'programmfehler'."""
    if isinstance(exc, FehlendeEinrichtung):
        return "einrichtung"
    if isinstance(exc, (ConnectionError, TimeoutError, asyncio.TimeoutError)):
        return "verbindung"
    if isinstance(exc, OSError) and exc.errno in _NETZ_ERRNOS:
        return "verbindung"
    name = type(exc).__name__.lower()
    if any(w in name for w in ("connect", "timeout", "clientoserror", "servernotfound", "unreachable")):
        return "verbindung"
    return "programmfehler"


def fehler_merken(funktion: str, exc: BaseException, wo: str) -> str:
    """Speichert einen Fehler und gibt einen Hinweis für das Gehirn zurück."""
    art = klassifizieren(exc)
    kurz = schwaerzen(f"{type(exc).__name__}: {exc}")[:600]
    if art != "einrichtung":
        tb = schwaerzen("".join(traceback.format_exception(type(exc), exc, exc.__traceback__)))[-4000:]
        LOGGER.warning("Fehler in %s (%s, %s): %s\n%s", funktion, wo, art, kurz, tb)
        try:
            config.ensure_data_dir()
            datei = fehler_datei()
            zeilen = datei.read_text(encoding="utf-8").splitlines()[-199:] if datei.exists() else []
            zeilen.append(json.dumps({"zeit": datetime.now().isoformat(timespec="seconds"), "funktion": funktion,
                                      "wo": wo, "art": art, "fehler": kurz, "verlauf": tb}, ensure_ascii=False))
            datei.write_text("\n".join(zeilen) + "\n", encoding="utf-8")
        except OSError:
            pass
    if art == "einrichtung":
        return str(exc)
    if art == "verbindung":
        return (f"Verbindungsproblem (vermutlich kein Programmfehler): {kurz}. Wahrscheinlich ist das Gerät oder "
                f"der Dienst nicht erreichbar, ausgeschaltet oder die Adresse stimmt nicht.")
    return f"Programmfehler in '{funktion}': {kurz}. Das kann Claude Code per selbst_verbessern reparieren."


def letzte_fehler(funktion: str = "", anzahl: int = 10) -> list[dict[str, Any]]:
    try:
        zeilen = fehler_datei().read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    eintraege = []
    for zeile in reversed(zeilen):
        try:
            e = json.loads(zeile)
        except json.JSONDecodeError:
            continue
        if not funktion or e.get("funktion") == funktion:
            eintraege.append(e)
        if len(eintraege) >= anzahl:
            break
    return eintraege


def protokoll_ende(stunden: int = 48, max_zeilen: int = 30) -> str:
    """Die letzten Warnungen/Fehler aus dem Protokoll (für Claude Code beim Reparieren)."""
    try:
        zeilen = protokoll_datei().read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    grenze = datetime.now() - timedelta(hours=stunden)
    treffer: list[str] = []
    for zeile in zeilen:
        try:
            zeit = datetime.strptime(zeile[:19], "%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
        if zeit >= grenze and (" WARNING " in zeile or " ERROR " in zeile):
            treffer.append(zeile[:400])
    return "\n".join(treffer[-max_zeilen:])
