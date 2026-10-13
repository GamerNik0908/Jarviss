"""Hauptprogramm: verbindet Eingaben (Tastatur, Mikrofon, Kanäle, Ereignisse), Gehirn, Stimme, Dienste und Entwickler.

Grundregel: Nur die Hauptschleife (run) spricht mit dem Gehirn – ein Gesprächszug nach dem anderen. Alles andere
(Dienste, Claude Code, Kanäle) reiht sich in self.queue ein. Antworten auf Bestätigungsfragen werden direkt in
_eingang abgefangen, weil die Hauptschleife in dem Moment auf das Gehirn wartet.

GESCHÜTZTER BEREICH: die Neustart-/Herunterfahr-Logik (run, _herunterfahren, Exit-Codes).
"""

from __future__ import annotations

import asyncio
import contextvars
import json
import logging
import os
import re
import sys
import threading
import time
import traceback
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from jarvis import RESTART_EXIT_CODE, api, einrichtung, protokoll, ui
from jarvis.brain import Brain
from jarvis.config import DATA_DIR, ensure_data_dir
from jarvis.developer import UPDATE_FILE, Developer, DevResult
from jarvis.dienste import Dienstverwaltung
from jarvis.einrichtung import Einrichtungswert, Programm
from jarvis.kern import kern_werkzeuge, winget_installieren
from jarvis.projekte import Projektverwaltung
from jarvis.sicherheit import (MAX_EREIGNISSE_PRO_STUNDE, Ratenbegrenzer, Zug, ist_zustimmung, schwaerzen,
                               sieht_nach_schluessel_aus)
from jarvis.skills import dienste, funktionen, load_skills
from jarvis.voice.listener import Listener
from jarvis.voice.speaker import Speaker

HINTS_FILE = DATA_DIR / "hinweise.json"
FUNKTIONEN_FILE = DATA_DIR / "funktionen.json"
LOCK_FILE = DATA_DIR / "jarvis.lock"
WARTESCHLANGE_FILE = DATA_DIR / "entwicklung_warteschlange.json"
NACH_UPDATE_FILE = DATA_DIR / "nach_update.json"     # Wunsch, den Jarvis nach dem Neustart erledigen soll
NEUSTART_HOECHSTENS_WARTEN = 20 * 60                 # so lange wartet ein Neustart auf die laufende Projektaufgabe
CONNECTION_FAILED_EXIT_CODE = 2
_log = logging.getLogger("jarvis.app")

_FILLER = re.compile(r"^(hey|hi|hallo|ok|okay|oh|ja|na|yo|moin|servus)\b[\s,.!?]*", re.I)


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


@dataclass
class _Offen:
    """Eine offene Frage, auf deren Antwort jemand wartet."""

    future: asyncio.Future[str]
    text: str = ""
    vorschlag: str = ""
    kanal: str | None = None


class Jarvis:
    test_pfad = None  # für jarvis.api (nur die Testumgebung setzt das)

    def __init__(self, cfg: dict[str, Any], text_only: bool = False):
        self.cfg = cfg
        self.text_only = text_only or cfg["zuhoeren"]["modus"] == "aus"
        self._anrede = cfg["nutzer"]["anrede"] or "Sir"
        self.developer = Developer(cfg, frage_stellen=self.frage_vom_entwickler,
                                   bestaetigen_lokal=self.bestaetigen_lokal)
        self.speaker = Speaker({"engine": "aus"} if text_only else cfg["sprechen"])
        self.listener: Listener | None = None
        self.brain: Brain | None = None
        self.dienste: Dienstverwaltung | None = None
        self.queue: asyncio.Queue[tuple[str, Any]] = asyncio.Queue()
        self.loop: asyncio.AbstractEventLoop | None = None
        self.exit_code: int | None = None
        self.hintergrund: set[asyncio.Task[Any]] = set()
        self.ausgeschaltet: set[str] = _ausgeschaltete_laden()
        self._followup_until = 0.0
        self._sprech_lock = asyncio.Lock()
        self._frage_lock = asyncio.Lock()
        self._offene_bestaetigung: _Offen | None = None
        self._offene_entwicklerfrage: _Offen | None = None
        self._ereignisse: list[tuple[str, str]] = []
        self._kontext: list[str] = []
        self._kanaele: dict[str, Callable[[str], Awaitable[None]]] = {}
        self._rate = Ratenbegrenzer()
        self._lock_datei: Any = None
        self._offene_projektfrage: _Offen | None = None
        self._aktueller_auftrag: dict[str, Any] | None = None     # Selbstverbesserung, die gerade läuft
        self._warteschlange: list[dict[str, Any]] = _json_laden(WARTESCHLANGE_FILE, [])
        self._neustart_ausstehend = False
        self._zustand = "bereit"
        self._im_gespraech = False
        self._funktionen_cache: tuple[float, list[dict[str, Any]]] = (0.0, [])
        self.projekte = Projektverwaltung(cfg, gastgeber=self)
        self.oberflaeche: Any = None

    def core_tools(self):  # für Selbsttest/Tests
        return kern_werkzeuge(self)

    # ======================================================================================
    # Start & Hauptschleife
    # ======================================================================================
    async def run(self) -> int:
        self.loop = asyncio.get_running_loop()
        ensure_data_dir()
        protokoll.einrichten()
        if not self._instanz_sperren():
            ui.error("Jarvis läuft bereits in einem anderen Fenster.")
            return 0
        api._verbinden(self)
        ui.banner()
        await self._oberflaeche_starten()

        skills, errors = load_skills(aus=self.ausgeschaltet)
        for e in errors:
            ui.error(e)
        hints = _pop_hints()
        notes = list(hints)
        if errors:
            notes.append("Einige Funktionen konnten nicht geladen werden (Details: letzte_fehler / Konsole). "
                         "Biete an, das per selbst_verbessern zu reparieren.")
        if self.ausgeschaltet:
            notes.append(f"Ausgeschaltete Funktionen: {', '.join(sorted(self.ausgeschaltet))}")
        update = _read_update()
        fresh_update = update if update and not update.get("bestaetigt") else None

        self.brain = Brain(self.cfg, skills + kern_werkzeuge(self), extra_notes=notes)
        self.brain.bestaetiger = self.bestaetigen
        ui.info(f"{len(funktionen())} Funktionen geladen. Verbinde mit Claude …")
        try:
            await self.brain.connect()
        except Exception as e:  # noqa: BLE001
            ui.error(f"Keine Verbindung zu Claude: {e}")
            ui.info("Ist Claude Code installiert und angemeldet? Einmal 'claude' im Terminal starten und /login "
                    "ausführen (oder die Umgebungsvariable ANTHROPIC_API_KEY setzen).")
            if self.oberflaeche is not None:
                await self.oberflaeche.stoppen()
            return CONNECTION_FAILED_EXIT_CODE

        _confirm_update(fresh_update)
        nach_update = _json_laden(NACH_UPDATE_FILE, {})
        NACH_UPDATE_FILE.unlink(missing_ok=True)
        einrichtung.bei_aenderung(lambda name: self._threadsafe(self._einrichtung_geaendert))
        self._start_inputs()
        for hint in hints:
            await self.say(hint)
        naechster = self._naechsten_auftrag_starten()
        if fresh_update is not None:
            wunsch = str(nach_update.get("wunsch") or "") if fresh_update.get("art") == "update" else ""
            self._ereignis_einreihen(_update_ereignis(fresh_update, wunsch, naechster), "System")
        else:
            await self.say(greeting(self._anrede))
            fehlend = einrichtung.fehlende()
            if fehlend:
                ui.info("Noch nicht eingerichtet: " + ", ".join(f"{w.funktion}/{w.name}" for w in fehlend)
                        + " – sag \"Jarvis, richte das ein\" oder tippe /einrichtung")
            if naechster:
                await self.say(f"Claude Code macht mit dem nächsten Auftrag weiter: {naechster['titel']}.")
        self.dienste = Dienstverwaltung(dienste(), lambda text, quelle: self.melden(text, quelle))
        self.dienste.starten()
        self.projekte.starten()
        threading.Thread(target=self._lag_waechter, daemon=True, name="lag-waechter").start()

        try:
            while self.exit_code is None:
                kind, payload = await self.queue.get()
                try:
                    await self._verarbeiten(kind, payload)
                except Exception:  # noqa: BLE001 – ein Fehler darf die Hauptschleife nie beenden
                    ui.error(f"Interner Fehler: {traceback.format_exc(limit=3)}")
                    protokoll.LOGGER.exception("Fehler in der Hauptschleife")
        finally:
            await self._herunterfahren()
        return self.exit_code

    async def _verarbeiten(self, kind: str, payload: Any) -> None:
        if kind == "eingabe":
            await self._handle_typed(payload)
        elif kind == "sprache":
            await self._handle_spoken(payload)
        elif kind == "ereignis":
            await self._ereignisse_verarbeiten()
        elif kind == "kanal":
            await self._kanal_verarbeiten(*payload)
        elif kind == "einrichten":
            name, wert = payload
            w = einrichtung.finden(name)
            if w is None:
                ui.warn(f"Unbekannter Einrichtungswert '{name}'. /einrichtung zeigt alle.")
            else:
                ui.info(await self.wert_speichern(w, wert, als_ereignis=False))
        elif kind == "befehl":
            from jarvis.befehle import befehl_ausfuehren
            await befehl_ausfuehren(self, payload)
        elif kind == "entwickler":
            await self._handle_dev_result(payload)
        # "wecken": nichts tun – die Hauptschleife prüft danach nur exit_code

    async def _herunterfahren(self) -> None:
        """Dienste stoppen, Hintergrundaufgaben beenden, Gehirn trennen – und notfalls hart beenden."""
        code = self.exit_code if self.exit_code is not None else 1
        watchdog = threading.Timer(20, os._exit, args=(code,))
        watchdog.daemon = True
        watchdog.start()
        if self.dienste:
            await self.dienste.stoppen(5)
        await self.projekte.stoppen(8)       # eine laufende Projektaufgabe wird zurückgesetzt und später wiederholt
        if self.developer.busy and self.developer._task is not None:
            self.developer._task.cancel()  # setzt den Code sofort zurück (siehe Developer.improve)
            await asyncio.wait({self.developer._task}, timeout=8)
        for task in list(self.hintergrund):
            task.cancel()
        if self.hintergrund:
            await asyncio.wait(set(self.hintergrund), timeout=3)
        if self.listener:
            self.listener.stop()
        if self.brain:
            await self.brain.disconnect()
        if self.oberflaeche is not None:
            await self.oberflaeche.stoppen()

    def _neustart(self) -> None:
        """Neustart aus einer Hintergrundaufgabe heraus (weckt die Hauptschleife)."""
        if self.exit_code is None:
            self.exit_code = RESTART_EXIT_CODE
            self.queue.put_nowait(("wecken", None))

    def _instanz_sperren(self) -> bool:
        """Verhindert, dass Jarvis zweimal läuft (doppelte Dienste, doppelte Stimme)."""
        try:
            self._lock_datei = open(LOCK_FILE, "a+")
            if os.name == "nt":
                import msvcrt
                self._lock_datei.seek(0)
                msvcrt.locking(self._lock_datei.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._lock_datei.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            return False

    def _lag_waechter(self) -> None:
        """Erkennt, wenn etwas den Event-Loop blockiert (z. B. time.sleep in einer async-Funktion), und
        schreibt die Stelle ins Protokoll."""
        assert self.loop is not None
        main = threading.main_thread()
        letzte = [time.monotonic()]
        gemeldet = False
        while self.exit_code is None:
            try:
                self.loop.call_soon_threadsafe(lambda: letzte.__setitem__(0, time.monotonic()))
            except RuntimeError:
                return
            time.sleep(1)
            haengt = time.monotonic() - letzte[0] > 4
            if haengt and not gemeldet:
                gemeldet = True
                frame = sys._current_frames().get(main.ident or 0)
                stelle = "".join(traceback.format_stack(frame)[-6:]) if frame else "?"
                protokoll.LOGGER.warning("Jarvis hing länger als 4 Sekunden (blockierender Aufruf?):\n%s", stelle)
            elif not haengt:
                gemeldet = False

    # ======================================================================================
    # Eingaben
    # ======================================================================================
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

    def _threadsafe(self, fn: Callable[[], Any]) -> None:
        """Führt fn im Event-Loop aus – egal aus welchem Thread."""
        if self.loop is None:
            return
        try:
            if asyncio.get_running_loop() is self.loop:
                fn()
                return
        except RuntimeError:
            pass
        self.loop.call_soon_threadsafe(fn)

    def _post(self, kind: str, payload: Any) -> None:
        assert self.loop is not None
        self.loop.call_soon_threadsafe(self._eingang, kind, payload)

    def _eingang(self, kind: str, payload: Any) -> None:
        """Läuft im Event-Loop. Antworten auf offene Ja/Nein-Fragen werden hier direkt abgefangen."""
        offen = self._offene_bestaetigung
        if offen is not None and not offen.future.done():
            if kind == "eingabe":
                ui.user(payload)
                offen.future.set_result(payload)
                return
            if kind == "sprache":
                text = extract_command(payload, self.cfg["zuhoeren"]["aktivierungswoerter"])
                text = payload if text is None else text
                if text:
                    ui.user(text, "mikrofon")
                    offen.future.set_result(text)
                    return
        self.queue.put_nowait((kind, payload))

    def _keyboard_loop(self) -> None:
        import getpass

        while True:
            try:
                line = input()
            except (EOFError, OSError):
                return  # keine Tastatur (z. B. ohne Konsole gestartet)
            line = line.strip()
            if not line:
                continue
            if line.lower().startswith("/einrichten"):
                # Werte nie an Claude schicken und nicht im Fenster stehen lassen.
                teile = line.split(maxsplit=2)
                if len(teile) == 3:
                    print("\x1b[1A\x1b[2K", end="", flush=True)  # eingetippte Zeile wieder löschen
                    ui.info(f"/einrichten {teile[1]} ••••")
                    self._post("einrichten", (teile[1].lower(), teile[2]))
                    continue
                if len(teile) == 2:
                    w = einrichtung.finden(teile[1].lower())
                    if w is not None and not isinstance(w, Programm):
                        try:
                            wert = getpass.getpass(f"  Wert für {w.beschreibung} (wird nicht angezeigt): ")
                        except (EOFError, KeyboardInterrupt):
                            continue
                        if wert.strip():
                            self._post("einrichten", (w.name, wert.strip()))
                        continue
            if line.startswith("/"):
                self._post("befehl", line)
            else:
                self._post("eingabe", line)

    def _on_speaking_start(self) -> None:
        self._zustand_setzen("spricht")
        if self.listener:
            self.listener.mute()

    def _on_speaking_end(self) -> None:
        self._zustand_setzen("denkt" if self._im_gespraech else "bereit")
        if self.listener:
            self.listener.unmute()

    def _zustand_setzen(self, zustand: str) -> None:
        if zustand != self._zustand:
            self._zustand = zustand
            ui.zustand(zustand)

    async def _handle_typed(self, text: str) -> None:
        if sieht_nach_schluessel_aus(text):
            ui.warn("Das sieht aus wie ein Passwort oder Schlüssel – das schicke ich nicht an Claude. "
                    "Speichern mit: /einrichten <name>   (/einrichtung zeigt, was fehlt)")
            return
        ui.user(text)
        await self._converse(text, Zug("tastatur", vom_nutzer=True))

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
            await self.say(f"Ja, {self._anrede}?")
            return
        ui.user(command, "mikrofon")
        await self._converse(command, Zug("mikrofon", vom_nutzer=True))

    # ======================================================================================
    # Gespräch
    # ======================================================================================
    async def _zug(self, text: str, zug: Zug) -> AsyncIterator[str]:
        """Ein Gesprächszug mit dem Gehirn. Hintergrundinfos werden vorangestellt."""
        assert self.brain is not None
        kontext = list(self._kontext)
        self._kontext.clear()
        frage = self._offene_entwicklerfrage
        if frage is not None and not frage.future.done():
            kontext.append(f"Claude Code wartet auf eine Antwort auf: „{frage.text}“"
                           + (f" (Vorschlag: {frage.vorschlag})" if frage.vorschlag else "")
                           + ". Ist die folgende Nachricht die Antwort, gib sie mit an_entwickler weiter.")
        elif self.developer.busy and self.developer.status:
            kontext.append(f"Claude Code arbeitet gerade an: {self.developer.current_task}.")
        pfrage = self._offene_projektfrage
        if pfrage is not None and not pfrage.future.done():
            kontext.append(f"Das Projekt-Team ({pfrage.kanal}) wartet auf eine Antwort auf: „{pfrage.text}“"
                           + (f" (Vorschlag: {pfrage.vorschlag})" if pfrage.vorschlag else "")
                           + ". Ist die folgende Nachricht die Antwort, gib sie mit projekt_antworten weiter.")
        prompt = f"<kontext>\n{chr(10).join(kontext)}\n</kontext>\n\n{text}" if kontext else text
        self.brain.zug = zug
        self._im_gespraech = True
        self._zustand_setzen("denkt")
        try:
            async for chunk in self.brain.ask(schwaerzen(prompt)):
                yield chunk
        finally:
            self.brain.zug = None
            self._im_gespraech = False
            if self._zustand == "denkt":
                self._zustand_setzen("bereit")

    async def _converse(self, text: str, zug: Zug) -> None:
        ui.info("…")
        try:
            async for chunk in self._zug(text, zug):
                await self.say(chunk)
        except Exception as e:  # noqa: BLE001
            ui.error(f"Fehler im Gehirn: {e}")
            await self.say(f"Verzeihung, {self._anrede}, da ist etwas schiefgelaufen.")

    async def say(self, text: str, nachfrage: float | None = None) -> None:
        async with self._sprech_lock:
            ui.jarvis(text)
            await self.speaker.say(schwaerzen(text))
            # Kurz nach einer Antwort darf man ohne Aktivierungswort weiterreden.
            dauer = nachfrage if nachfrage is not None else float(self.cfg["zuhoeren"].get("nachfrage_sekunden", 0))
            self._followup_until = max(self._followup_until, time.monotonic() + dauer)

    def _kontext_merken(self, text: str) -> None:
        self._kontext.append(text)
        del self._kontext[:-10]

    # --- Ereignisse ---------------------------------------------------------------------------
    def _ereignis_einreihen(self, text: str, quelle: str) -> None:
        """Läuft im Event-Loop. Ereignisse werden gebündelt und je Quelle begrenzt."""
        if quelle not in ("System", "Claude Code") and \
                not self._rate.erlaubt(f"ereignis:{quelle}", MAX_EREIGNISSE_PRO_STUNDE):
            if self._rate.erlaubt(f"ereignis_hinweis:{quelle}", 1):
                ui.warn(f"Zu viele Meldungen von '{quelle}' – weitere werden diese Stunde ignoriert.")
            return
        leer = not self._ereignisse
        self._ereignisse.append((schwaerzen(text), quelle))
        if leer:
            self.queue.put_nowait(("ereignis", None))

    async def _ereignisse_verarbeiten(self) -> None:
        ereignisse, self._ereignisse = self._ereignisse, []
        if not ereignisse:
            return
        if len(ereignisse) == 1:
            text, quelle = ereignisse[0]
            nachricht = f"[Ereignis · {quelle}] {text}"
        else:
            quelle = ereignisse[0][1]
            nachricht = "[Ereignisse]\n" + "\n".join(f"{i}. ({q}) {t}" for i, (t, q) in enumerate(ereignisse, 1))
        ui.info(nachricht if len(nachricht) < 300 else nachricht[:300] + " …")
        await self._converse(nachricht, Zug(f"ereignis:{quelle}", vom_nutzer=False))
        if any(q == "Claude Code" for _, q in ereignisse):
            self._followup_until = max(self._followup_until, time.monotonic() + 60)

    # --- Kanäle (Handy) -------------------------------------------------------------------------
    async def kanal_zug(self, text: str, kanal: str, antworten: Callable[[str], Awaitable[None]]) -> None:
        """Für jarvis.api.kanal_eingabe (Besitzer ist bereits geprüft). Wartet, bis beantwortet."""
        self._kanaele[kanal] = antworten
        offen = self._offene_bestaetigung
        if offen is not None and offen.kanal == kanal and not offen.future.done():
            ui.user(text, kanal)
            offen.future.set_result(text)
            return
        fertig: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        await self.queue.put(("kanal", (text, kanal, antworten, fertig)))
        await asyncio.wait_for(asyncio.shield(fertig), timeout=900)

    async def _kanal_verarbeiten(self, text: str, kanal: str, antworten: Callable[[str], Awaitable[None]],
                                 fertig: asyncio.Future[None]) -> None:
        ui.user(text, kanal)
        teile: list[str] = []
        try:
            async for chunk in self._zug(f"[{kanal.capitalize()}] {text}", Zug(f"kanal:{kanal}", True, kanal=kanal)):
                ui.jarvis(chunk)
                teile.append(chunk)
            await antworten(schwaerzen("\n\n".join(teile)) or "…")
        except Exception as e:  # noqa: BLE001
            ui.error(f"Fehler bei Nachricht über {kanal}: {e}")
            try:
                await antworten("Da ist etwas schiefgelaufen.")
            except Exception:  # noqa: BLE001
                pass
        finally:
            if not fertig.done():
                fertig.set_result(None)

    def kopplung_starten(self, kanal: str) -> str:
        code = api._waechter.koppeln_starten(kanal.lower())
        ui.kasten(f"Kopplung {kanal}", f"Schick diesen Code an deinen Bot: {code}\n(10 Minuten gültig)")
        return code

    def kopplung_aufheben(self, kanal: str) -> None:
        api._waechter.entkoppeln(kanal.lower())

    def kopplung_gemeldet(self, kanal: str) -> None:
        self.sagen(f"{kanal.capitalize()} ist jetzt mit Ihrem Handy gekoppelt, {self._anrede}.", False)

    # ======================================================================================
    # Bestätigungen und Rückfragen
    # ======================================================================================
    async def bestaetigen(self, frage: str, zug: Zug, timeout: float = 90) -> bool:
        """Fragt den Nutzer (lokal oder über den Kanal des Zugs) und wartet auf ein klares Ja."""
        async with self._frage_lock:
            assert self.loop is not None
            fut: asyncio.Future[str] = self.loop.create_future()
            kanal = zug.kanal if zug.kanal in self._kanaele else None
            self._offene_bestaetigung = _Offen(fut, frage, kanal=kanal)
            try:
                if kanal:
                    await self._kanaele[kanal](f"{frage} (ja/nein)")
                    ui.info(f"Rückfrage über {kanal}: {frage}")
                else:
                    await self.say(frage, nachfrage=timeout)
                    ui.info("Antworte mit ja oder nein (sprechen oder tippen).")
                antwort: str | None = await asyncio.wait_for(asyncio.shield(fut), timeout)
            except asyncio.TimeoutError:
                antwort = None
            finally:
                self._offene_bestaetigung = None
            ok = ist_zustimmung(antwort)
            if not ok:
                ui.info("→ nicht ausgeführt")
            return ok

    async def bestaetigen_lokal(self, frage: str) -> bool:
        """Für den Entwickler (Änderungen am Sicherheitsnetz): nur lokale Antworten, 5 Minuten Zeit."""
        return await self.bestaetigen(frage, Zug("tastatur", vom_nutzer=True), timeout=300)

    async def frage_vom_entwickler(self, frage: str, vorschlag: str) -> str | None:
        """Rückfrage von Claude Code: geht als Ereignis ans Gehirn; die Antwort kommt über an_entwickler."""
        assert self.loop is not None
        fut: asyncio.Future[str] = self.loop.create_future()
        self._offene_entwicklerfrage = _Offen(fut, frage, vorschlag)
        self._ereignis_einreihen(
            f"Claude Code fragt: {frage}" + (f" Vorschlag: {vorschlag}." if vorschlag else "")
            + " Stell dem Nutzer die Frage kurz in deinen Worten (mit Vorschlag). Seine Antwort gibst du mit "
              "an_entwickler weiter. Steht die Antwort eindeutig in deinem Gedächtnis, antworte selbst mit "
              "an_entwickler und sag dem Nutzer, was du geantwortet hast.", "Claude Code")
        timeout = float(self.cfg["entwickler"].get("frage_timeout_minuten", 10)) * 60
        try:
            try:
                return await asyncio.wait_for(asyncio.shield(fut), timeout / 2)
            except asyncio.TimeoutError:
                self._ereignis_einreihen(f"Claude Code wartet noch auf die Antwort auf: {frage} – erinnere den "
                                         "Nutzer kurz daran.", "Claude Code")
            return await asyncio.wait_for(asyncio.shield(fut), timeout / 2)
        except asyncio.TimeoutError:
            return None
        finally:
            self._offene_entwicklerfrage = None

    def entwicklerfrage_beantworten(self, antwort: str) -> bool:
        frage = self._offene_entwicklerfrage
        if frage is None or frage.future.done():
            return False
        frage.future.set_result(schwaerzen(antwort))
        return True

    def projektfrage_beantworten(self, antwort: str) -> bool:
        frage = self._offene_projektfrage
        if frage is None or frage.future.done():
            return False
        frage.future.set_result(schwaerzen(antwort))
        return True

    # ======================================================================================
    # Selbstverbesserung (mit Warteschlange)
    # ======================================================================================
    def entwicklung_einplanen(self, auftrag: str, *, titel: str = "", umfang: str = "klein", funktion: str = "",
                              danach: str = "") -> int:
        """Startet einen Auftrag an Claude Code – oder stellt ihn in die Warteschlange, wenn schon einer läuft.

        danach: der ursprüngliche Wunsch des Nutzers; nach dem Update-Neustart erledigt Jarvis ihn automatisch.
        Gibt 0 zurück (gestartet) oder den Platz in der Warteschlange.
        """
        auftrag_info = {"auftrag": auftrag, "titel": titel or auftrag[:60], "umfang": umfang, "funktion": funktion,
                        "danach": danach, "zeit": datetime.now().isoformat(timespec="seconds")}
        if self.developer.busy or self._neustart_ausstehend:
            self._warteschlange.append(auftrag_info)
            self._warteschlange_speichern()
            ui.dev(f"In die Warteschlange (Platz {len(self._warteschlange)}): {auftrag_info['titel']}")
            return len(self._warteschlange)
        self._entwicklung_starten(auftrag_info)
        return 0

    def warteschlange(self) -> list[dict[str, Any]]:
        return list(self._warteschlange)

    def aus_warteschlange_entfernen(self, suche: str) -> list[str]:
        suche = suche.strip().lower()
        weg = [a for a in self._warteschlange if suche and (suche in a["titel"].lower() or suche in a["auftrag"].lower())]
        self._warteschlange = [a for a in self._warteschlange if a not in weg]
        self._warteschlange_speichern()
        return [a["titel"] for a in weg]

    def _warteschlange_speichern(self) -> None:
        WARTESCHLANGE_FILE.parent.mkdir(parents=True, exist_ok=True)
        WARTESCHLANGE_FILE.write_text(json.dumps(self._warteschlange, ensure_ascii=False, indent=2), encoding="utf-8")

    def _entwicklung_starten(self, auftrag_info: dict[str, Any]) -> None:
        self._aktueller_auftrag = auftrag_info
        self.developer.start(auftrag_info["auftrag"], self.entwicklung_fertig, titel=auftrag_info["titel"],
                             umfang=auftrag_info["umfang"], funktion=auftrag_info.get("funktion", ""))

    def _naechsten_auftrag_starten(self) -> dict[str, Any] | None:
        if self.developer.busy or self._neustart_ausstehend or self.exit_code is not None or not self._warteschlange:
            return None
        auftrag_info = self._warteschlange.pop(0)
        self._warteschlange_speichern()
        ui.dev(f"Nächster Auftrag aus der Warteschlange: {auftrag_info['titel']}")
        self._entwicklung_starten(auftrag_info)
        return auftrag_info

    async def entwicklung_fertig(self, result: DevResult) -> None:
        await self.queue.put(("entwickler", result))

    async def _handle_dev_result(self, result: DevResult) -> None:
        auftrag_info, self._aktueller_auftrag = self._aktueller_auftrag, None
        wunsch = (auftrag_info or {}).get("danach", "")
        if result.abgebrochen:
            await self.say(result.zusammenfassung)
            self._naechsten_auftrag_starten()
            return
        if result.erfolg and result.geaendert:
            text = f"Fertig, {self._anrede}. {result.zusammenfassung}"
            if result.einschraenkungen:
                text += f" Hinweis: {result.einschraenkungen[0]}"
            await self.say(text)
            if wunsch:
                NACH_UPDATE_FILE.parent.mkdir(parents=True, exist_ok=True)
                NACH_UPDATE_FILE.write_text(json.dumps({"wunsch": wunsch, "titel": result.titel}, ensure_ascii=False),
                                            encoding="utf-8")
            if not self.cfg["entwickler"].get("automatisch_neustarten", True):
                await self.say("Das Update wird beim nächsten Neustart aktiv.")
                self._naechsten_auftrag_starten()
            elif self.projekte.beschaeftigt:
                self._neustart_ausstehend = True      # vor dem Sprechen setzen: die Aufgabe kann währenddessen enden

                async def spaetestens() -> None:
                    await asyncio.sleep(NEUSTART_HOECHSTENS_WARTEN)
                    self._neustart()

                self._aufgabe(spaetestens(), "Neustart spätestens")
                await self.say("Ich starte neu, sobald die laufende Projektaufgabe fertig ist.")
                if not self.projekte.beschaeftigt:
                    self._neustart()
            else:
                await self.say("Ich starte mich kurz neu, um das Update zu aktivieren.")
                self.exit_code = RESTART_EXIT_CODE
            return
        nachsatz = f" Der ursprüngliche Wunsch des Nutzers war: „{wunsch}“." if wunsch else ""
        if result.erfolg:
            self._ereignis_einreihen(f"Claude Code ist fertig, es war keine Änderung nötig: {result.zusammenfassung}"
                                     + nachsatz, "Claude Code")
        else:
            self._ereignis_einreihen(f"Claude Code ist fertig, aber es hat nicht geklappt: {result.zusammenfassung}"
                                     + nachsatz + " Sag es dem Nutzer kurz und biete an, es noch einmal zu versuchen.",
                                     "Claude Code")
        naechster = self._naechsten_auftrag_starten()
        if naechster:
            self._kontext_merken(f"Claude Code arbeitet jetzt am nächsten Auftrag aus der Warteschlange: "
                                 f"{naechster['titel']}")

    # ======================================================================================
    # Projektarbeit (Gastgeber für jarvis.projekte)
    # ======================================================================================
    async def projektfrage(self, projekt: str, frage: str, vorschlag: str) -> str | None:
        """Rückfrage des Projekt-Teams: geht als Ereignis ans Gehirn; die Antwort kommt über projekt_antworten."""
        assert self.loop is not None
        fut: asyncio.Future[str] = self.loop.create_future()
        self._offene_projektfrage = _Offen(fut, frage, vorschlag, kanal=projekt)
        self._ereignis_einreihen(
            f"Das Projekt-Team von „{projekt}“ fragt: {frage}" + (f" Vorschlag: {vorschlag}." if vorschlag else "")
            + " Stell dem Nutzer die Frage kurz in deinen Worten (mit Vorschlag); seine Antwort gibst du mit "
              "projekt_antworten weiter. Steht die Antwort eindeutig in deinem Gedächtnis, antworte selbst und sag "
              "dem Nutzer, was du geantwortet hast.", "Projekte")
        try:
            return await asyncio.wait_for(asyncio.shield(fut),
                                          float(self.cfg["entwickler"].get("frage_timeout_minuten", 10)) * 60)
        except asyncio.TimeoutError:
            return None
        finally:
            self._offene_projektfrage = None

    def darf_arbeiten(self) -> bool:
        """Projektarbeit beginnt keine neue Aufgabe, während Jarvis sich selbst umbaut oder gleich neu startet."""
        return self.exit_code is None and not self._neustart_ausstehend and not self.developer.busy

    def arbeit_beendet(self) -> None:
        if self._neustart_ausstehend:
            self._neustart()

    # ======================================================================================
    # Oberfläche (HUD)
    # ======================================================================================
    async def _oberflaeche_starten(self) -> None:
        if not self.cfg["oberflaeche"].get("an", True):
            return
        try:
            from jarvis.oberflaeche import Oberflaeche

            self.oberflaeche = Oberflaeche(self, self.cfg)
            await self.oberflaeche.starten()
            self._aufgabe(self.oberflaeche.oeffnen_falls_noetig(), "Oberfläche öffnen")
        except Exception as e:  # noqa: BLE001 – ohne Oberfläche geht es auch (Konsole)
            ui.warn(f"Die Oberfläche konnte nicht gestartet werden ({e}) – die Konsole funktioniert weiter.")
            self.oberflaeche = None

    def oberflaeche_eingabe(self, daten: dict[str, Any]) -> None:
        """Eingaben aus dem HUD-Fenster (läuft im Event-Loop). Zählt wie die Tastatur am PC."""
        typ = daten.get("typ")
        if typ == "eingabe":
            text = str(daten.get("text", "")).strip()[:4000]
            if not text:
                return
            if text.lower().startswith("/einrichten"):
                teile = text.split(maxsplit=2)
                if len(teile) == 3:
                    ui.info(f"/einrichten {teile[1]} ••••")
                    self._eingang("einrichten", (teile[1].lower(), teile[2]))
                    return
                w = einrichtung.finden(teile[1].lower()) if len(teile) == 2 else None
                if w is not None and not isinstance(w, Programm):
                    self.im_hintergrund(self.wert_erfassen(w), name=f"Einrichtung {w.name}")
                    return
            self._eingang("befehl" if text.startswith("/") else "eingabe", text)
        elif typ == "projekt_aufgabe":
            p = self.projekte.finden(str(daten.get("projekt", "")))
            text = str(daten.get("text", "")).strip()[:4000]
            if p is None or not text:
                return
            if sieht_nach_schluessel_aus(text):
                ui.warn("Das sieht aus wie ein Passwort oder Schlüssel – das gebe ich nicht an das Projekt-Team.")
                return
            a = self.projekte.aufgabe_hinzufuegen(p, text.split("\n")[0], text,
                                                  prioritaet=1 if daten.get("wichtig") else 2)
            ui.projekt(p.name, f"Neue Aufgabe {a.id} von dir: {a.titel}")
        elif typ == "projekt_aktion":
            p = self.projekte.finden(str(daten.get("projekt", "")))
            aktion = str(daten.get("aktion", ""))
            if p is None or aktion not in ("pausieren", "fortsetzen", "dauerbetrieb_an", "dauerbetrieb_aus",
                                           "jetzt_planen", "studio_sync_an", "studio_sync_aus", "ordner_oeffnen"):
                return

            async def ausfuehren() -> None:
                ui.projekt(p.name, await self.projekte.steuern(p, aktion))

            self._aufgabe(ausfuehren(), f"Projekt {aktion}")

    def oberflaeche_status(self) -> dict[str, Any]:
        """Alles, was das HUD anzeigt – ohne geheime Werte."""
        dev = self.developer
        st = dev.status if dev.busy else None
        jetzt = time.monotonic()
        if jetzt - self._funktionen_cache[0] > 15:
            liste = [{"name": f.name, "titel": f.titel, "bereit": not einrichtung.fehlende(f.name)}
                     for f in sorted(funktionen().values(), key=lambda f: f.name)]
            self._funktionen_cache = (jetzt, liste)
        offen = self._offene_bestaetigung
        z = self.cfg["zuhoeren"]
        return {
            "zustand": self._zustand,
            "anrede": self._anrede,
            "stimme": self.speaker.enabled,
            "mikrofon": self.listener is not None,
            "aktivierungswort": (z.get("aktivierungswoerter") or ["jarvis"])[0].capitalize()
            if z.get("modus") == "aktivierungswort" else "",
            "bestaetigung": offen.text if offen is not None and not offen.future.done() else None,
            "entwickler": None if st is None else {
                "titel": st.titel or st.auftrag[:80], "umfang": st.umfang, "phase": st.phase, "meldung": st.meldung,
                "letzte_aktion": st.letzte_aktion, "schritte": st.schritte, "frage": st.frage,
                "minuten": int((jetzt - st.start) // 60), "aufgaben": st.aufgaben[:12]},
            "warteschlange": [a["titel"] for a in self._warteschlange],
            "neustart_ausstehend": self._neustart_ausstehend,
            "projekte": self.projekte.uebersicht(),
            "funktionen": self._funktionen_cache[1],
        }

    # ======================================================================================
    # Einrichtung
    # ======================================================================================
    async def wert_erfassen(self, w: Einrichtungswert) -> None:
        from jarvis import eingabefenster

        wert = await eingabefenster.abfragen(f"Jarvis – {w.beschreibung}", w.anleitung_text(), geheim=w.geheim)
        if wert is None:
            self._ereignis_einreihen(f"Die Eingabe für {w.beschreibung} wurde abgebrochen oder das Fenster ließ sich "
                                     f"nicht öffnen. Alternative: am PC tippen: /einrichten {w.name}", "System")
            return
        await self.wert_speichern(w, wert, als_ereignis=True)

    async def wert_speichern(self, w: Einrichtungswert, wert: str, als_ereignis: bool) -> str:
        """Speichert einen Einrichtungswert, prüft ihn (falls die Funktion das kann) und meldet das Ergebnis."""
        w.setzen(wert.strip())
        ergebnis = f"{w.beschreibung} gespeichert."
        if w.pruefen is not None:
            try:
                ok_text = await asyncio.wait_for(asyncio.to_thread(w.pruefen, w.wert()), timeout=20)
                ergebnis += f" Prüfung erfolgreich: {ok_text}"
            except Exception as e:  # noqa: BLE001
                ergebnis += f" Prüfung fehlgeschlagen: {schwaerzen(e)}"
        ergebnis = schwaerzen(ergebnis)
        if als_ereignis:
            self._ereignis_einreihen(f"Einrichtung: {ergebnis} Fahre mit der Einrichtung fort, falls noch etwas "
                                     "fehlt (einrichtung_anleitung), sonst sag, wie man die Funktion benutzt.", "System")
        else:
            self._kontext_merken(f"Einrichtung: {ergebnis}")
        return ergebnis

    def _einrichtung_geaendert(self) -> None:
        if self.dienste:
            self.dienste.einrichtung_geaendert()

    async def programm_installieren(self, w: Programm) -> None:
        assert w.winget
        try:
            code = await winget_installieren(w.winget)
        except Exception as e:  # noqa: BLE001
            self._ereignis_einreihen(f"Die Installation von {w.beschreibung} ist fehlgeschlagen: {e}", "System")
            return
        if w.vorhanden:
            self._einrichtung_geaendert()
            self._ereignis_einreihen(f"{w.beschreibung} wurde installiert und gefunden.", "System")
        else:
            self._ereignis_einreihen(f"Die Installation von {w.beschreibung} ist beendet (Code {code}), aber das "
                                     f"Programm wurde nicht gefunden. Anleitung:\n{w.anleitung_text()}", "System")

    # ======================================================================================
    # Funktionen an/aus, Übersicht
    # ======================================================================================
    def funktion_schalten(self, funktion: str, an: bool) -> None:
        if an:
            self.ausgeschaltet.discard(funktion)
        else:
            self.ausgeschaltet.add(funktion)
        FUNKTIONEN_FILE.parent.mkdir(parents=True, exist_ok=True)
        FUNKTIONEN_FILE.write_text(json.dumps({"aus": sorted(self.ausgeschaltet)}), encoding="utf-8")

    def funktionen_text(self, nur: str = "") -> str:
        dienst_status = {s.split(" ", 1)[0]: s for s in (self.dienste.status() if self.dienste else [])}
        zeilen = []
        for f in sorted(funktionen().values(), key=lambda f: f.name):
            if nur and f.name != nur:
                continue
            fehlend = einrichtung.fehlende(f.name)
            zustand = "Einrichtung fehlt: " + ", ".join(w.beschreibung for w in fehlend) if fehlend else "bereit"
            zeile = f"{f.name} – {f.titel} [{zustand}]; Werkzeuge: {', '.join(s.name for s in f.skills) or '–'}"
            if f.dienste:
                zeile += "; Dienste: " + "; ".join(dienst_status.get(d.name, d.name) for d in f.dienste)
            zeilen.append(zeile)
        for name in sorted(self.ausgeschaltet):
            if not nur or nur == name:
                zeilen.append(f"{name} – ausgeschaltet")
        return "\n".join(zeilen) if zeilen else "Keine Funktionen gefunden."

    # ======================================================================================
    # Backend für jarvis.api (Funktionen rufen das über api.sagen/melden/… auf)
    # ======================================================================================
    def sagen(self, text: str, wichtig: bool) -> None:
        async def proaktiv() -> None:
            await self.say(text)
            self._kontext_merken(f"Du hast von dir aus gesagt: {text}")

        self._threadsafe(lambda: self._aufgabe(proaktiv(), "sagen"))

    def melden(self, text: str, quelle: str) -> None:
        self._threadsafe(lambda: self._ereignis_einreihen(text, quelle))

    def im_hintergrund(self, arbeit: Any, name: str) -> None:
        async def lauf() -> None:
            try:
                if asyncio.iscoroutine(arbeit):
                    await arbeit
                else:
                    ergebnis = await asyncio.to_thread(arbeit)
                    if asyncio.iscoroutine(ergebnis):
                        await ergebnis
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                hinweis = protokoll.fehler_merken("hintergrund", e, f"Hintergrundaufgabe {name}")
                self._ereignis_einreihen(f"Hintergrundaufgabe '{name}' ist fehlgeschlagen: {hinweis}", "System")

        self._threadsafe(lambda: self._aufgabe(lauf(), name))

    def _aufgabe(self, coro: Any, name: str) -> asyncio.Task[Any]:
        assert self.loop is not None
        task = self.loop.create_task(coro, name=name, context=contextvars.Context())
        self.hintergrund.add(task)
        task.add_done_callback(self.hintergrund.discard)
        return task

    def abspielen(self, datei: Path) -> None:
        async def spielen() -> None:
            async with self._sprech_lock:
                await self.speaker.abspielen(datei)

        self._threadsafe(lambda: self._aufgabe(spielen(), "abspielen"))

    async def denken(self, anweisung: str, daten: str, schema: dict[str, Any] | None) -> Any:
        return await api._echtes_denken(anweisung, daten, schema)

    def anrede(self) -> str:
        return self._anrede


# ==========================================================================================
# Hilfsfunktionen
# ==========================================================================================

def _ausgeschaltete_laden() -> set[str]:
    try:
        return set(json.loads(FUNKTIONEN_FILE.read_text(encoding="utf-8")).get("aus", []))
    except (OSError, json.JSONDecodeError, AttributeError):
        return set()


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


def _json_laden(datei: Path, standard: Any) -> Any:
    try:
        daten = json.loads(datei.read_text(encoding="utf-8"))
        return daten if isinstance(daten, type(standard)) else standard
    except (OSError, json.JSONDecodeError):
        return standard


def _update_ereignis(update: dict[str, Any], wunsch: str = "", naechster: dict[str, Any] | None = None) -> str:
    if update.get("art") == "rueckgaengig":
        return (f"Neustart: {update.get('zusammenfassung', 'Die letzte Änderung wurde rückgängig gemacht.')} "
                "Sag dem Nutzer kurz Bescheid.")
    text = f"Neustart nach Update „{update.get('titel') or ''}“. Neu: {update.get('zusammenfassung', '')}"
    if update.get("naechste_schritte"):
        text += f" Was der Nutzer noch tun muss: {update['naechste_schritte']}"
    fehlend = einrichtung.fehlende()
    if fehlend:
        text += " Noch nicht eingerichtet: " + "; ".join(f"{w.beschreibung} ({w.name})" for w in fehlend) + "."
    if naechster:
        text += f" Claude Code arbeitet jetzt am nächsten Auftrag aus der Warteschlange: {naechster['titel']}."
    if wunsch:
        return text + (f" Der Nutzer hatte sich gewünscht: „{wunsch}“ – dafür hast du dir das gerade beigebracht. "
                       "Sag in einem Satz, was neu ist, und erledige den Wunsch dann sofort mit deinen Werkzeugen. "
                       "Fehlt dafür noch eine Einrichtung, führe den Nutzer zuerst hindurch.")
    if update.get("ausprobieren"):
        text += f" Beispielsatz zum Ausprobieren: „{update['ausprobieren']}“."
    return text + (" Begrüße den Nutzer kurz (ein bis zwei Sätze) und sag, was neu ist. Fehlt eine Einrichtung, "
                   "biete an, sie jetzt gemeinsam zu machen (meist zwei Minuten); sonst nenne den Beispielsatz.")
