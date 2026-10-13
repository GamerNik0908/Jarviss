"""Selbsttest: prüft, ob Jarvis nach einer Änderung noch startfähig ist.

Aufruf: python -m jarvis --selbsttest
Wird nach jeder Selbstverbesserung automatisch ausgeführt. Braucht weder Mikrofon
noch Internet noch eine Claude-Anmeldung.
"""

from __future__ import annotations

import importlib
import importlib.util
import json
import re
import subprocess
import sys
import traceback

from jarvis.config import ROOT_DIR

MODULES = [
    "jarvis.config", "jarvis.ui", "jarvis.memory", "jarvis.skills", "jarvis.brain",
    "jarvis.developer", "jarvis.voice.speaker", "jarvis.voice.listener", "jarvis.app",
]
TOOL_NAME = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


def run_selftest() -> int:
    problems: list[str] = []

    def check(name: str, fn) -> object:
        try:
            result = fn()
            print(f"  ✓ {name}")
            return result
        except Exception:  # noqa: BLE001
            problems.append(f"{name}:\n{traceback.format_exc()}")
            print(f"  ✖ {name}")
            return None

    print("Jarvis-Selbsttest")
    from jarvis.config import load_config

    cfg = check("config.toml lesen", load_config)
    for module in MODULES:
        check(f"Modul {module} importieren", lambda m=module: importlib.import_module(m))

    def skills_ok():
        from jarvis.skills import load_skills

        skills, _ = load_skills(strict=True)
        return skills

    skills = check("Fähigkeiten laden", skills_ok) or []

    if cfg is not None:
        def tools_ok():
            from jarvis.app import Jarvis
            from jarvis.brain import Brain

            all_tools = list(skills) + Jarvis(cfg, text_only=True).core_tools()
            names = [t.name for t in all_tools]
            dupes = {n for n in names if names.count(n) > 1}
            if dupes:
                raise ValueError(f"Doppelte Werkzeugnamen: {dupes}")
            for t in all_tools:
                if not TOOL_NAME.match(t.name):
                    raise ValueError(f"Ungültiger Werkzeugname '{t.name}' (nur a-z, A-Z, 0-9, _ und -; keine Umlaute)")
                if not t.beschreibung.strip():
                    raise ValueError(f"Werkzeug '{t.name}' hat keine Beschreibung (Docstring fehlt)")
                json.dumps(t.schema)
            if importlib.util.find_spec("claude_agent_sdk"):
                Brain(cfg, all_tools).build_options()
            return len(all_tools)

        count = check("Werkzeuge für das Gehirn bauen", tools_ok)
        if count:
            print(f"    ({count} Werkzeuge)")

    tests_dir = ROOT_DIR / "tests"
    if tests_dir.is_dir():
        if importlib.util.find_spec("pytest"):
            proc = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", str(tests_dir)],
                                  cwd=ROOT_DIR, capture_output=True, text=True, encoding="utf-8", errors="replace")
            if proc.returncode == 0:
                print("  ✓ Tests (pytest)")
            else:
                print("  ✖ Tests (pytest)")
                problems.append(f"pytest:\n{proc.stdout[-4000:]}\n{proc.stderr[-2000:]}")
        else:
            print("  - Tests übersprungen (pytest nicht installiert)")

    if problems:
        print("\nSELBSTTEST FEHLGESCHLAGEN\n")
        for p in problems:
            print(p)
        return 1
    print("\nSELBSTTEST OK")
    return 0
