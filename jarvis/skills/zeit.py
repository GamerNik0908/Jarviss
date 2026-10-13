"""Datum & Uhrzeit"""

from datetime import datetime

from jarvis.api import skill

_WOCHENTAGE = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]
_MONATE = ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli",
           "August", "September", "Oktober", "November", "Dezember"]


@skill
def datum_und_uhrzeit() -> str:
    """Gibt das aktuelle Datum, den Wochentag und die Uhrzeit zurück."""
    now = datetime.now()
    return (f"{_WOCHENTAGE[now.weekday()]}, {now.day}. {_MONATE[now.month - 1]} {now.year}, "
            f"{now.hour}:{now.minute:02d} Uhr")
