"""Selbstverbesserung: Claude Code verändert Jarvis' eigenen Programmcode.

Ablauf eines Auftrags:
  1. Sicherung per Git-Commit (damit alles rückgängig gemacht werden kann)
  2. Claude Code setzt den Auftrag im Projektordner um (liest dafür CLAUDE.md)
  3. Selbsttest ("python -m jarvis --selbsttest"); schlägt er fehl, darf Claude Code reparieren
  4. Klappt es nicht, wird automatisch auf die Sicherung zurückgesetzt
  5. Erfolg: Commit "Jarvis-Update: …" und Neustart (übernimmt app.py / start.py)
"""

from __future__ import annotations

import asyncio
import json
import platform
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Awaitable, Callable

from jarvis import ui
from jarvis.config import DATA_DIR, ROOT_DIR

UPDATE_FILE = DATA_DIR / "update.json"
# Existiert diese Datei beim Start, wurde eine Änderung mittendrin abgebrochen -> start.py setzt zurück.
IN_PROGRESS_FILE = DATA_DIR / "entwicklung_laeuft.json"
UPDATE_PREFIX = "Jarvis-Update:"


@dataclass
class DevResult:
    erfolg: bool
    zusammenfassung: str
    geaendert: bool = False


@dataclass
class UpdateEntry:
    commit: str
    datum: str
    titel: str
    rueckgaengig: bool = False

    def __str__(self) -> str:
        return f"{self.datum}: {self.titel}" + (" (rückgängig gemacht)" if self.rueckgaengig else "")


class GitError(RuntimeError):
    pass


class Git:
    def __init__(self, root: Path = ROOT_DIR):
        self.root = root
        self._identity: list[str] | None = None

    @staticmethod
    def available() -> bool:
        return shutil.which("git") is not None

    def run(self, *args: str, check: bool = True) -> str:
        proc = subprocess.run(
            ["git", *args], cwd=self.root, capture_output=True, text=True,
            encoding="utf-8", errors="replace",
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


class Developer:
    def __init__(self, cfg: dict[str, Any]):
        self.cfg = cfg["entwickler"]
        self.git = Git()
        self.current_task: str | None = None
        self._task: asyncio.Task[Any] | None = None

    @property
    def busy(self) -> bool:
        return self._task is not None and not self._task.done()

    def start(self, auftrag: str, on_done: Callable[[DevResult], Awaitable[None]]) -> None:
        """Startet einen Auftrag im Hintergrund; on_done wird mit dem Ergebnis aufgerufen."""
        if self.busy:
            raise RuntimeError(f"Ich arbeite noch an: {self.current_task}")
        self.current_task = auftrag

        async def runner() -> None:
            try:
                result = await self.improve(auftrag)
            except Exception as e:  # noqa: BLE001
                result = DevResult(False, f"Unerwarteter Fehler beim Entwickeln: {e}")
            finally:
                self.current_task = None
            await on_done(result)

        self._task = asyncio.create_task(runner())

    async def improve(self, auftrag: str) -> DevResult:
        if not self.git.available():
            return DevResult(False, "Git ist nicht installiert. Ohne Git kann ich mich nicht sicher verändern. "
                                    "Bitte installiere Git von git-scm.com.")
        self.git.ensure_repo()
        self.git.commit_all("Jarvis: Sicherung vor Änderung")
        before = self.git.head()
        assert before is not None
        ui.dev(f"Neuer Auftrag: {auftrag}")
        IN_PROGRESS_FILE.parent.mkdir(exist_ok=True)
        IN_PROGRESS_FILE.write_text(json.dumps({"vorher": before, "auftrag": auftrag}, ensure_ascii=False),
                                    encoding="utf-8")
        try:
            return self._finish(before, auftrag, *await self._develop(auftrag, before))
        finally:
            IN_PROGRESS_FILE.unlink(missing_ok=True)

    async def _develop(self, auftrag: str, before: str) -> tuple[bool, str]:
        """Lässt Claude Code arbeiten und testet. Gibt (erfolgreich, Zusammenfassung/Fehlertext) zurück."""
        try:
            summary, session_id = await self._run_claude(self._task_prompt(auftrag))
            tries = int(self.cfg.get("reparaturversuche", 2))
            for attempt in range(tries + 1):
                ok, output = await self._selftest()
                if ok:
                    break
                if attempt == tries:
                    self.git.reset_hard(before)
                    ui.dev("Selbsttest weiterhin fehlerhaft – Änderungen wurden zurückgesetzt.")
                    return False, ("Das hat leider nicht geklappt. Meine Änderungen haben den Selbsttest "
                                   "nicht bestanden, deshalb habe ich alles zurückgesetzt.")
                ui.dev(f"Selbsttest fehlgeschlagen – Reparaturversuch {attempt + 1} von {tries} …")
                summary, session_id = await self._run_claude(self._fix_prompt(output), resume=session_id)
        except Exception as e:  # noqa: BLE001
            self.git.reset_hard(before)
            ui.error(f"Entwicklung abgebrochen: {e}")
            return False, f"Bei der Umsetzung ist ein Fehler aufgetreten, ich habe alles zurückgesetzt. Fehler: {e}"
        return True, summary or "Erledigt."

    def _finish(self, before: str, auftrag: str, ok: bool, summary: str) -> DevResult:
        if not ok:
            return DevResult(False, summary)
        if not self.git.commit_all(f"{UPDATE_PREFIX} {_short(auftrag)}\n\nAuftrag: {auftrag}\n\nErgebnis: {summary}"):
            return DevResult(True, summary, geaendert=False)
        self._record_update(before, auftrag, summary)
        ui.dev("Fertig und gespeichert.")
        return DevResult(True, summary, geaendert=True)

    def _record_update(self, before: str, auftrag: str, summary: str, art: str = "update") -> None:
        """Merkt sich das Update, damit start.py es bei einem Absturz zurücknehmen kann."""
        UPDATE_FILE.parent.mkdir(exist_ok=True)
        UPDATE_FILE.write_text(json.dumps({
            "art": art,
            "vorher": before,
            "nachher": self.git.head(),
            "auftrag": auftrag,
            "zusammenfassung": summary,
            "zeit": datetime.now().isoformat(timespec="seconds"),
            "bestaetigt": False,
        }, ensure_ascii=False, indent=2), encoding="utf-8")

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
        self._record_update(before, f"Rückgängig: {entry.titel}",
                            f"Die Änderung \"{entry.titel}\" wurde rückgängig gemacht.", art="rueckgaengig")
        return entry.titel

    # --- Claude Code ---------------------------------------------------------
    async def _run_claude(self, prompt: str, resume: str | None = None) -> tuple[str, str | None]:
        from claude_agent_sdk import (AssistantMessage, ClaudeAgentOptions, ResultMessage, TextBlock,
                                      ToolUseBlock, query)

        c = self.cfg
        options: dict[str, Any] = dict(
            cwd=str(ROOT_DIR),
            system_prompt={"type": "preset", "preset": "claude_code"},
            setting_sources=["project"],          # lädt CLAUDE.md und .claude/settings.json
            permission_mode=c["berechtigungsmodus"],
            allowed_tools=list(c["erlaubte_werkzeuge"]),
            max_turns=int(c["max_schritte"]),
            resume=resume,
        )
        if c.get("modell"):
            options["model"] = c["modell"]
        if c.get("aufwand"):
            options["effort"] = c["aufwand"]

        result_text, session_id = "", resume
        async for msg in query(prompt=prompt, options=ClaudeAgentOptions(**options)):
            if isinstance(msg, AssistantMessage):
                for block in msg.content:
                    if isinstance(block, ToolUseBlock):
                        ui.dev(_describe_tool(block.name, block.input))
                    elif isinstance(block, TextBlock) and block.text.strip():
                        ui.dev(_short(block.text.strip(), 160))
            elif isinstance(msg, ResultMessage):
                session_id = msg.session_id or session_id
                if msg.is_error:
                    raise RuntimeError(msg.result or msg.subtype)
                result_text = (msg.result or "").strip()
        return result_text, session_id

    def _task_prompt(self, auftrag: str) -> str:
        py = sys.executable
        return f"""Der Nutzer von Jarvis hat per Sprache folgenden Auftrag gegeben (von Jarvis formuliert):

<auftrag>
{auftrag}
</auftrag>

Setze diesen Auftrag im Jarvis-Projekt um. Lies zuerst CLAUDE.md – dort stehen Aufbau und Regeln des Projekts.
Du kannst den Nutzer nicht fragen; triff bei Unklarheiten sinnvolle Entscheidungen.

Technische Hinweise:
- Betriebssystem des Nutzers: {platform.system()} {platform.release()}
- Python-Interpreter von Jarvis: "{py}"
  Neue Pakete installierst du mit "{py}" -m pip install <paket> und trägst sie in requirements.txt ein.
- Prüfe deine Arbeit am Ende mit: "{py}" -m jarvis --selbsttest
- Starte Jarvis nicht normal (er läuft bereits und würde sonst aufs Mikrofon warten) und committe nicht selbst.

Antworte zum Schluss mit ein bis zwei kurzen deutschen Sätzen, was du geändert hast. Diese Zusammenfassung wird
dem Nutzer vorgelesen: kein Markdown, keine Dateinamen, keine Aufzählungen."""

    @staticmethod
    def _fix_prompt(output: str) -> str:
        return f"""Der Selbsttest ist fehlgeschlagen. Ausgabe:

{output[-6000:]}

Bitte behebe die Ursache und prüfe erneut mit dem Selbsttest. Antworte zum Schluss wieder mit ein bis zwei kurzen
deutschen Sätzen für den Nutzer, was du insgesamt geändert hast."""

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
        text = out.decode("utf-8", errors="replace")
        return proc.returncode == 0, text


def _short(text: str, n: int = 70) -> str:
    text = " ".join(text.split())
    return text if len(text) <= n else text[: n - 1] + "…"


def _describe_tool(name: str, inp: dict[str, Any]) -> str:
    path = inp.get("file_path") or inp.get("path") or inp.get("notebook_path")
    if path:
        try:
            path = str(Path(path).resolve().relative_to(ROOT_DIR))
        except (ValueError, OSError):
            pass
    if name == "Bash":
        return f"$ {_short(str(inp.get('command', '')), 110)}"
    verbs = {"Read": "liest", "Write": "schreibt", "Edit": "bearbeitet", "MultiEdit": "bearbeitet"}
    if name in verbs and path:
        return f"{verbs[name]} {path}"
    if name in ("Glob", "Grep"):
        return f"sucht {inp.get('pattern', '')}"
    if name == "TodoWrite":
        return "plant die nächsten Schritte"
    return name
