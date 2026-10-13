"""Grundlegende Steuerung des PCs: Webseiten, Programme, Systeminfos."""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
import webbrowser
from typing import Annotated
from urllib.parse import quote_plus

from jarvis.skills import skill


@skill
def webseite_oeffnen(url: Annotated[str, "Vollständige Adresse, z. B. https://youtube.com"]) -> str:
    """Öffnet eine Webseite im Standardbrowser."""
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    webbrowser.open(url)
    return f"Geöffnet: {url}"


@skill
def im_web_suchen(suchbegriff: str) -> str:
    """Öffnet eine Google-Suche im Browser, damit der Nutzer die Ergebnisse selbst sieht."""
    webbrowser.open(f"https://www.google.com/search?q={quote_plus(suchbegriff)}")
    return f"Suche nach '{suchbegriff}' geöffnet."


@skill
def programm_starten(
    programm: Annotated[str, "Programmname oder Pfad, z. B. 'notepad', 'calc', 'spotify' oder ein Dateipfad"],
) -> str:
    """Startet ein Programm oder öffnet eine Datei bzw. einen Ordner auf dem PC."""
    try:
        if sys.platform == "win32":
            path = os.path.expandvars(os.path.expanduser(programm))
            if os.path.exists(path):
                os.startfile(path)  # type: ignore[attr-defined]
            else:
                # "start" findet auch Programme aus dem Startmenü/App-Pfaden (z. B. spotify, chrome)
                name = programm.replace('"', "")
                subprocess.Popen(f'start "" "{name}"', shell=True)
        elif sys.platform == "darwin":
            if os.path.exists(os.path.expanduser(programm)):
                subprocess.Popen(["open", os.path.expanduser(programm)])
            else:
                subprocess.Popen(["open", "-a", programm])
        else:
            exe = shutil.which(programm)
            if exe:
                subprocess.Popen([exe], start_new_session=True)
            else:
                subprocess.Popen(["xdg-open", os.path.expanduser(programm)], start_new_session=True)
    except Exception as e:  # noqa: BLE001 – Fehler geht als Text an das Gehirn zurück
        return f"Konnte '{programm}' nicht starten: {e}"
    return f"'{programm}' wird gestartet."


@skill
def systeminfo() -> str:
    """Gibt Infos über den PC zurück: Betriebssystem, Rechnername, freier Speicherplatz."""
    total, _, free = shutil.disk_usage(os.path.expanduser("~"))
    return (f"Betriebssystem: {platform.system()} {platform.release()}, "
            f"Rechner: {platform.node()}, Python {platform.python_version()}, "
            f"Freier Speicher: {free / 1e9:.0f} von {total / 1e9:.0f} GB")
