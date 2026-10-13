"""Farbige Konsolenausgabe."""

from __future__ import annotations

import os
import threading

if os.name == "nt":
    os.system("")  # schaltet ANSI-Farben in der Windows-Konsole ein

_lock = threading.Lock()

CYAN = "\033[96m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
MAGENTA = "\033[95m"
RED = "\033[91m"
GREY = "\033[90m"
BOLD = "\033[1m"
RESET = "\033[0m"


def _print(text: str) -> None:
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


def user(text: str, spoken: bool = False) -> None:
    icon = "🎤" if spoken else "⌨ "
    _print(f"{GREEN}{icon} Du:{RESET} {text}")


def info(text: str) -> None:
    _print(f"{GREY}  {text}{RESET}")


def dev(text: str) -> None:
    _print(f"{MAGENTA}  [Entwickler] {text}{RESET}")


def warn(text: str) -> None:
    _print(f"{YELLOW}  ⚠ {text}{RESET}")


def error(text: str) -> None:
    _print(f"{RED}  ✖ {text}{RESET}")
