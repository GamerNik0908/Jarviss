"""Tests für die Beispiel-Funktion Timer & Erinnerungen."""

from datetime import datetime, timedelta

from jarvis.api import Testumgebung
from jarvis.skills import timer


def test_timer_laeuft_ab(tmp_path):
    with Testumgebung(tmp_path, anrede="Chef") as t:
        assert "läuft bis" in timer.timer_stellen(5, "Pizza")
        assert timer.einmal_pruefen() == 0
        assert timer.einmal_pruefen(datetime.now() + timedelta(minutes=6)) == 1
        assert t.gesagt and "Pizza" in t.gesagt[0] and "Chef" in t.gesagt[0]
        assert timer.timer_anzeigen() == "Es laufen keine Timer oder Erinnerungen."


def test_routine_geht_ans_gehirn_und_wiederholt_sich(tmp_path):
    with Testumgebung(tmp_path) as t:
        morgen = (datetime.now() + timedelta(days=1)).replace(hour=7, minute=0, second=0, microsecond=0)
        antwort = timer.erinnerung_stellen(morgen.isoformat(timespec="minutes"), "Sag mir das Wetter",
                                           wiederholen="täglich", als_auftrag=True)
        assert "Routine" in antwort
        assert timer.einmal_pruefen(morgen + timedelta(seconds=1)) == 1
        assert t.gemeldet == [("Geplante Routine des Nutzers: Sag mir das Wetter", "Zeitplan")]
        assert "täglich" in timer.timer_anzeigen()   # für den nächsten Tag neu geplant


def test_verspaetet_nach_neustart(tmp_path):
    with Testumgebung(tmp_path) as t:
        timer.timer_stellen(1, "Tee")
        timer.einmal_pruefen(datetime.now() + timedelta(minutes=30))
        assert "verspätet" in t.gesagt[0]


def test_ungueltige_eingaben(tmp_path):
    with Testumgebung(tmp_path):
        assert "ungültig" in timer.erinnerung_stellen("morgen früh", "x")
        assert "Vergangenheit" in timer.erinnerung_stellen("2001-01-01T10:00", "x")
        assert "zwischen" in timer.timer_stellen(0)
        timer.timer_stellen(10, "A")
        timer.timer_stellen(10, "B")
        assert "1 Einträge" in timer.timer_loeschen("a")
        assert "1 Einträge" in timer.timer_loeschen("alle")
