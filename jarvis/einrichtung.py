"""Einrichtung: Zugangsdaten, Adressen und Programme, die Funktionen vom Nutzer brauchen.

Eine Funktion deklariert auf Modulebene, was sie braucht:

    TOKEN = Geheimnis("telegram_token", "Telegram-Bot-Schlüssel",
                      anleitung=["Öffne Telegram und suche @BotFather.", "Schreib /newbot …"],
                      link="https://t.me/BotFather", pruefen=_token_pruefen)
    ADRESSE = Einrichtungswert("drucker_adresse", "IP-Adresse des 3D-Druckers", sprachlich=True,
                               anleitung=["Am Drucker-Display unter Netzwerk nachsehen."])
    SLICER = Programm("prusaslicer", "PrusaSlicer", finden=_finde_slicer, link="https://…",
                      winget="Prusa3D.PrusaSlicer")

und benutzt sie mit TOKEN.wert() bzw. SLICER.pfad(). Fehlt etwas, wird FehlendeEinrichtung geworfen –
Jarvis erklärt dem Nutzer dann, was zu tun ist.

Gespeichert wird AUSSERHALB des Projektordners (nie in Git):
  Windows: %APPDATA%\\Jarvis\\einrichtung.json    sonst: ~/.config/jarvis/einrichtung.json
"""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from jarvis import sicherheit

NAME_MUSTER = re.compile(r"^[a-z][a-z0-9_]{1,40}$")


class FehlendeEinrichtung(Exception):
    """Ein benötigter Wert ist (noch) nicht eingerichtet. str() ist eine Erklärung für den Nutzer."""

    def __init__(self, wert: Einrichtungswert):
        self.wert = wert
        super().__init__(wert.fehlt_text())


# ---------------------------------------------------------------------------
# Speicher (eine JSON-Datei außerhalb des Projekts)
# ---------------------------------------------------------------------------

def speicher_datei() -> Path:
    eigen = os.environ.get("JARVIS_EINRICHTUNG_DATEI")
    if eigen:
        return Path(eigen)
    if os.environ.get("JARVIS_SELBSTTEST") == "1":
        return Path(tempfile.gettempdir()) / f"jarvis_selbsttest_einrichtung_{os.getpid()}.json"
    if sys.platform == "win32" and os.environ.get("APPDATA"):
        return Path(os.environ["APPDATA"]) / "Jarvis" / "einrichtung.json"
    return Path.home() / ".config" / "jarvis" / "einrichtung.json"


_lock = threading.RLock()
_bei_aenderung: list[Callable[[str], None]] = []


def _laden() -> dict[str, str]:
    try:
        daten = json.loads(speicher_datei().read_text(encoding="utf-8"))
        return {str(k): str(v) for k, v in daten.items()} if isinstance(daten, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _speichern(daten: dict[str, str]) -> None:
    datei = speicher_datei()
    datei.parent.mkdir(parents=True, exist_ok=True)
    tmp = datei.with_suffix(".tmp")
    tmp.write_text(json.dumps(daten, ensure_ascii=False, indent=2), encoding="utf-8")
    if os.name != "nt":
        os.chmod(tmp, 0o600)
    os.replace(tmp, datei)


def speicher_lesen(name: str) -> str | None:
    with _lock:
        return _laden().get(name)


def speicher_schreiben(name: str, wert: str | None) -> None:
    with _lock:
        daten = _laden()
        if wert is None:
            daten.pop(name, None)
        else:
            daten[name] = wert
        _speichern(daten)
    for rueckruf in list(_bei_aenderung):
        try:
            rueckruf(name)
        except Exception:  # noqa: BLE001
            pass


def bei_aenderung(rueckruf: Callable[[str], None]) -> None:
    """Rückruf, wenn ein Wert gesetzt/gelöscht wird (z. B. um wartende Dienste zu starten)."""
    _bei_aenderung.append(rueckruf)


# ---------------------------------------------------------------------------
# Deklarationen
# ---------------------------------------------------------------------------

_REGISTER: dict[str, Einrichtungswert] = {}


def funktion_von_modul(modul: str) -> str:
    """'jarvis.skills.telegram.dienst' -> 'telegram'; Kernmodule -> 'kern'."""
    teile = modul.split(".")
    if len(teile) >= 3 and teile[0] == "jarvis" and teile[1] == "skills":
        return teile[2]
    return "kern"


def _aufrufer_modul() -> str:
    """Das Modul, das den Einrichtungswert deklariert (erster Aufrufer außerhalb dieser Datei)."""
    frame = sys._getframe(1)
    while frame is not None and frame.f_globals.get("__name__") == __name__:
        frame = frame.f_back
    return frame.f_globals.get("__name__", "?") if frame is not None else "?"


class Einrichtungswert:
    """Ein Wert, den eine Funktion vom Nutzer braucht (Adresse, Name, Schlüssel …)."""

    art = "wert"

    def __init__(self, name: str, beschreibung: str, *, anleitung: str | list[str] = "",
                 geheim: bool = False, sprachlich: bool = False, standard: str | None = None,
                 link: str | None = None, pruefen: Callable[[str], str] | None = None) -> None:
        if not NAME_MUSTER.match(name):
            raise ValueError(f"Ungültiger Einrichtungsname '{name}' (nur a-z, 0-9, _; beginnt mit Buchstaben)")
        if geheim and sprachlich:
            raise ValueError(f"'{name}': geheime Werte dürfen nicht per Sprache gesetzt werden (sprachlich=False)")
        self.name = name
        self.beschreibung = beschreibung
        self.anleitung = [anleitung] if isinstance(anleitung, str) and anleitung else list(anleitung or [])
        self.geheim = geheim
        self.sprachlich = sprachlich
        self.standard = standard
        self.link = link
        self.pruefen = pruefen
        self.modul = _aufrufer_modul()
        self.funktion = funktion_von_modul(self.modul)
        bestehend = _REGISTER.get(name)
        if bestehend is not None and bestehend.modul != self.modul:
            raise ValueError(f"Einrichtungswert '{name}' existiert doppelt ({bestehend.modul} und {self.modul})")
        _REGISTER[name] = self

    # --- Wert ---------------------------------------------------------------
    @property
    def umgebungsvariable(self) -> str:
        return f"JARVIS_{self.name.upper()}"

    def _roh(self) -> str | None:
        return speicher_lesen(self.name) or os.environ.get(self.umgebungsvariable) or self.standard

    def wert(self) -> str:
        """Der eingerichtete Wert. Wirft FehlendeEinrichtung, wenn er fehlt."""
        wert = self._roh()
        if not wert:
            raise FehlendeEinrichtung(self)
        return wert

    @property
    def vorhanden(self) -> bool:
        return bool(self._roh())

    def setzen(self, wert: str) -> None:
        """Speichert den Wert (z. B. nach einer automatischen Kopplung)."""
        speicher_schreiben(self.name, str(wert).strip())

    def loeschen(self) -> None:
        speicher_schreiben(self.name, None)

    # --- Texte ----------------------------------------------------------------
    def anleitung_text(self) -> str:
        schritte = [f"{i}. {s}" for i, s in enumerate(self.anleitung, 1)]
        if self.link:
            schritte.append(f"Link: {self.link}")
        return "\n".join(schritte)

    def wie_eingeben(self) -> str:
        if self.geheim:
            return (f"Eingabe am PC in einem kleinen Fenster (Jarvis öffnet es mit einrichtung_erfassen) "
                    f"oder getippt: /einrichten {self.name}")
        if self.sprachlich:
            return f"Der Nutzer kann den Wert einfach sagen, oder tippen: /einrichten {self.name} <wert>"
        return f"Eingabe am PC: /einrichten {self.name} (oder per einrichtung_erfassen)"

    def fehlt_text(self) -> str:
        text = f"Für die Funktion '{self.funktion}' fehlt noch: {self.beschreibung} ({self.name})."
        if self.anleitung:
            text += f" So geht's:\n{self.anleitung_text()}"
        return text + f"\n{self.wie_eingeben()}"

    def anzeige(self) -> str:
        """Status ohne geheime Werte."""
        if not self.vorhanden:
            return "fehlt"
        if self.geheim:
            return "eingerichtet (geheim)"
        return f"eingerichtet: {self._roh()}"


class Geheimnis(Einrichtungswert):
    """Ein geheimer Wert (Token, Passwort, API-Schlüssel). Wird nie angezeigt, vorgelesen oder an Claude geschickt."""

    art = "geheimnis"

    def __init__(self, name: str, beschreibung: str, **kwargs: Any) -> None:
        kwargs["geheim"] = True
        kwargs.pop("sprachlich", None)
        super().__init__(name, beschreibung, **kwargs)


class Programm(Einrichtungswert):
    """Ein Programm auf dem PC (z. B. ein Slicer). Wird gesucht; der Nutzer installiert es selbst."""

    art = "programm"

    def __init__(self, name: str, beschreibung: str, *, finden: Callable[[], str | None],
                 anleitung: str | list[str] = "", link: str | None = None, winget: str | None = None) -> None:
        super().__init__(name, beschreibung, anleitung=anleitung, link=link)
        self.finden = finden
        self.winget = winget

    def _roh(self) -> str | None:
        eigen = speicher_lesen(self.name) or os.environ.get(self.umgebungsvariable)
        if eigen and Path(eigen).exists():
            return eigen
        try:
            return self.finden() or None
        except Exception:  # noqa: BLE001
            return None

    def pfad(self) -> str:
        """Pfad zum Programm. Wirft FehlendeEinrichtung, wenn es nicht gefunden wird."""
        return self.wert()

    def wie_eingeben(self) -> str:
        text = "Das Programm ist nicht installiert (oder wurde nicht gefunden)."
        if self.winget and sys.platform == "win32":
            text += f" Jarvis kann es nach Zustimmung installieren (programm_installieren('{self.name}'))."
        return text + f" Eigener Pfad: /einrichten {self.name} <pfad>"

    def anzeige(self) -> str:
        pfad = self._roh()
        return f"gefunden: {pfad}" if pfad else "nicht gefunden"


# ---------------------------------------------------------------------------
# Abfragen
# ---------------------------------------------------------------------------

def alle() -> list[Einrichtungswert]:
    return list(_REGISTER.values())


def finden(name: str) -> Einrichtungswert | None:
    return _REGISTER.get(name)


def fehlende(funktion: str = "") -> list[Einrichtungswert]:
    return [w for w in _REGISTER.values()
            if not w.vorhanden and w.standard is None and (not funktion or w.funktion == funktion)]


def geheime_werte() -> list[str]:
    werte = []
    for w in _REGISTER.values():
        if w.geheim:
            roh = w._roh()
            if roh:
                werte.append(roh)
    return werte


def status_text(funktion: str = "") -> str:
    """Übersicht für Gehirn und Konsole – ohne geheime Werte."""
    zeilen = []
    for w in sorted(_REGISTER.values(), key=lambda w: (w.funktion, w.name)):
        if funktion and w.funktion != funktion:
            continue
        zeilen.append(f"[{w.funktion}] {w.name} – {w.beschreibung}: {w.anzeige()}")
    return "\n".join(zeilen) if zeilen else "Keine Funktion braucht eine Einrichtung."


sicherheit.registriere_geheimnisquelle(geheime_werte)
