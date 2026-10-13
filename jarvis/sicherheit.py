"""Sicherheitsnetz: Schwärzen von Geheimnissen, Vertrauensstufen, Bestätigungen, Kanal-Kopplung, Entwickler-Wächter.

GESCHÜTZTE DATEI: Änderungen hieran fragt Jarvis vor dem Übernehmen ausdrücklich beim Nutzer nach.
Die Regeln hier greifen im Code (nicht nur im Prompt) und schlagen im Zweifel "zu" (fail-closed).
"""

from __future__ import annotations

import re
import secrets
import tempfile
import threading
import time
from collections import deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Dateien, deren Änderung durch Claude Code der Nutzer vor dem Übernehmen bestätigen muss.
GESCHUETZT = (
    "start.py",
    "jarvis/developer.py",
    "jarvis/selftest.py",
    "jarvis/sicherheit.py",
    "tests/test_sicherheit.py",
    "tests/conftest.py",
    ".gitignore",
    ".claude/",
)

# ---------------------------------------------------------------------------
# Geheimnisse erkennen und schwärzen
# ---------------------------------------------------------------------------

TOKEN_MUSTER = [
    re.compile(r"(?<!\d)\d{6,12}:[A-Za-z0-9_-]{30,}"),          # Telegram-Bot-Token (auch in URLs)
    re.compile(r"sk-ant-[A-Za-z0-9_-]{20,}"),                   # Anthropic
    re.compile(r"\bsk-[A-Za-z0-9_-]{32,}"),                     # OpenAI u. ä.
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}"),                # GitHub
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{40,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),                        # AWS
    re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"),                   # Google
    re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"),              # Slack
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
]
# Nur für Eingaben des Nutzers: lange Zeichenkette aus Buchstaben UND Ziffern ohne Leerzeichen.
_LANGES_WORT = re.compile(r"(?<![\w/.:-])(?=[A-Za-z0-9_-]*\d)(?=[A-Za-z0-9_-]*[A-Za-z])[A-Za-z0-9_-]{32,}(?![\w/.-])")
PLATZHALTER = re.compile(r"test|dummy|beispiel|example|platzhalter|fake|xxxx|0000|1234", re.I)

GESCHWAERZT = "[geheim]"

# Liefert die aktuell gespeicherten geheimen Werte (registriert von jarvis.einrichtung).
_geheimnisquellen: list[Callable[[], Iterable[str]]] = []


def registriere_geheimnisquelle(quelle: Callable[[], Iterable[str]]) -> None:
    _geheimnisquellen.append(quelle)


def bekannte_geheimnisse() -> list[str]:
    werte: set[str] = set()
    for quelle in _geheimnisquellen:
        try:
            werte.update(w for w in quelle() if w and len(w) >= 6)
        except Exception:  # noqa: BLE001 – Schwärzen darf nie selbst scheitern
            pass
    return sorted(werte, key=len, reverse=True)


def schwaerzen(text: Any) -> str:
    """Ersetzt bekannte Geheimnisse und typische Schlüssel-Muster durch [geheim]."""
    text = str(text)
    for wert in bekannte_geheimnisse():
        if wert in text:
            text = text.replace(wert, GESCHWAERZT)
    for muster in TOKEN_MUSTER:
        text = muster.sub(GESCHWAERZT, text)
    return text


def sieht_nach_schluessel_aus(text: str) -> bool:
    """True, wenn eine Eingabe vermutlich ein Passwort/Token enthält (soll nicht an Claude gehen)."""
    if any(w in text for w in bekannte_geheimnisse()):
        return True
    if any(m.search(text) for m in TOKEN_MUSTER):
        return True
    return bool(_LANGES_WORT.search(text))


# ---------------------------------------------------------------------------
# Vertrauen pro Gesprächszug und Bestätigungen
# ---------------------------------------------------------------------------

NACHFRAGEN = ("nie", "wenn_fremd", "immer")
LOKALE_QUELLEN = ("tastatur", "mikrofon")


@dataclass
class Zug:
    """Ein Gesprächszug des Gehirns – wer hat ihn ausgelöst, und kamen fremde Inhalte hinzu?"""

    quelle: str                 # "tastatur" | "mikrofon" | "kanal:telegram" | "ereignis:<quelle>"
    vom_nutzer: bool            # nur Tastatur, Mikrofon und der gekoppelte Besitzer eines Kanals
    fremd: bool = False         # Webseiten, fremde Nachrichten o. ä. wurden in diesem Zug gelesen
    kanal: str | None = None    # bei Kanal-Zügen: Name des Kanals (für Rückfragen)

    @property
    def lokal(self) -> bool:
        return self.quelle in LOKALE_QUELLEN


def unbekannter_zug() -> Zug:
    """Für Werkzeugaufrufe ohne bekannten Zug: so misstrauisch wie möglich."""
    return Zug(quelle="unbekannt", vom_nutzer=False, fremd=True)


def braucht_bestaetigung(nachfragen: str, zug: Zug | None) -> bool:
    if nachfragen == "immer":
        return True
    if nachfragen == "wenn_fremd":
        return zug is None or zug.fremd or not zug.vom_nutzer
    if nachfragen == "nie":
        return False
    return True  # unbekannter Wert -> sicherheitshalber fragen


_JA = re.compile(r"^\W*(ja|jawohl|jep|jup|klar|sicher|ok(ay)?|gerne?|mach(e)?( (das|es|ruhig))?|los|leg los|"
                 r"bestätig\w*|einverstanden|passt|yes|y|j)\b", re.I)
_NEIN = re.compile(r"\b(nein|nicht|nö|stopp?|abbrechen|lass( es)?|warte)\b", re.I)


def ist_zustimmung(antwort: str | None) -> bool:
    """Nur ein klares Ja zählt. Alles andere (auch Schweigen) ist ein Nein."""
    if not antwort:
        return False
    antwort = antwort.strip()
    return bool(_JA.search(antwort)) and not _NEIN.search(antwort)


class Ratenbegrenzer:
    """Begrenzt Gehirn-Züge pro Quelle und Stunde (schützt das Claude-Nutzungslimit)."""

    def __init__(self) -> None:
        self._zeiten: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def erlaubt(self, schluessel: str, max_pro_stunde: int) -> bool:
        jetzt = time.monotonic()
        with self._lock:
            q = self._zeiten.setdefault(schluessel, deque())
            while q and jetzt - q[0] > 3600:
                q.popleft()
            if len(q) >= max_pro_stunde:
                return False
            q.append(jetzt)
            return True


MAX_EREIGNISSE_PRO_STUNDE = 30   # je Quelle (Dienste, Zeitpläne …)
MAX_KANAL_PRO_STUNDE = 60        # je Kanal (Nachrichten vom Handy)
MAX_DENKEN_PRO_STUNDE = 60       # api.denken()

# ---------------------------------------------------------------------------
# Kanäle (z. B. Telegram): nur der gekoppelte Besitzer darf mit Jarvis reden
# ---------------------------------------------------------------------------


@dataclass
class _Kopplung:
    code: str
    bis: float
    fehlversuche: int = 0


@dataclass
class Kanalwaechter:
    """Entscheidet, ob eine eingehende Nachricht vom Besitzer kommt. Kopplung nur per Code, lokal gestartet."""

    lesen: Callable[[str], str | None]
    schreiben: Callable[[str, str | None], None]
    _kopplungen: dict[str, _Kopplung] = field(default_factory=dict)
    _gemeldet: dict[tuple[str, str], float] = field(default_factory=dict)

    @staticmethod
    def schluessel(kanal: str) -> str:
        return f"besitzer_{kanal}"

    def besitzer(self, kanal: str) -> str | None:
        return self.lesen(self.schluessel(kanal))

    def koppeln_starten(self, kanal: str) -> str:
        code = f"{secrets.randbelow(1_000_000):06d}"
        self._kopplungen[kanal] = _Kopplung(code, time.monotonic() + 600)
        return code

    def entkoppeln(self, kanal: str) -> None:
        self.schreiben(self.schluessel(kanal), None)
        self._kopplungen.pop(kanal, None)

    def pruefen(self, kanal: str, absender: str, text: str) -> str:
        """Gibt "besitzer", "gekoppelt" oder "ignorieren" zurück."""
        absender = str(absender)
        if absender and absender == self.besitzer(kanal):
            return "besitzer"
        k = self._kopplungen.get(kanal)
        if k and time.monotonic() < k.bis:
            if text.strip() == k.code:
                self.schreiben(self.schluessel(kanal), absender)
                del self._kopplungen[kanal]
                return "gekoppelt"
            k.fehlversuche += 1
            if k.fehlversuche >= 5:
                del self._kopplungen[kanal]
        return "ignorieren"

    def soll_melden(self, kanal: str, absender: str) -> bool:
        """Unbekannte Absender höchstens einmal pro Stunde in der Konsole erwähnen."""
        jetzt = time.monotonic()
        letzte = self._gemeldet.get((kanal, absender), -1e9)
        if jetzt - letzte < 3600:
            return False
        self._gemeldet[(kanal, absender)] = jetzt
        return True


# ---------------------------------------------------------------------------
# Wächter für Claude Code beim Selbst-Entwickeln
# ---------------------------------------------------------------------------

_VERBOTENE_BEFEHLE: list[tuple[re.Pattern[str], str]] = [(re.compile(m, re.I), g) for m, g in [
    (r"\bgit\b(?:\s+-[cC]\s+\S+)*\s+(commit|push|pull|reset|checkout|switch|restore|clean|stash|rebase|revert|"
     r"merge|tag|remote|config|am|apply|cherry-pick|filter-branch|gc|update-ref|init|rm|mv)\b",
     "Git-Befehle, die etwas verändern, führt Jarvis selbst aus (Sicherung, Commit, Rollback). "
     "Erlaubt sind git status, git diff, git log, git show."),
    (r"-m\s+jarvis(?![.\w])(?!.*--(selbsttest|einrichtung))",
     "Jarvis läuft bereits. Prüfen nur mit: python -m jarvis --selbsttest"),
    (r"\bstart\.py\b|jarvis starten", "Jarvis läuft bereits und darf nicht ein zweites Mal gestartet werden."),
    (r"\b(winget|choco|scoop|msiexec)\b|\b(apt|apt-get|dnf|yum|pacman|brew|snap)\s+install\b|\bsudo\b|"
     r"-verb\s+runas|set-executionpolicy",
     "Keine Systemprogramme installieren und keine Admin-Rechte. Fehlt ein Programm, deklariere es mit "
     "Programm(...) aus jarvis.api – der Nutzer installiert es dann selbst."),
    (r"\bschtasks\b|\breg(\.exe)?\s+(add|delete|import)\b|currentversion\\+run|\\startup\\|\bnetsh\b|"
     r"new-netfirewallrule|\bngrok\b|\bcloudflared\b|\bupnpc?\b|\bcrontab\b|systemctl\s+(enable|start)",
     "Keine Autostarts, Firewall-Regeln, Portfreigaben oder Tunnel."),
    (r"\|\s*(sh|bash|zsh|iex|powershell|pwsh|python3?)\b|invoke-expression|\biex\b|downloadstring",
     "Heruntergeladene Skripte nicht direkt ausführen."),
    (r"\bpip3?\b.*\binstall\b.*(git\+|https?://|--index-url|--extra-index-url|\s-i\s|--trusted-host|"
     r"\s-e\s|--editable)",
     "Pakete nur per Name von PyPI installieren."),
    (r"\bpip3?\b.*\buninstall\b", "Keine Pakete deinstallieren – andere Teile von Jarvis brauchen sie."),
    (r"\bpip3?\b.*\binstall\b.*(\s-U\b|--upgrade|--force-reinstall)",
     "Vorhandene Pakete nicht aktualisieren. Neue Pakete ohne -U/--upgrade installieren."),
    (r"einrichtung\.json|geheimnis|\.ssh\b|\.credentials|\.aws\b|id_rsa|id_ed25519|appdata.*jarvis",
     "Gespeicherte Zugangsdaten und Schlüssel sind tabu. Status: python -m jarvis --einrichtung"),
    (r"\brm\s+-[a-z]*r[a-z]*\s+(/|~|\$home|c:)|remove-item.*-recurse.*(c:\\|\$env:|~)|\bformat\s+[a-z]:|"
     r"\brd\s+/s|\bdel\s+/[sq]",
     "Nichts außerhalb des Projektordners löschen."),
]]

_SCHREIB_WERKZEUGE = ("Write", "Edit", "MultiEdit", "NotebookEdit")
_LESE_WERKZEUGE = ("Read", "Glob", "Grep")
_TABU_LESEN = re.compile(r"einrichtung\.json|geheimnis|[/\\]\.ssh([/\\]|$)|\.credentials|[/\\]\.aws([/\\]|$)", re.I)


def _innerhalb(pfad: Path, basis: Path) -> bool:
    try:
        pfad.relative_to(basis)
        return True
    except ValueError:
        return False


def pruefe_entwickler_werkzeug(name: str, eingabe: dict[str, Any], root: Path,
                               tabu_pfade: Iterable[Path] = ()) -> str | None:
    """Gibt einen Ablehnungsgrund zurück, wenn Claude Code dieses Werkzeug so nicht benutzen darf."""
    root = root.resolve()
    tabu = [p.resolve() for p in tabu_pfade]
    if name in ("Bash", "PowerShell"):
        befehl = str(eingabe.get("command", ""))
        for muster, grund in _VERBOTENE_BEFEHLE:
            if muster.search(befehl):
                return grund
        return None

    roh = eingabe.get("file_path") or eingabe.get("notebook_path") or eingabe.get("path")
    if not roh:
        return None
    pfad = Path(str(roh)).expanduser()
    if not pfad.is_absolute():
        pfad = root / pfad
    pfad = pfad.resolve()

    if any(_innerhalb(pfad, t) or pfad == t for t in tabu) or _TABU_LESEN.search(str(pfad)):
        return "Gespeicherte Zugangsdaten und Schlüssel sind tabu. Status: python -m jarvis --einrichtung"
    if name in _SCHREIB_WERKZEUGE:
        temp = Path(tempfile.gettempdir()).resolve()
        if not (_innerhalb(pfad, root) or _innerhalb(pfad, temp)):
            return "Nur Dateien im Jarvis-Projektordner (oder im Temp-Ordner) ändern."
        for verboten in ("data", ".git", ".venv", ".claude"):
            if _innerhalb(pfad, root / verboten):
                return f"Der Ordner {verboten}/ ist tabu (Laufzeitdaten/Versionsverwaltung/Einstellungen)."
    return None
