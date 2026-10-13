"""Farbige Konsolenausgabe. Alles wird vor der Ausgabe geschwärzt (keine Geheimnisse im Fenster)."""

from __future__ import annotations

import logging
import os
import threading

from jarvis.sicherheit import schwaerzen

if os.name == "nt":
    os.system("")  # schaltet ANSI-Farben in der Windows-Konsole ein

_lock = threading.Lock()
_log = logging.getLogger("jarvis.ui")

CYAN = "\033[96m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
MAGENTA = "\033[95m"
RED = "\033[91m"
GREY = "\033[90m"
BOLD = "\033[1m"
RESET = "\033[0m"


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


def jarvis(text: str) -> None:
    _print(f"{CYAN}{BOLD}Jarvis:{RESET} {CYAN}{text}{RESET}")


def user(text: str, quelle: str = "tastatur") -> None:
    icon = {"mikrofon": "🎤", "tastatur": "⌨ "}.get(quelle, "📱")
    name = "Du" if quelle in ("mikrofon", "tastatur") else f"Du ({quelle})"
    _print(f"{GREEN}{icon} {name}:{RESET} {text}")


def info(text: str) -> None:
    eingerueckt = "\n".join(f"  {zeile}" for zeile in str(text).splitlines() or [""])
    _print(f"{GREY}{eingerueckt}{RESET}")


def dev(text: str) -> None:
    _print(f"{MAGENTA}  [Claude Code] {text}{RESET}")


def warn(text: str) -> None:
    _print(f"{YELLOW}  ⚠ {text}{RESET}")
    _log.warning(text)


def error(text: str) -> None:
    _print(f"{RED}  ✖ {text}{RESET}")
    _log.error(text)


def kasten(titel: str, text: str) -> None:
    """Eine hervorgehobene Box (z. B. Einrichtungs-Anleitungen mit Links)."""
    zeilen = text.splitlines() or [""]
    breite = min(max(len(titel) + 4, *(len(z) for z in zeilen)) + 2, 100)
    rahmen = "─" * breite
    inhalt = "\n".join(f"{YELLOW}│{RESET} {z}" for z in zeilen)
    _print(f"{YELLOW}┌{rahmen}\n│ {BOLD}{titel}{RESET}\n{inhalt}\n{YELLOW}└{rahmen}{RESET}")
