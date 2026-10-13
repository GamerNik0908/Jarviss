"""Gedächtnis: sich Dinge über den Nutzer dauerhaft merken"""

from typing import Annotated

from jarvis import memory
from jarvis.api import skill


@skill(nachfragen="wenn_fremd", frage="Soll ich mir merken: {fakt}?")
def merken(fakt: Annotated[str, "Der Fakt als kurzer, eigenständiger Satz, z. B. 'Lieblingsfarbe ist Blau'"]) -> str:
    """Merkt sich dauerhaft etwas über den Nutzer oder seine Wünsche (bleibt auch nach Neustarts erhalten).
    Nutze das, wenn der Nutzer sagt "merk dir ..." oder wenn du etwas Wichtiges über ihn erfährst."""
    memory.remember(fakt)
    return f"Gemerkt: {fakt}"


@skill(nachfragen="wenn_fremd", frage="Soll ich alles zu „{stichwort}“ vergessen?")
def vergessen(stichwort: Annotated[str, "Stichwort, das im zu löschenden Fakt vorkommt"]) -> str:
    """Löscht gemerkte Fakten, die das Stichwort enthalten."""
    removed = memory.forget(stichwort)
    if not removed:
        return f"Nichts zu '{stichwort}' gefunden."
    return "Vergessen: " + "; ".join(removed)


@skill
def gedaechtnis_anzeigen() -> str:
    """Listet alles auf, was sich Jarvis gemerkt hat."""
    items = memory.facts()
    return "\n".join(f"- {f}" for f in items) if items else "Ich habe mir noch nichts gemerkt."
