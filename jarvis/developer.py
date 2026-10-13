"""Selbstverbesserung: Claude Code verändert Jarvis' eigenen Programmcode.

GESCHÜTZTE DATEI (Sicherheitsnetz): Änderungen hieran fragt Jarvis vor dem Übernehmen beim Nutzer nach.

Ablauf eines Auftrags:
  1. Sicherung per Git-Commit + Paketstand merken (damit alles rückgängig gemacht werden kann)
  2. Claude Code setzt den Auftrag um (liest dafür CLAUDE.md). Es darf über Jarvis kurz nachfragen
     (nutzer_fragen) und meldet Fortschritt (fortschritt_melden). Ein Wächter blockiert gefährliche Befehle.
  3. Selbsttest + Prüfungen (große Dateien, Geheimnisse im Code, geänderte Kernpakete);
     bei Fehlern darf Claude Code nachbessern
  4. Klappt es nicht (oder wird abgebrochen), wird alles auf die Sicherung zurückgesetzt
  5. Erfolg: Commit "Jarvis-Update: …" und Neustart (übernehmen app.py / start.py)
"""

from __future__ import annotations

import asyncio
import contextvars
import json
import platform
import re
import shutil
import subprocess
import sys
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from jarvis import einrichtung, memory, protokoll, ui
from jarvis.config import DATA_DIR, ROOT_DIR
from jarvis.sicherheit import GESCHUETZT, PLATZHALTER, TOKEN_MUSTER, bekannte_geheimnisse, pruefe_entwickler_werkzeug, schwaerzen

UPDATE_FILE = DATA_DIR / "update.json"
# Existiert diese Datei beim Start, wurde eine Änderung mittendrin abgebrochen -> start.py setzt zurück.
IN_PROGRESS_FILE = DATA_DIR / "entwicklung_laeuft.json"
FEHLSCHLAG_FILE = DATA_DIR / "letzter_fehlschlag.json"
UPDATE_PREFIX = "Jarvis-Update:"
DEV_SERVER = "jarvis_dev"

# Werkzeuge von Claude Code beim Entwickeln (fest im Code, nicht in config.toml).
ERLAUBTE_WERKZEUGE = [
    "Read", "Write", "Edit", "MultiEdit", "Glob", "Grep", "Bash", "PowerShell", "WebSearch", "WebFetch",
    "TodoWrite", "TaskCreate", "TaskUpdate", "TaskList", "TaskGet", "ToolSearch",
    f"mcp__{DEV_SERVER}__nutzer_fragen", f"mcp__{DEV_SERVER}__fortschritt_melden",
]
# Diese Pakete darf eine Änderung nicht auf eine andere Version bringen (sonst bricht Jarvis selbst).
GESCHUETZTE_PAKETE = {"claude-agent-sdk", "mcp", "anyio", "pydantic", "pydantic-core", "edge-tts",
                      "speechrecognition", "pytest", "pip", "setuptools", "aiohttp"}
MAX_DATEIGROESSE = 1_000_000
_GEHEIM_FRAGE = re.compile(r"passwort|kennwort|token|schlüssel|schluessel|api.?key|zugangscode|secret|pin\b", re.I)

ERGEBNIS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "erfolg": {"type": "boolean", "description": "true = umgesetzt und SELBSTTEST OK; false = der Wunsch ist "
                   "nicht sinnvoll umsetzbar (kostenpflichtig, unmöglich, gefährlich) – dann wird alles verworfen"},
        "titel": {"type": "string", "description": "3 bis 6 Wörter, z. B. 'Telegram-Nachrichten aufs Handy'"},
        "zusammenfassung": {"type": "string", "description": "1–2 vorlesbare deutsche Sätze: was jetzt neu ist. "
                            "Kein Markdown, keine Dateinamen"},
        "ausprobieren": {"type": "string", "description": "Ein Beispielsatz, mit dem der Nutzer es ausprobiert"},
        "naechste_schritte": {"type": "string", "description": "Was der Nutzer nach dem Neustart tun muss, das NICHT "
                              "über Einrichtungswerte abgedeckt ist (z. B. 'Drucker in den LAN-Modus schalten'); "
                              "sonst leer"},
        "annahmen": {"type": "array", "items": {"type": "string"}},
        "einschraenkungen": {"type": "array", "items": {"type": "string"},
                             "description": "Was nicht geht oder nur mit Attrappen getestet wurde"},
    },
    "required": ["erfolg", "titel", "zusammenfassung"],
}


@dataclass
class DevResult:
    erfolg: bool
    zusammenfassung: str
    geaendert: bool = False
    titel: str = ""
    ausprobieren: str = ""
    naechste_schritte: str = ""
    annahmen: list[str] = field(default_factory=list)
    einschraenkungen: list[str] = field(default_factory=list)
    abgebrochen: bool = False


@dataclass
class DevStatus:
    auftrag: str
    titel: str
    umfang: str
    start: float = field(default_factory=time.monotonic)
    phase: str = "startet"
    meldung: str = ""
    letzte_aktion: str = ""
    schritte: int = 0
    aufgaben: list[list[str]] = field(default_factory=list)   # [[Betreff, Status], ...] aus TaskCreate/Update
    frage: str | None = None

    def beschreiben(self) -> str:
        minuten = int((time.monotonic() - self.start) // 60)
        dauer = "seit gut einer Minute" if minuten < 2 else f"seit {minuten} Minuten"
        text = f"Claude Code arbeitet {dauer} an „{self.titel or _short(self.auftrag, 60)}“ ({self.phase})."
        if self.frage:
            return text + f" Er wartet auf deine Antwort: {self.frage}"
        if self.aufgaben:
            erledigt = sum(1 for _, s in self.aufgaben if s == "completed")
            aktuell = next((t for t, s in self.aufgaben if s == "in_progress"), "")
            text += f" Schritt {min(erledigt + 1, len(self.aufgaben))} von {len(self.aufgaben)}"
            text += f": {aktuell}." if aktuell else "."
        if self.meldung:
            text += f" Stand: {self.meldung}."
        if self.letzte_aktion:
            text += f" Gerade: {self.letzte_aktion} ({self.schritte} Schritte)."
        return text


class Abgebrochen(Exception):
    pass


class DevFehler(Exception):
    """Fehler mit einer für den Nutzer verständlichen Meldung."""


@dataclass
class _Lauf:
    ok: bool
    session_id: str | None
    art: str = ""          # "", "max_turns", "zeit", "limit", "voruebergehend", "fehler"
    fehler: str = ""
    ergebnis: DevResult | None = None


class GitError(RuntimeError):
    pass


@dataclass
class UpdateEntry:
    commit: str
    datum: str
    titel: str
    rueckgaengig: bool = False

    def __str__(self) -> str:
        return f"{self.datum}: {self.titel}" + (" (rückgängig gemacht)" if self.rueckgaengig else "")


class Git:
    def __init__(self, root: Path = ROOT_DIR):
        self.root = root
        self._identity: list[str] | None = None

    @staticmethod
    def available() -> bool:
        return shutil.which("git") is not None

    def run(self, *args: str, check: bool = True) -> str:
        # Git-Hooks werden nie ausgeführt (eine Änderung könnte sonst Code in .git/hooks einschleusen).
        leer = self.root / ".git" / "jarvis-ohne-hooks"
        proc = subprocess.run(
            ["git", "-c", f"core.hooksPath={leer.as_posix()}", *args], cwd=self.root, capture_output=True,
            text=True, encoding="utf-8", errors="replace",
        )
        if check and proc.returncode != 0:
            raise GitError(f"git {' '.join(args)} fehlgeschlagen: {proc.stderr.strip() or proc.stdout.strip()}")
        return proc.stdout.strip()

    def _commit_identity(self) -> list[str]:
        """Falls kein Git-Name/E-Mail eingerichtet ist, committet Jarvis unter eigenem Namen."""
        if self._identity is None:
            has_name = bool(self.run("config", "user.name", check=False))
            has_mail = bool(self.run("config", "user.email", check=False))
            self._identity = [] if has_name and has_mail else [
                "-c", "user.name=Jarvis", "-c", "user.email=jarvis@localhost"]
        return self._identity

    def ensure_repo(self) -> None:
        if not (self.root / ".git").exists():
            self.run("init")
        if self.head() is None:
            self.commit_all("Jarvis: Startzustand")

    def head(self) -> str | None:
        out = self.run("rev-parse", "HEAD", check=False)
        return out if out and " " not in out and len(out) >= 7 else None

    def is_dirty(self) -> bool:
        return bool(self.run("status", "--porcelain"))

    def commit_all(self, message: str) -> bool:
        """Committet alle Änderungen. Gibt False zurück, wenn es nichts zu committen gab."""
        if not self.is_dirty():
            return False
        self.run("add", "-A")
        self.run(*self._commit_identity(), "commit", "-q", "-m", message)
        return True

    def reset_hard(self, rev: str) -> None:
        self.run("reset", "-q", "--hard", rev)
        self.run("clean", "-q", "-fd")  # neue, nicht ignorierte Dateien entfernen (data/ ist ignoriert)

    def geaenderte_dateien(self, seit: str) -> list[str]:
        """Alle seit `seit` geänderten/neuen/gelöschten Dateien (inkl. noch nicht committeter)."""
        dateien = set(self.run("diff", "--name-only", seit, check=False).splitlines())
        dateien |= set(self.run("ls-files", "--others", "--exclude-standard", check=False).splitlines())
        return sorted(d for d in dateien if d.strip())

    def neue_zeilen(self, seit: str) -> str:
        """Hinzugefügte Zeilen seit `seit` (inkl. neuer Dateien)."""
        text = self.run("diff", "--unified=0", "--no-color", seit, check=False)
        zeilen = [z[1:] for z in text.splitlines() if z.startswith("+") and not z.startswith("+++")]
        for datei in self.run("ls-files", "--others", "--exclude-standard", check=False).splitlines():
            pfad = self.root / datei
            try:
                if pfad.is_file() and pfad.stat().st_size <= MAX_DATEIGROESSE:
                    zeilen.extend(pfad.read_text(encoding="utf-8", errors="ignore").splitlines())
            except OSError:
                pass
        return "\n".join(zeilen)

    def update_history(self, n: int = 10) -> list[UpdateEntry]:
        """Die letzten n Selbst-Änderungen (neueste zuerst)."""
        out = self.run("log", f"-n{n}", "--date=format:%d.%m.%Y %H:%M",
                       "--pretty=format:%H|%ad|%s", f"--grep=^{UPDATE_PREFIX}", check=False)
        reverted = set(re.findall(r"This reverts commit ([0-9a-f]{40})",
                                  self.run("log", "--format=%B", "--grep=This reverts commit", check=False)))
        entries = []
        for line in out.splitlines():
            if line.count("|") >= 2:
                commit, date, subject = line.split("|", 2)
                entries.append(UpdateEntry(commit, date, subject.removeprefix(UPDATE_PREFIX).strip(),
                                           commit in reverted))
        return entries


# ---------------------------------------------------------------------------
# Pakete (pip) – Rollback deckt auch die Python-Umgebung ab
# ---------------------------------------------------------------------------

def _paketname(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def pakete_stand() -> dict[str, str]:
    try:
        out = subprocess.run([sys.executable, "-m", "pip", "freeze", "--all", "--disable-pip-version-check"],
                             capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120).stdout
    except (OSError, subprocess.TimeoutExpired):
        return {}
    stand = {}
    for zeile in out.splitlines():
        if "==" in zeile:
            name, version = zeile.split("==", 1)
            stand[_paketname(name)] = version.strip()
    return stand


def pakete_wiederherstellen(vorher: dict[str, str]) -> list[str]:
    """Bringt Pakete, deren Version sich geändert hat, auf den alten Stand (bestmöglich)."""
    if not vorher:
        return []
    jetzt = pakete_stand()
    geaendert = [f"{n}=={v}" for n, v in vorher.items() if n in jetzt and jetzt[n] != v]
    if geaendert:
        subprocess.run([sys.executable, "-m", "pip", "install", "-q", "--disable-pip-version-check", *geaendert],
                       capture_output=True, timeout=900)
    return geaendert


# ---------------------------------------------------------------------------
# Developer
# ---------------------------------------------------------------------------

FrageStellen = Callable[[str, str], Awaitable[str | None]]
BestaetigenLokal = Callable[[str], Awaitable[bool]]


class Developer:
    def __init__(self, cfg: dict[str, Any], frage_stellen: FrageStellen | None = None,
                 bestaetigen_lokal: BestaetigenLokal | None = None):
        self.cfg = cfg["entwickler"]
        self.git = Git()
        self.frage_stellen = frage_stellen
        self.bestaetigen_lokal = bestaetigen_lokal
        self.status: DevStatus | None = None
        self._task: asyncio.Task[Any] | None = None
        self._client: Any = None
        self._abbruch = False
        self._zeit_um = False
        self._fragen = 0
        self._wartezeit = 0.0
        self._nachtraege: list[str] = []

    @property
    def busy(self) -> bool:
        return self._task is not None and not self._task.done()

    @property
    def current_task(self) -> str | None:
        return self.status.titel or self.status.auftrag if self.status else None

    def start(self, auftrag: str, on_done: Callable[[DevResult], Awaitable[None]], *, titel: str = "",
              umfang: str = "klein", funktion: str = "") -> None:
        """Startet einen Auftrag im Hintergrund; on_done wird mit dem Ergebnis aufgerufen."""
        if self.busy:
            raise RuntimeError(f"Ich arbeite noch an: {self.current_task}")
        self.status = DevStatus(auftrag, titel, "gross" if umfang == "gross" else "klein")

        async def runner() -> None:
            try:
                result = await self.improve(auftrag, titel=titel, umfang=umfang, funktion=funktion)
            except asyncio.CancelledError:
                self.status = None
                raise  # Jarvis wird beendet – improve() hat bereits zurückgesetzt
            except Exception as e:  # noqa: BLE001
                ui.error(f"Unerwarteter Fehler beim Entwickeln: {e}")
                result = DevResult(False, f"Unerwarteter Fehler beim Entwickeln: {schwaerzen(e)}")
            finally:
                self.status = None
            await on_done(result)

        # Eigener Kontext: Der Auftrag kommt meist aus einem Werkzeugaufruf des Gehirns.
        self._task = asyncio.get_running_loop().create_task(runner(), context=contextvars.Context())

    async def abbrechen(self) -> None:
        """Bricht den laufenden Auftrag ab; alles wird auf den Stand davor zurückgesetzt."""
        if not self.busy:
            return
        self._abbruch = True
        if self._client is not None:
            try:
                await asyncio.wait_for(self._client.interrupt(), 10)
            except Exception:  # noqa: BLE001
                pass
        # Reagiert Claude Code nicht (oder läuft gerade der Selbsttest): hart abbrechen.
        task = self._task
        if task is not None and not task.done():
            done, _ = await asyncio.wait({task}, timeout=5)
            if not done:
                task.cancel()

    def nachtrag(self, text: str) -> str:
        """Zusätzliche Info des Nutzers an Claude Code, während es arbeitet."""
        if not self.busy:
            return "Gerade arbeitet Claude Code an nichts."
        self._nachtraege.append(schwaerzen(text))
        return "Nachtrag wird an Claude Code weitergegeben."

    # --- Hauptablauf ------------------------------------------------------------
    async def improve(self, auftrag: str, *, titel: str = "", umfang: str = "klein", funktion: str = "") -> DevResult:
        if not self.git.available():
            return DevResult(False, "Git ist nicht installiert. Ohne Git kann ich mich nicht sicher verändern. "
                                    "Bitte installiere Git von git-scm.com.")
        if self.status is None:
            self.status = DevStatus(auftrag, titel, umfang)
        self._abbruch = self._zeit_um = False
        self._fragen, self._wartezeit, self._nachtraege = 0, 0.0, []
        self.git.ensure_repo()
        self.git.commit_all("Jarvis: Sicherung vor Änderung")
        before = self.git.head()
        assert before is not None
        pakete_vorher = await asyncio.to_thread(pakete_stand)
        ui.dev(f"Neuer Auftrag ({umfang}): {_short(auftrag, 200)}")
        IN_PROGRESS_FILE.parent.mkdir(parents=True, exist_ok=True)
        IN_PROGRESS_FILE.write_text(json.dumps({"vorher": before, "auftrag": schwaerzen(auftrag), "titel": titel,
                                                "pakete": pakete_vorher}, ensure_ascii=False), encoding="utf-8")
        try:
            ok, ergebnis = await self._develop(auftrag, titel, umfang, funktion, before, pakete_vorher)
            if ok:
                ergebnis = await self._uebernehmen(before, auftrag, ergebnis, pakete_vorher)
            if not ergebnis.geaendert:
                await self._zuruecksetzen(before, pakete_vorher)
                if not ergebnis.erfolg:
                    self._fehlschlag_merken(auftrag, titel, ergebnis.zusammenfassung)
            IN_PROGRESS_FILE.unlink(missing_ok=True)
            return ergebnis
        except Abgebrochen:
            await self._zuruecksetzen(before, pakete_vorher)
            IN_PROGRESS_FILE.unlink(missing_ok=True)
            ui.dev("Abgebrochen – alles zurückgesetzt.")
            return DevResult(False, "Abgebrochen. Ich habe alles auf den Stand vor dem Auftrag zurückgesetzt.",
                             abgebrochen=True)
        except asyncio.CancelledError:
            if self._abbruch:  # vom Nutzer abgebrochen (abbrechen() hat den Task gestoppt)
                await self._zuruecksetzen(before, pakete_vorher)
                IN_PROGRESS_FILE.unlink(missing_ok=True)
                ui.dev("Abgebrochen – alles zurückgesetzt.")
                return DevResult(False, "Abgebrochen. Ich habe alles auf den Stand vor dem Auftrag "
                                        "zurückgesetzt.", abgebrochen=True)
            # Jarvis wird beendet: Code sofort zurücksetzen. Die Markierungsdatei bleibt, damit start.py beim
            # nächsten Start auch die Pakete wiederherstellt und Bescheid gibt.
            self.git.reset_hard(before)
            raise
        except Exception as e:  # noqa: BLE001
            await self._zuruecksetzen(before, pakete_vorher)
            IN_PROGRESS_FILE.unlink(missing_ok=True)
            grund = str(e) if isinstance(e, DevFehler) else f"Fehler: {schwaerzen(e)}"
            ui.error(f"Entwicklung abgebrochen: {grund}")
            self._fehlschlag_merken(auftrag, titel, grund)
            return DevResult(False, f"Das hat leider nicht geklappt. {grund} Ich habe alles zurückgesetzt.")

    async def _develop(self, auftrag: str, titel: str, umfang: str, funktion: str, before: str,
                       pakete_vorher: dict[str, str]) -> tuple[bool, DevResult]:
        profil = self._profil(umfang)
        lauf = await self._sitzung(self._task_prompt(auftrag, umfang, funktion, profil), None, profil)
        assert lauf.ergebnis is not None
        if not lauf.ergebnis.erfolg:
            return False, lauf.ergebnis
        versuche = int(self.cfg.get("reparaturversuche", 2))
        for versuch in range(versuche + 1):
            self._phase("Selbsttest")
            ok, ausgabe = await self._selftest()
            probleme = self._pruefe_aenderungen(before, pakete_vorher) if ok else []
            if ok and not probleme:
                return True, lauf.ergebnis
            if versuch == versuche:
                grund = "Der Selbsttest ist nach mehreren Versuchen weiter fehlgeschlagen." if not ok else \
                    "Die Änderung hat meine Sicherheitsprüfungen nicht bestanden."
                ui.dev(grund)
                return False, DevResult(False, f"Das hat leider nicht geklappt. {grund} Ich habe alles "
                                               "zurückgesetzt.")
            self._phase(f"Reparaturversuch {versuch + 1} von {versuche}")
            ui.dev("Prüfung fehlgeschlagen – Claude Code bessert nach …")
            text = ausgabe if not ok else "Prüfungen fehlgeschlagen:\n" + "\n".join(f"- {p}" for p in probleme)
            lauf = await self._sitzung(self._fix_prompt(text), lauf.session_id, profil)
            assert lauf.ergebnis is not None
            if not lauf.ergebnis.erfolg:
                return False, lauf.ergebnis
        return False, DevResult(False, "Unerwarteter Abbruch.")

    async def _uebernehmen(self, before: str, auftrag: str, ergebnis: DevResult,
                           pakete_vorher: dict[str, str]) -> DevResult:
        """Commit + update.json. Änderungen am Sicherheitsnetz muss der Nutzer bestätigen."""
        self._phase("speichert")
        geaendert = self.git.geaenderte_dateien(before)
        geschuetzt = [d for d in geaendert if any(d == g or d.startswith(g) for g in GESCHUETZT)]
        if geschuetzt:
            frage = (f"Diese Änderung betrifft mein Sicherheitsnetz ({', '.join(geschuetzt)}). "
                     "Soll ich sie trotzdem übernehmen?")
            ok = await self.bestaetigen_lokal(frage) if self.bestaetigen_lokal else False
            if not ok:
                return DevResult(False, "Die Änderung hätte mein Sicherheitsnetz verändert und wurde nicht "
                                        "bestätigt. Ich habe alles zurückgesetzt.")
        titel = ergebnis.titel or _short(auftrag)
        nachricht = (f"{UPDATE_PREFIX} {_short(titel, 70)}\n\nAuftrag: {auftrag}\n\nErgebnis: {ergebnis.zusammenfassung}")
        if ergebnis.annahmen:
            nachricht += "\nAnnahmen: " + "; ".join(ergebnis.annahmen)
        if ergebnis.einschraenkungen:
            nachricht += "\nEinschränkungen: " + "; ".join(ergebnis.einschraenkungen)
        self.git.commit_all(schwaerzen(nachricht))
        ergebnis.geaendert = self.git.head() != before
        if not ergebnis.geaendert:
            return ergebnis
        UPDATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        UPDATE_FILE.write_text(json.dumps({
            "art": "update", "vorher": before, "nachher": self.git.head(), "titel": ergebnis.titel,
            "auftrag": schwaerzen(auftrag), "zusammenfassung": ergebnis.zusammenfassung,
            "ausprobieren": ergebnis.ausprobieren, "naechste_schritte": ergebnis.naechste_schritte,
            "einschraenkungen": ergebnis.einschraenkungen, "pakete": pakete_vorher,
            "zeit": datetime.now().isoformat(timespec="seconds"), "bestaetigt": False,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        ui.dev("Fertig und gespeichert.")
        return ergebnis

    async def _zuruecksetzen(self, before: str, pakete_vorher: dict[str, str]) -> None:
        self.git.reset_hard(before)
        wieder = await asyncio.to_thread(pakete_wiederherstellen, pakete_vorher)
        if wieder:
            ui.dev(f"Pakete zurückgesetzt: {', '.join(wieder)}")

    def _pruefe_aenderungen(self, before: str, pakete_vorher: dict[str, str]) -> list[str]:
        """Prüfungen nach dem Selbsttest. Gibt Probleme als Text zurück (leer = alles gut)."""
        probleme = []
        for datei in self.git.geaenderte_dateien(before):
            pfad = ROOT_DIR / datei
            if pfad.is_file() and pfad.stat().st_size > MAX_DATEIGROESSE:
                probleme.append(f"{datei} ist größer als 1 MB – große Dateien (Downloads, Modelle, Testdaten) "
                                "gehören in den Temp-Ordner oder zur Laufzeit in daten_ordner().")
        neu = self.git.neue_zeilen(before)
        if any(g in neu for g in bekannte_geheimnisse()):
            probleme.append("Im Code steht ein gespeicherter geheimer Wert des Nutzers. Entferne ihn und lies "
                            "ihn zur Laufzeit über Geheimnis(...).wert().")
        for muster in TOKEN_MUSTER:
            for treffer in muster.findall(neu):
                text = treffer if isinstance(treffer, str) else "".join(treffer)
                if not PLATZHALTER.search(text):
                    probleme.append("Im Code steht etwas, das wie ein echter Schlüssel/Token aussieht. Nutze in "
                                    "Tests Platzhalter wie 'test-token' und im Code Geheimnis(...).")
                    break
        if pakete_vorher:
            jetzt = pakete_stand()
            for name in sorted(GESCHUETZTE_PAKETE):
                if name in pakete_vorher and jetzt.get(name) != pakete_vorher[name]:
                    probleme.append(f"Das Kernpaket {name} wurde von {pakete_vorher[name]} auf "
                                    f"{jetzt.get(name, 'deinstalliert')} geändert – stelle Version "
                                    f"{pakete_vorher[name]} wieder her (\"{_python()}\" -m pip install "
                                    f"{name}=={pakete_vorher[name]}) und finde eine verträgliche Lösung.")
        return probleme

    # --- Claude-Code-Sitzung --------------------------------------------------------
    def _profil(self, umfang: str) -> dict[str, Any]:
        c = self.cfg
        if umfang == "gross":
            return {"aufwand": c.get("aufwand_gross", "xhigh"), "max_schritte": int(c.get("max_schritte_gross", 400)),
                    "max_minuten": float(c.get("max_minuten_gross", 75))}
        return {"aufwand": c.get("aufwand", "high"), "max_schritte": int(c.get("max_schritte", 150)),
                "max_minuten": float(c.get("max_minuten", 25))}

    async def _sitzung(self, prompt: str, resume: str | None, profil: dict[str, Any]) -> _Lauf:
        """Ein Arbeitsschritt von Claude Code – mit Wiederaufnahme bei Aussetzern und am Schrittlimit."""
        wiederholt = 0
        abschluss = False
        while True:
            lauf = await self._run_claude(prompt, resume, profil)
            if self._abbruch:
                raise Abgebrochen()
            if lauf.ok:
                return lauf
            resume = lauf.session_id or resume
            if lauf.art in ("max_turns", "zeit") and not abschluss and resume:
                abschluss = True
                self._zeit_um = False
                ui.dev("Zeit-/Schrittlimit erreicht – Claude Code bringt den Stand zum Abschluss …")
                profil = {**profil, "max_schritte": 40, "max_minuten": 10}
                prompt = ("Das Zeit- bzw. Schrittlimit ist erreicht. Fang nichts Neues mehr an: Bring den jetzigen "
                          "Stand in einen lauffähigen Zustand (Unfertiges sauber weglassen), führe den Selbsttest "
                          "aus und gib dann das Ergebnis zurück; Fehlendes unter einschraenkungen.")
                continue
            if lauf.art == "limit":
                raise DevFehler("Dein Claude-Nutzungslimit ist gerade aufgebraucht. Versuch es in ein paar Stunden "
                                "noch einmal.")
            if lauf.art == "voruebergehend" and wiederholt < 3 and resume:
                wiederholt += 1
                warte = (30, 90, 180)[wiederholt - 1]
                ui.dev(f"Verbindung zu Claude unterbrochen – neuer Versuch in {warte} s …")
                await asyncio.sleep(warte)
                prompt = "Die Verbindung war kurz unterbrochen. Mach genau dort weiter, wo du warst."
                continue
            raise DevFehler(lauf.fehler or "Claude Code hat mit einem Fehler aufgehört.")

    async def _run_claude(self, prompt: str, resume: str | None, profil: dict[str, Any]) -> _Lauf:
        from claude_agent_sdk import (AssistantMessage, ClaudeAgentOptions, ClaudeSDKClient, ClaudeSDKError,
                                      HookMatcher, ResultMessage, TextBlock, ToolUseBlock)

        options: dict[str, Any] = dict(
            cwd=str(ROOT_DIR),
            system_prompt={"type": "preset", "preset": "claude_code"},
            setting_sources=["project"],          # lädt CLAUDE.md und .claude/settings.json
            permission_mode="dontAsk",            # nur die erlaubten Werkzeuge, nie nachfragen
            allowed_tools=list(ERLAUBTE_WERKZEUGE),
            disallowed_tools=["AskUserQuestion"],
            mcp_servers={DEV_SERVER: self._dev_server()},
            hooks={"PreToolUse": [HookMatcher(matcher=None, hooks=[self._waechter])],
                   "PostToolUse": [HookMatcher(matcher=None, hooks=[self._nach_werkzeug])]},
            output_format={"type": "json_schema", "schema": ERGEBNIS_SCHEMA},
            max_turns=int(profil["max_schritte"]),
            resume=resume,
            env=self._umgebung(),
        )
        if self.cfg.get("modell"):
            options["model"] = self.cfg["modell"]
        if profil.get("aufwand"):
            options["effort"] = profil["aufwand"]

        session_id = resume
        start = time.monotonic()
        wartezeit_start = self._wartezeit
        waechter: asyncio.Task[None] | None = None
        try:
            async with ClaudeSDKClient(ClaudeAgentOptions(**options)) as client:
                self._client = client
                waechter = asyncio.get_running_loop().create_task(
                    self._zeitwaechter(client, start, wartezeit_start, float(profil["max_minuten"])))
                await client.query(prompt)
                async for msg in client.receive_response():
                    if isinstance(msg, AssistantMessage):
                        for block in msg.content:
                            if isinstance(block, ToolUseBlock):
                                self._werkzeug_gesehen(block.name, block.input)
                            elif isinstance(block, TextBlock) and block.text.strip():
                                ui.dev(_short(block.text.strip(), 200))
                    elif isinstance(msg, ResultMessage):
                        session_id = msg.session_id or session_id
                        return self._auswerten(msg, session_id)
        except ClaudeSDKError as e:
            if self._abbruch:
                raise Abgebrochen() from None
            return _Lauf(False, session_id, "voruebergehend", f"Verbindung zu Claude Code verloren: {e}")
        finally:
            self._client = None
            if waechter is not None:
                waechter.cancel()
        return _Lauf(False, session_id, "voruebergehend", "Claude Code hat ohne Ergebnis aufgehört.")

    def _auswerten(self, msg: Any, session_id: str | None) -> _Lauf:
        text = (msg.result or "").strip()
        if not msg.is_error and msg.subtype in ("success", "error_max_structured_output_retries"):
            return _Lauf(True, session_id, ergebnis=_ergebnis_lesen(msg.structured_output, text))
        if msg.subtype == "error_max_turns":
            return _Lauf(False, session_id, "max_turns", "Schrittlimit erreicht.")
        if self._zeit_um:
            return _Lauf(False, session_id, "zeit", "Zeitlimit erreicht.")
        status = getattr(msg, "api_error_status", None)
        niedrig = text.lower()
        if "usage limit" in niedrig or "limit reached" in niedrig or "nutzungslimit" in niedrig:
            return _Lauf(False, session_id, "limit", text)
        if status in (408, 429, 500, 502, 503, 504, 529) or msg.subtype == "error_during_execution" \
                or "overloaded" in niedrig or "timeout" in niedrig:
            return _Lauf(False, session_id, "voruebergehend", text or msg.subtype)
        return _Lauf(False, session_id, "fehler", schwaerzen(text or msg.subtype))

    async def _zeitwaechter(self, client: Any, start: float, wartezeit_start: float, max_minuten: float) -> None:
        """Unterbricht Claude Code nach max_minuten (Wartezeit auf Antworten des Nutzers zählt nicht)."""
        while True:
            await asyncio.sleep(5)
            gearbeitet = time.monotonic() - start - (self._wartezeit - wartezeit_start)
            if self.status and self.status.frage:
                continue
            if gearbeitet > max_minuten * 60:
                self._zeit_um = True
                try:
                    await client.interrupt()
                except Exception:  # noqa: BLE001
                    pass
                return

    def _umgebung(self) -> dict[str, str]:
        env = {"MCP_TOOL_TIMEOUT": str(60 * 60 * 1000), "CLAUDE_CODE_DISABLE_BACKGROUND_TASKS": "1",
               "JARVIS_ENTWICKLER": "1"}
        # Geheimnisse aus Umgebungsvariablen nicht an Claude Code weitergeben.
        for wert in einrichtung.alle():
            if wert.geheim:
                env[wert.umgebungsvariable] = ""
        return env

    # --- Werkzeuge & Hooks für Claude Code ----------------------------------------------
    def _dev_server(self) -> Any:
        from claude_agent_sdk import create_sdk_mcp_server, tool

        dev = self

        @tool("nutzer_fragen",
              "Kurze Rückfrage an den Nutzer – Jarvis stellt sie per Sprache und wartet auf die Antwort. Nur für "
              "Entscheidungen, die vom Nutzer abhängen (Gerät/Modell, Konto, gewünschtes Verhalten) und die weder "
              "Auftrag noch Recherche klären. Höchstens drei pro Auftrag, möglichst früh, immer mit Vorschlag. "
              "NIEMALS nach Passwörtern, Tokens oder Schlüsseln fragen – dafür Geheimnis(...) deklarieren.",
              {"frage": str, "vorschlag": str})
        async def nutzer_fragen(args: dict[str, Any]) -> dict[str, Any]:
            return _text(await dev._nutzer_fragen(str(args.get("frage", "")), str(args.get("vorschlag", ""))))

        @tool("fortschritt_melden",
              "Kurzer Satz zum aktuellen Stand (z. B. 'Recherchiere die Bambu-LAN-Schnittstelle'). Bei jedem "
              "Phasenwechsel aufrufen. Wird nicht vorgelesen, nur angezeigt, wenn der Nutzer nach dem Stand fragt.",
              {"text": str})
        async def fortschritt_melden(args: dict[str, Any]) -> dict[str, Any]:
            if dev.status:
                dev.status.meldung = _short(str(args.get("text", "")), 150)
                ui.dev(f"Stand: {dev.status.meldung}")
            return _text("ok")

        return create_sdk_mcp_server(name=DEV_SERVER, version="1.0.0", tools=[nutzer_fragen, fortschritt_melden])

    async def _nutzer_fragen(self, frage: str, vorschlag: str) -> str:
        if _GEHEIM_FRAGE.search(frage):
            return ("Nach Passwörtern, Tokens oder Schlüsseln wird nie gefragt. Deklariere sie mit "
                    "Geheimnis(...) – der Nutzer gibt sie nach dem Update in einem Eingabefenster ein.")
        if self.frage_stellen is None:
            return "Keine Rückfrage möglich – entscheide selbst sinnvoll und nenne es unter annahmen."
        if self._fragen >= int(self.cfg.get("max_fragen", 3)):
            return "Fragelimit erreicht – entscheide selbst und nenne es unter annahmen."
        self._fragen += 1
        alte_phase = self.status.phase if self.status else ""
        if self.status:
            self.status.frage, self.status.phase = frage, "wartet auf deine Antwort"
        ui.dev(f"Rückfrage: {frage}" + (f" (Vorschlag: {vorschlag})" if vorschlag else ""))
        start = time.monotonic()
        try:
            antwort = await self.frage_stellen(frage, vorschlag)
        finally:
            self._wartezeit += time.monotonic() - start
            if self.status:
                self.status.frage, self.status.phase = None, alte_phase
        if not antwort:
            minuten = self.cfg.get("frage_timeout_minuten", 10)
            return (f"Keine Antwort innerhalb von {minuten} Minuten – nimm deinen Vorschlag ({vorschlag or 'eine '
                    'sinnvolle Annahme'}) und nenne die Annahme unter annahmen.")
        ui.dev(f"Antwort: {antwort}")
        return f"Antwort des Nutzers: {schwaerzen(antwort)}"

    async def _waechter(self, eingabe: Any, tool_use_id: str | None, kontext: Any) -> dict[str, Any]:
        name = eingabe.get("tool_name", "")
        werkzeug_eingabe = eingabe.get("tool_input") or {}
        grund = pruefe_entwickler_werkzeug(name, werkzeug_eingabe, ROOT_DIR,
                                           tabu_pfade=[einrichtung.speicher_datei()])
        if grund:
            ui.dev(f"Blockiert ({name}): {grund}")
            return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                           "permissionDecisionReason": grund}}
        return {}

    async def _nach_werkzeug(self, eingabe: Any, tool_use_id: str | None, kontext: Any) -> dict[str, Any]:
        """Gibt Nachträge des Nutzers an der nächsten Werkzeuggrenze an Claude Code weiter."""
        if not self._nachtraege:
            return {}
        texte, self._nachtraege = self._nachtraege, []
        return {"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext":
                "Nachtrag vom Nutzer (über Jarvis): " + " | ".join(texte)}}

    def _werkzeug_gesehen(self, name: str, eingabe: dict[str, Any]) -> None:
        beschreibung = _describe_tool(name, eingabe)
        if self.status:
            self.status.schritte += 1
            self.status.letzte_aktion = beschreibung
            if name in ("WebSearch", "WebFetch"):
                self.status.phase = "recherchiert"
            elif name in ("Write", "Edit", "MultiEdit"):
                self.status.phase = "programmiert"
            elif name in ("Bash", "PowerShell"):
                befehl = str(eingabe.get("command", ""))
                if "pip" in befehl and "install" in befehl:
                    self.status.phase = "installiert Pakete"
                elif "pytest" in befehl or "--selbsttest" in befehl:
                    self.status.phase = "testet"
            elif name == "TaskCreate" and eingabe.get("subject"):
                self.status.aufgaben.append([str(eingabe["subject"]), "pending"])
            elif name == "TaskUpdate":
                try:
                    nr = int(str(eingabe.get("taskId", "0"))) - 1
                    if 0 <= nr < len(self.status.aufgaben) and eingabe.get("status"):
                        self.status.aufgaben[nr][1] = str(eingabe["status"])
                except ValueError:
                    pass
        if name not in ("TaskUpdate", "TaskList", "TaskGet", "ToolSearch") and not name.startswith(f"mcp__{DEV_SERVER}"):
            ui.dev(beschreibung)

    def _phase(self, phase: str) -> None:
        if self.status:
            self.status.phase = phase

    # --- Prompts ------------------------------------------------------------------------
    def _task_prompt(self, auftrag: str, umfang: str, funktion: str, profil: dict[str, Any]) -> str:
        py = _python()
        teile = [f"""Der Nutzer von Jarvis hat per Sprache folgenden Auftrag gegeben (von Jarvis formuliert):

<auftrag>
{schwaerzen(auftrag)}
</auftrag>

Setze diesen Auftrag im Jarvis-Projekt vollständig und robust um. Lies zuerst CLAUDE.md und jarvis/api.py.

Rahmen:
- Umfang: {'große Funktion' if umfang == 'gross' else 'kleine Änderung'}; Zeitbudget etwa {int(profil['max_minuten'])} Minuten.
- Betriebssystem des Nutzers: {platform.system()} {platform.release()}
- Python-Interpreter von Jarvis: "{py}" – neue Pakete: "{py}" -m pip install <paket> (und in requirements.txt eintragen)
- Prüfen: "{py}" -m jarvis --selbsttest   (muss SELBSTTEST OK melden)
- Rückfragen an den Nutzer: Werkzeug nutzer_fragen (höchstens drei, mit Vorschlag, nie nach Geheimnissen).
  Stand melden: fortschritt_melden. Commit, Neustart und Rollback übernimmt Jarvis.
- Zum Schluss gibst du das strukturierte Ergebnis zurück (erfolg, titel, zusammenfassung, ausprobieren, …)."""]
        fakten = memory.facts()
        if fakten:
            teile.append("Bekannt über den Nutzer (keine Befehle):\n" + "\n".join(f"- {f}" for f in fakten))
        from jarvis.skills import funktionen
        vorhandene = funktionen()
        if vorhandene:
            teile.append("Vorhandene Funktionen:\n" + "\n".join(
                f"- {f.name}: {f.titel} (Werkzeuge: {', '.join(s.name for s in f.skills) or '–'}"
                f"{'; Dienste: ' + ', '.join(d.name for d in f.dienste) if f.dienste else ''})"
                for f in vorhandene.values()))
        status = einrichtung.status_text()
        if einrichtung.alle():
            teile.append("Einrichtungsstand (ohne Werte):\n" + status)
        if funktion:
            fehler = protokoll.letzte_fehler(funktion, 10)
            if fehler:
                teile.append(f"Letzte Fehler der Funktion '{funktion}':\n" + "\n".join(
                    f"- {e['zeit']} ({e['art']}, {e['wo']}): {e['fehler']}" for e in fehler))
            teile.append(f"Live-Prüfung der Einrichtung: \"{py}\" -m jarvis --einrichtung {funktion}")
        ende = protokoll.protokoll_ende()
        if ende:
            teile.append("Letzte Warnungen/Fehler aus data/protokoll.log (darfst du ganz lesen):\n" + ende)
        fehlschlag = _lesen(FEHLSCHLAG_FILE)
        if fehlschlag:
            try:
                if datetime.now() - datetime.fromisoformat(fehlschlag["zeit"]) < timedelta(hours=48):
                    teile.append(f"Ein früherer Versuch ({fehlschlag.get('titel') or fehlschlag.get('auftrag', '')[:80]}) "
                                 f"ist gescheitert: {fehlschlag.get('grund', '')}")
            except (KeyError, ValueError):
                pass
        return "\n\n".join(teile)

    def _fix_prompt(self, ausgabe: str) -> str:
        nachtraege = ""
        if self._nachtraege:
            nachtraege = "\n\nNachtrag vom Nutzer: " + " | ".join(self._nachtraege)
            self._nachtraege = []
        return (f"Die Prüfung nach deiner Änderung ist fehlgeschlagen. Ausgabe:\n\n{schwaerzen(ausgabe)[-6000:]}\n\n"
                f"Behebe die Ursache (Tests nicht abschwächen oder löschen), prüfe erneut mit dem Selbsttest und gib "
                f"dann wieder das strukturierte Ergebnis für die gesamte Änderung zurück.{nachtraege}")

    @staticmethod
    async def _selftest() -> tuple[bool, str]:
        ui.dev("Führe Selbsttest aus …")
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "jarvis", "--selbsttest", cwd=str(ROOT_DIR),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        )
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=600)
        except asyncio.TimeoutError:
            proc.kill()
            return False, "Selbsttest hat länger als 10 Minuten gedauert (hängt etwas?)."
        except asyncio.CancelledError:
            proc.kill()
            raise
        return proc.returncode == 0, out.decode("utf-8", errors="replace")

    def _fehlschlag_merken(self, auftrag: str, titel: str, grund: str) -> None:
        try:
            FEHLSCHLAG_FILE.parent.mkdir(parents=True, exist_ok=True)
            FEHLSCHLAG_FILE.write_text(json.dumps({"zeit": datetime.now().isoformat(timespec="seconds"),
                                                   "titel": titel, "auftrag": schwaerzen(auftrag)[:500],
                                                   "grund": grund[:500]}, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass

    # --- Rückgängig ---------------------------------------------------------------------
    def undo_last(self) -> str:
        """Macht das letzte Jarvis-Update per 'git revert' rückgängig."""
        if not self.git.available():
            raise RuntimeError("Git ist nicht installiert.")
        entry = next((e for e in self.git.update_history(50) if not e.rueckgaengig), None)
        if entry is None:
            raise RuntimeError("Es gibt keine Änderung, die ich rückgängig machen könnte.")
        self.git.commit_all("Jarvis: Sicherung vor Rückgängig")
        before = self.git.head()
        assert before is not None
        try:
            self.git.run(*self.git._commit_identity(), "revert", "--no-edit", entry.commit)
        except GitError:
            self.git.run("revert", "--abort", check=False)
            raise RuntimeError("Die Änderung lässt sich nicht automatisch rückgängig machen, "
                               "weil spätere Änderungen darauf aufbauen.") from None
        UPDATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        UPDATE_FILE.write_text(json.dumps({
            "art": "rueckgaengig", "vorher": before, "nachher": self.git.head(), "titel": f"Rückgängig: {entry.titel}",
            "auftrag": f"Rückgängig: {entry.titel}", "zusammenfassung": f"Die Änderung „{entry.titel}“ wurde "
            "rückgängig gemacht.", "zeit": datetime.now().isoformat(timespec="seconds"), "bestaetigt": False,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        return entry.titel


# ---------------------------------------------------------------------------
# Hilfsfunktionen
# ---------------------------------------------------------------------------

def _python() -> str:
    return Path(sys.executable).as_posix()


def _text(text: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}]}


def _lesen(datei: Path) -> dict[str, Any] | None:
    try:
        daten = json.loads(datei.read_text(encoding="utf-8"))
        return daten if isinstance(daten, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def _ergebnis_lesen(strukturiert: Any, text: str) -> DevResult:
    """Strukturiertes Ergebnis von Claude Code lesen (mit Rückfall auf den Freitext)."""
    if isinstance(strukturiert, dict) and strukturiert.get("zusammenfassung"):
        return DevResult(
            erfolg=bool(strukturiert.get("erfolg", True)),
            zusammenfassung=str(strukturiert["zusammenfassung"]).strip(),
            titel=str(strukturiert.get("titel", "")).strip(),
            ausprobieren=str(strukturiert.get("ausprobieren", "")).strip(),
            naechste_schritte=str(strukturiert.get("naechste_schritte", "")).strip(),
            annahmen=[str(a) for a in strukturiert.get("annahmen") or []],
            einschraenkungen=[str(e) for e in strukturiert.get("einschraenkungen") or []],
        )
    zusammenfassung = text if text and not text.lstrip().startswith("{") else "Erledigt."
    return DevResult(True, _short(zusammenfassung, 400))


def _short(text: str, n: int = 70) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[: n - 1] + "…"


def _describe_tool(name: str, inp: dict[str, Any]) -> str:
    path = inp.get("file_path") or inp.get("path") or inp.get("notebook_path")
    if path:
        try:
            path = str(Path(path).resolve().relative_to(ROOT_DIR))
        except (ValueError, OSError):
            pass
    if name in ("Bash", "PowerShell"):
        return f"$ {_short(str(inp.get('command', '')), 110)}"
    verbs = {"Read": "liest", "Write": "schreibt", "Edit": "bearbeitet", "MultiEdit": "bearbeitet"}
    if name in verbs and path:
        return f"{verbs[name]} {path}"
    if name in ("Glob", "Grep"):
        return f"sucht {inp.get('pattern', '')}"
    if name == "WebSearch":
        return f"sucht im Web: {_short(str(inp.get('query', '')), 80)}"
    if name == "WebFetch":
        return f"liest Webseite {_short(str(inp.get('url', '')), 80)}"
    if name in ("TodoWrite", "TaskCreate"):
        return "plant: " + _short(str(inp.get("subject", "die nächsten Schritte")), 80)
    if name == "StructuredOutput":
        return "fasst das Ergebnis zusammen"
    return name.removeprefix(f"mcp__{DEV_SERVER}__")
