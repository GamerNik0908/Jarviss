"""Einstellungen aus config.toml laden (mit Standardwerten für alles, was fehlt)."""

from __future__ import annotations

import copy
import tomllib
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT_DIR / "data"
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
        "aufwand": "high",
        "max_schritte": 150,
        "berechtigungsmodus": "dontAsk",
        "erlaubte_werkzeuge": [
            "Read", "Write", "Edit", "Glob", "Grep", "Bash",
            "WebSearch", "WebFetch", "TodoWrite",
        ],
        "reparaturversuche": 2,
        "automatisch_neustarten": True,
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
    DATA_DIR.mkdir(exist_ok=True)
    return DATA_DIR
