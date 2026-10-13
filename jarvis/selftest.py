"""Selbsttest: prüft, ob Jarvis nach einer Änderung noch startfähig und sauber ist.

GESCHÜTZTE DATEI (Sicherheitsnetz): Änderungen hieran fragt Jarvis vor dem Übernehmen beim Nutzer nach.

Aufruf: python -m jarvis --selbsttest
Läuft wie eine frische Installation: leerer Daten-Ordner, leerer Einrichtungs-Speicher, kein Internet,
kein Mikrofon, keine Claude-Anmeldung nötig. Wird nach jeder Selbstverbesserung automatisch ausgeführt.
"""

from __future__ import annotations

import ast
import importlib
import importlib.metadata
import importlib.util
import json
import re
import socket
import subprocess
import sys
import threading
import traceback
from pathlib import Path
from typing import Any

from jarvis.config import ROOT_DIR

MODULES = [
    "jarvis.config", "jarvis.sicherheit", "jarvis.ui", "jarvis.einrichtung", "jarvis.protokoll", "jarvis.memory",
    "jarvis.skills", "jarvis.api", "jarvis.dienste", "jarvis.brain", "jarvis.developer", "jarvis.kern",
    "jarvis.befehle", "jarvis.eingabefenster", "jarvis.voice.speaker", "jarvis.voice.listener", "jarvis.app",
]
SKILLS_DIR = ROOT_DIR / "jarvis" / "skills"
TOOL_NAME = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")
_LOOPBACK = {"127.0.0.1", "::1", "localhost", "0.0.0.0", None, ""}


# ---------------------------------------------------------------------------
# Netzwerk sperren (Tests und Selbsttest dürfen nicht ins Internet)
# ---------------------------------------------------------------------------

def netz_sperren() -> None:
    """Verbietet Verbindungen nach außen (Loopback bleibt erlaubt, Windows braucht das für asyncio)."""
    if getattr(socket, "_jarvis_gesperrt", False):
        return
    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex
    original_getaddrinfo = socket.getaddrinfo

    def _pruefen(adresse: Any) -> None:
        host = adresse[0] if isinstance(adresse, tuple) else adresse
        if isinstance(host, str) and (host in _LOOPBACK or host.startswith("127.")):
            return
        if isinstance(adresse, (str, bytes)):  # Unix-Sockets
            return
        raise RuntimeError(f"Tests/Selbsttest dürfen nicht ins Internet ({host}). Netzwerkzugriffe im Test "
                           "ersetzen (monkeypatch) und beim Import nichts verbinden.")

    def connect(self: socket.socket, adresse: Any) -> Any:
        _pruefen(adresse)
        return original_connect(self, adresse)

    def connect_ex(self: socket.socket, adresse: Any) -> Any:
        _pruefen(adresse)
        return original_connect_ex(self, adresse)

    def getaddrinfo(host: Any, *args: Any, **kwargs: Any) -> Any:
        _pruefen((host.decode() if isinstance(host, bytes) else host, 0))
        return original_getaddrinfo(host, *args, **kwargs)

    socket.socket.connect = connect  # type: ignore[method-assign]
    socket.socket.connect_ex = connect_ex  # type: ignore[method-assign]
    socket.getaddrinfo = getaddrinfo  # type: ignore[assignment]
    socket._jarvis_gesperrt = True  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Statische Prüfungen der Funktionen
# ---------------------------------------------------------------------------

_BLOCKIEREND = re.compile(r"^(time\.sleep|requests\.\w+|urllib\.request\.urlopen|urlopen|subprocess\.(run|call|"
                          r"check_output|check_call)|os\.system|input|httpx\.(get|post|put|patch|delete|request|stream))$")


def _aufruf_name(knoten: ast.AST) -> str:
    if isinstance(knoten, ast.Name):
        return knoten.id
    if isinstance(knoten, ast.Attribute):
        innen = _aufruf_name(knoten.value)
        return f"{innen}.{knoten.attr}" if innen else knoten.attr
    return ""


def blockierende_aufrufe(quelltext: str, datei: str = "") -> list[str]:
    """Findet blockierende Aufrufe (time.sleep, requests …) direkt in async-Funktionen."""
    baum = ast.parse(quelltext)
    funde: list[str] = []

    def besuchen(knoten: ast.AST, erwartet: set[int]) -> None:
        for kind in ast.iter_child_nodes(knoten):
            if isinstance(kind, (ast.FunctionDef, ast.Lambda, ast.AsyncFunctionDef, ast.ClassDef)):
                continue  # verschachtelte Funktionen werden separat geprüft
            if isinstance(kind, ast.Await):
                erwartet.add(id(kind.value))
            if isinstance(kind, ast.Call) and id(kind) not in erwartet:
                name = _aufruf_name(kind.func)
                if _BLOCKIEREND.match(name):
                    funde.append(f"{datei}:{kind.lineno} blockiert in async-Funktion: {name}(…) – nutze "
                                 "await asyncio.sleep / aiohttp / asyncio.create_subprocess_exec / asyncio.to_thread")
            besuchen(kind, erwartet)

    for knoten in ast.walk(baum):
        if isinstance(knoten, ast.AsyncFunctionDef):
            besuchen(knoten, set())
    return funde


def _normalisiert(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def fehlende_anforderungen(dateien: list[Path], requirements: Path) -> list[str]:
    """Pakete, die Funktionen auf Modulebene importieren, die aber nicht in requirements.txt stehen."""
    eingetragen = set()
    for zeile in requirements.read_text(encoding="utf-8").splitlines():
        zeile = zeile.split("#", 1)[0].strip()
        if zeile:
            eingetragen.add(_normalisiert(re.split(r"[<>=!~\[; ]", zeile, maxsplit=1)[0]))
    verteilung = importlib.metadata.packages_distributions()
    probleme = []
    for datei in dateien:
        try:
            baum = ast.parse(datei.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):
            continue
        for knoten in baum.body:
            namen: list[str] = []
            if isinstance(knoten, ast.Import):
                namen = [a.name.split(".")[0] for a in knoten.names]
            elif isinstance(knoten, ast.ImportFrom) and knoten.level == 0 and knoten.module:
                namen = [knoten.module.split(".")[0]]
            for name in namen:
                if name in sys.stdlib_module_names or name in ("jarvis", "__future__"):
                    continue
                pakete = verteilung.get(name, [])
                if pakete and not any(_normalisiert(p) in eingetragen for p in pakete):
                    probleme.append(f"{datei.relative_to(ROOT_DIR)} importiert '{name}' (Paket {pakete[0]}), "
                                    "das fehlt in requirements.txt")
    return probleme


def _projektdateien() -> list[Path]:
    try:
        aus_git = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard"], cwd=ROOT_DIR,
                                 capture_output=True, text=True, encoding="utf-8", timeout=30).stdout.splitlines()
        if aus_git:
            return [ROOT_DIR / d for d in aus_git if (ROOT_DIR / d).is_file()]
    except (OSError, subprocess.TimeoutExpired):
        pass
    return [p for p in ROOT_DIR.rglob("*") if p.is_file()
            and not any(t in p.parts for t in (".git", ".venv", "data", "__pycache__"))]


def geheimnisse_im_code() -> list[str]:
    from jarvis.sicherheit import PLATZHALTER, TOKEN_MUSTER

    funde = []
    for datei in _projektdateien():
        if datei.suffix.lower() in (".png", ".jpg", ".ico", ".mp3", ".wav", ".stl", ".zip") or \
                datei.stat().st_size > 1_000_000:
            continue
        try:
            text = datei.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for muster in TOKEN_MUSTER:
            for m in muster.finditer(text):
                if not PLATZHALTER.search(m.group(0)):
                    zeile = text.count("\n", 0, m.start()) + 1
                    funde.append(f"{datei.relative_to(ROOT_DIR)}:{zeile} enthält etwas, das wie ein echter Schlüssel "
                                 "aussieht – Geheimnisse gehören in Geheimnis(...), in Tests Platzhalter wie 'test-token'")
    return funde


def offene_netzwerkfreigaben() -> list[str]:
    funde = []
    for datei in sorted((ROOT_DIR / "jarvis").rglob("*.py")):
        for nr, zeile in enumerate(datei.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
            if re.search(r"""0\.0\.0\.0|host\s*=\s*(""|'')|\b(ngrok|cloudflared|upnp)\b""", zeile) and \
                    "netz-freigabe:" not in zeile and datei.name != "selftest.py":
                funde.append(f"{datei.relative_to(ROOT_DIR)}:{nr} öffnet etwas fürs ganze Netzwerk. Server nur auf "
                             "127.0.0.1; fürs Heimnetz nur mit Zugangsschlüssel und Kommentar '# netz-freigabe: <Grund>'")
    return funde


# ---------------------------------------------------------------------------
# Selbsttest
# ---------------------------------------------------------------------------

def run_selftest() -> int:
    problems: list[str] = []

    def check(name: str, fn: Any) -> Any:
        try:
            result = fn()
            print(f"  ✓ {name}")
            return result
        except Exception:  # noqa: BLE001
            problems.append(f"{name}:\n{traceback.format_exc()}")
            print(f"  ✖ {name}")
            return None

    def check_liste(name: str, fn: Any) -> None:
        try:
            funde = fn()
        except Exception:  # noqa: BLE001
            problems.append(f"{name}:\n{traceback.format_exc()}")
            print(f"  ✖ {name}")
            return
        if funde:
            problems.append(f"{name}:\n" + "\n".join(f"  - {f}" for f in funde))
            print(f"  ✖ {name}")
        else:
            print(f"  ✓ {name}")

    print("Jarvis-Selbsttest")
    netz_sperren()
    from jarvis.config import load_config

    cfg = check("config.toml lesen", load_config)
    threads_vorher = threading.active_count()
    for module in MODULES:
        check(f"Modul {module} importieren", lambda m=module: importlib.import_module(m))

    def skills_ok() -> list[Any]:
        from jarvis.skills import load_skills

        skills, _ = load_skills(strict=True)
        return skills

    skills = check("Funktionen laden", skills_ok) or []

    def keine_nebenwirkungen() -> None:
        if threading.active_count() > threads_vorher:
            namen = [t.name for t in threading.enumerate()]
            raise RuntimeError(f"Beim Import wurden Threads gestartet ({namen}). Nichts beim Import starten – "
                               "dafür gibt es @dienst und im_hintergrund().")

    check("Import ohne Nebenwirkungen", keine_nebenwirkungen)

    if cfg is not None:
        def tools_ok() -> int:
            from jarvis.app import Jarvis
            from jarvis.brain import Brain

            alle = list(skills) + Jarvis(cfg, text_only=True).core_tools()
            namen = [t.name for t in alle]
            doppelt = {n for n in namen if namen.count(n) > 1}
            if doppelt:
                raise ValueError(f"Doppelte Werkzeugnamen: {doppelt}")
            for t in alle:
                if not TOOL_NAME.match(t.name):
                    raise ValueError(f"Ungültiger Werkzeugname '{t.name}' (nur a-z, A-Z, 0-9, _ und -; keine Umlaute)")
                if not t.beschreibung.strip():
                    raise ValueError(f"Werkzeug '{t.name}' hat keine Beschreibung (Docstring fehlt)")
                json.dumps(t.schema)
            if importlib.util.find_spec("claude_agent_sdk"):
                Brain(cfg, alle).build_options()
            return len(alle)

        anzahl = check("Werkzeuge für das Gehirn bauen", tools_ok)
        if anzahl:
            print(f"    ({anzahl} Werkzeuge)")

    def deklarationen_ok() -> None:
        from jarvis import einrichtung
        from jarvis.skills import dienste, hinweise

        for w in einrichtung.alle():
            if not w.beschreibung.strip():
                raise ValueError(f"Einrichtungswert '{w.name}' braucht eine beschreibung")
            if not w.anleitung and w.standard is None:
                raise ValueError(f"Einrichtungswert '{w.name}' braucht eine anleitung (Schritte für den Nutzer)")
        for d in dienste():
            if not d.funktion.__doc__:
                raise ValueError(f"Dienst '{d.name}' braucht einen Docstring")
        for h in hinweise():
            if len(h) > 300:
                raise ValueError(f"hinweis() ist zu lang ({len(h)} Zeichen, max. 300): {h[:60]}…")

    check("Dienste, Einrichtung, Hinweise", deklarationen_ok)

    skill_dateien = sorted(SKILLS_DIR.rglob("*.py"))
    check_liste("Keine blockierenden Aufrufe in async-Funktionen", lambda: [
        f for d in skill_dateien for f in blockierende_aufrufe(d.read_text(encoding="utf-8"), str(d.relative_to(ROOT_DIR)))])
    check_liste("Pakete stehen in requirements.txt",
                lambda: fehlende_anforderungen(skill_dateien, ROOT_DIR / "requirements.txt"))
    check_liste("Keine Geheimnisse im Code", geheimnisse_im_code)
    check_liste("Keine offenen Netzwerkfreigaben", offene_netzwerkfreigaben)

    tests_dir = ROOT_DIR / "tests"
    if tests_dir.is_dir():
        if importlib.util.find_spec("pytest"):
            try:
                proc = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                                       "--durations=3", str(tests_dir)], cwd=ROOT_DIR, capture_output=True, text=True,
                                      encoding="utf-8", errors="replace", timeout=300)
                ok, ausgabe = proc.returncode == 0, f"{proc.stdout[-5000:]}\n{proc.stderr[-2000:]}"
            except subprocess.TimeoutExpired:
                ok, ausgabe = False, "Tests haben länger als 5 Minuten gedauert (echte Wartezeiten oder Netzwerk?)."
            if ok:
                print("  ✓ Tests (pytest)")
            else:
                print("  ✖ Tests (pytest)")
                problems.append(f"pytest:\n{ausgabe}")
        else:
            print("  - Tests übersprungen (pytest nicht installiert)")

    if problems:
        print("\nSELBSTTEST FEHLGESCHLAGEN\n")
        for p in problems:
            print(p)
        return 1
    print("\nSELBSTTEST OK")
    return 0


def einrichtung_anzeigen(funktion: str = "") -> int:
    """python -m jarvis --einrichtung [funktion]: Stand anzeigen und gesetzte Werte live prüfen."""
    from concurrent.futures import ThreadPoolExecutor
    from concurrent.futures import TimeoutError as ZeitUeberschritten

    from jarvis import einrichtung
    from jarvis.sicherheit import schwaerzen
    from jarvis.skills import load_skills

    _, fehler = load_skills()
    for f in fehler:
        print(schwaerzen(f))
    print(einrichtung.status_text(funktion))
    with ThreadPoolExecutor(max_workers=1) as pool:
        for w in einrichtung.alle():
            if (funktion and w.funktion != funktion) or w.pruefen is None or not w.vorhanden:
                continue
            try:
                ergebnis = pool.submit(w.pruefen, w.wert()).result(timeout=20)
                print(schwaerzen(f"Prüfung {w.name}: OK – {ergebnis}"))
            except ZeitUeberschritten:
                print(f"Prüfung {w.name}: keine Antwort innerhalb von 20 Sekunden")
            except Exception as e:  # noqa: BLE001
                print(schwaerzen(f"Prüfung {w.name}: FEHLER – {type(e).__name__}: {e}"))
    return 0
