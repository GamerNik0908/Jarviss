"""Tests für die HUD-Oberfläche: nur auf diesem PC, nur mit Schlüssel, Eingaben zählen wie die Tastatur."""

import asyncio
import json

import aiohttp
import pytest

from jarvis import ui
from jarvis.app import Jarvis
from jarvis.config import load_config
from jarvis.oberflaeche import Oberflaeche


class FakeApp:
    def __init__(self):
        self.eingaben = []

    def oberflaeche_status(self):
        return {"zustand": "bereit", "projekte": [], "funktionen": []}

    def oberflaeche_eingabe(self, daten):
        self.eingaben.append(daten)


def _cfg():
    cfg = load_config()
    cfg["oberflaeche"].update(port=0, fenster="aus")
    return cfg


async def _mit_server(pruefung):
    app = FakeApp()
    o = Oberflaeche(app, _cfg())
    await o.starten()
    try:
        async with aiohttp.ClientSession() as sitzung:
            await pruefung(o, app, sitzung)
    finally:
        await o.stoppen()


def _ws_url(o, schluessel=None):
    return f"http://127.0.0.1:{o.port}/ws?schluessel={schluessel if schluessel is not None else o.schluessel}"


def test_fenster_bekommt_verlauf_meldungen_und_schickt_eingaben():
    async def pruefung(o, app, sitzung):
        ui.jarvis("Guten Abend, Sir.")
        await asyncio.sleep(0.05)
        async with sitzung.ws_connect(_ws_url(o), origin=f"http://127.0.0.1:{o.port}") as ws:
            start = json.loads((await ws.receive(timeout=3)).data)
            assert start["typ"] == "start" and start["status"]["zustand"] == "bereit"
            assert any(e["text"] == "Guten Abend, Sir." for e in start["verlauf"])
            ui.user("Wie spät ist es?")
            ui.zustand("denkt")
            nachrichten = [json.loads((await ws.receive(timeout=3)).data) for _ in range(2)]
            assert nachrichten[0]["art"] == "user" and nachrichten[1] == {"typ": "zustand", "zustand": "denkt"}
            await ws.send_str(json.dumps({"typ": "eingabe", "text": "Hallo Jarvis"}))
            await ws.send_str("kein json")
            for _ in range(50):
                if app.eingaben:
                    break
                await asyncio.sleep(0.02)
            assert app.eingaben == [{"typ": "eingabe", "text": "Hallo Jarvis"}]
        assert o.verbunden.is_set()

    asyncio.run(_mit_server(pruefung))


def test_ohne_schluessel_oder_von_fremder_seite_kein_zugriff():
    async def pruefung(o, app, sitzung):
        for url, origin in ((_ws_url(o, "falsch"), f"http://127.0.0.1:{o.port}"),
                            (_ws_url(o), "https://boese-seite.example"),
                            (_ws_url(o), None)):
            with pytest.raises(aiohttp.WSServerHandshakeError):
                await sitzung.ws_connect(url, origin=origin)
        async with sitzung.get(f"http://127.0.0.1:{o.port}/") as antwort:
            assert antwort.status == 200 and "J.A.R.V.I.S." in await antwort.text()
            assert "default-src 'none'" in antwort.headers["Content-Security-Policy"]
        async with sitzung.get(f"http://127.0.0.1:{o.port}/", headers={"Host": "boese.example"}) as antwort:
            assert antwort.status == 403                                  # Schutz gegen DNS-Rebinding

    asyncio.run(_mit_server(pruefung))


def test_schluessel_bleibt_ueber_neustarts_gleich():
    a, b = Oberflaeche(FakeApp(), _cfg()), Oberflaeche(FakeApp(), _cfg())
    assert a._schluessel_laden() == b._schluessel_laden() and len(a._schluessel_laden()) >= 24


def test_eingaben_aus_dem_fenster_zaehlen_wie_tastatur(tmp_path, monkeypatch):
    from jarvis import config

    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    app = Jarvis(load_config(), text_only=True)

    async def ablauf():
        app.loop = asyncio.get_running_loop()
        app.oberflaeche_eingabe({"typ": "eingabe", "text": "Hallo"})
        app.oberflaeche_eingabe({"typ": "eingabe", "text": "/status"})
        app.oberflaeche_eingabe({"typ": "eingabe", "text": "/einrichten telegram_token geheim-123"})
        app.oberflaeche_eingabe({"typ": "eingabe", "text": "   "})
        app.oberflaeche_eingabe({"typ": "unbekannt"})
        return [app.queue.get_nowait() for _ in range(app.queue.qsize())]

    assert asyncio.run(ablauf()) == [("eingabe", "Hallo"), ("befehl", "/status"),
                                     ("einrichten", ("telegram_token", "geheim-123"))]
    p = app.projekte.anlegen("Obby", "Parkour", "roblox", ordner=str(tmp_path / "obby"))
    app.oberflaeche_eingabe({"typ": "projekt_aufgabe", "projekt": "obby", "text": "Baue einen Shop", "wichtig": True})
    assert p.offene()[0].titel == "Baue einen Shop" and p.offene()[0].prioritaet == 1
    status = app.oberflaeche_status()
    json.dumps(status)
    assert status["projekte"][0]["titel"] == "Obby" and status["entwickler"] is None
