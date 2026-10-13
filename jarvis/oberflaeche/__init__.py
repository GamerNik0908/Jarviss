"""HUD-Oberfläche: ein Fenster im Iron-Man-Stil – Gespräch, Jarvis' Zustand, Claude Code und die Projekte.

Läuft als kleiner Webserver NUR auf diesem PC (127.0.0.1) und erscheint als eigenes App-Fenster (Edge/Chrome im
App-Modus) oder als Browser-Tab. Nach einem Neustart verbindet sich ein offenes Fenster von selbst wieder.

Schutz:
  * nur 127.0.0.1, nie im Heimnetz erreichbar
  * zufälliger Zugangsschlüssel (steht nur hinter der Raute der Adresse und wird beim Verbinden geprüft)
  * Host- und Origin-Prüfung (gegen DNS-Rebinding und fremde Webseiten, die sich verbinden wollen)
Eingaben aus dem Fenster zählen wie Eingaben an der Tastatur.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
import shutil
import socket
import subprocess
import sys
import webbrowser
from collections import deque
from pathlib import Path
from typing import Any

from jarvis import config, ui
from jarvis.sicherheit import schwaerzen

_log = logging.getLogger("jarvis.oberflaeche")
SEITE = Path(__file__).resolve().parent / "index.html"
MAX_NACHRICHT = 64 * 1024


def _datei() -> Path:
    return config.DATA_DIR / "oberflaeche.json"


def _freier_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def app_browser() -> str | None:
    """Edge oder Chrome (für ein eigenes Fenster ohne Adressleiste)."""
    kandidaten: list[str | None] = []
    if sys.platform == "win32":
        for basis in (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles"),
                      os.environ.get("LOCALAPPDATA")):
            if basis:
                kandidaten += [str(Path(basis) / "Microsoft" / "Edge" / "Application" / "msedge.exe"),
                               str(Path(basis) / "Google" / "Chrome" / "Application" / "chrome.exe")]
    elif sys.platform == "darwin":
        kandidaten += ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                       "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"]
    else:
        kandidaten += [shutil.which(n) for n in ("google-chrome", "chromium", "chromium-browser", "microsoft-edge")]
    return next((k for k in kandidaten if k and Path(k).is_file()), None)


class Oberflaeche:
    def __init__(self, app: Any, cfg: dict[str, Any]) -> None:
        self.app = app
        self.cfg = cfg["oberflaeche"]
        self.port = int(self.cfg.get("port") or 0)
        self.schluessel = ""
        self.verbunden = asyncio.Event()
        self._clients: set[Any] = set()
        self._verlauf: deque[dict[str, Any]] = deque(maxlen=300)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._runner: Any = None
        self._abmelden: Any = None
        self._status_task: asyncio.Task[None] | None = None
        self._letzter_status = ""
        self._zustand = "bereit"

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/#schluessel={self.schluessel}"

    # --- Start & Stopp ----------------------------------------------------------------------
    async def starten(self) -> None:
        from aiohttp import web

        self._loop = asyncio.get_running_loop()
        self.schluessel = self._schluessel_laden()
        app = web.Application(client_max_size=MAX_NACHRICHT)
        app.router.add_get("/", self._seite)
        app.router.add_get("/ws", self._websocket)
        self._runner = web.AppRunner(app, access_log=None)
        await self._runner.setup()
        self.port = self.port or _freier_port()
        for versuch in range(8):
            try:
                await web.TCPSite(self._runner, "127.0.0.1", self.port).start()   # nur dieser PC, nie im Netz
                break
            except OSError:
                if versuch < 5:          # nach einem Neustart ist der alte Port manchmal noch kurz belegt
                    await asyncio.sleep(0.5)
                else:
                    self.port = _freier_port()
        self._abmelden = ui.beobachten(self._beobachter)
        self._status_task = self._loop.create_task(self._status_schleife(), name="oberflaeche-status")
        ui.info(f"Oberfläche: {self.url}")

    async def stoppen(self) -> None:
        if self._abmelden:
            self._abmelden()
        if self._status_task:
            self._status_task.cancel()
        for ws in list(self._clients):
            try:
                await ws.close()
            except Exception:  # noqa: BLE001
                pass
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None

    def _schluessel_laden(self) -> str:
        """Schlüssel bleibt über Neustarts gleich, damit sich ein offenes Fenster wieder verbindet."""
        try:
            daten = json.loads(_datei().read_text(encoding="utf-8"))
            if isinstance(daten, dict) and len(str(daten.get("schluessel", ""))) >= 24:
                return str(daten["schluessel"])
        except (OSError, json.JSONDecodeError):
            pass
        schluessel = secrets.token_urlsafe(24)
        _datei().parent.mkdir(parents=True, exist_ok=True)
        _datei().write_text(json.dumps({"schluessel": schluessel}), encoding="utf-8")
        return schluessel

    async def oeffnen_falls_noetig(self) -> None:
        """Ein Fenster von vorher verbindet sich nach dem Neustart selbst – sonst eins öffnen."""
        modus = str(self.cfg.get("fenster", "app"))
        if modus == "aus":
            return
        try:
            await asyncio.wait_for(self.verbunden.wait(), timeout=4)
        except asyncio.TimeoutError:
            self.fenster_oeffnen()

    def fenster_oeffnen(self) -> None:
        exe = app_browser() if str(self.cfg.get("fenster", "app")) == "app" else None
        if exe:
            try:
                flags = {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}
                subprocess.Popen([exe, f"--app={self.url}", "--window-size=1400,880"], stdin=subprocess.DEVNULL,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **flags)
                return
            except OSError:
                pass
        webbrowser.open(self.url)

    # --- HTTP ------------------------------------------------------------------------------------
    def _host_ok(self, request: Any) -> bool:
        return request.host in (f"127.0.0.1:{self.port}", f"localhost:{self.port}")

    async def _seite(self, request: Any) -> Any:
        from aiohttp import web

        if not self._host_ok(request):
            return web.Response(status=403, text="Nur auf diesem PC erreichbar.")
        return web.Response(text=SEITE.read_text(encoding="utf-8"), content_type="text/html", charset="utf-8",
                            headers={"Cache-Control": "no-store", "X-Frame-Options": "DENY",
                                     "Referrer-Policy": "no-referrer",
                                     "Content-Security-Policy": "default-src 'none'; script-src 'unsafe-inline'; "
                                     "style-src 'unsafe-inline'; img-src data:; connect-src "
                                     f"ws://127.0.0.1:{self.port} ws://localhost:{self.port}"})

    async def _websocket(self, request: Any) -> Any:
        from aiohttp import WSMsgType, web

        origin = request.headers.get("Origin", "")
        schluessel = request.query.get("schluessel", "")
        if not self._host_ok(request) or origin not in (f"http://127.0.0.1:{self.port}", f"http://localhost:{self.port}") \
                or not secrets.compare_digest(schluessel.encode(), self.schluessel.encode()):
            return web.Response(status=403, text="Kein Zugriff.")
        ws = web.WebSocketResponse(heartbeat=20, max_msg_size=MAX_NACHRICHT)
        await ws.prepare(request)
        self._clients.add(ws)
        self.verbunden.set()
        try:
            await ws.send_str(schwaerzen(json.dumps({"typ": "start", "verlauf": list(self._verlauf),
                                                     "zustand": self._zustand, "status": self._status()},
                                                    ensure_ascii=False)))
            async for msg in ws:
                if msg.type != WSMsgType.TEXT:
                    continue
                try:
                    daten = json.loads(msg.data)
                except json.JSONDecodeError:
                    continue
                if isinstance(daten, dict):
                    try:
                        self.app.oberflaeche_eingabe(daten)
                    except Exception:  # noqa: BLE001 – eine kaputte Eingabe darf das Fenster nicht trennen
                        _log.exception("Eingabe aus der Oberfläche fehlgeschlagen")
        finally:
            self._clients.discard(ws)
        return ws

    # --- Weitergabe ------------------------------------------------------------------------------
    def _beobachter(self, eintrag: dict[str, Any]) -> None:
        """Aus jedem Thread: Konsolenausgabe/Zustand an alle Fenster."""
        if self._loop is None:
            return
        try:
            self._loop.call_soon_threadsafe(self._verteilen, eintrag)
        except RuntimeError:
            pass        # Event-Loop ist schon zu

    def _verteilen(self, eintrag: dict[str, Any]) -> None:
        if eintrag.get("typ") == "log":
            if eintrag.get("art") == "info" and eintrag.get("text") in ("…", ""):
                return
            self._verlauf.append(eintrag)
        elif eintrag.get("typ") == "zustand":
            self._zustand = str(eintrag.get("zustand"))
        if self._clients and self._loop is not None:
            self._loop.create_task(self._an_alle(eintrag))

    async def _an_alle(self, eintrag: dict[str, Any]) -> None:
        text = schwaerzen(json.dumps(eintrag, ensure_ascii=False))
        for ws in list(self._clients):
            try:
                await ws.send_str(text)
            except Exception:  # noqa: BLE001
                self._clients.discard(ws)

    def _status(self) -> dict[str, Any]:
        try:
            return self.app.oberflaeche_status()
        except Exception:  # noqa: BLE001
            _log.exception("Status für die Oberfläche fehlgeschlagen")
            return {}

    async def _status_schleife(self) -> None:
        while True:
            await asyncio.sleep(1)
            if not self._clients:
                continue
            status = self._status()
            text = json.dumps(status, ensure_ascii=False, sort_keys=True)
            if text != self._letzter_status:
                self._letzter_status = text
                await self._an_alle({"typ": "status", "status": status})
