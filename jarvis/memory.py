"""Langzeitgedächtnis: Fakten, die sich Jarvis über dich merkt (data/gedaechtnis.json)."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from jarvis.config import DATA_DIR

MEMORY_FILE = DATA_DIR / "gedaechtnis.json"


def _load(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []


def _save(entries: list[dict[str, str]], path: Path) -> None:
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")


def facts(path: Path = MEMORY_FILE) -> list[str]:
    return [e["fakt"] for e in _load(path)]


def remember(fact: str, path: Path = MEMORY_FILE) -> None:
    entries = _load(path)
    entries.append({"fakt": fact.strip(), "datum": datetime.now().strftime("%Y-%m-%d")})
    _save(entries, path)


def forget(keyword: str, path: Path = MEMORY_FILE) -> list[str]:
    """Löscht alle Fakten, die das Stichwort enthalten. Gibt die gelöschten zurück."""
    entries = _load(path)
    kw = keyword.lower().strip()
    removed = [e["fakt"] for e in entries if kw in e["fakt"].lower()]
    _save([e for e in entries if kw not in e["fakt"].lower()], path)
    return removed
