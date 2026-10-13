"""Dienstverwaltung: startet die @dienst-Schleifen der Funktionen, überwacht sie und beendet sie sauber.

Verhalten pro Dienst:
  * normales Ende            -> fertig (einmalige Startarbeit ist erlaubt)
  * FehlendeEinrichtung      -> wartet, bis irgendein Einrichtungswert gesetzt wird, dann neuer Versuch
  * jeder andere Fehler      -> protokollieren, nach 5 / 15 / 60 / 300 s neu starten;
                                beim 3. Absturz in Folge EIN Ereignis ans Gehirn (Reparatur anbieten)
"""

from __future__ import annotations

import asyncio
import contextvars
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass

from jarvis import protokoll
from jarvis.einrichtung import FehlendeEinrichtung
from jarvis.skills import Dienst

_log = logging.getLogger("jarvis.dienste")
WARTEZEITEN = (5, 15, 60, 300)


@dataclass
class _Zustand:
    dienst: Dienst
    status: str = "startet"
    abstuerze: int = 0
    gemeldet: bool = False
    task: asyncio.Task[None] | None = None


class Dienstverwaltung:
    def __init__(self, dienste: list[Dienst], melden: Callable[[str, str], None]):
        self._zustaende = {d.name: _Zustand(d) for d in dienste}
        self._melden = melden
        self._generation = 0                 # zählt Einrichtungs-Änderungen
        self._bedingung = asyncio.Condition()

    def starten(self) -> None:
        for z in self._zustaende.values():
            z.task = asyncio.get_running_loop().create_task(self._betreuen(z), name=f"dienst:{z.dienst.name}",
                                                             context=contextvars.Context())

    def einrichtung_geaendert(self) -> None:
        """Aus dem Event-Loop aufrufen: weckt Dienste, die auf eine Einrichtung warten."""
        self._generation += 1
        asyncio.get_running_loop().create_task(self._wecken())

    async def _wecken(self) -> None:
        async with self._bedingung:
            self._bedingung.notify_all()

    async def _betreuen(self, z: _Zustand) -> None:
        while True:
            gestartet = time.monotonic()
            generation = self._generation
            z.status = "läuft"
            try:
                await z.dienst.funktion()
                z.status = "beendet"
                return
            except asyncio.CancelledError:
                z.status = "gestoppt"
                raise
            except FehlendeEinrichtung as e:
                z.status = f"wartet auf Einrichtung: {e.wert.name}"
                async with self._bedingung:
                    await self._bedingung.wait_for(lambda: self._generation != generation)
                continue
            except BaseException as e:  # noqa: BLE001 – auch SystemExit aus Fremdcode darf Jarvis nicht beenden
                if isinstance(e, KeyboardInterrupt):
                    raise
                if time.monotonic() - gestartet > 600:
                    z.abstuerze = 0
                z.abstuerze += 1
                hinweis = protokoll.fehler_merken(z.dienst.gruppe, e, f"Dienst {z.dienst.name}")
                warte = WARTEZEITEN[min(z.abstuerze - 1, len(WARTEZEITEN) - 1)]
                z.status = f"Fehler, neuer Versuch in {warte} s"
                _log.warning("Dienst %s abgestürzt (%d. Mal): %s", z.dienst.name, z.abstuerze, hinweis)
                if z.abstuerze >= 3 and not z.gemeldet:
                    z.gemeldet = True
                    self._melden(f"Der Dienst '{z.dienst.name}' der Funktion '{z.dienst.gruppe}' stürzt wiederholt "
                                 f"ab. Letzter Fehler: {hinweis} Biete dem Nutzer an, das per selbst_verbessern "
                                 f"reparieren zu lassen.", "System")
                await asyncio.sleep(warte)

    def status(self) -> list[str]:
        return [f"{z.dienst.name} ({z.dienst.gruppe}): {z.status}" for z in self._zustaende.values()]

    async def stoppen(self, timeout: float = 5.0) -> None:
        tasks = [z.task for z in self._zustaende.values() if z.task and not z.task.done()]
        for t in tasks:
            t.cancel()
        if tasks:
            await asyncio.wait(tasks, timeout=timeout)
