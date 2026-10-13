"""Tests: Jarvis merkt, was er noch nicht kann, lernt es (Warteschlange) und erledigt danach den ursprünglichen Wunsch."""

import asyncio
import json

import pytest

from jarvis import app as app_modul
from jarvis.app import NACH_UPDATE_FILE, WARTESCHLANGE_FILE, Jarvis, _update_ereignis
from jarvis.brain import build_system_prompt
from jarvis.config import load_config
from jarvis.developer import DevResult, Developer
from jarvis import RESTART_EXIT_CODE


@pytest.fixture
def jarvis_app(monkeypatch):
    WARTESCHLANGE_FILE.unlink(missing_ok=True)
    NACH_UPDATE_FILE.unlink(missing_ok=True)
    app = Jarvis(load_config(), text_only=True)
    gestartet = []
    beschaeftigt = {"wert": False}
    monkeypatch.setattr(Developer, "busy", property(lambda self: beschaeftigt["wert"]))
    monkeypatch.setattr(app.developer, "start",
                        lambda auftrag, fertig, **kw: (gestartet.append((auftrag, kw)), beschaeftigt.update(wert=True)))
    app.gestartet, app.beschaeftigt = gestartet, beschaeftigt
    yield app
    WARTESCHLANGE_FILE.unlink(missing_ok=True)
    NACH_UPDATE_FILE.unlink(missing_ok=True)


def _werkzeug(app, name):
    return {w.name: w for w in app.core_tools()}[name]


def test_zweiter_auftrag_kommt_in_die_warteschlange(jarvis_app):
    lernen = _werkzeug(jarvis_app, "selbst_verbessern")
    text = asyncio.run(lernen.ausfuehren({"auftrag": "Dokumente erstellen und drucken", "kurzfassung": "Drucken",
                                          "danach": "Erstelle ein Dokument über Vulkane und drucke es aus"}))
    assert "übergeben" in text and "ursprünglichen Wunsch" in text and len(jarvis_app.gestartet) == 1
    text = asyncio.run(lernen.ausfuehren({"auftrag": "Spotify steuern", "kurzfassung": "Spotify"}))
    assert "Platz 1" in text and len(jarvis_app.gestartet) == 1
    assert json.loads(WARTESCHLANGE_FILE.read_text(encoding="utf-8"))[0]["titel"] == "Spotify"
    assert "Warteschlange: 1. Spotify" in asyncio.run(_werkzeug(jarvis_app, "entwicklungsstatus").ausfuehren({}))
    # übersteht einen Neustart
    assert [a["titel"] for a in Jarvis(load_config(), text_only=True).warteschlange()] == ["Spotify"]
    assert asyncio.run(_werkzeug(jarvis_app, "auftrag_streichen").ausfuehren({"suche": "spoti"})) == "Gestrichen: Spotify"
    assert jarvis_app.warteschlange() == []


def test_nach_dem_update_wird_der_wunsch_erledigt(jarvis_app):
    jarvis_app.entwicklung_einplanen("Drucken lernen", titel="Drucken", danach="Drucke ein Dokument über Vulkane")
    asyncio.run(jarvis_app._handle_dev_result(DevResult(True, "Ich kann jetzt drucken.", geaendert=True,
                                                         titel="Dokumente drucken")))
    assert jarvis_app.exit_code == RESTART_EXIT_CODE
    gespeichert = json.loads(NACH_UPDATE_FILE.read_text(encoding="utf-8"))
    assert gespeichert["wunsch"] == "Drucke ein Dokument über Vulkane"
    text = _update_ereignis({"art": "update", "titel": "Dokumente drucken", "zusammenfassung": "Neu: Drucken."},
                            gespeichert["wunsch"], {"titel": "Spotify"})
    assert "Drucke ein Dokument über Vulkane" in text and "erledige den Wunsch" in text and "Spotify" in text


def test_fehlschlag_startet_naechsten_auftrag_und_nennt_wunsch(jarvis_app):
    jarvis_app.entwicklung_einplanen("A", titel="A", danach="Mach A")
    jarvis_app.entwicklung_einplanen("B", titel="B")
    assert len(jarvis_app.gestartet) == 1
    jarvis_app.beschaeftigt["wert"] = False
    asyncio.run(jarvis_app._handle_dev_result(DevResult(False, "Geht nicht.")))
    assert [a for a, _ in jarvis_app.gestartet] == ["A", "B"] and jarvis_app.warteschlange() == []
    assert "Mach A" in jarvis_app._ereignisse[0][0] and not NACH_UPDATE_FILE.exists()


def test_neustart_wartet_auf_laufende_projektaufgabe(jarvis_app):
    async def ablauf():
        jarvis_app.loop = asyncio.get_running_loop()
        jarvis_app.projekte.aktuell = {"projekt": "spiel"}
        await jarvis_app._handle_dev_result(DevResult(True, "Fertig.", geaendert=True))
        assert jarvis_app.exit_code is None and jarvis_app._neustart_ausstehend
        assert not jarvis_app.darf_arbeiten()
        jarvis_app.entwicklung_einplanen("C", titel="C")            # neue Aufträge warten bis nach dem Neustart
        assert jarvis_app.warteschlange()[0]["titel"] == "C"
        jarvis_app.projekte.aktuell = None
        jarvis_app.arbeit_beendet()
        assert jarvis_app.exit_code == RESTART_EXIT_CODE and jarvis_app.queue.get_nowait() == ("wecken", None)
        for t in list(jarvis_app.hintergrund):
            t.cancel()

    asyncio.run(ablauf())


def test_lernen_fragt_standardmaessig_nach(monkeypatch):
    assert _werkzeug(Jarvis(load_config(), text_only=True), "selbst_verbessern").nachfragen == "immer"
    cfg = load_config()
    cfg["entwickler"]["ohne_nachfrage"] = True
    assert _werkzeug(Jarvis(cfg, text_only=True), "selbst_verbessern").nachfragen == "wenn_fremd"


def test_system_prompt_erklaert_lernen_und_projekte():
    prompt = build_system_prompt(load_config())
    assert "Das kann" in prompt and "danach" in prompt and "selbst_verbessern" in prompt
    assert "projekt_anlegen" in prompt and "Roblox" in prompt and "Morgen" in prompt


def test_projektfrage_wird_beantwortet(jarvis_app):
    async def ablauf():
        jarvis_app.loop = asyncio.get_running_loop()
        frage = asyncio.get_running_loop().create_task(jarvis_app.projektfrage("Obby", "Welche Farbe?", "Rot"))
        await asyncio.sleep(0)
        assert "Welche Farbe?" in jarvis_app._ereignisse[0][0]
        assert jarvis_app.projektfrage_beantworten("Blau")
        return await frage

    assert asyncio.run(ablauf()) == "Blau"
    assert not jarvis_app.projektfrage_beantworten("x")


def test_verzoegerter_neustart_hat_obergrenze(jarvis_app, monkeypatch):
    monkeypatch.setattr(app_modul, "NEUSTART_HOECHSTENS_WARTEN", 0.01)

    async def ablauf():
        jarvis_app.loop = asyncio.get_running_loop()
        jarvis_app.projekte.aktuell = {"projekt": "spiel"}
        await jarvis_app._handle_dev_result(DevResult(True, "Fertig.", geaendert=True))
        await asyncio.sleep(0.05)

    asyncio.run(ablauf())
    assert jarvis_app.exit_code == RESTART_EXIT_CODE
