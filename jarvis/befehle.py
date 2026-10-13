"""Konsolenbefehle (alles, was mit "/" beginnt). Werden nie an Claude geschickt."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from jarvis import RESTART_EXIT_CODE, einrichtung, protokoll, ui

if TYPE_CHECKING:
    from jarvis.app import Jarvis

HILFE = """Befehle:
  /hilfe                        Diese Hilfe
  /verbessern [gross] <text>    Auftrag direkt an Claude Code (ohne Umweg übers Gespräch)
  /status                       Woran Claude Code gerade arbeitet (und was in der Warteschlange steht)
  /antwort <text>               Rückfrage von Claude Code beantworten
  /abbrechen                    Laufende Selbstverbesserung abbrechen (alles wird zurückgesetzt)
  /verlauf  /rueckgaengig       Selbst-Änderungen anzeigen / letzte rückgängig machen
  /funktionen                   Alle Funktionen und ihr Zustand
  /projekte                     Stand aller Projekte (z. B. dein Roblox-Spiel)
  /hud                          Das Jarvis-Fenster (Oberfläche) öffnen
  /aus <name>  /an <name>       Funktion aus- bzw. einschalten
  /einrichtung [funktion]       Was eingerichtet ist und was fehlt
  /einrichten <name> [wert]     Wert speichern (ohne Wert: verdeckte Eingabe)
  /koppeln <kanal>              Handy-Kanal koppeln (z. B. /koppeln telegram)
  /entkoppeln <kanal>
  /dienste  /fehler [funktion]  Hintergrunddienste / letzte Fehler
  /neu                          Gespräch neu beginnen (Gedächtnis bleibt)
  /stumm                        Sprachausgabe an/aus
  /mikrofone                    Mikrofone auflisten (für mikrofon_index in config.toml)
  /neustart  /beenden"""


async def befehl_ausfuehren(app: Jarvis, zeile: str) -> None:  # noqa: C901 – einfache Verteilung
    cmd, _, arg = zeile[1:].partition(" ")
    cmd, arg = cmd.lower(), arg.strip()
    dev = app.developer

    if cmd in ("hilfe", "help", "?"):
        ui.info(HILFE)
    elif cmd in ("beenden", "exit", "quit"):
        if dev.busy:
            ui.warn("Claude Code arbeitet noch – die Arbeit wird verworfen.")
        app.exit_code = 0
    elif cmd == "neustart":
        if dev.busy:
            ui.warn("Claude Code arbeitet gerade an mir – ein Neustart würde das abbrechen. Warte, nutze /abbrechen "
                    "oder /beenden.")
        else:
            app.exit_code = RESTART_EXIT_CODE
    elif cmd == "neu":
        assert app.brain is not None
        await app.brain.reset()
        ui.info("Neues Gespräch begonnen.")
    elif cmd == "stumm":
        if app.text_only:
            ui.info("Im Textmodus gibt es keine Sprachausgabe.")
        else:
            app.speaker.engine = "aus" if app.speaker.enabled else app.cfg["sprechen"]["engine"]
            ui.info("Sprachausgabe " + ("an." if app.speaker.enabled else "aus."))
    elif cmd == "mikrofone":
        try:
            from jarvis.voice.listener import list_microphones
            for i, name in enumerate(list_microphones()):
                ui.info(f"{i}: {name}")
        except Exception as e:  # noqa: BLE001
            ui.error(f"Mikrofone können nicht aufgelistet werden: {e}")
    elif cmd == "verbessern":
        if not arg:
            ui.info("Beispiel: /verbessern gross Ich will mir Nachrichten aufs Handy schicken können (Telegram).")
        else:
            erstes, _, rest = arg.partition(" ")
            if erstes.lower() in ("gross", "groß", "klein") and rest:
                umfang, auftrag = ("gross" if erstes.lower() != "klein" else "klein"), rest
            else:
                umfang, auftrag = ("gross" if len(arg) > 150 else "klein"), arg
            app.entwicklung_einplanen(auftrag, titel=auftrag[:60], umfang=umfang)
    elif cmd == "status":
        ui.info(dev.status.beschreiben() if dev.busy and dev.status else "Gerade läuft keine Selbstverbesserung.")
        for i, auftrag in enumerate(app.warteschlange(), 1):
            ui.info(f"Warteschlange {i}: {auftrag['titel']}")
    elif cmd == "projekte":
        projekte = list(app.projekte.projekte.values())
        if not projekte:
            ui.info("Noch keine Projekte. Sag z. B.: Jarvis, lass uns mein Roblox-Spiel neu bauen.")
        for p in projekte:
            ui.kasten(f"Projekt {p.titel}", app.projekte.status_text(p))
    elif cmd == "hud":
        if app.oberflaeche is None:
            ui.info("Die Oberfläche ist aus (config.toml: [oberflaeche] an = true).")
        else:
            app.oberflaeche.fenster_oeffnen()
            ui.info(f"Oberfläche: {app.oberflaeche.url}")
    elif cmd == "antwort":
        if not arg:
            ui.info("Beispiel: /antwort Bambu Lab A1")
        elif app.entwicklerfrage_beantworten(arg):
            ui.info("Antwort an Claude Code weitergegeben.")
        else:
            ui.info(dev.nachtrag(arg))
    elif cmd == "abbrechen":
        if not dev.busy:
            ui.info("Es läuft gerade keine Selbstverbesserung.")
        else:
            ui.warn("Breche ab und setze alles zurück …")
            await dev.abbrechen()
    elif cmd == "verlauf":
        history = dev.git.update_history(20) if dev.git.available() else []
        for entry in history or ["Noch keine Selbst-Änderungen."]:
            ui.info(str(entry))
    elif cmd == "rueckgaengig":
        if dev.busy:
            ui.warn("Geht nicht, während Claude Code arbeitet.")
            return
        try:
            titel = await asyncio.to_thread(dev.undo_last)
            ui.info(f"Rückgängig gemacht: {titel}")
            app.exit_code = RESTART_EXIT_CODE
        except Exception as e:  # noqa: BLE001
            ui.error(str(e))
    elif cmd == "funktionen":
        ui.info(app.funktionen_text())
    elif cmd in ("aus", "an"):
        if not arg:
            ui.info(f"Beispiel: /{cmd} telegram")
        else:
            app.funktion_schalten(arg, cmd == "an")
            ui.info(f"Funktion '{arg}' {'eingeschaltet' if cmd == 'an' else 'ausgeschaltet'} – wirkt nach /neustart.")
    elif cmd == "einrichtung":
        ui.kasten("Einrichtung", einrichtung.status_text(arg))
        for w in einrichtung.fehlende(arg):
            ui.kasten(f"Fehlt: {w.beschreibung} ({w.name})", f"{w.anleitung_text() or '(keine Anleitung)'}\n→ "
                                                            f"{w.wie_eingeben()}")
    elif cmd == "einrichten":
        # "/einrichten name wert" fängt schon der Tastatur-Thread ab (verdeckt); hier nur Fehlbedienung.
        ui.info("Benutzung: /einrichten <name>   (dann verdeckt eingeben)   –   /einrichtung zeigt alle Namen")
    elif cmd == "koppeln":
        if not arg:
            ui.info("Beispiel: /koppeln telegram")
        else:
            app.kopplung_starten(arg)
    elif cmd == "entkoppeln":
        if arg:
            app.kopplung_aufheben(arg)
            ui.info(f"{arg} entkoppelt.")
    elif cmd == "dienste":
        ui.info("\n".join(app.dienste.status()) if app.dienste and app.dienste.status() else "Keine Dienste.")
    elif cmd == "fehler":
        eintraege = protokoll.letzte_fehler(arg, 10)
        for e in eintraege or [{"zeit": "", "funktion": "", "art": "", "wo": "", "fehler": "Keine Fehler."}]:
            ui.info(f"{e['zeit']} [{e['funktion']}, {e['art']}] {e['wo']}: {e['fehler']}")
    else:
        ui.warn("Unbekannter Befehl. /hilfe zeigt alle Befehle.")
