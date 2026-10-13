# Jarvis – Hinweise für Claude Code

Jarvis ist ein deutschsprachiger Sprachassistent für den PC des Nutzers (meist Windows). Er verändert sich
selbst: Der Nutzer sagt Jarvis, was er können soll, und Jarvis beauftragt dich (Claude Code) über
`jarvis/developer.py`, das umzusetzen. Du arbeitest also direkt am laufenden Assistenten deines Nutzers.

## Aufbau

| Datei | Aufgabe |
|---|---|
| `start.py` | Starter/Wächter: venv + Pakete, Neustart bei Exit-Code 3, Rollback bei Absturz nach Update. Importiert absichtlich nichts aus `jarvis/`. |
| `jarvis/__main__.py` | `python -m jarvis` (`--text`, `--selbsttest`) |
| `jarvis/app.py` | Hauptschleife: Tastatur + Mikrofon → Gehirn → Sprachausgabe. Kern-Werkzeuge (`selbst_verbessern`, `neu_starten`, …), Aktivierungswort, `/`-Befehle |
| `jarvis/brain.py` | Gespräch über das Claude Agent SDK (`ClaudeSDKClient`), System-Prompt/Persönlichkeit, Fähigkeiten als MCP-Tools |
| `jarvis/developer.py` | Selbstverbesserung: Git-Sicherung → Claude Code → Selbsttest → Commit `Jarvis-Update: …` oder Rollback |
| `jarvis/skills/` | **Fähigkeiten.** Jede `.py`-Datei hier wird automatisch geladen |
| `jarvis/voice/speaker.py` | Sprachausgabe (edge-tts online, pyttsx3 offline) |
| `jarvis/voice/listener.py` | Spracherkennung (SpeechRecognition + Google, Mikrofon über PyAudio) |
| `jarvis/memory.py` | Langzeitgedächtnis (`data/gedaechtnis.json`) |
| `jarvis/selftest.py` | Selbsttest nach jeder Änderung |
| `config.toml` | Einstellungen (Stimme, Modell, Aktivierungswort …). Neue Einstellungen auch in `DEFAULTS` in `jarvis/config.py` eintragen |
| `data/` | Laufzeitdaten (Gedächtnis, Sitzung, Update-Status). Nicht in Git, nicht anfassen |

## Neue Fähigkeit hinzufügen (der häufigste Auftrag)

Neue Datei in `jarvis/skills/` anlegen (oder passende bestehende erweitern):

```python
"""Wetter-Fähigkeiten."""
from typing import Annotated
from jarvis.skills import skill

@skill
def wetter(ort: Annotated[str, "Stadt, z. B. Berlin"]) -> str:
    """Gibt das aktuelle Wetter für einen Ort zurück."""
    ...
    return "In Berlin sind es 18 Grad und sonnig."
```

- Docstring = Beschreibung für das Gehirn (Pflicht). Typ-Hinweise = Parameter-Schema; `Annotated[typ, "Beschreibung"]` für Parameter-Erklärungen; Parameter mit Default sind optional.
- Funktionsname = Werkzeugname: nur `a-z`, `0-9`, `_` (keine Umlaute!), eindeutig.
- Sync oder `async def` ist beides ok. Sync-Funktionen laufen in einem Thread, dürfen also blockieren (aber nicht ewig).
- Rückgabe: kurzer deutscher Text mit dem Ergebnis – das Gehirn formuliert daraus die gesprochene Antwort. Fehler dürfen als Exception geworfen werden; sie gehen als Fehlermeldung ans Gehirn.
- Bevorzuge Lösungen ohne API-Schlüssel (z. B. Open-Meteo fürs Wetter). Braucht etwas zwingend einen Schlüssel, lies ihn aus einer Umgebungsvariable oder `config.toml` und gib eine verständliche Meldung zurück, wenn er fehlt.
- Plattformabhängiges (Programme, Lautstärke, …) für Windows umsetzen und, wo es einfach geht, macOS/Linux mit abdecken.
- Für jede neue Fähigkeit einen kleinen Test in `tests/` ergänzen, der ohne Internet/Mikrofon läuft (Netzwerkzugriffe mocken).

## Regeln

- Alles, was der Nutzer hört oder liest, ist **Deutsch**. Code-Kommentare ebenfalls auf Deutsch, Bezeichner wie im umgebenden Code.
- Jarvis' Antworten werden vorgelesen → Persönlichkeit/Stil im System-Prompt in `jarvis/brain.py` (`build_system_prompt`).
- Neue Pakete: mit dem Interpreter installieren, den der Auftrag nennt (`"<python>" -m pip install …`), **und** in `requirements.txt` eintragen.
- Am Ende immer `python -m jarvis --selbsttest` (mit dem genannten Interpreter) ausführen – er muss `SELBSTTEST OK` melden.
- Nicht selbst committen, nicht `git reset`/`git checkout` ausführen und Jarvis nicht normal starten (er läuft bereits). Commit, Neustart und Rollback übernimmt `developer.py`.
- Vorsicht bei `start.py`, `jarvis/developer.py`, `jarvis/selftest.py` und der Neustart-Logik in `app.py`: Sie sind das Sicherheitsnetz. Nur ändern, wenn der Auftrag es wirklich verlangt, und dann besonders sorgfältig.
- Keine Geheimnisse (API-Schlüssel, Passwörter) in Dateien schreiben, die in Git landen.
- Bleib beim Auftrag. Kleine, saubere Änderungen statt großer Umbauten.
