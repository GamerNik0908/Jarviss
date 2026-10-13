"""Tests für den Selbstverbesserungs-Ablauf (ohne echtes Claude Code)."""

import asyncio
from types import SimpleNamespace

import pytest

from jarvis import developer
from jarvis.config import load_config
from jarvis.developer import DevResult, DevStatus, Developer, _ergebnis_lesen
from jarvis.selftest import blockierende_aufrufe


def test_strukturiertes_ergebnis_und_rueckfall():
    r = _ergebnis_lesen({"erfolg": True, "titel": "Telegram", "zusammenfassung": "Jetzt geht Telegram.",
                         "annahmen": ["Bot heißt Jarvis"]}, '{"erfolg": true}')
    assert r.titel == "Telegram" and r.annahmen == ["Bot heißt Jarvis"]
    # Ohne strukturiertes Ergebnis darf kein JSON vorgelesen werden
    assert _ergebnis_lesen(None, '{"erfolg": true}').zusammenfassung == "Erledigt."
    assert _ergebnis_lesen(None, "Ich habe X gebaut.").zusammenfassung == "Ich habe X gebaut."


def test_status_beschreibung():
    s = DevStatus("Baue Telegram ein", "Telegram-Anbindung", "gross")
    s.aufgaben = [["Recherche", "completed"], ["Dienst schreiben", "in_progress"], ["Tests", "pending"]]
    s.letzte_aktion = "bearbeitet jarvis/skills/telegram.py"
    text = s.beschreiben()
    assert "Telegram-Anbindung" in text and "Schritt 2 von 3: Dienst schreiben" in text
    s.frage = "Welches Handy?"
    assert "wartet auf deine Antwort" in s.beschreiben()


def _msg(**kw):
    basis = dict(is_error=False, subtype="success", result="", structured_output=None, api_error_status=None)
    basis.update(kw)
    return SimpleNamespace(**basis)


def test_ergebnis_einordnen():
    d = Developer(load_config())
    assert d._auswerten(_msg(structured_output={"erfolg": True, "titel": "x", "zusammenfassung": "y"}), "s").ok
    assert d._auswerten(_msg(is_error=True, subtype="error_max_turns"), "s").art == "max_turns"
    assert d._auswerten(_msg(is_error=True, api_error_status=529, result="Overloaded"), "s").art == "voruebergehend"
    assert d._auswerten(_msg(is_error=True, result="Claude usage limit reached"), "s").art == "limit"
    assert d._auswerten(_msg(is_error=True, result="kaputt"), "s").art == "fehler"


def test_sitzung_macht_nach_aussetzer_weiter(monkeypatch):
    d = Developer(load_config())
    laeufe = [developer._Lauf(False, "sitzung-1", "voruebergehend", "529"),
              developer._Lauf(True, "sitzung-1", ergebnis=DevResult(True, "fertig"))]
    prompts = []

    async def fake_run(prompt, resume, profil):
        prompts.append((prompt, resume))
        return laeufe.pop(0)

    async def kein_schlaf(_):
        return None

    monkeypatch.setattr(d, "_run_claude", fake_run)
    monkeypatch.setattr(developer.asyncio, "sleep", kein_schlaf)
    lauf = asyncio.run(d._sitzung("Baue X", None, d._profil("klein")))
    assert lauf.ok and prompts[1][1] == "sitzung-1" and "weiter" in prompts[1][0]


def test_sitzung_schliesst_am_schrittlimit_ab(monkeypatch):
    d = Developer(load_config())
    laeufe = [developer._Lauf(False, "s", "max_turns"), developer._Lauf(True, "s", ergebnis=DevResult(True, "ok"))]
    profile = []

    async def fake_run(prompt, resume, profil):
        profile.append(profil["max_schritte"])
        return laeufe.pop(0)

    monkeypatch.setattr(d, "_run_claude", fake_run)
    assert asyncio.run(d._sitzung("Baue X", None, d._profil("gross"))).ok
    assert profile == [d._profil("gross")["max_schritte"], 40]


class _FakeGit:
    def __init__(self):
        self.resets = []

    def available(self):
        return True

    def ensure_repo(self):
        pass

    def commit_all(self, msg):
        return False

    def head(self):
        return "abc1234"

    def reset_hard(self, rev):
        self.resets.append(rev)


def _haengender_developer(monkeypatch):
    d = Developer(load_config())
    d.git = _FakeGit()
    monkeypatch.setattr(developer, "pakete_stand", lambda: {})
    monkeypatch.setattr(developer, "pakete_wiederherstellen", lambda vorher: [])

    async def haengt(*args):
        await asyncio.sleep(3600)

    monkeypatch.setattr(d, "_develop", haengt)
    return d


def test_abbrechen_setzt_zurueck_und_meldet(monkeypatch):
    d = _haengender_developer(monkeypatch)
    ergebnisse = []

    async def fertig(r):
        ergebnisse.append(r)

    async def ablauf():
        d.start("Baue etwas Großes", fertig, umfang="gross")
        await asyncio.sleep(0.05)
        assert developer.IN_PROGRESS_FILE.exists()
        await d.abbrechen()
        await asyncio.sleep(0.05)

    asyncio.run(ablauf())
    assert d.git.resets == ["abc1234"] and ergebnisse and ergebnisse[0].abgebrochen
    assert not developer.IN_PROGRESS_FILE.exists()


def test_beenden_waehrend_entwicklung_setzt_zurueck(monkeypatch):
    """Beim Beenden von Jarvis (Task wird abgebrochen) darf nichts Halbfertiges übrig bleiben."""
    d = _haengender_developer(monkeypatch)

    async def fertig(r):
        pass

    async def ablauf():
        d.start("Baue etwas", fertig)
        await asyncio.sleep(0.05)
        d._task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await d._task

    asyncio.run(ablauf())
    assert d.git.resets == ["abc1234"]
    assert developer.IN_PROGRESS_FILE.exists()   # start.py stellt beim nächsten Start auch die Pakete wieder her
    developer.IN_PROGRESS_FILE.unlink()


def test_geheimnis_fragen_werden_abgefangen():
    d = Developer(load_config())
    d.frage_stellen = None
    antwort = asyncio.run(d._nutzer_fragen("Wie lautet dein Telegram-Token?", ""))
    assert "nie gefragt" in antwort


def test_blockierende_aufrufe_werden_gefunden():
    code = '''
import time, asyncio, requests
async def dienst():
    time.sleep(1)
    await asyncio.sleep(1)
    requests.get("x")
    def innen():
        time.sleep(1)
    await asyncio.to_thread(time.sleep, 1)
def normal():
    time.sleep(1)
'''
    funde = blockierende_aufrufe(code, "x.py")
    assert len(funde) == 2 and "x.py:4" in funde[0] and "requests.get" in funde[1]
