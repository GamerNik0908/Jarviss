"""Hauptprogramm: verbindet Eingabe (Tastatur/Mikrofon), Gehirn, Sprachausgabe und Entwickler."""

from __future__ import annotations

import asyncio
import json
import re
import threading
import time
from datetime import datetime
from typing import Annotated, Any

from jarvis import RESTART_EXIT_CODE, ui
from jarvis.brain import Brain
from jarvis.config import DATA_DIR, ensure_data_dir
from jarvis.developer import UPDATE_FILE, Developer, DevResult
from jarvis.skills import Skill, load_skills, make_skill
from jarvis.voice.listener import Listener, list_microphones
from jarvis.voice.speaker import Speaker

HINTS_FILE = DATA_DIR / "hinweise.json"
CONNECTION_FAILED_EXIT_CODE = 2

_FILLER = re.compile(r"^(hey|hi|hallo|ok|okay|oh|ja|na|yo|moin|servus)\b[\s,.!?]*", re.I)

HELP = """Befehle:
  /hilfe                Diese Hilfe
  /verbessern <text>    Auftrag direkt an Claude Code geben (ohne Umweg übers Gespräch)
  /verlauf              Letzte Selbst-Änderungen anzeigen
  /rueckgaengig         Letzte Selbst-Änderung rückgängig machen
  /neu                  Gespräch neu beginnen (Gedächtnis bleibt)
  /stumm                Sprachausgabe an/aus
  /mikrofone            Verfügbare Mikrofone anzeigen (für mikrofon_index in config.toml)
  /neustart             Jarvis neu starten
  /beenden              Jarvis beenden"""


def extract_command(text: str, wakewords: list[str]) -> str | None:
    """Sucht das Aktivierungswort im erkannten Text.

    Gibt den Befehl ohne Aktivierungswort zurück ("" wenn nur das Wort gesagt wurde)
    oder None, wenn kein Aktivierungswort vorkommt.
    """
    for word in wakewords:
        m = re.search(rf"\b{re.escape(word)}\w*\b", text, flags=re.I)
        if m:
            command = f"{text[:m.start()]} {text[m.end():]}"
            command = _FILLER.sub("", command.strip(" ,.!?:;-"))
            return re.sub(r"\s+", " ", command).strip(" ,.!?:;-")
    return None


def greeting(anrede: str) -> str:
    hour = datetime.now().hour
    tageszeit = "Guten Morgen" if 5 <= hour < 11 else "Guten Tag" if hour < 18 else "Guten Abend"
    return f"{tageszeit}, {anrede}. Jarvis ist online."


class Jarvis:
    def __init__(self, cfg: dict[str, Any], text_only: bool = False):
        self.cfg = cfg
        self.text_only = text_only or cfg["zuhoeren"]["modus"] == "aus"
        self.anrede = cfg["nutzer"]["anrede"] or "Sir"
        self.developer = Developer(cfg)
        self.speaker = Speaker({"engine": "aus"} if text_only else cfg["sprechen"])
        self.listener: Listener | None = None
        self.brain: Brain | None = None
        self.queue: asyncio.Queue[tuple[str, Any]] = asyncio.Queue()
        self.loop: asyncio.AbstractEventLoop | None = None
        self.exit_code: int | None = None
        self._followup_until = 0.0

    # --- Kern-Werkzeuge (immer vorhanden, brauchen Zugriff auf Jarvis selbst) ---
    def core_tools(self) -> list[Skill]:
        app = self

        async def selbst_verbessern(
            auftrag: Annotated[str, "Ausführlicher, präziser Auftrag auf Deutsch mit allen Details des Nutzers"],
        ) -> str:
            """Verändert Jarvis' eigenen Programmcode mit Claude Code: neue Fähigkeiten hinzufügen, Verhalten oder
            Persönlichkeit ändern, Einstellungen anpassen (Stimme, Tempo, Aktivierungswort …), Fehler beheben.
            Läuft im Hintergrund (meist ein paar Minuten). Danach startet Jarvis automatisch neu."""
            if app.developer.busy:
                return (f"Abgelehnt: Ich arbeite noch am vorherigen Auftrag ({app.developer.current_task}). "
                        "Neue Aufträge erst, wenn der fertig ist.")
            app.developer.start(auftrag, app._on_dev_done)
            return "Auftrag an Claude Code übergeben. Er läuft jetzt im Hintergrund; ich melde mich, wenn er fertig ist."

        async def entwicklungsstatus() -> str:
            """Sagt, ob Claude Code gerade an einer Selbstverbesserung arbeitet und woran."""
            if app.developer.busy:
                return f"Claude Code arbeitet gerade an: {app.developer.current_task}"
            return "Gerade läuft keine Selbstverbesserung."

        def aenderungsverlauf() -> str:
            """Listet die letzten Änderungen auf, die Jarvis an sich selbst vorgenommen hat."""
            history = app.developer.git.update_history(10) if app.developer.git.available() else []
            if not history:
                return "Bisher wurden keine Selbst-Änderungen vorgenommen."
            return "\n".join(str(e) for e in history)

        async def letzte_aenderung_rueckgaengig() -> str:
            """Macht die letzte Selbst-Änderung von Jarvis rückgängig und startet danach neu."""
            if app.developer.busy:
                return "Geht gerade nicht – Claude Code arbeitet noch an einem Auftrag."
            subject = await asyncio.to_thread(app.developer.undo_last)
            app.exit_code = RESTART_EXIT_CODE
            return f"Rückgängig gemacht: {subject}. Ich starte nach dieser Antwort neu."

        async def neu_starten() -> str:
            """Startet Jarvis neu (z. B. damit Änderungen wirksam werden)."""
            if app.developer.busy:
                return "Lieber nicht – Claude Code arbeitet gerade an mir. Ein Neustart würde das abbrechen."
            app.exit_code = RESTART_EXIT_CODE
            return "Neustart nach dieser Antwort."

        async def jarvis_beenden() -> str:
            """Beendet Jarvis komplett (nur wenn der Nutzer das ausdrücklich will)."""
            app.exit_code = 0
            note = " Achtung: Die laufende Selbstverbesserung wird dabei abgebrochen." if app.developer.busy else ""
            return "Jarvis wird nach dieser Antwort beendet." + note

        return [make_skill(f) for f in (selbst_verbessern, entwicklungsstatus, aenderungsverlauf,
                                        letzte_aenderung_rueckgaengig, neu_starten, jarvis_beenden)]

    # --- Start ----------------------------------------------------------------
    async def run(self) -> int:
        self.loop = asyncio.get_running_loop()
        ensure_data_dir()
        ui.banner()

        skills, errors = load_skills()
        for e in errors:
            ui.error(e)
        hints = _pop_hints()
        notes = list(hints)
        if errors:
            notes.append("Einige Fähigkeiten konnten nicht geladen werden (Details in der Konsole). "
                         "Biete an, das per selbst_verbessern zu reparieren.")
        update = _read_update()
        fresh_update = update if update and not update.get("bestaetigt") else None
        if fresh_update and fresh_update.get("zusammenfassung"):
            notes.append(f"Gerade frisch an dir geändert: {fresh_update['zusammenfassung']}")

        self.brain = Brain(self.cfg, skills + self.core_tools(), extra_notes=notes)
        ui.info(f"{len(skills)} Fähigkeiten geladen. Verbinde mit Claude …")
        try:
            await self.brain.connect()
        except Exception as e:  # noqa: BLE001
            ui.error(f"Keine Verbindung zu Claude: {e}")
            ui.info("Ist Claude Code installiert und angemeldet? Einmal 'claude' im Terminal starten und /login ausführen "
                    "(oder die Umgebungsvariable ANTHROPIC_API_KEY setzen).")
            return CONNECTION_FAILED_EXIT_CODE

        _confirm_update(fresh_update)
        self._start_inputs()
        for hint in hints:
            await self.say(hint)
        if fresh_update is None:
            await self.say(greeting(self.anrede))
        elif fresh_update.get("art") == "rueckgaengig":
            await self.say(f"Bin wieder da, {self.anrede}. Die Änderung ist rückgängig gemacht.")
        else:
            await self.say(f"Bin wieder da, {self.anrede}. Das Update ist aktiv.")

        try:
            while self.exit_code is None:
                kind, payload = await self.queue.get()
                if kind == "eingabe":
                    await self._handle_typed(payload)
                elif kind == "sprache":
                    await self._handle_spoken(payload)
                elif kind == "entwickler":
                    await self._handle_dev_result(payload)
        finally:
            if self.listener:
                self.listener.stop()
            await self.brain.disconnect()
        return self.exit_code

    def _start_inputs(self) -> None:
        threading.Thread(target=self._keyboard_loop, daemon=True, name="keyboard").start()
        if self.text_only:
            ui.info("Textmodus: kein Mikrofon.")
        else:
            self.listener = Listener(self.cfg["zuhoeren"], on_text=lambda t: self._post("sprache", t))
            if self.listener.start():
                if self.cfg["zuhoeren"]["modus"] == "aktivierungswort":
                    ui.info(f"Sag \"{self.cfg['zuhoeren']['aktivierungswoerter'][0].capitalize()}\", "
                            "um mich anzusprechen.")
            else:
                self.listener = None
        self.speaker.on_start = self._on_speaking_start
        self.speaker.on_end = self._on_speaking_end

    def _post(self, kind: str, payload: Any) -> None:
        assert self.loop is not None
        self.loop.call_soon_threadsafe(self.queue.put_nowait, (kind, payload))

    def _keyboard_loop(self) -> None:
        while True:
            try:
                line = input()
            except (EOFError, OSError):
                return  # keine Tastatur (z. B. ohne Konsole gestartet)
            if line.strip():
                self._post("eingabe", line.strip())

    def _on_speaking_start(self) -> None:
        if self.listener:
            self.listener.mute()

    def _on_speaking_end(self) -> None:
        if self.listener:
            self.listener.unmute()

    # --- Eingaben ---------------------------------------------------------------
    async def _handle_typed(self, text: str) -> None:
        if text.startswith("/"):
            await self._command(text)
        else:
            ui.user(text)
            await self._converse(text)

    async def _handle_spoken(self, text: str) -> None:
        z = self.cfg["zuhoeren"]
        command = extract_command(text, z["aktivierungswoerter"])
        if command is None:
            if z["modus"] == "immer" or time.monotonic() < self._followup_until:
                command = text
            else:
                ui.info(f"(gehört, ignoriert: {text})")
                return
        if not command:
            await self.say(f"Ja, {self.anrede}?")
            return
        ui.user(command, spoken=True)
        await self._converse(command)

    async def _converse(self, text: str) -> None:
        assert self.brain is not None
        ui.info("…")
        try:
            async for chunk in self.brain.ask(text):
                await self.say(chunk)
        except Exception as e:  # noqa: BLE001
            ui.error(f"Fehler im Gehirn: {e}")
            await self.say(f"Verzeihung, {self.anrede}, da ist etwas schiefgelaufen.")

    async def say(self, text: str) -> None:
        ui.jarvis(text)
        await self.speaker.say(text)
        # Kurz nach einer Antwort darf man ohne Aktivierungswort weiterreden.
        self._followup_until = time.monotonic() + float(self.cfg["zuhoeren"].get("nachfrage_sekunden", 0))

    # --- Selbstverbesserung -------------------------------------------------------
    async def _on_dev_done(self, result: DevResult) -> None:
        await self.queue.put(("entwickler", result))

    async def _handle_dev_result(self, result: DevResult) -> None:
        await self.say(result.zusammenfassung)
        if result.erfolg and result.geaendert:
            if self.cfg["entwickler"].get("automatisch_neustarten", True):
                await self.say("Ich starte mich kurz neu, um das Update zu aktivieren.")
                self.exit_code = RESTART_EXIT_CODE
            else:
                await self.say("Das Update wird beim nächsten Neustart aktiv.")

    # --- Konsolenbefehle ------------------------------------------------------------
    async def _command(self, line: str) -> None:
        cmd, _, arg = line[1:].partition(" ")
        cmd = cmd.lower()
        if cmd in ("hilfe", "help", "?"):
            ui.info(HELP)
        elif cmd in ("beenden", "exit", "quit"):
            self.exit_code = 0
        elif cmd == "neustart":
            if self.developer.busy:
                ui.warn("Claude Code arbeitet gerade an mir – ein Neustart würde das abbrechen. "
                        "Warte kurz oder nutze /beenden.")
            else:
                self.exit_code = RESTART_EXIT_CODE
        elif cmd == "neu":
            assert self.brain is not None
            await self.brain.reset()
            ui.info("Neues Gespräch begonnen.")
        elif cmd == "stumm":
            if self.text_only:
                ui.info("Im Textmodus gibt es keine Sprachausgabe.")
            else:
                self.speaker.engine = "aus" if self.speaker.enabled else self.cfg["sprechen"]["engine"]
                ui.info("Sprachausgabe " + ("an." if self.speaker.enabled else "aus."))
        elif cmd == "mikrofone":
            try:
                for i, name in enumerate(list_microphones()):
                    ui.info(f"{i}: {name}")
            except Exception as e:  # noqa: BLE001
                ui.error(f"Mikrofone können nicht aufgelistet werden: {e}")
        elif cmd == "verbessern" and arg.strip():
            if self.developer.busy:
                ui.warn(f"Claude Code arbeitet noch an: {self.developer.current_task}")
            else:
                self.developer.start(arg.strip(), self._on_dev_done)
        elif cmd == "verlauf":
            history = self.developer.git.update_history(20) if self.developer.git.available() else []
            for entry in history or ["Noch keine Selbst-Änderungen."]:
                ui.info(str(entry))
        elif cmd == "rueckgaengig":
            try:
                subject = await asyncio.to_thread(self.developer.undo_last)
                ui.info(f"Rückgängig gemacht: {subject}")
                self.exit_code = RESTART_EXIT_CODE
            except Exception as e:  # noqa: BLE001
                ui.error(str(e))
        else:
            ui.warn("Unbekannter Befehl. /hilfe zeigt alle Befehle.")


def _pop_hints() -> list[str]:
    """Hinweise, die der Starter (start.py) für Jarvis hinterlegt hat, z. B. nach einem Rollback."""
    try:
        hints = json.loads(HINTS_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    HINTS_FILE.unlink(missing_ok=True)
    return [str(h) for h in hints]


def _read_update() -> dict[str, Any] | None:
    try:
        return json.loads(UPDATE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _confirm_update(update: dict[str, Any] | None) -> None:
    """Markiert ein frisches Update als funktionierend (Jarvis ist damit erfolgreich gestartet)."""
    if update:
        update["bestaetigt"] = True
        UPDATE_FILE.write_text(json.dumps(update, ensure_ascii=False, indent=2), encoding="utf-8")
