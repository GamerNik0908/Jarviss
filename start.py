"""Starter für Jarvis – einfach `python start.py` (oder "Jarvis starten.bat" doppelklicken).

Kümmert sich um alles rund um Jarvis:
  * legt beim ersten Start eine virtuelle Umgebung (.venv) an und installiert die Pakete
  * installiert neue Pakete nach, wenn sich requirements.txt geändert hat
  * startet Jarvis neu, wenn er sich selbst verbessert hat
  * setzt ein Update automatisch zurück, falls Jarvis danach abstürzt

Diese Datei importiert absichtlich nichts aus dem jarvis-Paket: Sie muss auch dann
funktionieren, wenn eine Selbst-Änderung Jarvis kaputt gemacht hat.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
VENV = ROOT / ".venv"
UPDATE_FILE = DATA / "update.json"
IN_PROGRESS_FILE = DATA / "entwicklung_laeuft.json"
HINTS_FILE = DATA / "hinweise.json"

RESTART = 3
CONNECTION_FAILED = 2
# Optionale Pakete: Wenn sie sich nicht installieren lassen, läuft Jarvis trotzdem (mit Einschränkungen).
OPTIONAL = {
    "pyaudio": "Ohne PyAudio kein Mikrofon – du kannst aber tippen.",
    "pygame": "Ohne pygame wird der System-Audioplayer genutzt.",
}


def say(text: str) -> None:
    print(f"\033[90m[Start] {text}\033[0m", flush=True)


def venv_python() -> Path:
    return VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def in_venv() -> bool:
    return sys.prefix != sys.base_prefix


def ensure_venv_and_reexec() -> None:
    """Sorgt dafür, dass wir in .venv laufen (startet start.py ggf. darin neu)."""
    if in_venv():
        return
    if not venv_python().exists():
        say("Erster Start: lege virtuelle Umgebung an (.venv) …")
        subprocess.check_call([sys.executable, "-m", "venv", str(VENV)])
    sys.exit(subprocess.call([str(venv_python()), str(Path(__file__).resolve()), *sys.argv[1:]]))


def pip(*args: str, quiet: bool = False) -> int:
    cmd = [sys.executable, "-m", "pip", "--disable-pip-version-check", *args]
    if quiet:
        return subprocess.call(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return subprocess.call(cmd)


def ensure_requirements() -> None:
    req = ROOT / "requirements.txt"
    digest = hashlib.sha256(req.read_bytes()).hexdigest()
    stamp = Path(sys.prefix) / "jarvis-requirements.sha256"   # liegt in der aktiven venv
    if stamp.exists() and stamp.read_text().strip() == digest:
        return
    say("Installiere/aktualisiere Pakete (das kann beim ersten Mal ein paar Minuten dauern) …")
    if pip("install", "-q", "-r", str(req)) != 0:
        say("Pakete konnten nicht installiert werden. Internetverbindung prüfen und erneut starten.")
        sys.exit(1)
    for package, hint in OPTIONAL.items():
        if pip("install", "-q", package, quiet=True) != 0:
            say(f"Hinweis: '{package}' ließ sich nicht installieren. {hint}")
    stamp.write_text(digest)


def git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


def read_json(path: Path) -> dict | list | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def add_hint(text: str) -> None:
    hints = read_json(HINTS_FILE)
    hints = hints if isinstance(hints, list) else []
    hints.append(text)
    DATA.mkdir(exist_ok=True)
    HINTS_FILE.write_text(json.dumps(hints, ensure_ascii=False), encoding="utf-8")


def rollback_to(commit: str) -> bool:
    ok = git("reset", "-q", "--hard", commit).returncode == 0
    git("clean", "-q", "-fd")
    return ok


def recover_interrupted_development() -> None:
    """Wurde Jarvis beendet, während Claude Code an ihm gearbeitet hat, Halbfertiges verwerfen."""
    info = read_json(IN_PROGRESS_FILE)
    if not isinstance(info, dict):
        return
    if info.get("vorher") and rollback_to(info["vorher"]):
        say("Eine unterbrochene Selbst-Änderung wurde zurückgesetzt.")
        add_hint(f"Die Änderung \"{info.get('auftrag', '')}\" wurde unterbrochen und deshalb verworfen. "
                 "Sag Bescheid, wenn ich es noch einmal versuchen soll.")
    IN_PROGRESS_FILE.unlink(missing_ok=True)


def rollback_unconfirmed_update() -> bool:
    """Ist Jarvis direkt nach einem Update abgestürzt, das Update zurücknehmen."""
    update = read_json(UPDATE_FILE)
    if not isinstance(update, dict) or update.get("bestaetigt") or not update.get("vorher"):
        return False
    if not rollback_to(update["vorher"]):
        return False
    UPDATE_FILE.unlink(missing_ok=True)
    say("Jarvis ist nach dem letzten Update abgestürzt – Update wurde zurückgesetzt.")
    add_hint(f"Mein letztes Update (\"{update.get('auftrag', '')}\") hat mich zum Absturz gebracht. "
             "Ich habe es automatisch rückgängig gemacht. Die Fehlermeldung steht in der Konsole.")
    return True


def main() -> int:
    if sys.version_info < (3, 11):
        print("Jarvis braucht Python 3.11 oder neuer. Download: https://www.python.org/downloads/")
        return 1
    ensure_venv_and_reexec()
    os.chdir(ROOT)
    DATA.mkdir(exist_ok=True)
    args = sys.argv[1:]
    crashes: list[float] = []

    while True:
        ensure_requirements()
        recover_interrupted_development()
        try:
            code = subprocess.call([sys.executable, "-m", "jarvis", *args], cwd=ROOT)
        except KeyboardInterrupt:
            return 0

        if code == 0:
            return 0
        if code == RESTART:
            say("Neustart …")
            continue
        if code == CONNECTION_FAILED:
            try:
                input("Drücke Enter für einen neuen Versuch (Strg+C zum Beenden) … ")
            except (KeyboardInterrupt, EOFError):
                return 1
            continue

        say(f"Jarvis wurde unerwartet beendet (Code {code}).")
        if rollback_unconfirmed_update():
            continue
        now = time.monotonic()
        crashes = [t for t in crashes if now - t < 120] + [now]
        if len(crashes) >= 3:
            try:
                input("Jarvis stürzt wiederholt ab. Enter = erneut versuchen, Strg+C = beenden … ")
            except (KeyboardInterrupt, EOFError):
                return 1
            crashes.clear()
        else:
            time.sleep(3)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(0)
