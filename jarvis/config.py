"""Einstellungen aus config.toml laden (mit Standardwerten für alles, was fehlt)."""

from __future__ import annotations

import copy
import os
import tomllib
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parent.parent
# Laufzeitdaten. Selbsttest und Tests lenken das per Umgebungsvariable in einen leeren Temp-Ordner um.
DATA_DIR = Path(os.environ.get("JARVIS_DATA_DIR") or ROOT_DIR / "data")
CONFIG_FILE = ROOT_DIR / "config.toml"

DEFAULTS: dict[str, Any] = {
    "nutzer": {
        "name": "",
        "anrede": "Sir",
    },
    "gehirn": {
        "modell": "claude-opus-5-5",
        "aufwand": "low",
        "gespraech_fortsetzen": True,
        "websuche": True,
    },
    "entwickler": {
        "modell": "claude-opus-5-5",
        # "klein": Einstellungen, kleine Fähigkeiten, Reparaturen
        "aufwand": "high",
        "max_schritte": 150,
        "max_minuten": 25,
        # "gross": neue Funktionen mit Diensten, Geräten, Konten
        "aufwand_gross": "xhigh",
        "max_schritte_gross": 400,
        "max_minuten_gross": 75,
        "reparaturversuche": 2,
        "automatisch_neustarten": True,
        "frage_timeout_minuten": 10,
        "max_fragen": 3,
    },
    "sprechen": {
        "engine": "edge",
        "stimme": "de-DE-ConradNeural",
        "tempo": "+8%",
    },
    "zuhoeren": {
        "modus": "aktivierungswort",
        "aktivierungswoerter": ["jarvis"],
        "sprache": "de-DE",
        "nachfrage_sekunden": 10,
        "mikrofon_index": -1,
        "pause_sekunden": 0.8,
    },
    "ordner": {
        "eingang": "",   # leer = <Benutzerordner>/Jarvis/Eingang
    },
}


def _merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge(result[key], value)
        else:
            result[key] = value
    return result


def load_config(path: Path = CONFIG_FILE) -> dict[str, Any]:
    """Lädt config.toml. Fehlende Werte werden mit DEFAULTS aufgefüllt."""
    if not path.exists():
        return copy.deepcopy(DEFAULTS)
    with path.open("rb") as f:
        return _merge(DEFAULTS, tomllib.load(f))


def ensure_data_dir() -> Path:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return DATA_DIR
