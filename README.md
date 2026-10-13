# J.A.R.V.I.S.

Dein persönlicher Sprachassistent für den PC – und das Besondere: **Jarvis baut sich selbst aus.**
Du sagst ihm, was er können soll – auch richtig große Sachen wie „schick mir Nachrichten aufs Handy“ oder
„schick 3D-Modelle an meinen Drucker“ –, und er programmiert sich mit Claude Code um, testet sich und
startet mit der neuen Funktion neu.

Das hier ist die **Basis**. Alles Weitere bringst du ihm im Gespräch bei.

## Was er von Anfang an kann

- **Reden:** Du sprichst ins Mikrofon („Jarvis, …“), er antwortet mit Stimme. Tippen geht auch.
- **Denken:** Claude als Gehirn – mit Persönlichkeit à la Iron Man, Websuche und Gesprächsgedächtnis.
- **Sich selbst ausbauen:** kleine Dinge (Stimme, Verhalten) und große Funktionen mit Hintergrunddiensten,
  Geräten und Konten. Claude Code fragt bei Bedarf über Jarvis nach („Welchen Drucker haben Sie?“).
- **Einrichtung per Sprache:** Braucht eine Funktion einen Schlüssel oder eine Adresse, führt Jarvis dich
  Schritt für Schritt durch – Geheimes gibst du in einem kleinen Fenster ein, nie per Sprache.
- **Handy-Kanäle:** Funktionen wie Telegram koppelt Jarvis per Code mit deinem Handy; Fremde kommen nicht durch.
- **Sicherheitsnetz:** Jede Änderung wird per Git gesichert, getestet und notfalls automatisch zurückgesetzt.
  Riskante Aktionen bestätigst du vorher.
- **Grundfunktionen:** Uhrzeit, Timer & Erinnerungen & tägliche Routinen, Webseiten und Programme öffnen,
  Systeminfos, sich Dinge merken.

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
- **Bestätigen:** Bei Aktionen mit Folgen fragt Jarvis nach – einfach „ja“ oder „nein“ sagen oder tippen.

## Jarvis neue Funktionen beibringen

Sag ihm einfach, was du willst. Große Sachen sind ausdrücklich erwünscht:

- „Jarvis, ich will mir Nachrichten aufs Handy schicken und dir vom Handy aus schreiben können.“
- „Jarvis, du sollst 3D-Modelle suchen, slicen und an meinen Drucker schicken.“
- „Jarvis, steuer meine Lampen“, „lies mir neue E-Mails vor“, „steuer Spotify“.
- „Jarvis, jeden Morgen um sieben sagst du mir das Wetter und meine Termine.“
- „Jarvis, sprich ein bisschen langsamer und nenn mich Chef.“
- „Jarvis, das Drucken geht nicht, repariere das.“

So läuft es ab:

1. **Kurz klären:** Bei großen Wünschen fragt Jarvis vorher nur das Nötigste – z. B. welcher Drucker oder
   welche App. Dann fragt er „Soll Claude Code das umsetzen: …?“ – du sagst „ja“.
2. **Bauen:** Claude Code arbeitet im Hintergrund (kleine Dinge 2–10 Minuten, große Funktionen 15–60
   Minuten). Du kannst normal weiterreden. „Jarvis, wie weit bist du?“ zeigt den Stand. Fragt Claude Code
   etwas, stellt Jarvis dir die Frage; antworte einfach. Ergänzungen („und er soll auch Bilder schicken
   können“) gibt Jarvis weiter.
3. **Prüfen:** Selbsttest, Tests und Sicherheitsprüfungen. Bei Problemen bessert Claude Code nach. Klappt es
   nicht, wird alles zurückgesetzt – auch die installierten Pakete.
4. **Neustart:** Jarvis startet neu und sagt dir, was neu ist. Braucht die Funktion noch etwas (z. B. einen
   Bot-Schlüssel), bietet er an, das gleich gemeinsam einzurichten.

„Jarvis, mach die letzte Änderung rückgängig“ oder `/rueckgaengig` nimmt eine Änderung jederzeit zurück.
„Jarvis, brich das ab“ stoppt eine laufende Änderung.

Tipp: Mit `git push` lädst du Jarvis' Änderungen auf GitHub hoch – so hast du ein Backup.
Zugangsdaten landen dabei nie auf GitHub: Sie liegen außerhalb des Projekts
(`%APPDATA%\Jarvis\einrichtung.json`).

## Befehle (tippen)

| Befehl | Wirkung |
|---|---|
| `/hilfe` | alle Befehle |
| `/verbessern [gross] <Auftrag>` | Auftrag direkt an Claude Code geben |
| `/status`, `/antwort <text>`, `/abbrechen` | Stand, Rückfrage beantworten, abbrechen |
| `/verlauf`, `/rueckgaengig` | Selbst-Änderungen anzeigen / letzte zurücknehmen |
| `/funktionen`, `/aus <name>`, `/an <name>` | Funktionen anzeigen, aus- und einschalten |
| `/einrichtung`, `/einrichten <name>` | Einrichtungsstand / Wert verdeckt eingeben |
| `/koppeln <kanal>`, `/entkoppeln <kanal>` | Handy-Kanal (z. B. Telegram) koppeln |
| `/dienste`, `/fehler` | Hintergrunddienste, letzte Fehler |
| `/neu`, `/stumm`, `/mikrofone` | neues Gespräch, Stimme an/aus, Mikrofone |
| `/neustart`, `/beenden` | neu starten / beenden |

## Einstellungen

Alles steht in [`config.toml`](config.toml) (Anrede, Stimme, Tempo, Modell, Aktivierungswort, Mikrofon,
Rechenaufwand für kleine/große Aufträge …). Du kannst die Datei selbst bearbeiten oder Jarvis bitten,
sie zu ändern.

Zum Modell: Standard ist `claude-opus-5-5` (im Gespräch mit niedrigem Denkaufwand, beim Programmieren mit
hohem). Für noch schnellere Antworten `claude-haiku-5-5` eintragen. Jede Nachricht und vor allem jede
Selbstverbesserung zählt zu deinem Claude-Nutzungslimit (bzw. kostet API-Guthaben) – große Funktionen
brauchen spürbar mehr.

## Wie Jarvis aufgebaut ist

```
start.py              Starter: Pakete, Neustarts, automatisches Zurücksetzen (Code und Pakete)
jarvis/app.py         Hauptschleife (Mikrofon/Tastatur/Handy → Gehirn → Stimme), Bestätigungen
jarvis/brain.py       Gehirn: Claude über das Claude Agent SDK, Persönlichkeit, Werkzeug-Prüfung
jarvis/developer.py   Selbstverbesserung mit Claude Code (Rückfragen, Fortschritt, Wächter, Rollback)
jarvis/api.py         Schnittstelle für Funktionen (Dienste, Einrichtung, Kanäle, sagen/melden …)
jarvis/skills/        Funktionen – jede Datei/jedes Paket hier wird automatisch geladen
jarvis/sicherheit.py  Schwärzen von Geheimnissen, Bestätigungsregeln, Kanal-Kopplung, Wächter
jarvis/voice/         Spracherkennung und Sprachausgabe
CLAUDE.md             Anleitung für Claude Code, wie es Funktionen für Jarvis baut
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
- **Eine Funktion stört:** „Jarvis, schalte Telegram aus“ oder `/aus telegram`.
- **Was ist schiefgelaufen?** `/fehler` zeigt die letzten Fehler, `data\protokoll.log` das ganze Protokoll.
- **Nur Text, ohne Mikrofon und Stimme:** `python start.py --text`
