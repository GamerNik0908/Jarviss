"""Tests für die Funktions-Schnittstelle (jarvis.api), Einrichtung und Dienstverwaltung."""

import asyncio

import pytest

from jarvis import api, dienste, einrichtung
from jarvis.api import FehlendeEinrichtung, Geheimnis, Programm, Speicher, Testumgebung
from jarvis.skills import Dienst


def test_einrichtungswert_lebenszyklus(tmp_path, monkeypatch):
    with Testumgebung(tmp_path):
        w = einrichtung.Einrichtungswert("apitest_adresse", "Adresse des Testgeräts", sprachlich=True,
                                         anleitung=["Am Gerät nachsehen."])
        assert not w.vorhanden
        with pytest.raises(FehlendeEinrichtung) as fehler:
            w.wert()
        assert "Adresse des Testgeräts" in str(fehler.value) and "Am Gerät nachsehen" in str(fehler.value)
        w.setzen("192.168.1.50")
        assert w.wert() == "192.168.1.50" and "192.168.1.50" in einrichtung.status_text()
        w.loeschen()
        monkeypatch.setenv("JARVIS_APITEST_ADRESSE", "10.0.0.1")
        assert w.wert() == "10.0.0.1"
        assert w.funktion == "kern"  # deklariert außerhalb von jarvis/skills


def test_geheimnis_wird_nie_angezeigt(tmp_path):
    with Testumgebung(tmp_path, einrichtung={"apitest_geheim": "streng-geheim-123"}):
        g = Geheimnis("apitest_geheim", "Geheimer Testwert", anleitung="-")
        assert g.wert() == "streng-geheim-123"
        assert "streng-geheim-123" not in einrichtung.status_text()
        assert "streng-geheim-123" in einrichtung.geheime_werte()
    with pytest.raises(ValueError):
        einrichtung.Einrichtungswert("apitest_falsch", "x", geheim=True, sprachlich=True)


def test_programm_wird_gesucht(tmp_path):
    with Testumgebung(tmp_path):
        p = Programm("apitest_slicer", "Test-Slicer", finden=lambda: None, anleitung="Installieren.")
        assert not p.vorhanden
        with pytest.raises(FehlendeEinrichtung):
            p.pfad()
        exe = tmp_path / "slicer.exe"
        exe.write_text("x")
        p.setzen(str(exe))
        assert p.pfad() == str(exe)


def test_doppelte_namen_verboten():
    einrichtung.Einrichtungswert("apitest_doppelt", "x", anleitung="-")
    with pytest.raises(ValueError):
        _andere_datei_deklariert("apitest_doppelt")


def _andere_datei_deklariert(name):
    # simuliert eine Deklaration aus einem anderen Modul
    ns = {"__name__": "jarvis.skills.anderes", "einrichtung": einrichtung}
    exec(f"einrichtung.Einrichtungswert({name!r}, 'y', anleitung='-')", ns)


def test_testumgebung_zeichnet_auf(tmp_path):
    with Testumgebung(tmp_path, anrede="Chef") as t:
        api.sagen("Hallo")
        api.melden("Datei angekommen", quelle="Test")
        api.abspielen(tmp_path / "ton.mp3")
        assert t.gesagt == ["Hallo"] and t.gemeldet == [("Datei angekommen", "Test")]
        assert t.gespielt == [tmp_path / "ton.mp3"] and api.anrede() == "Chef"
        assert api.daten_ordner("abc") == tmp_path / "daten" / "abc"


def test_speicher_uebersteht_neue_instanz(tmp_path):
    with Testumgebung(tmp_path):
        s = Speicher("speichertest")
        s.schreiben("offset", 17)
        assert Speicher("speichertest").lesen("offset") == 17
        s.loeschen("offset")
        assert s.lesen("offset", 0) == 0


def test_eingang_speichern(tmp_path):
    with Testumgebung(tmp_path):
        pfad = api.eingang_speichern("../../böse:name.stl", b"solid x")
        assert pfad.parent == api.eingang_ordner() and pfad.suffix == ".stl" and ".." not in pfad.name
        zweite = api.eingang_speichern("böse:name.stl", b"solid y")
        assert zweite != pfad
        with pytest.raises(ValueError):
            api.eingang_speichern("virus.exe", b"MZ")
        with pytest.raises(ValueError):
            api.eingang_speichern("bild.png", b"x", erlaubte_endungen={".stl"})


def test_im_hintergrund(tmp_path):
    ergebnisse = []

    async def arbeit():
        await asyncio.sleep(0)
        ergebnisse.append("fertig")

    async def ablauf(t):
        api.im_hintergrund(arbeit(), name="test")
        api.im_hintergrund(lambda: ergebnisse.append("thread"), name="sync")
        api.im_hintergrund(lambda: 1 / 0, name="kaputt")
        await t.hintergrund_abwarten()

    with Testumgebung(tmp_path) as t:
        asyncio.run(ablauf(t))
        assert sorted(ergebnisse) == ["fertig", "thread"]
        assert any("kaputt" in text for text, _ in t.gemeldet)


def test_denken_in_testumgebung(tmp_path):
    with Testumgebung(tmp_path, denken=lambda anweisung, daten: {"titel": daten.upper()}):
        assert asyncio.run(api.denken("Titel?", daten="benchy", schema={"type": "object"})) == {"titel": "BENCHY"}


def test_kanal_eingabe_nicht_in_faehigkeit(tmp_path):
    async def ablauf():
        token = api.IN_FAEHIGKEIT.set("test")
        try:
            with pytest.raises(RuntimeError):
                await api.kanal_eingabe("x", kanal="k", absender="1", antworten=lambda t: asyncio.sleep(0))
        finally:
            api.IN_FAEHIGKEIT.reset(token)

    with Testumgebung(tmp_path):
        asyncio.run(ablauf())


# --- Dienstverwaltung -------------------------------------------------------------------------------

def test_dienst_neustart_nach_absturz(monkeypatch):
    monkeypatch.setattr(dienste, "WARTEZEITEN", (0.01,))
    laeufe = []

    async def wackelig():
        """Test."""
        laeufe.append(1)
        if len(laeufe) < 4:
            raise RuntimeError("kaputt")

    gemeldet = []

    async def ablauf():
        v = dienste.Dienstverwaltung([Dienst("wackelig", wackelig)], lambda t, q: gemeldet.append(t))
        v.starten()
        for _ in range(100):
            await asyncio.sleep(0.01)
            if len(laeufe) >= 4:
                break
        await v.stoppen()
        return v.status()

    status = asyncio.run(ablauf())
    assert len(laeufe) == 4 and "beendet" in status[0]
    assert len(gemeldet) == 1 and "wackelig" in gemeldet[0]


def test_dienst_wartet_auf_einrichtung(tmp_path):
    with Testumgebung(tmp_path):
        wert = einrichtung.Einrichtungswert("diensttest_token", "Token", anleitung="-")
        gestartet = []

        async def braucht_token():
            """Test."""
            gestartet.append(wert.wert())

        async def ablauf():
            v = dienste.Dienstverwaltung([Dienst("braucht_token", braucht_token)], lambda t, q: None)
            v.starten()
            await asyncio.sleep(0.05)
            assert "wartet auf Einrichtung" in v.status()[0] and not gestartet
            wert.setzen("abc")
            v.einrichtung_geaendert()
            await asyncio.sleep(0.05)
            await v.stoppen()

        asyncio.run(ablauf())
        assert gestartet == ["abc"]


def test_dienst_stoppen_bricht_ab():
    async def endlos():
        """Test."""
        while True:
            await asyncio.sleep(1)

    async def ablauf():
        v = dienste.Dienstverwaltung([Dienst("endlos", endlos)], lambda t, q: None)
        v.starten()
        await asyncio.sleep(0.01)
        await v.stoppen(1)
        return v.status()

    assert "gestoppt" in asyncio.run(ablauf())[0]
