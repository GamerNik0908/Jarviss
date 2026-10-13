"""Kern-Werkzeuge des Gehirns: Selbstverbesserung, Einrichtung, Kanäle, Status. Brauchen Zugriff auf Jarvis selbst."""

from __future__ import annotations

import asyncio
import shutil
import subprocess
import sys
from typing import TYPE_CHECKING, Annotated

from jarvis import RESTART_EXIT_CODE, einrichtung, protokoll, ui
from jarvis.einrichtung import Programm
from jarvis.projekte import projekt_werkzeuge
from jarvis.sicherheit import sieht_nach_schluessel_aus
from jarvis.skills import Skill, funktionen, make_skill

if TYPE_CHECKING:
    from jarvis.app import Jarvis


def kern_werkzeuge(app: Jarvis) -> list[Skill]:  # noqa: C901 – bewusst eine Sammlung kleiner Werkzeuge
    dev = app.developer

    # --- Selbstverbesserung ---------------------------------------------------------------
    async def selbst_verbessern(
        auftrag: Annotated[str, "Ausführlicher Auftrag auf Deutsch: Ziel, alle Antworten des Nutzers, was er schon "
                                "hat, 3–5 Beispielsätze. Keine Passwörter/Schlüssel."],
        kurzfassung: Annotated[str, "Ein kurzer Satz zum Vorlesen, z. B. 'Dokumente erstellen und drucken'"],
        umfang: Annotated[str, "'klein' für Einstellungen, kleine Fähigkeiten und Reparaturen; 'gross' für neue "
                               "Funktionen mit Diensten, Geräten, Konten oder Hintergrundbetrieb"] = "klein",
        funktion: Annotated[str, "Bei Reparaturen: Name der betroffenen Funktion, sonst leer"] = "",
        danach: Annotated[str, "Der ursprüngliche Wunsch des Nutzers, den du nach dem Update sofort erledigen sollst "
                               "(mit allen Details, z. B. 'Erstelle ein Dokument über Vulkane und drucke es aus'); "
                               "leer, wenn es nur um die Fähigkeit selbst geht"] = "",
    ) -> str:
        """Verändert Jarvis' eigenen Programmcode mit Claude Code: neue Fähigkeiten (auch große wie Dokumente drucken,
        Nachrichten aufs Handy, 3D-Drucker, Smart Home), anderes Verhalten, Einstellungen, Fehlerbehebungen. Nutze das
        auch von dir aus, wenn ein Wunsch an einer fehlenden Fähigkeit scheitert. Läuft im Hintergrund; danach startet
        Jarvis neu und erledigt den Wunsch aus 'danach'. Läuft schon ein Auftrag, kommt dieser in die Warteschlange."""
        if sieht_nach_schluessel_aus(auftrag) or sieht_nach_schluessel_aus(danach):
            return ("Abgelehnt: Der Auftrag enthält etwas, das wie ein Passwort oder Schlüssel aussieht. Formuliere "
                    "ihn ohne – Zugangsdaten gibt der Nutzer nach dem Update im Eingabefenster ein.")
        umfang = "gross" if umfang.strip().lower() in ("gross", "groß", "big") else "klein"
        laufend = dev.current_task
        platz = app.entwicklung_einplanen(auftrag, titel=kurzfassung, umfang=umfang, funktion=funktion.strip(),
                                          danach=danach.strip())
        if platz:
            return (f"Claude Code arbeitet noch an „{laufend or 'einem anderen Auftrag'}“. Dieser Auftrag steht auf "
                    f"Platz {platz} der Warteschlange und startet danach automatisch. Sag das dem Nutzer kurz.")
        nach = " Nach dem Neustart erledigst du automatisch den ursprünglichen Wunsch." if danach.strip() else ""
        if umfang == "gross":
            return ("Auftrag an Claude Code übergeben (große Funktion, meist 15 bis 60 Minuten). Sag dem Nutzer, dass "
                    "er normal weiterreden kann und du dich bei Rückfragen meldest." + nach)
        return "Auftrag an Claude Code übergeben (meist 2 bis 10 Minuten). Er läuft im Hintergrund." + nach

    async def entwicklungsstatus() -> str:
        """Sagt, ob und woran Claude Code gerade arbeitet (Dauer, Schritt, Tätigkeit, offene Rückfrage) und was in
        der Warteschlange steht."""
        text = dev.status.beschreiben() if dev.busy and dev.status else "Gerade läuft keine Selbstverbesserung."
        schlange = app.warteschlange()
        if schlange:
            text += " Warteschlange: " + "; ".join(f"{i}. {a['titel']}" for i, a in enumerate(schlange, 1))
        return text

    async def auftrag_streichen(suche: Annotated[str, "Teil des Titels eines Auftrags in der Warteschlange"]) -> str:
        """Streicht einen wartenden Auftrag aus der Warteschlange der Selbstverbesserung (der laufende wird mit
        entwicklung_abbrechen gestoppt)."""
        weg = app.aus_warteschlange_entfernen(suche)
        return "Gestrichen: " + ", ".join(weg) if weg else f"Nichts zu „{suche}“ in der Warteschlange."

    async def an_entwickler(nachricht: Annotated[str, "Antwort auf die Rückfrage oder Ergänzung des Nutzers, "
                                                      "inhaltlich unverändert"]) -> str:
        """Gibt eine Antwort auf eine Rückfrage von Claude Code oder eine Ergänzung des Nutzers an Claude Code
        weiter, während es an Jarvis arbeitet."""
        if app.entwicklerfrage_beantworten(nachricht):
            return "Antwort an Claude Code weitergegeben."
        return dev.nachtrag(nachricht)

    async def entwicklung_abbrechen() -> str:
        """Bricht die laufende Selbstverbesserung ab und setzt alles zurück. Nur auf ausdrücklichen Wunsch."""
        if not dev.busy:
            return "Es läuft gerade keine Selbstverbesserung."
        app.im_hintergrund(dev.abbrechen(), name="Abbrechen")
        return "Wird abgebrochen und zurückgesetzt."

    def aenderungsverlauf() -> str:
        """Listet die letzten Änderungen auf, die Jarvis an sich selbst vorgenommen hat."""
        history = dev.git.update_history(10) if dev.git.available() else []
        if not history:
            return "Bisher wurden keine Selbst-Änderungen vorgenommen."
        return "\n".join(str(e) for e in history)

    async def letzte_aenderung_rueckgaengig() -> str:
        """Macht die letzte Selbst-Änderung von Jarvis rückgängig und startet danach neu."""
        if dev.busy:
            return "Geht gerade nicht – Claude Code arbeitet noch an einem Auftrag."
        titel = await asyncio.to_thread(dev.undo_last)
        app.exit_code = RESTART_EXIT_CODE
        return f"Rückgängig gemacht: {titel}. Ich starte nach dieser Antwort neu."

    async def neu_starten() -> str:
        """Startet Jarvis neu (z. B. damit Änderungen wirksam werden)."""
        if dev.busy:
            return "Lieber nicht – Claude Code arbeitet gerade an mir. Ein Neustart würde das abbrechen."
        app.exit_code = RESTART_EXIT_CODE
        return "Neustart nach dieser Antwort."

    async def jarvis_beenden() -> str:
        """Beendet Jarvis komplett (nur wenn der Nutzer das ausdrücklich will)."""
        app.exit_code = 0
        note = " Die laufende Selbstverbesserung wird dabei verworfen." if dev.busy else ""
        return "Jarvis wird nach dieser Antwort beendet." + note

    # --- Einrichtung ------------------------------------------------------------------------
    def einrichtung_status(funktion: Annotated[str, "Name der Funktion oder leer für alle"] = "") -> str:
        """Zeigt, welche Zugangsdaten/Adressen/Programme die Funktionen brauchen und ob sie eingerichtet sind
        (ohne geheime Werte)."""
        return einrichtung.status_text(funktion.strip())

    def einrichtung_anleitung(funktion: Annotated[str, "Name der Funktion oder leer für alles Fehlende"] = "") -> str:
        """Schritt-für-Schritt-Anleitung für alles, was einer Funktion noch fehlt. Zeigt sie auch im Fenster an.
        Lies dem Nutzer einen Schritt pro Antwort vor; geheime Werte erfasst du mit einrichtung_erfassen."""
        fehlend = einrichtung.fehlende(funktion.strip())
        if not fehlend:
            return "Für diese Funktion ist alles eingerichtet." if funktion else "Es ist alles eingerichtet."
        bloecke = []
        for w in fehlend:
            bloecke.append(f"{w.beschreibung} ({w.name}, Funktion {w.funktion}):\n"
                           f"{w.anleitung_text() or '(keine Anleitung)'}\n→ {w.wie_eingeben()}")
        text = "\n\n".join(bloecke)
        ui.kasten("Einrichtung", text)
        return text

    async def einrichtung_erfassen(name: Annotated[str, "Name des Einrichtungswerts, z. B. telegram_token"]) -> str:
        """Öffnet am PC ein kleines Eingabefenster für einen Einrichtungswert (vor allem für geheime Werte wie
        Tokens und Passwörter). Kehrt sofort zurück; das Ergebnis kommt später als Ereignis."""
        w = einrichtung.finden(name.strip())
        if w is None:
            return f"Unbekannter Einrichtungswert '{name}'. Verfügbar:\n{einrichtung.status_text()}"
        if isinstance(w, Programm):
            return (f"{w.beschreibung} ist ein Programm – installieren mit programm_installieren('{w.name}') oder "
                    f"Pfad tippen: /einrichten {w.name} <pfad>")
        app.im_hintergrund(app.wert_erfassen(w), name=f"Einrichtung {w.name}")
        return ("Eingabefenster am PC ist geöffnet. Sag dem Nutzer, er soll den Wert dort einfügen und auf Speichern "
                "klicken – bitte nicht vorlesen. Das Ergebnis bekommst du gleich als Ereignis.")

    async def einrichtung_setzen(name: str, wert: Annotated[str, "Der Wert, sauber formatiert (z. B. '192.168.178.45')"]) -> str:
        """Speichert einen NICHT geheimen Einrichtungswert, den der Nutzer gesagt hat (z. B. eine IP-Adresse).
        Geheime Werte gehen nur über einrichtung_erfassen."""
        w = einrichtung.finden(name.strip())
        if w is None:
            return f"Unbekannter Einrichtungswert '{name}'."
        if w.geheim or not w.sprachlich:
            return (f"'{w.name}' darf nicht per Sprache/Chat gesetzt werden. Öffne stattdessen das Eingabefenster "
                    f"mit einrichtung_erfassen('{w.name}').")
        return await app.wert_speichern(w, wert, als_ereignis=False)

    async def einrichtung_loeschen(name: str) -> str:
        """Löscht einen gespeicherten Einrichtungswert (z. B. wenn eine Funktion entfernt wurde)."""
        w = einrichtung.finden(name.strip())
        if w is None:
            if not einrichtung.speicher_lesen(name.strip()):
                return f"Unbekannter Einrichtungswert '{name}'."
            einrichtung.speicher_schreiben(name.strip(), None)
        else:
            w.loeschen()
        return f"'{name}' gelöscht."

    async def programm_installieren(name: Annotated[str, "Name des Programms aus einrichtung_status"]) -> str:
        """Installiert ein Programm, das eine Funktion braucht (z. B. einen Slicer), über winget. Fragt vorher."""
        w = einrichtung.finden(name.strip())
        if not isinstance(w, Programm):
            return f"'{name}' ist kein bekanntes Programm."
        if w.vorhanden:
            return f"{w.beschreibung} ist schon installiert."
        if not (sys.platform == "win32" and w.winget and shutil.which("winget")):
            return (f"Automatisch installieren geht hier nicht. Anleitung:\n{w.anleitung_text()}\n"
                    f"Öffne den Link mit webseite_oeffnen.")
        app.im_hintergrund(app.programm_installieren(w), name=f"Installation {w.name}")
        return (f"Die Installation von {w.beschreibung} läuft in einem eigenen Fenster. Windows fragt eventuell nach "
                "Erlaubnis. Ich melde mich, wenn sie fertig ist.")

    # --- Kanäle (Handy) -----------------------------------------------------------------------
    async def kanal_koppeln(kanal: Annotated[str, "z. B. telegram"]) -> str:
        """Startet die Kopplung eines Kanals (z. B. Telegram) mit dem Handy des Nutzers: erzeugt einen Code, den
        der Nutzer dem Bot schickt. Nur direkt am PC möglich."""
        zug = app.brain.zug if app.brain else None
        if zug is None or not zug.lokal:
            return "Abgelehnt: Koppeln geht nur direkt am PC (Sprache oder Tastatur)."
        code = app.kopplung_starten(kanal)
        return (f"Kopplungscode für {kanal}: {code} (10 Minuten gültig, steht auch im Fenster). Sag dem Nutzer, er "
                f"soll genau diese Zahl an den Bot schicken. Lies die Ziffern einzeln vor.")

    async def kanal_entkoppeln(kanal: str) -> str:
        """Entkoppelt einen Kanal (danach antwortet Jarvis dort niemandem mehr). Nur direkt am PC."""
        zug = app.brain.zug if app.brain else None
        if zug is None or not zug.lokal:
            return "Abgelehnt: Entkoppeln geht nur direkt am PC."
        app.kopplung_aufheben(kanal)
        return f"{kanal} ist entkoppelt."

    # --- Überblick & Fehler ----------------------------------------------------------------------
    def faehigkeiten_uebersicht(funktion: Annotated[str, "Name einer Funktion oder leer für alle"] = "") -> str:
        """Überblick über Jarvis' Funktionen: was sie können, ob sie bereit sind (Einrichtung, Dienste), welche
        ausgeschaltet sind."""
        return app.funktionen_text(funktion.strip())

    def dienste_status() -> str:
        """Status der Hintergrunddienste (läuft, wartet auf Einrichtung, Fehler …)."""
        return "\n".join(app.dienste.status()) if app.dienste and app.dienste.status() else "Keine Hintergrunddienste."

    def letzte_fehler(funktion: Annotated[str, "Name der Funktion oder leer für alle"] = "") -> str:
        """Die letzten Fehler von Funktionen (Art: einrichtung, verbindung, programmfehler) – erst prüfen, bevor
        man eine Reparatur beauftragt."""
        eintraege = protokoll.letzte_fehler(funktion.strip(), 8)
        if not eintraege:
            return "Keine Fehler protokolliert."
        return "\n".join(f"{e['zeit']} [{e['funktion']}, {e['art']}] {e['wo']}: {e['fehler']}" for e in eintraege)

    async def funktion_schalten(funktion: str, an: bool) -> str:
        """Schaltet eine Funktion aus oder wieder an (z. B. wenn sie stört). Jarvis startet danach neu."""
        alle = {f.name for f in funktionen().values()} | app.ausgeschaltet
        if funktion not in alle:
            return f"Unbekannte Funktion '{funktion}'. Vorhanden: {', '.join(sorted(alle))}"
        app.funktion_schalten(funktion, an)
        app.exit_code = RESTART_EXIT_CODE
        return f"Funktion '{funktion}' wird {'eingeschaltet' if an else 'ausgeschaltet'}; Neustart nach dieser Antwort."

    immer = {"selbst_verbessern": "Soll ich mich dafür umprogrammieren: {kurzfassung}?",
             "entwicklung_abbrechen": "Soll ich die Arbeit von Claude Code wirklich abbrechen? Alles bisher "
                                      "Gebaute geht dabei verloren.",
             "programm_installieren": "Soll ich {name} installieren? Windows fragt dabei eventuell nach Erlaubnis."}
    wenn_fremd = {"letzte_aenderung_rueckgaengig", "neu_starten", "jarvis_beenden", "einrichtung_setzen",
                  "einrichtung_loeschen", "funktion_schalten", "an_entwickler", "auftrag_streichen"}
    if app.cfg["entwickler"].get("ohne_nachfrage"):
        # Der Nutzer will, dass Jarvis sich ohne Rückfrage Neues beibringt – aber nur bei direkten Wünschen.
        immer.pop("selbst_verbessern")
        wenn_fremd.add("selbst_verbessern")
    werkzeuge = []
    for fn in (selbst_verbessern, entwicklungsstatus, auftrag_streichen, an_entwickler, entwicklung_abbrechen,
               aenderungsverlauf,
               letzte_aenderung_rueckgaengig, neu_starten, jarvis_beenden, einrichtung_status, einrichtung_anleitung,
               einrichtung_erfassen, einrichtung_setzen, einrichtung_loeschen, programm_installieren, kanal_koppeln,
               kanal_entkoppeln, faehigkeiten_uebersicht, dienste_status, letzte_fehler, funktion_schalten):
        name = fn.__name__
        if name in immer:
            werkzeuge.append(make_skill(fn, nachfragen="immer", frage=immer[name]))
        elif name in wenn_fremd:
            werkzeuge.append(make_skill(fn, nachfragen="wenn_fremd"))
        else:
            werkzeuge.append(make_skill(fn))
    return werkzeuge + projekt_werkzeuge(app)


async def winget_installieren(paket_id: str) -> int:
    """Startet winget in einem eigenen, sichtbaren Fenster und wartet auf das Ende."""
    flags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
    proc = await asyncio.create_subprocess_exec(
        "winget", "install", "--id", paket_id, "-e", "--accept-package-agreements", "--accept-source-agreements",
        creationflags=flags)
    return await asyncio.wait_for(proc.wait(), timeout=1800)
