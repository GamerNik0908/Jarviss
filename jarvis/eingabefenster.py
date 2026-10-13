"""Kleines Eingabefenster für Einrichtungswerte (z. B. Tokens) – damit sie nie gesprochen oder in den Chat getippt werden.

Läuft als eigener Prozess (python -m jarvis.eingabefenster …), gibt den Wert auf stdout aus.
Exit-Codes: 0 = gespeichert, 1 = abgebrochen, 2 = Fenster nicht möglich (kein tkinter / kein Bildschirm).
"""

from __future__ import annotations

import argparse
import asyncio
import sys


async def abfragen(titel: str, text: str, geheim: bool) -> str | None:
    """Öffnet das Fenster und wartet (max. 10 Minuten). None bei Abbruch oder wenn es kein Fenster gibt."""
    argumente = [sys.executable, "-m", "jarvis.eingabefenster", "--titel", titel, "--text", text]
    if geheim:
        argumente.append("--geheim")
    proc = await asyncio.create_subprocess_exec(*argumente, stdout=asyncio.subprocess.PIPE,
                                                stderr=asyncio.subprocess.DEVNULL)
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=600)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        proc.kill()
        return None
    if proc.returncode != 0:
        return None
    wert = out.decode("utf-8", errors="replace").strip()
    return wert or None


def _fenster(titel: str, text: str, geheim: bool) -> int:
    try:
        import tkinter as tk
        root = tk.Tk()
    except Exception:  # noqa: BLE001 – kein tkinter oder kein Bildschirm
        return 2
    ergebnis: list[str] = []
    root.title(titel)
    root.attributes("-topmost", True)
    root.resizable(False, False)
    rahmen = tk.Frame(root, padx=16, pady=12)
    rahmen.pack(fill="both", expand=True)
    tk.Label(rahmen, text=titel.removeprefix("Jarvis – "), font=("Segoe UI", 12, "bold")).pack(anchor="w")
    if text:
        tk.Label(rahmen, text=text, justify="left", wraplength=460).pack(anchor="w", pady=(6, 8))
    eingabe = tk.Entry(rahmen, width=60, show="•" if geheim else "")
    eingabe.pack(fill="x")

    def speichern(_: object = None) -> None:
        ergebnis.append(eingabe.get())
        root.destroy()

    knoepfe = tk.Frame(rahmen, pady=10)
    knoepfe.pack(anchor="e")
    tk.Button(knoepfe, text="Abbrechen", command=root.destroy, width=10).pack(side="right", padx=(6, 0))
    tk.Button(knoepfe, text="Speichern", command=speichern, width=10).pack(side="right")
    root.bind("<Return>", speichern)
    root.bind("<Escape>", lambda _: root.destroy())
    root.after(200, lambda: (root.focus_force(), eingabe.focus_set()))
    root.mainloop()
    if ergebnis and ergebnis[0].strip():
        sys.stdout.write(ergebnis[0].strip())
        sys.stdout.flush()
        return 0
    return 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--titel", default="Jarvis")
    parser.add_argument("--text", default="")
    parser.add_argument("--geheim", action="store_true")
    a = parser.parse_args()
    sys.exit(_fenster(a.titel, a.text, a.geheim))
