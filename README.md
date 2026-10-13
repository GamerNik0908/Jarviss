# J.A.R.V.I.S.

Dein persönlicher Sprachassistent für den PC – und das Besondere: **Jarvis baut sich selbst aus.**
Du sagst ihm, was er können soll – auch richtig große Sachen wie „schick mir Nachrichten aufs Handy“ oder
„schick 3D-Modelle an meinen Drucker“ –, und er programmiert sich mit Claude Code um, testet sich und
startet mit der neuen Funktion neu.

Das hier ist die **Basis**. Alles Weitere bringst du ihm im Gespräch bei.

## Was er von Anfang an kann

- **Reden:** Du sprichst ins Mikrofon („Jarvis, …“), er antwortet mit Stimme. Tippen geht auch.
- **Fenster (HUD):** Beim Start öffnet sich das Jarvis-Fenster im Iron-Man-Stil: animierter Kern, Gesprächsverlauf,
  was Claude Code gerade baut, deine Projekte, Ja/Nein-Knöpfe für Bestätigungen. Läuft nur auf deinem PC.
- **Denken:** Claude als Gehirn – mit Persönlichkeit à la Iron Man, Websuche und Gesprächsgedächtnis.
- **Merkt selbst, was er noch nicht kann:** Bittest du ihn um etwas, wofür ihm die Fähigkeit fehlt („erstell mir
  ein Dokument über Vulkane und druck es aus“), sagt er „Das kann ich noch nicht – ich bringe es mir bei“,
  programmiert sich mit Claude Code um, startet neu und **erledigt danach automatisch deinen ursprünglichen Wunsch**.
- **Sich selbst ausbauen:** kleine Dinge (Stimme, Verhalten) und große Funktionen mit Hintergrunddiensten,
  Geräten und Konten. Claude Code fragt bei Bedarf über Jarvis nach („Welchen Drucker haben Sie?“). Mehrere
  Wünsche hintereinander? Die kommen in eine Warteschlange und werden nacheinander gebaut.
- **Projekte selbstständig bauen – z. B. dein Roblox-Spiel:** Ein KI-Team aus Planer, Entwickler (Claude Code) und
  Prüfer arbeitet rund um die Uhr Aufgabe für Aufgabe ab, sichert jeden Schritt per Git, fragt bei Unklarem nach
  und gibt dir **jeden Morgen einen Bericht**. Neue Aufgaben gibst du einfach per Sprache oder im Fenster dazu.
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

**Du musst nicht extra sagen, dass er lernen soll.** Bitte ihn einfach um etwas – kann er es noch nicht, merkt er
das, fragt kurz „Soll ich mich dafür umprogrammieren?“ und erledigt deinen Wunsch nach dem Update von selbst.
(Mit `ohne_nachfrage = true` in `config.toml` legt er bei Wünschen direkt von dir sogar ohne diese Frage los.)

„Jarvis, mach die letzte Änderung rückgängig“ oder `/rueckgaengig` nimmt eine Änderung jederzeit zurück.
„Jarvis, brich das ab“ stoppt eine laufende Änderung.

Tipp: Mit `git push` lädst du Jarvis' Änderungen auf GitHub hoch – so hast du ein Backup.
Zugangsdaten landen dabei nie auf GitHub: Sie liegen außerhalb des Projekts
(`%APPDATA%\Jarvis\einrichtung.json`).

## Dein Roblox-Spiel (und andere Projekte)

Jarvis kann dauerhaft an einem Projekt außerhalb von sich selbst arbeiten. So fängst du an:

1. **„Jarvis, lass uns mein Roblox-Spiel neu bauen.“** Er fragt kurz nach (was für ein Spiel, was zuerst wichtig
   ist) und legt das Projekt an – in `<Benutzerordner>\Jarvis\Projekte\<name>`, mit eigenem Git, Rojo-Struktur
   (`src/server`, `src/client`, `src/shared`) und Regeln für die KI (`CLAUDE.md`).
2. **Das KI-Team legt los:** Der *Planer* zerlegt dein Ziel in Aufgaben, *Claude Code* setzt eine nach der anderen
   um, der *Prüfer* kontrolliert jede Änderung (Fehler, Sicherheit – z. B. dass der Server nichts glaubt, was vom
   Client kommt) und Claude Code bessert nach. Klappt eine Aufgabe nicht, wird sie sauber zurückgesetzt.
3. **Ins Roblox Studio bringen:** „Jarvis, richte die Roblox-Werkzeuge ein“ (installiert Rojo, Selene, StyLua und
   das Rojo-Plugin über Rokit), dann „Jarvis, starte den Live-Abgleich mit Studio“ und in Studio im Rojo-Plugin auf
   **Connect** klicken – ab dann landet jede Änderung sofort in Studio.
4. **Optional – Claude Code direkt in Studio:** In Roblox Studio **Assistant** → **…** → **Manage MCP Servers** →
   **Enable Studio as MCP server** einschalten. Dann kann Claude Code selbst Playtests starten, die Ausgabe lesen,
   Bildschirmfotos machen und mit der Roblox-KI 3D-Modelle und Materialien erzeugen.

Im Alltag:

- „Jarvis, neue Aufgabe fürs Spiel: Baue einen Shop für Skins.“ – deine Aufgaben kommen vor den selbst geplanten.
- „Jarvis, wie weit ist mein Spiel?“ · „Was hast du heute Nacht gemacht?“
- „Jarvis, pausiere das Spiel“ / „mach weiter“ / „plane selbst weiter“ (Dauerbetrieb).
- **Morgenbericht** um `bericht_uhrzeit` (Standard 8 Uhr) – war der PC aus, kommt er beim nächsten Start.
- **Ruhezeit** (Standard 22:00–07:30): Das Team arbeitet leise weiter, ohne Ansagen. Fragen entscheidet es dann
  selbst und schreibt die Annahme in den Bericht – du kannst sie morgens korrigieren.
- Im Fenster (Reiter **Projekte**) siehst du live, wer gerade woran arbeitet, und kannst Aufgaben hinzufügen.

Damit das Team arbeitet, muss Jarvis laufen (PC an). Pro Tag nimmt es sich höchstens `max_auto_aufgaben_pro_tag`
eigene Aufgaben vor – deine eigenen laufen immer. Ist dein Claude-Nutzungslimit erreicht, pausiert es eine Stunde.
Veröffentlichen machst du selbst in Studio.

## Befehle (tippen)

| Befehl | Wirkung |
|---|---|
| `/hilfe` | alle Befehle |
| `/verbessern [gross] <Auftrag>` | Auftrag direkt an Claude Code geben |
| `/status`, `/antwort <text>`, `/abbrechen` | Stand, Rückfrage beantworten, abbrechen |
| `/verlauf`, `/rueckgaengig` | Selbst-Änderungen anzeigen / letzte zurücknehmen |
| `/funktionen`, `/aus <name>`, `/an <name>` | Funktionen anzeigen, aus- und einschalten |
| `/projekte` | Stand aller Projekte (z. B. Roblox-Spiel) |
| `/hud` | Das Jarvis-Fenster (wieder) öffnen |
| `/einrichtung`, `/einrichten <name>` | Einrichtungsstand / Wert verdeckt eingeben |
| `/koppeln <kanal>`, `/entkoppeln <kanal>` | Handy-Kanal (z. B. Telegram) koppeln |
| `/dienste`, `/fehler` | Hintergrunddienste, letzte Fehler |
| `/neu`, `/stumm`, `/mikrofone` | neues Gespräch, Stimme an/aus, Mikrofone |
| `/neustart`, `/beenden` | neu starten / beenden |

## Einstellungen

Alles steht in [`config.toml`](config.toml) (Anrede, Stimme, Tempo, Modell, Aktivierungswort, Mikrofon,
Rechenaufwand für kleine/große Aufträge, Fenster, Projekte: Modelle des KI-Teams, Morgenbericht, Ruhezeit …). Du kannst die Datei selbst bearbeiten oder Jarvis bitten,
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
jarvis/projekte.py    Projektarbeit (z. B. Roblox-Spiel): Aufgaben, Git-Sicherung, Morgenbericht, Ruhezeit
jarvis/projekt_ki.py  Das KI-Team der Projekte: Planer, Entwickler (Claude Code + Roblox Studio), Prüfer
jarvis/oberflaeche/   Das HUD-Fenster (nur auf diesem PC, mit Zugangsschlüssel)
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
- **Fenster zu oder nicht aufgegangen:** `/hud` tippen. Ohne Fenster: `[oberflaeche] an = false` in `config.toml`.
- **Roblox-Code kommt nicht in Studio an:** Läuft der Live-Abgleich („Jarvis, starte den Live-Abgleich“) und ist im
  Rojo-Plugin in Studio „Connect“ gedrückt? Fehlen Werkzeuge: „Jarvis, richte die Roblox-Werkzeuge ein“.
