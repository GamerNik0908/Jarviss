"""Farbige Konsolenausgabe. Alles wird vor der Ausgabe geschwärzt (keine Geheimnisse im Fenster).

Beobachter (z. B. die HUD-Oberfläche) bekommen jede Ausgabe zusätzlich als dict – ebenfalls geschwärzt:
    {"typ": "log", "art": "jarvis|user|info|dev|warn|error|kasten", "text": …, "quelle": …, "titel": …, "zeit": …}
    {"typ": "zustand", "zustand": "bereit|denkt|spricht"}
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections.abc import Callable
from typing import Any

from jarvis.sicherheit import schwaerzen

if os.name == "nt":
    os.system("")  # schaltet ANSI-Farben in der Windows-Konsole ein

_lock = threading.Lock()
_log = logging.getLogger("jarvis.ui")
_beobachter: list[Callable[[dict[str, Any]], None]] = []

CYAN = "\033[96m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
MAGENTA = "\033[95m"
RED = "\033[91m"
GREY = "\033[90m"
BOLD = "\033[1m"
RESET = "\033[0m"


def beobachten(rueckruf: Callable[[dict[str, Any]], None]) -> Callable[[], None]:
    """Meldet einen Beobachter an (muss thread-sicher sein). Gibt eine Funktion zum Abmelden zurück."""
    _beobachter.append(rueckruf)
    return lambda: _beobachter.remove(rueckruf) if rueckruf in _beobachter else None


def _benachrichtigen(eintrag: dict[str, Any]) -> None:
    for rueckruf in list(_beobachter):
        try:
            rueckruf(eintrag)
        except Exception:  # noqa: BLE001 – die Konsole darf nie an einem Beobachter scheitern
            pass


def _ausgeben(art: str, text: str, konsole: str, **extra: Any) -> None:
    _print(konsole)
    if _beobachter:
        _benachrichtigen({"typ": "log", "art": art, "text": schwaerzen(text), "zeit": time.time(),
                          **{k: schwaerzen(v) for k, v in extra.items()}})


def _print(text: str) -> None:
    text = schwaerzen(text)
    with _lock:
        try:
            print(text, flush=True)
        except UnicodeEncodeError:
            print(text.encode("ascii", "replace").decode(), flush=True)


def banner() -> None:
    _print(f"""{CYAN}{BOLD}
     ╦╔═╗╦═╗╦  ╦╦╔═╗
     ║╠═╣╠╦╝╚╗╔╝║╚═╗
    ╚╝╩ ╩╩╚═ ╚╝ ╩╚═╝{RESET}
{GREY}  Tippe eine Nachricht und drücke Enter – oder sprich mit mir.
  /hilfe zeigt alle Befehle.{RESET}
""")


def zustand(name: str) -> None:
    """Was Jarvis gerade tut ("bereit", "denkt", "spricht") – nur für Beobachter (HUD)."""
    _benachrichtigen({"typ": "zustand", "zustand": name})


def jarvis(text: str) -> None:
    _ausgeben("jarvis", text, f"{CYAN}{BOLD}Jarvis:{RESET} {CYAN}{text}{RESET}")


def user(text: str, quelle: str = "tastatur") -> None:
    icon = {"mikrofon": "🎤", "tastatur": "⌨ "}.get(quelle, "📱")
    name = "Du" if quelle in ("mikrofon", "tastatur") else f"Du ({quelle})"
    _ausgeben("user", text, f"{GREEN}{icon} {name}:{RESET} {text}", quelle=quelle)


def info(text: str) -> None:
    eingerueckt = "\n".join(f"  {zeile}" for zeile in str(text).splitlines() or [""])
    _ausgeben("info", str(text), f"{GREY}{eingerueckt}{RESET}")


def dev(text: str) -> None:
    _ausgeben("dev", text, f"{MAGENTA}  [Claude Code] {text}{RESET}")


def projekt(name: str, text: str) -> None:
    """Fortschritt der Projektarbeit (z. B. am Roblox-Spiel)."""
    _ausgeben("projekt", text, f"{MAGENTA}  [Projekt {name}] {text}{RESET}", quelle=name)


def warn(text: str) -> None:
    _ausgeben("warn", text, f"{YELLOW}  ⚠ {text}{RESET}")
    _log.warning(text)


def error(text: str) -> None:
    _ausgeben("error", text, f"{RED}  ✖ {text}{RESET}")
    _log.error(text)


def kasten(titel: str, text: str) -> None:
    """Eine hervorgehobene Box (z. B. Einrichtungs-Anleitungen mit Links)."""
    zeilen = text.splitlines() or [""]
    breite = min(max(len(titel) + 4, *(len(z) for z in zeilen)) + 2, 100)
    rahmen = "─" * breite
    inhalt = "\n".join(f"{YELLOW}│{RESET} {z}" for z in zeilen)
    _ausgeben("kasten", text, f"{YELLOW}┌{rahmen}\n│ {BOLD}{titel}{RESET}\n{inhalt}\n{YELLOW}└{rahmen}{RESET}", titel=titel)
