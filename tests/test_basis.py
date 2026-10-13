"""Grundlegende Tests. Laufen ohne Mikrofon, Internet und Claude-Anmeldung."""

import asyncio
from typing import Annotated

import pytest

from jarvis import memory
from jarvis.app import Jarvis, extract_command
from jarvis.config import DEFAULTS, load_config
from jarvis.skills import load_skills, make_skill, schema_from_function
from jarvis.voice.speaker import clean_for_speech

WAKE = ["jarvis", "jervis"]


@pytest.mark.parametrize("heard, expected", [
    ("Jarvis wie spät ist es", "wie spät ist es"),
    ("Hey Jarvis, mach Musik an", "mach Musik an"),
    ("wie wird das Wetter Jarvis", "wie wird das Wetter"),
    ("Jarvis", ""),
    ("Jervis öffne YouTube", "öffne YouTube"),
    ("wie spät ist es", None),
])
def test_extract_command(heard, expected):
    assert extract_command(heard, WAKE) == expected


def test_config_defaults_and_file():
    cfg = load_config()
    for section in DEFAULTS:
        assert section in cfg
    assert cfg["zuhoeren"]["aktivierungswoerter"]


def test_schema_from_function():
    def f(text: Annotated[str, "Ein Text"], anzahl: int = 2, laut: bool = False) -> str:
        """Test."""
        return text * anzahl

    schema = schema_from_function(f)
    assert schema["required"] == ["text"]
    assert schema["properties"]["text"] == {"type": "string", "description": "Ein Text"}
    assert schema["properties"]["anzahl"]["type"] == "integer"
    assert schema["properties"]["laut"]["default"] is False


def test_skills_load_and_run():
    skills, errors = load_skills(strict=True)
    assert not errors
    by_name = {s.name: s for s in skills}
    assert "datum_und_uhrzeit" in by_name
    assert "Uhr" in asyncio.run(by_name["datum_und_uhrzeit"].ausfuehren({}))


def test_async_skill():
    async def hallo(name: str) -> str:
        """Begrüßt."""
        return f"Hallo {name}"

    assert asyncio.run(make_skill(hallo).ausfuehren({"name": "Tony"})) == "Hallo Tony"


def test_core_tools_exist():
    names = {t.name for t in Jarvis(load_config(), text_only=True).core_tools()}
    assert {"selbst_verbessern", "letzte_aenderung_rueckgaengig", "aenderungsverlauf"} <= names


def test_memory(tmp_path):
    f = tmp_path / "m.json"
    memory.remember("Lieblingsfarbe ist Blau", f)
    memory.remember("Hat einen Hund namens Bello", f)
    assert memory.facts(f) == ["Lieblingsfarbe ist Blau", "Hat einen Hund namens Bello"]
    assert memory.forget("hund", f) == ["Hat einen Hund namens Bello"]
    assert memory.facts(f) == ["Lieblingsfarbe ist Blau"]


def test_clean_for_speech():
    assert clean_for_speech("**Fett** und [Link](https://x.de) 🚀") == "Fett und Link"
    assert clean_for_speech("Siehe https://www.example.com/pfad") == "Siehe example.com"
