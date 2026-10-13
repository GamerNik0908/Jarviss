# J.A.R.V.I.S.

Dein persönlicher Sprachassistent für den PC – und das Besondere: **Jarvis verbessert sich selbst.**
Du sagst ihm einfach, was er können soll („Jarvis, lern mal das Wetter abzufragen“), und er schreibt
mit Claude Code seinen eigenen Programmcode um, testet ihn und startet sich mit der neuen Fähigkeit neu.

Das hier ist die **Basis**. Alles Weitere bringst du ihm im Gespräch bei.

## Was er von Anfang an kann

- **Reden:** Du sprichst ins Mikrofon („Jarvis, …“), er antwortet mit Stimme. Tippen geht auch.
- **Denken:** Claude als Gehirn – mit Persönlichkeit à la Iron Man, Websuche und Gesprächsgedächtnis.
- **Sich selbst verändern:** neue Fähigkeiten, anderes Verhalten, andere Stimme … per Sprachbefehl.
- **Sicherheitsnetz:** Jede Änderung wird per Git gesichert und getestet. Geht etwas kaputt, setzt er sich selbst zurück.
- Ein paar Grundfähigkeiten: Uhrzeit/Datum, Webseiten und Programme öffnen, Systeminfos, sich Dinge merken.

## Installation (Windows)

1. **Python 3.11 bis 3.13** installieren: <https://www.python.org/downloads/> – beim Installieren
   den Haken bei **„Add python.exe to PATH“** setzen.
2. **Git** installieren: <https://git-scm.com/download/win> (Standardeinstellungen reichen).
3. **Claude Code** installieren und einmal anmelden. In PowerShell:
   ```powershell
   irm https://claude.ai/install.ps1 | iex
   claude
   ```
   Beim ersten Start von `claude` meldest du dich mit deinem Claude-Konto an (Pro/Max-Abo) und kannst
   es danach mit `/exit` wieder schließen. Alternativ funktioniert auch ein API-Schlüssel in der
   Umgebungsvariable `ANTHROPIC_API_KEY`.
4. Dieses Repository herunterladen:
   ```powershell
   git clone https://github.com/GamerNik0908/Jarviss.git
   ```
5. Im Ordner **`Jarvis starten.bat`** doppelklicken. Beim ersten Start werden alle Pakete installiert
   (dauert ein paar Minuten), danach begrüßt dich Jarvis.

> Linux/macOS: `./start.sh` (bzw. `python3 start.py`). Fürs Mikrofon wird PortAudio gebraucht
> (`sudo apt install portaudio19-dev` bzw. `brew install portaudio`).

## Benutzung

- **Sprechen:** „**Jarvis**, wie spät ist es?“ – Nach seiner Antwort kannst du ca. 10 Sekunden lang
  ohne „Jarvis“ weiterreden.
- **Tippen:** Einfach ins Fenster schreiben und Enter drücken.
- **Befehle** (tippen):

| Befehl | Wirkung |
|---|---|
| `/hilfe` | alle Befehle |
| `/verbessern <Auftrag>` | Auftrag direkt an Claude Code geben |
| `/verlauf` | letzte Selbst-Änderungen |
| `/rueckgaengig` | letzte Selbst-Änderung zurücknehmen |
| `/neu` | neues Gespräch (Gedächtnis bleibt) |
| `/stumm` | Sprachausgabe an/aus |
| `/mikrofone` | Mikrofone auflisten |
| `/neustart`, `/beenden` | neu starten / beenden |

## Jarvis verbessern lassen

Sag ihm einfach, was du willst, zum Beispiel:

- „Jarvis, lern, mir das Wetter für meine Stadt zu sagen.“
- „Jarvis, ich will einen Timer stellen können.“
- „Jarvis, sprich ein bisschen langsamer und nenn mich Chef.“
- „Jarvis, du sollst Spotify steuern können.“
- „Jarvis, mach dir eine Oberfläche mit einem leuchtenden Kreis, wenn du sprichst.“
- „Jarvis, das Öffnen von Programmen klappt nicht richtig, repariere das.“

Was dann passiert:

1. Jarvis formuliert einen genauen Auftrag und gibt ihn an **Claude Code** weiter. Das läuft im Hintergrund
   (meist ein paar Minuten) – du kannst währenddessen weiter mit ihm reden. Im Fenster siehst du, woran
   Claude Code gerade arbeitet.
2. Vorher wird der aktuelle Stand per **Git** gesichert.
3. Danach läuft ein **Selbsttest**. Schlägt er fehl, darf Claude Code nachbessern. Klappt es gar nicht,
   wird alles zurückgesetzt.
4. Bei Erfolg wird die Änderung gespeichert (Commit „Jarvis-Update: …“), Jarvis sagt dir, was neu ist,
   und startet sich neu.
5. Stürzt Jarvis direkt nach einem Update ab, nimmt der Starter das Update automatisch zurück.

„Jarvis, mach die letzte Änderung rückgängig“ oder `/rueckgaengig` macht eine Änderung jederzeit rückgängig.

Tipp: Mit `git push` lädst du Jarvis' Änderungen auf GitHub hoch – so hast du ein Backup.

## Einstellungen

Alles steht in [`config.toml`](config.toml) (Anrede, Stimme, Tempo, Modell, Aktivierungswort, Mikrofon …).
Du kannst die Datei selbst bearbeiten oder Jarvis bitten, sie zu ändern.

Zum Modell: Standard ist `claude-opus-5-5` mit niedrigem Denkaufwand. Für noch schnellere Antworten
`claude-haiku-5-5` eintragen. Jede Nachricht und vor allem jede Selbstverbesserung zählt zu deinem
Claude-Nutzungslimit (bzw. kostet API-Guthaben).

## Wie Jarvis aufgebaut ist

```
start.py              Starter: Pakete, Neustarts, automatisches Zurücksetzen
jarvis/app.py         Hauptschleife (Mikrofon/Tastatur → Gehirn → Stimme)
jarvis/brain.py       Gehirn: Claude über das Claude Agent SDK, Persönlichkeit
jarvis/developer.py   Selbstverbesserung mit Claude Code
jarvis/skills/        Fähigkeiten – jede Datei hier wird automatisch geladen
jarvis/voice/         Spracherkennung und Sprachausgabe
CLAUDE.md             Anleitung für Claude Code, wie Jarvis aufgebaut ist
```

## Probleme?

- **„Keine Verbindung zu Claude“:** In einem Terminal `claude` starten und prüfen, ob du angemeldet bist.
- **Kein Mikrofon:** Beim Start erscheint ein Hinweis. Mit `/mikrofone` die Liste ansehen und die
  Nummer bei `mikrofon_index` in `config.toml` eintragen. Fehlt PyAudio, im Ordner
  `.venv\Scripts\python -m pip install pyaudio` ausführen.
- **Jarvis versteht sich selbst / reagiert auf den Fernseher:** Kopfhörer nutzen oder
  `nachfrage_sekunden = 0` setzen.
- **Spracherkennung und Online-Stimme brauchen Internet.** Ohne Internet fällt die Stimme automatisch auf
  die Windows-Stimme zurück.
- **Nur Text, ohne Mikrofon und Stimme:** `python start.py --text`
