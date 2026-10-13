"""Timer & Erinnerungen: Timer stellen, an Dinge erinnern, tägliche Routinen

Beispiel-Funktion, die die Bausteine aus jarvis.api zeigt: @skill (Befehle), @dienst (läuft im Hintergrund),
Speicher (übersteht Neustarts), sagen() (Jarvis spricht von sich aus) und melden() (Routine ans Gehirn).
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta
from typing import Annotated, Any

from jarvis.api import Speicher, anrede, dienst, melden, sagen, skill

SPEICHER = Speicher("timer")


def _eintraege() -> list[dict[str, Any]]:
    return list(SPEICHER.lesen("eintraege", []))


def _speichern(eintraege: list[dict[str, Any]]) -> None:
    SPEICHER.schreiben("eintraege", eintraege)


def _neu(zeitpunkt: datetime, text: str, *, art: str, wiederholen: str = "", als_auftrag: bool = False) -> dict[str, Any]:
    eintrag = {"id": uuid.uuid4().hex[:8], "zeit": zeitpunkt.isoformat(timespec="seconds"), "text": text.strip(),
               "art": art, "wiederholen": wiederholen, "als_auftrag": als_auftrag}
    _speichern(_eintraege() + [eintrag])
    return eintrag


def _uhrzeit(zeit: datetime) -> str:
    heute = datetime.now().date()
    tag = "heute" if zeit.date() == heute else "morgen" if zeit.date() == heute + timedelta(days=1) else \
        zeit.strftime("am %d.%m.")
    return f"{tag} um {zeit.hour}:{zeit.minute:02d} Uhr"


def faellige(eintraege: list[dict[str, Any]], jetzt: datetime) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Teilt in (fällige, verbleibende). Tägliche Einträge werden auf den nächsten Tag verschoben."""
    faellig, rest = [], []
    for e in eintraege:
        zeit = datetime.fromisoformat(e["zeit"])
        if zeit > jetzt:
            rest.append(e)
            continue
        faellig.append({**e, "verspaetet": (jetzt - zeit) > timedelta(minutes=2)})
        if e.get("wiederholen") == "täglich":
            naechste = zeit
            while naechste <= jetzt:
                naechste += timedelta(days=1)
            rest.append({**e, "zeit": naechste.isoformat(timespec="seconds")})
    return faellig, rest


@skill
def timer_stellen(minuten: Annotated[float, "Dauer in Minuten (z. B. 0.5 für 30 Sekunden)"],
                  bezeichnung: Annotated[str, "Wofür, z. B. 'Pizza'"] = "") -> str:
    """Stellt einen Timer. Jarvis sagt Bescheid, wenn er abgelaufen ist."""
    if not 0 < minuten <= 24 * 60:
        return "Ein Timer kann zwischen ein paar Sekunden und 24 Stunden lang sein."
    zeit = datetime.now() + timedelta(minutes=minuten)
    _neu(zeit, bezeichnung or "Timer", art="timer")
    return f"Timer{' für ' + bezeichnung if bezeichnung else ''} läuft bis {_uhrzeit(zeit)}."


@skill
def erinnerung_stellen(
    zeitpunkt: Annotated[str, "Datum und Uhrzeit im Format JJJJ-MM-TTTHH:MM, z. B. 2026-10-14T07:30 "
                              "(Datum/Uhrzeit vorher mit datum_und_uhrzeit klären)"],
    text: Annotated[str, "Woran erinnert werden soll – oder bei als_auftrag die Anweisung an Jarvis"],
    wiederholen: Annotated[str, "'' (einmalig) oder 'täglich'"] = "",
    als_auftrag: Annotated[bool, "true = zum Zeitpunkt führt Jarvis den Text als Auftrag aus (Routine, z. B. "
                                 "'Sag mir das Wetter und meine Termine'), false = einfach erinnern"] = False,
) -> str:
    """Erinnert zu einem Zeitpunkt an etwas oder führt zu einem Zeitpunkt (auch täglich) eine Routine aus."""
    try:
        zeit = datetime.fromisoformat(zeitpunkt)
    except ValueError:
        return "Der Zeitpunkt ist ungültig. Format: 2026-10-14T07:30"
    if zeit.tzinfo is not None:
        zeit = zeit.astimezone().replace(tzinfo=None)
    if zeit <= datetime.now() and wiederholen != "täglich":
        return "Dieser Zeitpunkt liegt in der Vergangenheit."
    if wiederholen not in ("", "täglich"):
        return "wiederholen kann nur leer oder 'täglich' sein."
    _neu(zeit, text, art="auftrag" if als_auftrag else "erinnerung", wiederholen=wiederholen, als_auftrag=als_auftrag)
    return f"{'Routine' if als_auftrag else 'Erinnerung'} gespeichert: {_uhrzeit(zeit)}" + \
        (", jeden Tag" if wiederholen else "") + "."


@skill
def timer_anzeigen() -> str:
    """Listet alle laufenden Timer, Erinnerungen und Routinen auf."""
    eintraege = sorted(_eintraege(), key=lambda e: e["zeit"])
    if not eintraege:
        return "Es laufen keine Timer oder Erinnerungen."
    return "\n".join(f"{e['art']}: {e['text']} – {_uhrzeit(datetime.fromisoformat(e['zeit']))}"
                     + (" (täglich)" if e.get("wiederholen") else "") for e in eintraege)


@skill
def timer_loeschen(bezeichnung: Annotated[str, "Teil des Textes oder 'alle'"]) -> str:
    """Löscht Timer/Erinnerungen, deren Text die Bezeichnung enthält (oder alle)."""
    eintraege = _eintraege()
    if bezeichnung.strip().lower() == "alle":
        _speichern([])
        return f"{len(eintraege)} Einträge gelöscht."
    behalten = [e for e in eintraege if bezeichnung.lower() not in e["text"].lower()]
    _speichern(behalten)
    weg = len(eintraege) - len(behalten)
    return f"{weg} Einträge gelöscht." if weg else f"Nichts zu „{bezeichnung}“ gefunden."


def einmal_pruefen(jetzt: datetime | None = None) -> int:
    """Ein Durchlauf des Dienstes: fällige Einträge auslösen. Gibt die Anzahl zurück (für Tests)."""
    faellig, rest = faellige(_eintraege(), jetzt or datetime.now())
    if not faellig:
        return 0
    _speichern(rest)
    for e in faellig:
        spaet = " (verspätet, ich war gerade nicht aktiv)" if e.get("verspaetet") else ""
        if e.get("als_auftrag"):
            melden(f"Geplante Routine des Nutzers{spaet}: {e['text']}", quelle="Zeitplan")
        elif e["art"] == "timer":
            sagen(f"{anrede()}, der Timer{' für ' + e['text'] if e['text'] != 'Timer' else ''} ist abgelaufen{spaet}.")
        else:
            sagen(f"{anrede()}, Erinnerung{spaet}: {e['text']}")
    return len(faellig)


@dienst
async def timer_wecker() -> None:
    """Prüft jede Sekunde, ob ein Timer oder eine Erinnerung fällig ist."""
    while True:
        einmal_pruefen()
        await asyncio.sleep(1)
