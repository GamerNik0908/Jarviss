"""Projekte: Jarvis arbeitet selbstständig an Projekten außerhalb seines eigenen Codes – z. B. am Roblox-Spiel.

So läuft es:
  * Ein Projekt hat einen eigenen Ordner (Standard: <Benutzerordner>/Jarvis/Projekte/<name>) mit eigenem Git,
    einer CLAUDE.md (Regeln für die KI), PROJEKT.md (Ziel) und FORTSCHRITT.md (Tagebuch).
  * Aufgaben kommen vom Nutzer ("Baue einen Shop") oder – im Dauerbetrieb – vom Planer.
  * Eine Aufgabe nach der anderen: Sicherung per Git → Entwickler (Claude Code) setzt um → automatische Prüfungen
    + Prüfer (KI) → bei Problemen bessert der Entwickler nach → Commit. Klappt es nicht, wird zurückgesetzt.
  * Rückfragen stellt Jarvis dem Nutzer; in der Ruhezeit (nachts) entscheidet die KI selbst und notiert es.
  * Jeden Morgen (bericht_uhrzeit) bekommt das Gehirn einen Bericht als Ereignis und erzählt ihn dem Nutzer.
  * Neustart-fest: Der Stand liegt in data/projekte/<name>.json. Eine unterbrochene Aufgabe wird beim nächsten
    Start zurückgesetzt und neu begonnen.

Die KI-Rollen selbst stehen in jarvis/projekt_ki.py.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, Literal

from jarvis import config, einrichtung, ui
from jarvis.developer import Git, GitError
from jarvis.projekt_ki import ARTEN, KILauf, ProjektKI, roblox_studio_mcp
from jarvis.sicherheit import schwaerzen, sieht_nach_schluessel_aus
from jarvis.skills import Skill, make_skill

if TYPE_CHECKING:
    from jarvis.app import Jarvis

_log = logging.getLogger("jarvis.projekte")
VORLAGEN = Path(__file__).resolve().parent / "vorlagen"
NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")
_GEHEIM_FRAGE = re.compile(r"passwort|kennwort|token|schlüssel|schluessel|api.?key|cookie|secret|pin\b", re.I)
MAX_DIFF = 60_000
MAX_VERSUCHE = 3
STATUS = {"offen": "offen", "in_arbeit": "in Arbeit", "erledigt": "erledigt", "fehlgeschlagen": "nicht geklappt",
          "verworfen": "verworfen"}


def projekt_ordner_basis(eigen: str = "") -> Path:
    return Path(eigen).expanduser() if eigen else Path.home() / "Jarvis" / "Projekte"


def _jetzt() -> str:
    return datetime.now().isoformat(timespec="milliseconds")     # genau genug, damit nichts doppelt berichtet wird


def _zeit(iso: str) -> datetime | None:
    try:
        return datetime.fromisoformat(iso) if iso else None
    except ValueError:
        return None


def _wann(iso: str) -> str:
    zeit = _zeit(iso)
    if zeit is None:
        return "?"
    heute = datetime.now().date()
    tag = "heute" if zeit.date() == heute else "gestern" if zeit.date() == heute - timedelta(days=1) else \
        zeit.strftime("%d.%m.")
    return f"{tag} {zeit:%H:%M}"


def _kurz(text: Any, n: int = 160) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[: n - 1] + "…"


def kennung(titel: str) -> str:
    """'Mein Roblox-Spiel!' -> 'mein_roblox-spiel'"""
    t = titel.lower().strip()
    for a, b in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss")):
        t = t.replace(a, b)
    t = re.sub(r"[^a-z0-9-]+", "_", t).strip("_-")[:40].strip("_-")
    return t or "projekt"


def in_zeitspanne(jetzt: datetime, spanne: str) -> bool:
    """True, wenn jetzt in "22:00-07:30" liegt (auch über Mitternacht)."""
    m = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})\s*", spanne or "")
    if not m:
        return False
    von, bis = int(m[1]) * 60 + int(m[2]), int(m[3]) * 60 + int(m[4])
    t = jetzt.hour * 60 + jetzt.minute
    if von == bis:
        return False
    return von <= t < bis if von < bis else (t >= von or t < bis)


def _uhrzeit(text: str) -> tuple[int, int] | None:
    m = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*", text or "")
    return (int(m[1]), int(m[2])) if m and int(m[1]) < 24 and int(m[2]) < 60 else None


# ---------------------------------------------------------------------------
# Daten
# ---------------------------------------------------------------------------

@dataclass
class Aufgabe:
    id: int
    titel: str
    beschreibung: str = ""
    quelle: str = "nutzer"           # nutzer | planer | entwickler | pruefer
    prioritaet: int = 2              # 1 = wichtig, 2 = normal, 3 = später
    status: str = "offen"            # offen | in_arbeit | erledigt | fehlgeschlagen | verworfen
    erstellt: str = field(default_factory=_jetzt)
    begonnen: str = ""
    beendet: str = ""
    ergebnis: str = ""
    hinweis: str = ""                # was der Nutzer tun/ausprobieren soll
    commit: str = ""
    vorher: str = ""                 # Git-Stand vor der Aufgabe (zum Zurücksetzen)
    versuche: int = 0
    annahmen: list[str] = field(default_factory=list)
    fragen: list[dict[str, str]] = field(default_factory=list)   # {"frage", "vorschlag", "antwort"}

    @classmethod
    def aus_dict(cls, d: dict[str, Any]) -> Aufgabe:
        namen = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in namen})


@dataclass
class Projekt:
    name: str
    titel: str
    art: str
    ordner: str
    ziel: str
    erstellt: str = field(default_factory=_jetzt)
    pausiert: bool = False
    dauerbetrieb: bool = True
    aufgaben: list[Aufgabe] = field(default_factory=list)
    naechste_id: int = 1
    letzter_bericht: str = ""
    letzter_morgenbericht: str = ""  # Datum (JJJJ-MM-TT)
    letzte_planung: str = ""
    planer_notiz: str = ""
    auto_heute: list[Any] = field(default_factory=lambda: ["", 0])   # [Datum, Anzahl selbst geplanter Aufgaben]

    @classmethod
    def aus_dict(cls, d: dict[str, Any]) -> Projekt:
        namen = {f.name for f in fields(cls)}
        p = cls(**{k: v for k, v in d.items() if k in namen and k != "aufgaben"})
        p.aufgaben = [Aufgabe.aus_dict(a) for a in d.get("aufgaben", [])]
        return p

    def aufgabe(self, nummer: int) -> Aufgabe | None:
        return next((a for a in self.aufgaben if a.id == nummer), None)

    def offene(self) -> list[Aufgabe]:
        """Offene Aufgaben in Bearbeitungsreihenfolge: wichtig vor normal, Nutzer vor Planer, alt vor neu."""
        return sorted((a for a in self.aufgaben if a.status == "offen"),
                      key=lambda a: (a.prioritaet, a.quelle != "nutzer", a.id))

    def zaehlen(self) -> dict[str, int]:
        z = {s: 0 for s in STATUS}
        for a in self.aufgaben:
            z[a.status] = z.get(a.status, 0) + 1
        return z


# ---------------------------------------------------------------------------
# Verbindung zur App
# ---------------------------------------------------------------------------

class Gastgeber:
    """Was die Projektverwaltung von der App braucht (in Tests ersetzbar)."""

    def melden(self, text: str, quelle: str) -> None:
        ui.info(f"[Ereignis · {quelle}] {text}")

    async def projektfrage(self, projekt: str, frage: str, vorschlag: str) -> str | None:
        return None

    def darf_arbeiten(self) -> bool:
        return True

    def arbeit_beendet(self) -> None:
        pass


# ---------------------------------------------------------------------------
# Roblox-Werkzeuge (Rokit verwaltet Rojo, Selene und StyLua pro Projekt)
# ---------------------------------------------------------------------------

def werkzeug_finden(name: str) -> str | None:
    gefunden = shutil.which(name)
    if gefunden:
        return gefunden
    endung = ".exe" if sys.platform == "win32" else ""
    ordner_liste = [Path.home() / ".rokit" / "bin", Path.home() / ".aftman" / "bin", Path.home() / ".foreman" / "bin"]
    if os.environ.get("LOCALAPPDATA"):          # winget legt hier Verknüpfungen an (gilt sofort, ohne Neustart)
        ordner_liste.append(Path(os.environ["LOCALAPPDATA"]) / "Microsoft" / "WinGet" / "Links")
    for ordner in ordner_liste:
        kandidat = ordner / f"{name}{endung}"
        if kandidat.is_file():
            return str(kandidat)
    return None


_ROKIT: einrichtung.Programm | None = None


def rokit_programm() -> einrichtung.Programm:
    """Rokit wird erst deklariert, wenn es ein Roblox-Projekt gibt (sonst stünde es ständig auf "fehlt")."""
    global _ROKIT
    if _ROKIT is None:
        _ROKIT = einrichtung.Programm(
            "rokit", "Rokit (installiert Rojo, Selene und StyLua für Roblox-Projekte)",
            finden=lambda: werkzeug_finden("rokit"), link="https://github.com/rojo-rbx/rokit", winget="Rojo.Rokit",
            anleitung=["Am PC: Jarvis kann Rokit über winget installieren (sag: installiere Rokit).",
                       "Sonst: auf der Rokit-Seite bei Releases die Datei für Windows laden, entpacken und "
                       "rokit.exe self-install ausführen.",
                       "Danach: Jarvis, richte die Roblox-Werkzeuge für mein Spiel ein."])
    return _ROKIT


def _ohne_fenster() -> dict[str, Any]:
    return {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}


async def _befehl(*args: str, ordner: Path, timeout: float = 180) -> tuple[int, str]:
    """Führt ein Programm im Projektordner aus (ohne Fenster) und liefert (Exit-Code, Ausgabe)."""
    proc = await asyncio.create_subprocess_exec(*args, cwd=str(ordner), stdin=asyncio.subprocess.DEVNULL,
                                                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
                                                **_ohne_fenster())
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        proc.kill()
        raise
    return proc.returncode or 0, out.decode("utf-8", errors="replace")


# ---------------------------------------------------------------------------
# Projektverwaltung
# ---------------------------------------------------------------------------

class Projektverwaltung:
    def __init__(self, cfg: dict[str, Any], gastgeber: Gastgeber | None = None, ki: ProjektKI | None = None) -> None:
        self.cfg = cfg["projekte"]
        self.gastgeber = gastgeber or Gastgeber()
        self.ki = ki or ProjektKI(cfg)
        self.projekte: dict[str, Projekt] = {}
        self.aktuell: dict[str, Any] | None = None      # was gerade läuft (Status, HUD)
        self.pause_bis = 0.0                             # time.time(), z. B. nach Erreichen des Nutzungslimits
        self.letztes_ende = 0.0
        self._wecker: asyncio.Event | None = None
        self._tasks: list[asyncio.Task[Any]] = []
        self._sync: dict[str, asyncio.subprocess.Process] = {}

    # --- Speichern & Laden ------------------------------------------------------------------
    @staticmethod
    def _dir() -> Path:
        return config.DATA_DIR / "projekte"

    def laden(self) -> None:
        self.projekte.clear()
        for datei in sorted(self._dir().glob("*.json")) if self._dir().is_dir() else []:
            try:
                p = Projekt.aus_dict(json.loads(datei.read_text(encoding="utf-8")))
                self.projekte[p.name] = p
                if p.art == "roblox":
                    rokit_programm()
            except (OSError, ValueError, TypeError, json.JSONDecodeError) as e:
                ui.warn(f"Projektdatei {datei.name} ist beschädigt: {e}")

    def speichern(self, p: Projekt) -> None:
        self._dir().mkdir(parents=True, exist_ok=True)
        datei = self._dir() / f"{p.name}.json"
        tmp = datei.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(p), ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, datei)

    # --- Nachschlagen ----------------------------------------------------------------------------
    def finden(self, name: str = "") -> Projekt | None:
        """Nach Kennung, Titel oder Teil davon. Leer = das einzige Projekt."""
        name = (name or "").strip()
        if not name:
            return next(iter(self.projekte.values())) if len(self.projekte) == 1 else None
        if name in self.projekte:
            return self.projekte[name]
        k, klein = kennung(name), name.lower()
        for p in self.projekte.values():
            if p.name == k or p.titel.lower() == klein:
                return p
        treffer = [p for p in self.projekte.values() if klein in p.titel.lower() or k in p.name
                   or (klein == "roblox" and p.art == "roblox")]
        return treffer[0] if len(treffer) == 1 else None

    def _unbekannt(self, name: str) -> str:
        if not self.projekte:
            return "Es gibt noch kein Projekt. Lege eins mit projekt_anlegen an."
        return f"Projekt '{name}' nicht eindeutig. Vorhanden: " + ", ".join(
            f"{p.name} („{p.titel}“)" for p in self.projekte.values())

    @property
    def beschaeftigt(self) -> bool:
        return self.aktuell is not None

    # --- Anlegen & Aufgaben ------------------------------------------------------------------------
    def anlegen(self, titel: str, ziel: str, art: str = "roblox", ordner: str = "",
                dauerbetrieb: bool = True) -> Projekt:
        art = art if art in ARTEN else "allgemein"
        name = kennung(titel)
        basis, nummer = name, 2
        while name in self.projekte:
            name, nummer = f"{basis}_{nummer}", nummer + 1
        ziel_ordner = Path(ordner).expanduser() if ordner.strip() else \
            projekt_ordner_basis(str(self.cfg.get("ordner") or "")) / name
        ziel_ordner = ziel_ordner.resolve()
        self._ordner_pruefen(ziel_ordner)
        neu = not ziel_ordner.exists() or not any(ziel_ordner.iterdir())
        ziel_ordner.mkdir(parents=True, exist_ok=True)
        werte = {"TITEL": titel.strip(), "ZIEL": ziel.strip(), "NAME": name, "DATUM": datetime.now().strftime("%d.%m.%Y")}
        self._vorlage_kopieren(art, ziel_ordner, werte, nur_regeln=not neu)
        git = Git(ziel_ordner)
        git.ensure_repo()
        git.commit_all(f"Jarvis: Projekt „{titel.strip()}“ angelegt")
        p = Projekt(name=name, titel=titel.strip(), art=art, ordner=str(ziel_ordner), ziel=ziel.strip(),
                    dauerbetrieb=dauerbetrieb)
        self.projekte[name] = p
        self.speichern(p)
        if art == "roblox":
            rokit_programm()
        return p          # wecken() ruft der Aufrufer im Event-Loop auf (anlegen läuft oft in einem Thread)

    @staticmethod
    def _ordner_pruefen(ordner: Path) -> None:
        heim = Path.home().resolve()
        jarvis = config.ROOT_DIR.resolve()
        gesperrt = {Path(ordner.anchor), heim / "Desktop", heim / "Documents", heim / "Downloads"}
        if ordner in gesperrt or heim.is_relative_to(ordner) or jarvis.is_relative_to(ordner) \
                or ordner.is_relative_to(jarvis) or ordner.is_relative_to(config.DATA_DIR.resolve()):
            raise ValueError(f"Der Ordner {ordner} ist dafür nicht geeignet – bitte einen eigenen Projektordner "
                             "nehmen (oder leer lassen, dann lege ich einen an).")
        if ordner.exists() and not ordner.is_dir():
            raise ValueError(f"{ordner} ist eine Datei, kein Ordner.")

    @staticmethod
    def _vorlage_kopieren(art: str, ziel: Path, werte: dict[str, str], nur_regeln: bool = False) -> None:
        """Kopiert jarvis/vorlagen/<art> ins Projekt ('CLAUDE.md.vorlage' -> 'CLAUDE.md', 'gitignore.txt' ->
        '.gitignore', 'gitattributes.txt' -> '.gitattributes'). Vorhandene Dateien werden nie überschrieben; in
        einen vorhandenen Ordner (nur_regeln) kommen nur Regeln und Notizen, kein Beispielcode."""
        quelle = VORLAGEN / art
        for datei in sorted(quelle.rglob("*")):
            if not datei.is_file():
                continue
            rel = datei.relative_to(quelle)
            if nur_regeln and (rel.parts[0] == "src" or rel.name in ("default.project.json", "README.md")):
                continue
            name = {"CLAUDE.md.vorlage": "CLAUDE.md", "gitignore.txt": ".gitignore",
                    "gitattributes.txt": ".gitattributes"}.get(rel.name, rel.name)
            ziel_datei = ziel / rel.parent / name
            if ziel_datei.exists():
                continue
            ziel_datei.parent.mkdir(parents=True, exist_ok=True)
            text = datei.read_text(encoding="utf-8")
            for k, v in werte.items():
                if datei.suffix in (".luau", ".json", ".toml"):     # in Zeichenketten: Anführungszeichen entschärfen
                    v = v.replace("\\", "\\\\").replace('"', "'").replace("\n", " ")
                text = text.replace("{{" + k + "}}", v)
            ziel_datei.write_text(text, encoding="utf-8", newline="\n")

    def aufgabe_hinzufuegen(self, p: Projekt, titel: str, beschreibung: str = "", *, prioritaet: int = 2,
                            quelle: str = "nutzer") -> Aufgabe:
        a = Aufgabe(id=p.naechste_id, titel=_kurz(titel, 90), beschreibung=beschreibung.strip(), quelle=quelle,
                    prioritaet=min(3, max(1, int(prioritaet))))
        p.naechste_id += 1
        p.aufgaben.append(a)
        if quelle == "nutzer":
            p.planer_notiz = ""          # neue Wünsche: der Planer darf wieder planen
        self.speichern(p)
        self.wecken()
        return a

    def aufgabe_entfernen(self, p: Projekt, suche: str) -> list[Aufgabe]:
        suche = suche.strip().lstrip("#").lower()
        weg = [a for a in p.aufgaben if a.status == "offen"
               and (str(a.id) == suche or (not suche.isdigit() and suche in a.titel.lower()))]
        for a in weg:
            a.status, a.beendet = "verworfen", _jetzt()
        if weg:
            self.speichern(p)
        return weg

    # --- Arbeitsschleife -----------------------------------------------------------------------------
    def starten(self) -> None:
        """Im laufenden Event-Loop: Arbeitsschleife und Morgenbericht starten."""
        self._wecker = asyncio.Event()
        self.laden()
        self._wiederherstellen()
        loop = asyncio.get_running_loop()
        self._tasks = [loop.create_task(self.arbeiten(), name="projekte"),
                       loop.create_task(self.bericht_schleife(), name="projektberichte")]

    async def stoppen(self, timeout: float = 8) -> None:
        for t in self._tasks:
            t.cancel()
        if self._tasks:
            await asyncio.wait(self._tasks, timeout=timeout)
        for name in list(self._sync):
            await self._sync_beenden(name)

    def wecken(self) -> None:
        if self._wecker is not None:
            self._wecker.set()

    def _wiederherstellen(self) -> None:
        """Nach einem Neustart: halbfertige Aufgaben zurücksetzen und neu einplanen."""
        for p in self.projekte.values():
            for a in p.aufgaben:
                if a.status == "in_arbeit":
                    try:
                        if a.vorher:
                            Git(Path(p.ordner)).reset_hard(a.vorher)
                    except (GitError, OSError) as e:
                        ui.warn(f"Projekt {p.name}: Zurücksetzen von Aufgabe {a.id} fehlgeschlagen ({e}).")
                    a.status = "offen"
                    ui.projekt(p.name, f"Aufgabe {a.id} „{a.titel}“ wurde unterbrochen – fange sie neu an.")
            self.speichern(p)

    async def arbeiten(self) -> None:
        assert self._wecker is not None
        while True:
            try:
                await asyncio.wait_for(self._wecker.wait(), timeout=60)
            except asyncio.TimeoutError:
                pass
            self._wecker.clear()
            pause = float(self.cfg.get("pause_minuten", 2)) * 60
            if time.time() - self.letztes_ende < pause:
                continue
            try:
                if await self.schritt():
                    self.letztes_ende = time.time()
                    if pause <= 0:
                        self._wecker.set()
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 – die Schleife darf nie sterben
                _log.exception("Fehler in der Projektarbeit")
                ui.error(f"Fehler in der Projektarbeit: {schwaerzen(e)}")
                self.letztes_ende = time.time()

    def naechster_schritt(self, jetzt: datetime | None = None) -> tuple[Projekt, Aufgabe | None] | None:
        """(Projekt, Aufgabe) zum Umsetzen, (Projekt, None) zum Planen – oder None (nichts zu tun)."""
        if time.time() < self.pause_bis or not self.gastgeber.darf_arbeiten():
            return None
        heute = (jetzt or datetime.now()).date().isoformat()
        max_auto = int(self.cfg.get("max_auto_aufgaben_pro_tag", 12))
        kandidaten: list[tuple[tuple[Any, ...], Projekt, Aufgabe | None]] = []
        for p in self.projekte.values():
            if p.pausiert:
                continue
            auto = p.auto_heute[1] if p.auto_heute and p.auto_heute[0] == heute else 0
            for a in p.offene():
                if a.quelle != "nutzer" and auto >= max_auto:
                    continue
                kandidaten.append(((a.prioritaet, a.quelle != "nutzer", a.erstellt), p, a))
                break
            else:
                nie_geplant = not p.letzte_planung and not p.aufgaben
                planen_erlaubt = p.dauerbetrieb and auto < max_auto and not p.planer_notiz
                kuerzlich = (_zeit(p.letzte_planung) or datetime.min) > (jetzt or datetime.now()) - timedelta(minutes=30)
                if nie_geplant or (planen_erlaubt and not kuerzlich):
                    kandidaten.append(((4, True, p.letzte_planung), p, None))
        if not kandidaten:
            return None
        _, p, a = min(kandidaten, key=lambda k: k[0])
        return p, a

    async def schritt(self) -> bool:
        """Ein Arbeitsschritt (eine Aufgabe oder eine Planung). False, wenn nichts zu tun war."""
        naechstes = self.naechster_schritt()
        if naechstes is None:
            return False
        p, a = naechstes
        try:
            if a is None:
                await self._planen(p)
            else:
                await self._aufgabe_ausfuehren(p, a)
        finally:
            self.aktuell = None
            self.gastgeber.arbeit_beendet()
        return True

    def _fortschritt(self, text: str) -> None:
        if self.aktuell is not None:
            self.aktuell["letzte_aktion"] = text
            if not text.startswith(("$ ", "liest ", "schreibt ", "sucht ")):
                self.aktuell["meldung"] = text
            ui.projekt(self.aktuell["projekt"], text)

    def _ruhezeit(self) -> bool:
        return in_zeitspanne(datetime.now(), str(self.cfg.get("ruhezeit", "")))

    def _limit_erreicht(self, p: Projekt) -> None:
        self.pause_bis = time.time() + 3600
        ui.warn("Das Claude-Nutzungslimit ist erreicht – die Projektarbeit pausiert eine Stunde.")
        _log.warning("Projekt %s: Nutzungslimit erreicht, Pause bis %s", p.name,
                     datetime.fromtimestamp(self.pause_bis).strftime("%H:%M"))

    # --- Planen ---------------------------------------------------------------------------------------
    async def _planen(self, p: Projekt) -> None:
        anlass = ("Das Projekt ist neu – plane die ersten Schritte." if not p.aufgaben else
                  "Alle Aufgaben sind abgearbeitet – plane die nächsten Schritte zum Ziel.")
        self.aktuell = {"projekt": p.name, "titel": p.titel, "rolle": "Planer", "aufgabe": None,
                        "aufgabe_titel": "plant die nächsten Schritte", "seit": time.time(), "letzte_aktion": "",
                        "meldung": ""}
        ui.projekt(p.name, "Der Planer plant die nächsten Schritte …")
        lauf = await self.ki.planen(p, anlass, self._fortschritt)
        p.letzte_planung = _jetzt()
        if lauf.art == "limit":
            self._limit_erreicht(p)
        elif not lauf.ok:
            ui.warn(f"Projekt {p.name}: Planung fehlgeschlagen ({lauf.fehler}).")
        else:
            neu = []
            for t in (lauf.daten.get("aufgaben") or [])[:6]:
                if isinstance(t, dict) and str(t.get("titel", "")).strip():
                    neu.append(self.aufgabe_hinzufuegen(p, str(t["titel"]), str(t.get("beschreibung", "")),
                                                        prioritaet=int(t.get("prioritaet") or 2), quelle="planer"))
            p.planer_notiz = "" if neu else (str(lauf.daten.get("notiz") or "").strip() or
                                             "Keine weiteren Aufgaben – ich brauche neue Wünsche.")
            ui.projekt(p.name, f"Neu geplant: {', '.join(a.titel for a in neu)}" if neu else
                       f"Planer: {p.planer_notiz}")
        self.speichern(p)

    # --- Eine Aufgabe -----------------------------------------------------------------------------------
    async def _aufgabe_ausfuehren(self, p: Projekt, a: Aufgabe) -> None:
        ordner = Path(p.ordner)
        if not ordner.is_dir():
            p.pausiert = True
            self.speichern(p)
            ui.warn(f"Projekt {p.name}: Ordner {ordner} fehlt – Projekt pausiert.")
            self.gastgeber.melden(f"Der Ordner des Projekts „{p.titel}“ ({ordner}) fehlt. Ich habe das Projekt "
                                  "pausiert. Frag den Nutzer, wo es jetzt liegt.", "Projekte")
            return
        git = Git(ordner)
        try:
            git.ensure_repo()
            git.commit_all(f"Jarvis: Stand vor Aufgabe {a.id}")      # sichert auch Änderungen des Nutzers
        except (GitError, OSError) as e:
            p.pausiert = True
            self.speichern(p)
            ui.warn(f"Projekt {p.name}: Git-Problem ({schwaerzen(e)}) – Projekt pausiert.")
            self.gastgeber.melden(f"Ich konnte den Stand des Projekts „{p.titel}“ nicht per Git sichern "
                                  f"({_kurz(schwaerzen(e), 200)}) und habe es pausiert. Ist Git installiert?", "Projekte")
            return
        a.vorher = git.head() or ""
        a.status, a.begonnen, a.versuche = "in_arbeit", _jetzt(), a.versuche + 1
        if a.quelle != "nutzer":
            heute = datetime.now().date().isoformat()
            p.auto_heute = [heute, (p.auto_heute[1] if p.auto_heute and p.auto_heute[0] == heute else 0) + 1]
        self.speichern(p)
        self.aktuell = {"projekt": p.name, "titel": p.titel, "rolle": "Entwickler", "aufgabe": a.id,
                        "aufgabe_titel": a.titel, "seit": time.time(), "letzte_aktion": "", "meldung": ""}
        ui.projekt(p.name, f"Aufgabe {a.id}: {a.titel}")
        studio = roblox_studio_mcp() if p.art == "roblox" and self.cfg.get("roblox_studio", True) else None

        async def fragen(frage: str, vorschlag: str) -> str:
            return await self._frage(p, a, frage, vorschlag)

        try:
            lauf = await self.ki.umsetzen(p, a, fragen=fragen, fortschritt=self._fortschritt, studio=studio)
            for runde in range(int(self.cfg.get("pruef_runden", 2)) + 1):
                if not lauf.ok:
                    self._zuruecksetzen(git, a)
                    self._nicht_geklappt(p, a, lauf)
                    return
                if not lauf.daten.get("erfolg", True):
                    self._zuruecksetzen(git, a)
                    a.status, a.beendet = "fehlgeschlagen", _jetzt()
                    a.ergebnis = str(lauf.daten.get("zusammenfassung") or "Nicht umsetzbar.")
                    return
                probleme = await self._pruefen(p, a, git, lauf)
                if not probleme:
                    self._abschliessen(p, a, git, lauf)
                    return
                if runde == int(self.cfg.get("pruef_runden", 2)):
                    self._zuruecksetzen(git, a)
                    a.status, a.beendet = "fehlgeschlagen", _jetzt()
                    a.ergebnis = "Die Prüfung wurde nicht bestanden: " + "; ".join(_kurz(x, 200) for x in probleme[:3])
                    return
                self.aktuell.update(rolle="Entwickler", meldung=f"bessert nach ({runde + 1}. Runde)")
                ui.projekt(p.name, f"Prüfung: {len(probleme)} Problem(e) – Entwickler bessert nach …")
                lauf = await self.ki.umsetzen(p, a, fragen=fragen, fortschritt=self._fortschritt, studio=studio,
                                              sitzung=lauf.sitzung, nachbessern=probleme)
        except asyncio.CancelledError:
            self._zuruecksetzen(git, a)
            a.status = "offen"
            raise
        except Exception as e:  # noqa: BLE001
            self._zuruecksetzen(git, a)
            self._nicht_geklappt(p, a, KILauf(False, "fehler", f"{type(e).__name__}: {schwaerzen(e)}"))
            _log.exception("Aufgabe %s in Projekt %s fehlgeschlagen", a.id, p.name)
        finally:
            self.speichern(p)
            self._nach_aufgabe(p, a)

    def _zuruecksetzen(self, git: Git, a: Aufgabe) -> None:
        if a.vorher:
            try:
                git.reset_hard(a.vorher)
            except GitError as e:
                ui.warn(f"Zurücksetzen fehlgeschlagen: {e}")

    def _nicht_geklappt(self, p: Projekt, a: Aufgabe, lauf: KILauf) -> None:
        if lauf.art == "limit":
            a.status = "offen"
            a.versuche -= 1
            self._limit_erreicht(p)
            return
        a.ergebnis = lauf.fehler or "Unbekannter Fehler."
        if a.versuche < MAX_VERSUCHE and lauf.art in ("voruebergehend", "zeit"):
            a.status = "offen"                       # später noch einmal
            ui.projekt(p.name, f"Aufgabe {a.id} hat nicht geklappt ({_kurz(a.ergebnis, 80)}) – neuer Versuch später.")
        else:
            a.status, a.beendet = "fehlgeschlagen", _jetzt()

    async def _pruefen(self, p: Projekt, a: Aufgabe, git: Git, lauf: KILauf) -> list[str]:
        """Automatische Prüfungen + Prüfer-KI. Gibt blockierende Probleme zurück (leer = in Ordnung)."""
        assert self.aktuell is not None
        git.run("add", "-A")
        diff = git.run("diff", "--cached", "--no-color", a.vorher, check=False) if a.vorher else ""
        if not diff.strip():
            return []
        self.aktuell.update(rolle="Prüfer", meldung="prüft die Änderung")
        ergebnisse = await self.pruefbefehle(p)
        probleme = [f"Automatische Prüfung „{name}“ fehlgeschlagen:\n{_kurz(ausgabe, 1500)}"
                    for name, ok, ausgabe in ergebnisse if not ok]
        zeilen = [f"{name}: {'OK' if ok else 'FEHLER'}" for name, ok, _ in ergebnisse]
        if len(diff) > MAX_DIFF:
            diff = diff[:MAX_DIFF] + f"\n… (gekürzt, insgesamt {len(diff)} Zeichen – lies Dateien bei Bedarf selbst)"
        urteil = await self.ki.pruefen(p, a, diff, zeilen, str(lauf.daten.get("zusammenfassung", "")), self._fortschritt)
        if urteil.art == "limit":
            self._limit_erreicht(p)
        if urteil.ok:
            probleme += [str(x) for x in urteil.daten.get("blockierend") or [] if str(x).strip()]
            a.annahmen += [f"Prüfer-Hinweis: {h}" for h in (urteil.daten.get("hinweise") or [])[:3]]
        return probleme

    async def pruefbefehle(self, p: Projekt) -> list[tuple[str, bool, str]]:
        """Projektart-abhängige Prüfungen, soweit die Werkzeuge installiert sind."""
        if p.art != "roblox":
            return []
        ordner = Path(p.ordner)
        befehle = []
        if (ordner / "src").is_dir():
            if werkzeug_finden("selene"):
                befehle.append(("Selene (Fehlersuche)", [werkzeug_finden("selene") or "selene", "src"]))
            if werkzeug_finden("stylua"):
                befehle.append(("StyLua (Formatierung)", [werkzeug_finden("stylua") or "stylua", "--check", "src"]))
        if werkzeug_finden("rojo") and (ordner / "default.project.json").is_file():
            ziel = Path(tempfile.gettempdir()) / f"jarvis_{p.name}_pruefung.rbxlx"
            befehle.append(("Rojo-Build", [werkzeug_finden("rojo") or "rojo", "build", "default.project.json",
                                           "-o", str(ziel)]))
        ergebnisse = []
        for name, args in befehle:
            try:
                code, ausgabe = await _befehl(*args, ordner=ordner)
                ergebnisse.append((name, code == 0, ausgabe[-3000:]))
            except (OSError, asyncio.TimeoutError) as e:
                ergebnisse.append((name, True, f"übersprungen: {e}"))
        return ergebnisse

    def _abschliessen(self, p: Projekt, a: Aufgabe, git: Git, lauf: KILauf) -> None:
        d = lauf.daten
        a.ergebnis = str(d.get("zusammenfassung") or "Erledigt.").strip()
        a.hinweis = str(d.get("hinweis_fuer_nutzer") or "").strip()
        a.annahmen = [str(x) for x in d.get("annahmen") or []] + a.annahmen
        if git.commit_all(schwaerzen(f"Aufgabe {a.id}: {a.titel}\n\n{a.ergebnis}")):
            a.commit = git.head() or ""
        a.status, a.beendet = "erledigt", _jetzt()
        ui.projekt(p.name, f"✓ Aufgabe {a.id} erledigt: {a.ergebnis}")
        if p.dauerbetrieb:
            for f in (d.get("folgeaufgaben") or [])[:3]:
                if isinstance(f, dict) and str(f.get("titel", "")).strip():
                    self.aufgabe_hinzufuegen(p, str(f["titel"]), str(f.get("beschreibung", "")), prioritaet=3,
                                             quelle="entwickler")

    def _nach_aufgabe(self, p: Projekt, a: Aufgabe) -> None:
        """Eigene Aufgaben des Nutzers werden sofort gemeldet (nicht in der Ruhezeit), der Rest im Bericht."""
        if a.quelle != "nutzer" or a.status not in ("erledigt", "fehlgeschlagen") or self._ruhezeit():
            return
        if a.status == "erledigt":
            text = f"Projekt „{p.titel}“: Die Aufgabe „{a.titel}“ ist fertig. {a.ergebnis}"
            if a.hinweis:
                text += f" Zum Ausprobieren: {a.hinweis}"
        else:
            text = f"Projekt „{p.titel}“: Die Aufgabe „{a.titel}“ hat nicht geklappt: {a.ergebnis}"
        self.gastgeber.melden(text + " Sag es dem Nutzer kurz.", "Projekte")

    async def _frage(self, p: Projekt, a: Aufgabe, frage: str, vorschlag: str) -> str:
        if _GEHEIM_FRAGE.search(frage):
            return "Nach Passwörtern, Tokens oder Schlüsseln wird nie gefragt. Arbeite ohne sie weiter."
        eintrag = {"frage": schwaerzen(frage), "vorschlag": schwaerzen(vorschlag), "antwort": ""}
        a.fragen.append(eintrag)
        self.speichern(p)
        if self._ruhezeit():
            return ("Der Nutzer schläft gerade (Ruhezeit). Nimm deinen Vorschlag, nenne ihn unter annahmen und "
                    "arbeite weiter – Jarvis fragt ihn morgen.")
        if sum(1 for f in a.fragen if f.get("antwort")) >= 2 or len(a.fragen) > 3:
            return "Für diese Aufgabe genug gefragt – entscheide selbst und nenne es unter annahmen."
        if self.aktuell is not None:
            self.aktuell["frage"] = eintrag["frage"]
        try:
            antwort = await self.gastgeber.projektfrage(p.titel, eintrag["frage"], eintrag["vorschlag"])
        finally:
            if self.aktuell is not None:
                self.aktuell["frage"] = None
        if not antwort:
            return "Keine Antwort vom Nutzer – nimm deinen Vorschlag und nenne ihn unter annahmen."
        eintrag["antwort"] = schwaerzen(antwort)
        self.speichern(p)
        return f"Antwort des Nutzers: {eintrag['antwort']}"

    # --- Steuern ----------------------------------------------------------------------------------------
    async def steuern(self, p: Projekt, aktion: str) -> str:
        if aktion == "pausieren":
            p.pausiert = True
            text = f"„{p.titel}“ ist pausiert" + (" (die laufende Aufgabe wird noch fertig)." if
                                                   self.aktuell and self.aktuell["projekt"] == p.name else ".")
        elif aktion == "fortsetzen":
            p.pausiert, self.pause_bis = False, 0.0
            text = f"Ich arbeite wieder an „{p.titel}“."
        elif aktion in ("dauerbetrieb_an", "dauerbetrieb_aus"):
            p.dauerbetrieb = aktion.endswith("_an")
            p.planer_notiz = ""
            text = (f"Dauerbetrieb für „{p.titel}“ ist an: Ich plane selbst weiter (höchstens "
                    f"{self.cfg.get('max_auto_aufgaben_pro_tag', 12)} eigene Aufgaben pro Tag)."
                    if p.dauerbetrieb else f"Dauerbetrieb aus: Ich arbeite nur noch deine Aufgaben für „{p.titel}“ ab.")
        elif aktion == "jetzt_planen":
            p.letzte_planung, p.planer_notiz = "", ""
            if not p.dauerbetrieb and not p.offene():
                p.dauerbetrieb = True
            text = "Der Planer überlegt sich die nächsten Schritte, sobald gerade nichts anderes läuft."
        elif aktion == "studio_sync_an":
            text = await self.studio_sync(p, True)
        elif aktion == "studio_sync_aus":
            text = await self.studio_sync(p, False)
        elif aktion == "ordner_oeffnen":
            self._ordner_oeffnen(Path(p.ordner))
            text = f"Ordner geöffnet: {p.ordner}"
        else:
            return f"Unbekannte Aktion '{aktion}'."
        self.speichern(p)
        self.wecken()
        return text

    @staticmethod
    def _ordner_oeffnen(ordner: Path) -> None:
        if sys.platform == "win32":
            os.startfile(str(ordner))  # type: ignore[attr-defined]
        else:
            subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(ordner)],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)

    async def werkzeuge_einrichten(self, p: Projekt) -> str:
        """Roblox: Rojo, Selene und StyLua (aus rokit.toml) und das Rojo-Plugin für Studio installieren."""
        if p.art != "roblox":
            return "Das ist kein Roblox-Projekt – hier gibt es keine Werkzeuge einzurichten."
        rokit = werkzeug_finden("rokit")
        if not rokit:
            return ("Rokit ist nicht installiert. Biete programm_installieren('rokit') an (oder Anleitung per "
                    "einrichtung_anleitung) und ruf danach projekt_werkzeuge_einrichten erneut auf.")
        ordner = Path(p.ordner)
        schritte = [("Rokit vorbereiten", [rokit, "self-install"]),
                    ("Rojo, Selene und StyLua installieren", [rokit, "install", "--no-trust-check"])]
        berichte = []
        for name, args in schritte:
            code, ausgabe = await _befehl(*args, ordner=ordner, timeout=600)
            if code != 0:
                return f"{name} ist fehlgeschlagen (Code {code}): {_kurz(ausgabe, 400)}"
            berichte.append(name)
        rojo = werkzeug_finden("rojo")
        if rojo:
            code, ausgabe = await _befehl(rojo, "plugin", "install", ordner=ordner, timeout=120)
            berichte.append("Rojo-Plugin für Studio installiert" if code == 0 else
                            f"Rojo-Plugin nicht installiert ({_kurz(ausgabe, 200)})")
        fehlend = [w for w in ("rojo", "selene", "stylua") if not werkzeug_finden(w)]
        text = "Erledigt: " + "; ".join(berichte) + "."
        if fehlend:
            text += (f" Noch nicht gefunden: {', '.join(fehlend)} – eventuell muss Jarvis neu gestartet werden, "
                     "damit der neue Programmpfad gilt.")
        return text + " Für den Live-Abgleich mit Studio: projekt_steuern(studio_sync_an)."

    async def studio_sync(self, p: Projekt, an: bool) -> str:
        """Startet/stoppt 'rojo serve' (nur auf diesem PC erreichbar), damit Code-Änderungen live in Studio landen."""
        if not an:
            gestoppt = await self._sync_beenden(p.name)
            return "Live-Abgleich mit Studio beendet." if gestoppt else "Der Live-Abgleich lief nicht."
        if p.art != "roblox":
            return "Der Live-Abgleich mit Studio gibt es nur für Roblox-Projekte."
        laufend = self._sync.get(p.name)
        if laufend is not None and laufend.returncode is None:
            return "Der Live-Abgleich läuft schon. In Studio im Rojo-Plugin auf Connect klicken."
        rojo = werkzeug_finden("rojo")
        if not rojo:
            return "Rojo fehlt noch – erst projekt_werkzeuge_einrichten."
        proc = await asyncio.create_subprocess_exec(
            rojo, "serve", "default.project.json", "--address", "127.0.0.1", cwd=p.ordner,
            stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
            **_ohne_fenster())
        try:
            await asyncio.wait_for(proc.wait(), timeout=2)
            return f"Rojo ist sofort beendet worden (Code {proc.returncode}) – läuft schon ein anderer Rojo-Server?"
        except asyncio.TimeoutError:
            pass
        self._sync[p.name] = proc
        return ("Live-Abgleich läuft. Der Nutzer öffnet in Roblox Studio sein Spiel, klickt im Rojo-Plugin auf "
                "Connect – ab dann landet jede Änderung sofort in Studio.")

    async def _sync_beenden(self, name: str) -> bool:
        proc = self._sync.pop(name, None)
        if proc is None or proc.returncode is not None:
            return False
        proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), timeout=5)
        except asyncio.TimeoutError:
            proc.kill()
        return True

    # --- Berichte ---------------------------------------------------------------------------------------
    def status_text(self, p: Projekt) -> str:
        z = p.zaehlen()
        gesamt = sum(z[s] for s in ("offen", "in_arbeit", "erledigt", "fehlgeschlagen"))
        zustand = "pausiert" if p.pausiert else "aktiv"
        if not p.pausiert and time.time() < self.pause_bis:
            zustand = f"Pause wegen Nutzungslimit bis {datetime.fromtimestamp(self.pause_bis):%H:%M}"
        zeilen = [f"Projekt „{p.titel}“ ({ARTEN.get(p.art, p.art)}; Ordner {p.ordner}): {zustand}, Dauerbetrieb "
                  f"{'an' if p.dauerbetrieb else 'aus'}. Erledigt: {z['erledigt']} von {gesamt} Aufgaben."]
        if self.aktuell and self.aktuell["projekt"] == p.name:
            minuten = int((time.time() - self.aktuell["seit"]) // 60)
            zeilen.append(f"Gerade: {self.aktuell['rolle']} arbeitet seit {minuten} Minuten an "
                          f"„{self.aktuell['aufgabe_titel']}“" + (f" – {self.aktuell['meldung']}"
                                                                 if self.aktuell.get("meldung") else "") + ".")
        fertig = sorted((a for a in p.aufgaben if a.status == "erledigt"), key=lambda a: a.beendet)[-3:]
        if fertig:
            zeilen.append("Zuletzt fertig: " + " | ".join(f"{a.id}. {a.titel} ({_wann(a.beendet)}): "
                                                         f"{_kurz(a.ergebnis, 140)}" for a in reversed(fertig)))
        offen = p.offene()
        if offen:
            zeilen.append("Als Nächstes: " + "; ".join(f"{a.id}. {a.titel}" + (" (dein Wunsch)" if a.quelle == "nutzer"
                                                                             else "") for a in offen[:4])
                          + (f" (+{len(offen) - 4} weitere)" if len(offen) > 4 else ""))
        hinweise = [a.hinweis for a in fertig if a.hinweis]
        if hinweise:
            zeilen.append("Zum Ausprobieren: " + " | ".join(hinweise))
        offene_fragen = self._unbeantwortete_fragen(p)
        if offene_fragen:
            zeilen.append("Ohne Antwort entschieden (Nutzer kann korrigieren): " + " | ".join(offene_fragen[:3]))
        if p.planer_notiz:
            zeilen.append(f"Planer: {p.planer_notiz}")
        return "\n".join(zeilen)

    @staticmethod
    def _unbeantwortete_fragen(p: Projekt, seit: datetime | None = None) -> list[str]:
        return [f"{f['frage']} (angenommen: {f.get('vorschlag') or 'eigene Entscheidung'})" for a in p.aufgaben
                for f in a.fragen if not f.get("antwort") and (seit is None or (_zeit(a.begonnen) or datetime.min) > seit)]

    def bericht_text(self, p: Projekt, markieren: bool = True) -> str:
        """Was seit dem letzten Bericht passiert ist (für den Morgenbericht und "Was hast du gemacht?")."""
        seit = _zeit(p.letzter_bericht) or _zeit(p.erstellt) or datetime.min
        erledigt = [a for a in p.aufgaben if a.status == "erledigt" and (_zeit(a.beendet) or datetime.min) > seit]
        gescheitert = [a for a in p.aufgaben if a.status == "fehlgeschlagen" and (_zeit(a.beendet) or datetime.min) > seit]
        zeilen = [f"Projekt „{p.titel}“ – seit {_wann(seit.isoformat()) if seit > datetime.min else 'Beginn'}:"]
        if erledigt:
            zeilen.append(f"Erledigt ({len(erledigt)}): " + " | ".join(f"{a.titel}: {_kurz(a.ergebnis, 200)}"
                                                                       for a in erledigt))
        else:
            zeilen.append("Seitdem keine Aufgabe fertig geworden.")
        if gescheitert:
            zeilen.append("Nicht geklappt: " + " | ".join(f"{a.titel} ({_kurz(a.ergebnis, 120)})" for a in gescheitert))
        annahmen = [x for a in erledigt for x in a.annahmen if not x.startswith("Prüfer-Hinweis")]
        if annahmen:
            zeilen.append("Selbst entschieden: " + " | ".join(_kurz(x, 120) for x in annahmen[:4]))
        fragen = self._unbeantwortete_fragen(p, seit)
        if fragen:
            zeilen.append("Offene Fragen an den Nutzer: " + " | ".join(fragen[:3]))
        hinweise = [a.hinweis for a in erledigt if a.hinweis]
        if hinweise:
            zeilen.append("Zum Ausprobieren: " + " | ".join(hinweise[:3]))
        offen = p.offene()
        z = p.zaehlen()
        zeilen.append(f"Als Nächstes: {', '.join(a.titel for a in offen[:3]) or 'nichts geplant'}. Gesamt: "
                      f"{z['erledigt']} erledigt, {z['offen']} offen" + (". Projekt ist pausiert." if p.pausiert else "."))
        if p.planer_notiz:
            zeilen.append(f"Planer: {p.planer_notiz}")
        if markieren:
            p.letzter_bericht = _jetzt()
            self.speichern(p)
        return "\n".join(zeilen)

    def morgenberichte_faellig(self, jetzt: datetime) -> list[Projekt]:
        uhr = _uhrzeit(str(self.cfg.get("bericht_uhrzeit", "")))
        if uhr is None or (jetzt.hour, jetzt.minute) < uhr:
            return []
        heute = jetzt.date().isoformat()
        faellig = []
        for p in self.projekte.values():
            if p.letzter_morgenbericht == heute:
                continue
            seit = _zeit(p.letzter_bericht) or datetime.min
            etwas_passiert = any((_zeit(a.beendet) or datetime.min) > seit for a in p.aufgaben)
            if etwas_passiert or (p.offene() and not p.pausiert):
                faellig.append(p)
            else:
                p.letzter_morgenbericht = heute
                self.speichern(p)
        return faellig

    async def bericht_schleife(self) -> None:
        """Prüft jede Minute, ob der Morgenbericht fällig ist (auch nachgeholt, wenn der PC morgens aus war)."""
        await asyncio.sleep(20)          # erst Begrüßung bzw. Update-Meldung abwarten
        while True:
            faellig = self.morgenberichte_faellig(datetime.now())
            if faellig:
                heute = datetime.now().date().isoformat()
                texte = []
                for p in faellig:
                    texte.append(self.bericht_text(p))
                    p.letzter_morgenbericht = heute
                    self.speichern(p)
                self.gastgeber.melden(
                    "Morgenbericht deiner Projektarbeit:\n" + "\n\n".join(texte) + "\n\nErzähl dem Nutzer in drei bis "
                    "fünf Sätzen, was fertig ist, was als Nächstes kommt und welche Fragen offen sind. Frag, ob er "
                    "neue Aufgaben hat.", "Projekte")
            await asyncio.sleep(60)

    # --- Für die Oberfläche ----------------------------------------------------------------------------
    def uebersicht(self) -> list[dict[str, Any]]:
        liste = []
        for p in self.projekte.values():
            z = p.zaehlen()
            aktuell = self.aktuell if self.aktuell and self.aktuell["projekt"] == p.name else None
            liste.append({
                "name": p.name, "titel": p.titel, "art": p.art, "ordner": p.ordner, "pausiert": p.pausiert,
                "dauerbetrieb": p.dauerbetrieb, "erledigt": z["erledigt"],
                "gesamt": sum(z[s] for s in ("offen", "in_arbeit", "erledigt", "fehlgeschlagen")),
                "aktuell": None if aktuell is None else {
                    "rolle": aktuell["rolle"], "aufgabe": aktuell["aufgabe_titel"], "meldung": aktuell.get("meldung", ""),
                    "letzte_aktion": aktuell.get("letzte_aktion", ""), "frage": aktuell.get("frage"),
                    "minuten": int((time.time() - aktuell["seit"]) // 60)},
                "offen": [{"id": a.id, "titel": a.titel, "nutzer": a.quelle == "nutzer"} for a in p.offene()[:8]],
                "fertig": [{"id": a.id, "titel": a.titel, "ergebnis": _kurz(a.ergebnis, 220), "wann": _wann(a.beendet),
                            "ok": a.status == "erledigt"}
                           for a in sorted((a for a in p.aufgaben if a.status in ("erledigt", "fehlgeschlagen")),
                                           key=lambda a: a.beendet, reverse=True)[:6]],
                "sync": p.name in self._sync and self._sync[p.name].returncode is None,
                "notiz": p.planer_notiz,
                "limit_pause_bis": datetime.fromtimestamp(self.pause_bis).strftime("%H:%M")
                if time.time() < self.pause_bis else "",
            })
        return liste


# ---------------------------------------------------------------------------
# Werkzeuge für das Gehirn
# ---------------------------------------------------------------------------

def projekt_werkzeuge(app: Jarvis) -> list[Skill]:  # noqa: C901 – eine Sammlung kleiner Werkzeuge
    def pv() -> Projektverwaltung:
        return app.projekte

    async def projekt_anlegen(
        titel: Annotated[str, "Name des Projekts, z. B. 'Mein Roblox-Spiel' oder 'Obby Abenteuer'"],
        ziel: Annotated[str, "Ausführlich auf Deutsch: was entstehen soll, alle Ideen und Wünsche des Nutzers, was zuerst "
                             "wichtig ist, Stil, Zielgruppe. Keine Passwörter."],
        art: Annotated[Literal["roblox", "allgemein"], "'roblox' für Roblox-Spiele, sonst 'allgemein'"] = "roblox",
        ordner: Annotated[str, "Nur wenn der Nutzer einen vorhandenen Ordner nennt, sonst leer"] = "",
        dauerbetrieb: Annotated[bool, "true = Jarvis plant selbstständig weiter, wenn alle Aufgaben erledigt sind"] = True,
    ) -> str:
        """Legt ein Projekt an (z. B. das Roblox-Spiel des Nutzers), an dem Jarvis ab sofort selbstständig im
        Hintergrund arbeitet: Ein Planer zerlegt das Ziel in Aufgaben, Claude Code setzt sie um, ein Prüfer
        kontrolliert jede Änderung, jede Aufgabe wird per Git gesichert. Jeden Morgen gibt es einen Bericht."""
        if sieht_nach_schluessel_aus(ziel) or sieht_nach_schluessel_aus(titel):
            return "Abgelehnt: Das Ziel enthält etwas, das wie ein Passwort oder Schlüssel aussieht."
        try:
            p = await asyncio.to_thread(pv().anlegen, titel, ziel, art, ordner, dauerbetrieb)
        except (ValueError, OSError, GitError) as e:
            return f"Konnte das Projekt nicht anlegen: {schwaerzen(e)}"
        pv().wecken()
        text = (f"Projekt „{p.titel}“ angelegt (Kennung {p.name}, Ordner {p.ordner}). Der Planer legt gleich los, "
                "danach arbeitet Claude Code die Aufgaben nacheinander ab.")
        if p.art == "roblox":
            text += (" Für Roblox: Damit der Code in Studio landet, braucht es einmal Rokit + Rojo "
                     "(projekt_werkzeuge_einrichten) und dann den Live-Abgleich (projekt_steuern studio_sync_an). "
                     "Optional: In Roblox Studio unter Assistant → … → Manage MCP Servers 'Enable Studio as MCP "
                     "server' einschalten – dann kann Claude Code direkt in Studio testen und 3D-Modelle erzeugen. "
                     "Erkläre das dem Nutzer kurz, einen Schritt nach dem anderen.")
        return text

    async def projekt_aufgabe(
        projekt: Annotated[str, "Name/Kennung des Projekts (leer, wenn es nur eins gibt)"],
        aufgabe: Annotated[str, "Was getan werden soll – mit allen Details des Nutzers, auf Deutsch"],
        wichtig: Annotated[bool, "true = vor allen anderen Aufgaben erledigen"] = False,
    ) -> str:
        """Gibt dem Projekt-Team eine neue Aufgabe (z. B. 'Baue einen Shop für Skins'). Aufgaben des Nutzers kommen
        vor selbst geplanten dran."""
        p = pv().finden(projekt)
        if p is None:
            return pv()._unbekannt(projekt)
        if sieht_nach_schluessel_aus(aufgabe):
            return "Abgelehnt: Die Aufgabe enthält etwas, das wie ein Passwort oder Schlüssel aussieht."
        titel = re.split(r"(?<=[.!?])\s", aufgabe.strip(), maxsplit=1)[0]
        a = pv().aufgabe_hinzufuegen(p, titel, aufgabe, prioritaet=1 if wichtig else 2)
        vor = [x for x in p.offene() if x.id != a.id and (x.prioritaet, x.quelle != "nutzer") <= (a.prioritaet, False)]
        laeuft = pv().aktuell is not None and pv().aktuell["projekt"] == p.name
        return (f"Aufgabe {a.id} „{a.titel}“ für „{p.titel}“ notiert. Davor: {len(vor)} Aufgabe(n)"
                + (" plus die laufende" if laeuft else "") + (". Projekt ist pausiert." if p.pausiert else "."))

    async def projekt_status(projekt: Annotated[str, "Name/Kennung oder leer für alle"] = "") -> str:
        """Aktueller Stand eines Projekts (oder aller): woran gerade gearbeitet wird, was fertig ist, was als Nächstes
        kommt, offene Fragen."""
        if not pv().projekte:
            return "Es gibt noch keine Projekte."
        if projekt.strip():
            p = pv().finden(projekt)
            return pv().status_text(p) if p else pv()._unbekannt(projekt)
        return "\n\n".join(pv().status_text(p) for p in pv().projekte.values())

    async def projekt_bericht(projekt: Annotated[str, "Name/Kennung (leer, wenn es nur eins gibt)"] = "") -> str:
        """Bericht, was seit dem letzten Bericht am Projekt passiert ist (erledigt, nicht geklappt, selbst
        entschieden, als Nächstes). Für 'Was hast du am Spiel gemacht?'."""
        p = pv().finden(projekt)
        return pv().bericht_text(p) if p else pv()._unbekannt(projekt)

    async def projekt_steuern(
        projekt: Annotated[str, "Name/Kennung (leer, wenn es nur eins gibt)"],
        aktion: Annotated[Literal["pausieren", "fortsetzen", "dauerbetrieb_an", "dauerbetrieb_aus", "jetzt_planen",
                                  "studio_sync_an", "studio_sync_aus", "ordner_oeffnen"],
                          "studio_sync_* = Code live nach Roblox Studio übertragen (Rojo)"],
    ) -> str:
        """Steuert die Projektarbeit: pausieren/fortsetzen, Dauerbetrieb (selbstständig weiterplanen) an/aus, sofort
        neu planen, Live-Abgleich mit Roblox Studio an/aus, Projektordner öffnen."""
        p = pv().finden(projekt)
        return await pv().steuern(p, aktion) if p else pv()._unbekannt(projekt)

    async def projekt_werkzeuge_einrichten(projekt: Annotated[str, "Name/Kennung (leer, wenn es nur eins gibt)"] = "") -> str:
        """Roblox-Projekte: installiert die Projekt-Werkzeuge Rojo, Selene und StyLua (über Rokit) und das
        Rojo-Plugin für Roblox Studio. Dauert ein bis zwei Minuten."""
        p = pv().finden(projekt)
        if p is None:
            return pv()._unbekannt(projekt)
        app.im_hintergrund(_werkzeuge_im_hintergrund(app, p), name=f"Werkzeuge {p.name}")
        return "Die Installation läuft im Hintergrund. Ich melde mich, wenn sie fertig ist."

    async def projekt_antworten(antwort: Annotated[str, "Antwort des Nutzers, inhaltlich unverändert"]) -> str:
        """Gibt die Antwort des Nutzers auf eine Rückfrage des Projekt-Entwicklers weiter (kommt als Ereignis)."""
        if app.projektfrage_beantworten(antwort):
            return "Antwort an das Projekt-Team weitergegeben."
        return "Gerade wartet keine Projekt-Rückfrage. Neue Wünsche mit projekt_aufgabe."

    async def projekt_aufgabe_entfernen(
        projekt: Annotated[str, "Name/Kennung (leer, wenn es nur eins gibt)"],
        aufgabe: Annotated[str, "Nummer der Aufgabe oder Teil des Titels"],
    ) -> str:
        """Streicht eine offene Aufgabe aus einem Projekt."""
        p = pv().finden(projekt)
        if p is None:
            return pv()._unbekannt(projekt)
        weg = pv().aufgabe_entfernen(p, aufgabe)
        return "Gestrichen: " + ", ".join(f"{a.id}. {a.titel}" for a in weg) if weg else \
            f"Keine offene Aufgabe zu „{aufgabe}“ gefunden."

    return [
        make_skill(projekt_anlegen, nachfragen="immer",
                   frage="Soll ich das Projekt „{titel}“ anlegen und ab jetzt selbstständig daran arbeiten?"),
        make_skill(projekt_aufgabe, nachfragen="wenn_fremd"),
        make_skill(projekt_status),
        make_skill(projekt_bericht),
        make_skill(projekt_steuern, nachfragen="wenn_fremd"),
        make_skill(projekt_werkzeuge_einrichten, nachfragen="immer",
                   frage="Soll ich die Roblox-Werkzeuge Rojo, Selene und StyLua und das Rojo-Plugin für Studio "
                         "installieren?"),
        make_skill(projekt_antworten, nachfragen="wenn_fremd"),
        make_skill(projekt_aufgabe_entfernen, nachfragen="wenn_fremd"),
    ]


async def _werkzeuge_im_hintergrund(app: Jarvis, p: Projekt) -> None:
    try:
        text = await app.projekte.werkzeuge_einrichten(p)
    except (OSError, asyncio.TimeoutError) as e:
        text = f"Die Installation der Roblox-Werkzeuge ist fehlgeschlagen: {e}"
    app.melden(f"Roblox-Werkzeuge für „{p.titel}“: {text}", "System")

