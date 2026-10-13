"""Die KI-Rollen der Projektarbeit: Planer, Entwickler (Claude Code) und Prüfer.

Jede Rolle ist eine eigene Claude-Sitzung über das Claude Agent SDK – mit genau den Werkzeugen, die sie braucht:
  Planer      liest das Projekt (nur lesen + Websuche) und plant die nächsten Aufgaben
  Entwickler  setzt eine Aufgabe um: lesen, schreiben, Befehle im Projektordner, Websuche. Bei Roblox-Projekten
              zusätzlich Roblox Studio über dessen eingebauten MCP-Server (Playtest, Konsole, Bildschirmfoto,
              3D-Modelle und Materialien per Roblox-KI) – sofern Studio das auf diesem PC anbietet
  Prüfer      liest die Änderung (Diff) und das Projekt und urteilt, ob die Aufgabe sauber erfüllt ist

Ein Wächter blockiert – wie bei der Selbstverbesserung – gefährliche Befehle und Schreibzugriffe außerhalb des
Projektordners. Commit, Rücksetzen, Fragen an den Nutzer und Berichte übernimmt jarvis.projekte.
"""

from __future__ import annotations

import asyncio
import os
import re
import sys
import tempfile
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from jarvis import config, einrichtung
from jarvis.sicherheit import _TABU_LESEN, _innerhalb, pruefe_entwickler_werkzeug, schwaerzen

if TYPE_CHECKING:
    from jarvis.projekte import Aufgabe, Projekt

PROJEKT_SERVER = "projekt"
STUDIO_SERVER = "Roblox_Studio"
LESEN = ["Read", "Glob", "Grep", "WebSearch", "WebFetch"]
ENTWICKLER_WERKZEUGE = [
    "Read", "Write", "Edit", "MultiEdit", "Glob", "Grep", "Bash", "PowerShell", "WebSearch", "WebFetch",
    "TodoWrite", "TaskCreate", "TaskUpdate", "TaskList", "TaskGet", "ToolSearch",
    f"mcp__{PROJEKT_SERVER}__nutzer_fragen", f"mcp__{PROJEKT_SERVER}__fortschritt_melden",
]
# Bilder hochladen landet öffentlich im Roblox-Konto des Nutzers (Moderation!) – das macht er selbst.
STUDIO_VERBOTEN = [f"mcp__{STUDIO_SERVER}__upload_image"]

# Zusätzlich zu den Regeln der Selbstverbesserung (jarvis.sicherheit):
_PROJEKT_VERBOTEN: list[tuple[re.Pattern[str], str]] = [(re.compile(m, re.I), g) for m, g in [
    (r"\b(rokit|aftman|foreman)\b.*\b(add|install|trust|self-install|update|remove)\b",
     "Werkzeuge richtet Jarvis selbst ein (projekt_werkzeuge_einrichten). Nutze nur, was in rokit.toml steht."),
    (r"\brojo\b.*\b(serve|plugin|upload)\b",
     "Den Live-Sync nach Studio, das Studio-Plugin und Uploads steuern Jarvis bzw. der Nutzer."),
    (r"\bnpx\b|\bbunx\b|\buvx\b|\bpipx\s+run\b|\bpnpm\s+dlx\b|\bnpm\s+(i|install|add)\b.*(\s-g\b|--global)",
     "Keine Programme aus dem Netz direkt ausführen oder global installieren."),
]]

UMSETZEN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "erfolg": {"type": "boolean", "description": "true = Aufgabe umgesetzt und geprüft; false = nicht sinnvoll "
                   "umsetzbar (dann wird alles verworfen)"},
        "zusammenfassung": {"type": "string", "description": "1–3 vorlesbare deutsche Sätze: was jetzt neu ist"},
        "annahmen": {"type": "array", "items": {"type": "string"}},
        "folgeaufgaben": {"type": "array", "maxItems": 3, "items": {
            "type": "object", "properties": {"titel": {"type": "string"}, "beschreibung": {"type": "string"}},
            "required": ["titel"]}},
        "hinweis_fuer_nutzer": {"type": "string", "description": "Was der Nutzer selbst tun oder ausprobieren "
                                "sollte (z. B. 'In Studio auf Play drücken und den Shop öffnen'), sonst leer"},
    },
    "required": ["erfolg", "zusammenfassung"],
}
PRUEFEN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "in_ordnung": {"type": "boolean"},
        "blockierend": {"type": "array", "items": {"type": "string"},
                        "description": "Nur echte Fehler, Sicherheitsprobleme oder klar nicht erfüllte Aufgabe"},
        "hinweise": {"type": "array", "items": {"type": "string"}, "description": "Verbesserungsideen, nicht blockierend"},
    },
    "required": ["in_ordnung", "blockierend"],
}
PLANEN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "aufgaben": {"type": "array", "maxItems": 6, "items": {
            "type": "object",
            "properties": {"titel": {"type": "string", "description": "kurz, z. B. 'Münzen einsammeln'"},
                           "beschreibung": {"type": "string", "description": "Was genau, wo, Abnahmekriterien"},
                           "prioritaet": {"type": "integer", "enum": [1, 2, 3]}},
            "required": ["titel", "beschreibung"]}},
        "notiz": {"type": "string", "description": "Kurze Notiz an Jarvis, z. B. 'Ziel erreicht – neue Wünsche nötig' "
                  "oder welche Entscheidung des Nutzers fehlt; sonst leer"},
    },
    "required": ["aufgaben"],
}

ARTEN = {"roblox": "Roblox-Spiel in Luau, Code-Sync mit Rojo", "allgemein": "Softwareprojekt"}

Fragen = Callable[[str, str], Awaitable[str]]
Fortschritt = Callable[[str], None]


@dataclass
class KILauf:
    ok: bool
    art: str = ""                 # "" | "limit" | "voruebergehend" | "zeit" | "fehler"
    fehler: str = ""
    sitzung: str | None = None
    daten: dict[str, Any] = field(default_factory=dict)
    text: str = ""


def roblox_studio_mcp() -> dict[str, Any] | None:
    """Konfiguration für den in Roblox Studio eingebauten MCP-Server, falls auf diesem PC vorhanden.

    Quelle: https://create.roblox.com/docs/studio/mcp – in Studio unter Assistant → … → Manage MCP Servers
    "Enable Studio as MCP server" einschalten.
    """
    if sys.platform == "win32":
        basis = os.environ.get("LOCALAPPDATA")
        bat = Path(basis) / "Roblox" / "mcp.bat" if basis else None
        if bat is not None and bat.is_file():
            return {"type": "stdio", "command": "cmd.exe", "args": ["/c", str(bat)]}
    elif sys.platform == "darwin":
        exe = Path("/Applications/RobloxStudio.app/Contents/MacOS/StudioMCP")
        if exe.is_file():
            return {"type": "stdio", "command": str(exe)}
    return None


def pruefe_projekt_werkzeug(name: str, eingabe: dict[str, Any], ordner: Path) -> str | None:
    """Ablehnungsgrund, wenn die Projekt-KI dieses Werkzeug so nicht benutzen darf (sonst None).

    Befehle: dieselben Regeln wie bei der Selbstverbesserung plus _PROJEKT_VERBOTEN. Dateien: Jarvis selbst, seine
    Laufzeitdaten und gespeicherte Zugangsdaten sind tabu; geschrieben wird nur im Projektordner (ohne .git/.claude)
    und im Temp-Ordner.
    """
    if name in ("Bash", "PowerShell"):
        befehl = str(eingabe.get("command", ""))
        for muster, grund in _PROJEKT_VERBOTEN:
            if muster.search(befehl):
                return grund
        return pruefe_entwickler_werkzeug(name, eingabe, ordner)
    roh = eingabe.get("file_path") or eingabe.get("notebook_path") or eingabe.get("path")
    if not roh:
        return None
    ordner = ordner.resolve()
    pfad = Path(str(roh)).expanduser()
    pfad = (pfad if pfad.is_absolute() else ordner / pfad).resolve()
    tabu = [einrichtung.speicher_datei().resolve(), config.ROOT_DIR.resolve(), config.DATA_DIR.resolve()]
    if any(pfad == t or _innerhalb(pfad, t) for t in tabu) or _TABU_LESEN.search(str(pfad)):
        return "Jarvis selbst, seine Daten und gespeicherte Zugangsdaten sind für die Projektarbeit tabu."
    if name in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
        if not (_innerhalb(pfad, ordner) or _innerhalb(pfad, Path(tempfile.gettempdir()).resolve())):
            return f"Nur Dateien im Projektordner ({ordner}) oder im Temp-Ordner ändern."
        if any(_innerhalb(pfad, ordner / d) for d in (".git", ".claude")):
            return "Die Ordner .git/ und .claude/ sind tabu."
    return None


def _umgebung() -> dict[str, str]:
    env = {"MCP_TOOL_TIMEOUT": str(30 * 60 * 1000), "CLAUDE_CODE_DISABLE_BACKGROUND_TASKS": "1",
           "JARVIS_PROJEKT": "1"}
    for wert in einrichtung.alle():          # Jarvis' Geheimnisse nie an die Projekt-KI weitergeben
        if wert.geheim:
            env[wert.umgebungsvariable] = ""
    return env


def _kurz(text: Any, n: int = 100) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[: n - 1] + "…"


def beschreibe_werkzeug(name: str, eingabe: dict[str, Any]) -> str:
    pfad = eingabe.get("file_path") or eingabe.get("path")
    if name in ("Bash", "PowerShell"):
        return f"$ {_kurz(eingabe.get('command', ''), 90)}"
    if name in ("Write", "Edit", "MultiEdit") and pfad:
        return f"schreibt {Path(str(pfad)).name}"
    if name == "Read" and pfad:
        return f"liest {Path(str(pfad)).name}"
    if name == "WebSearch":
        return f"sucht im Web: {_kurz(eingabe.get('query', ''), 60)}"
    if name.startswith(f"mcp__{STUDIO_SERVER}__"):
        return "Roblox Studio: " + name.removeprefix(f"mcp__{STUDIO_SERVER}__").replace("_", " ")
    return name.removeprefix(f"mcp__{PROJEKT_SERVER}__").replace("_", " ")


class ProjektKI:
    """Startet die Claude-Sitzungen für Planer, Entwickler und Prüfer."""

    def __init__(self, cfg: dict[str, Any]) -> None:
        self.cfg = cfg["projekte"]
        self._wartezeit = 0.0           # Zeit, die auf Antworten des Nutzers gewartet wurde (zählt nicht zum Limit)

    # --- Rollen ---------------------------------------------------------------------------------
    async def planen(self, projekt: Projekt, anlass: str, fortschritt: Fortschritt | None = None) -> KILauf:
        erledigt = [a for a in projekt.aufgaben if a.status == "erledigt"][-15:]
        offen = projekt.offene()
        gescheitert = [a for a in projekt.aufgaben if a.status == "fehlgeschlagen"][-5:]
        antworten = [f"{f['frage']} → {f['antwort']}" for a in projekt.aufgaben for f in a.fragen if f.get("antwort")]
        teile = [f"""Du bist der Planer im Projekt „{projekt.titel}“ ({ARTEN.get(projekt.art, projekt.art)}). Du
arbeitest im Team mit einem Entwickler (Claude Code) und einem Prüfer; Jarvis koordiniert und berichtet dem Nutzer.

Ziel des Nutzers:
<ziel>
{schwaerzen(projekt.ziel)}
</ziel>

Lies PROJEKT.md, FORTSCHRITT.md und den Code (nur lesen). Anlass: {anlass}"""]
        if erledigt:
            teile.append("Bisher erledigt:\n" + "\n".join(f"- {a.id}. {a.titel}: {_kurz(a.ergebnis, 160)}"
                                                         for a in reversed(erledigt)))
        if offen:
            teile.append("Schon geplant/offen (nicht doppelt planen):\n" + "\n".join(f"- {a.id}. {a.titel}" for a in offen))
        if gescheitert:
            teile.append("Zuletzt nicht geklappt:\n" + "\n".join(f"- {a.titel}: {_kurz(a.ergebnis, 160)}"
                                                               for a in gescheitert))
        if antworten:
            teile.append("Entscheidungen des Nutzers:\n" + "\n".join(f"- {t}" for t in antworten[-10:]))
        teile.append(f"""Plane die nächsten 1 bis 5 Aufgaben. Jede Aufgabe
- ist in einer Sitzung des Entwicklers umsetzbar (höchstens etwa {int(self.cfg['max_minuten_pro_aufgabe'])} Minuten)
  und bringt sichtbaren Fortschritt für den Nutzer,
- ist konkret: was genau, wo, und woran man erkennt, dass sie fertig ist (Abnahmekriterien in der Beschreibung),
- baut auf dem Bestehenden auf: erst das Spielbare und Wichtigste, dann Feinschliff.
Fehlt eine Entscheidung, die nur der Nutzer treffen kann, oder ist das Ziel erreicht: gib weniger oder keine
Aufgaben zurück und schreib es in notiz.""")
        return await self._lauf("\n\n".join(teile), rolle="planer", ordner=Path(projekt.ordner), schema=PLANEN_SCHEMA,
                                modell=self.cfg.get("modell_planer"), aufwand="high", max_schritte=40, max_minuten=15,
                                werkzeuge=LESEN, erlaubt=LESEN, fortschritt=fortschritt)

    async def umsetzen(self, projekt: Projekt, aufgabe: Aufgabe, *, fragen: Fragen, fortschritt: Fortschritt,
                       studio: dict[str, Any] | None = None, sitzung: str | None = None,
                       nachbessern: list[str] | None = None) -> KILauf:
        mcp: dict[str, Any] = {PROJEKT_SERVER: self._projekt_server(fragen, fortschritt)}
        erlaubt = list(ENTWICKLER_WERKZEUGE)
        if studio:
            mcp[STUDIO_SERVER] = studio
            erlaubt.append(f"mcp__{STUDIO_SERVER}")
        if nachbessern and sitzung:
            prompt = ("Die Prüfung deiner Änderung hat Probleme gefunden:\n" + "\n".join(f"- {p}" for p in nachbessern)
                      + "\n\nBehebe sie (Prüfungen nicht abschwächen), prüfe erneut und gib dann wieder das "
                        "strukturierte Ergebnis für die gesamte Aufgabe zurück.")
        else:
            prompt = self._umsetzen_prompt(projekt, aufgabe, studio is not None)
        lauf = await self._lauf(prompt, rolle="entwickler", ordner=Path(projekt.ordner), schema=UMSETZEN_SCHEMA,
                                modell=self.cfg.get("modell"), aufwand=self.cfg.get("aufwand"),
                                max_schritte=int(self.cfg["max_schritte_pro_aufgabe"]),
                                max_minuten=float(self.cfg["max_minuten_pro_aufgabe"]), erlaubt=erlaubt, mcp=mcp,
                                verboten=STUDIO_VERBOTEN, sitzung=sitzung, fortschritt=fortschritt)
        if lauf.art == "zeit" and lauf.sitzung:
            fortschritt("Zeitlimit erreicht – bringt den Stand zum Abschluss")
            lauf = await self._lauf(
                "Das Zeit- bzw. Schrittlimit ist erreicht. Fang nichts Neues an: Bring den jetzigen Stand in einen "
                "lauffähigen Zustand (Unfertiges sauber weglassen), prüfe kurz und gib das strukturierte Ergebnis "
                "zurück; Fehlendes als folgeaufgaben.", rolle="entwickler", ordner=Path(projekt.ordner),
                schema=UMSETZEN_SCHEMA, modell=self.cfg.get("modell"), aufwand=self.cfg.get("aufwand"),
                max_schritte=40, max_minuten=10, erlaubt=erlaubt, mcp=mcp, verboten=STUDIO_VERBOTEN,
                sitzung=lauf.sitzung, fortschritt=fortschritt)
        return lauf

    async def pruefen(self, projekt: Projekt, aufgabe: Aufgabe, diff: str, pruefungen: list[str],
                      zusammenfassung: str, fortschritt: Fortschritt | None = None) -> KILauf:
        prompt = f"""Du bist der Prüfer im Projekt „{projekt.titel}“ ({ARTEN.get(projekt.art, projekt.art)}).
Der Entwickler hat diese Aufgabe umgesetzt:

<aufgabe nr="{aufgabe.id}">
{aufgabe.titel}
{schwaerzen(aufgabe.beschreibung)}
</aufgabe>

Seine Zusammenfassung: {schwaerzen(zusammenfassung)}

Automatische Prüfungen:
{chr(10).join(pruefungen) or '(keine Prüfwerkzeuge installiert)'}

Die Änderung (git diff, ggf. gekürzt):
<diff>
{schwaerzen(diff)}
</diff>

Prüfe (du kannst Dateien im Projekt lesen): Erfüllt die Änderung die Aufgabe? Läuft sie vermutlich fehlerfrei?
Ist sie sicher (bei Roblox: der Server prüft alles, was vom Client kommt; keine Hintertüren, kein loadstring,
kein require(Asset-ID), keine fremden Skripte in Modellen, DataStores mit pcall und ohne Datenverlust)?
Passt sie zu CLAUDE.md?
blockierend = nur echte Fehler, Sicherheitsprobleme oder eine klar nicht erfüllte Aufgabe. Stil- und Ideen unter
hinweise. Sei pragmatisch: Kleinigkeiten blockieren nicht."""
        return await self._lauf(prompt, rolle="pruefer", ordner=Path(projekt.ordner), schema=PRUEFEN_SCHEMA,
                                modell=self.cfg.get("modell_pruefer"), aufwand="medium", max_schritte=30,
                                max_minuten=10, werkzeuge=["Read", "Glob", "Grep"], erlaubt=["Read", "Glob", "Grep"],
                                fortschritt=fortschritt)

    def _umsetzen_prompt(self, projekt: Projekt, aufgabe: Aufgabe, mit_studio: bool) -> str:
        teile = [f"""Du bist der Entwickler im Projekt „{projekt.titel}“ ({ARTEN.get(projekt.art, projekt.art)}). Ein
Planer hat die Aufgaben geplant, ein Prüfer schaut sich deine Änderung danach an. Jarvis (der Sprachassistent des
Nutzers) sichert jede Aufgabe per Git und berichtet dem Nutzer.

Lies zuerst CLAUDE.md, PROJEKT.md und FORTSCHRITT.md.

<aufgabe nr="{aufgabe.id}">
{aufgabe.titel}
{schwaerzen(aufgabe.beschreibung)}
</aufgabe>"""]
        if aufgabe.quelle == "nutzer":
            teile.append("Diese Aufgabe kommt direkt vom Nutzer – setz seinen Wunsch möglichst genau um.")
        if aufgabe.ergebnis and aufgabe.versuche > 1:
            teile.append(f"Ein früherer Versuch dieser Aufgabe ist gescheitert: {schwaerzen(aufgabe.ergebnis)}")
        entscheidungen = [f"{f['frage']} → {f['antwort']}" for a in projekt.aufgaben for f in a.fragen if f.get("antwort")]
        if entscheidungen:
            teile.append("Frühere Entscheidungen des Nutzers:\n" + "\n".join(f"- {t}" for t in entscheidungen[-10:]))
        teile.append("""Regeln:
- Arbeite nur im Projektordner. Keine Git-Befehle, die etwas verändern (Commit/Zurücksetzen macht Jarvis).
- Rückfragen nur, wenn du ohne Entscheidung des Nutzers nicht sinnvoll weiterkommst: nutzer_fragen (kurz, mit
  Vorschlag). Ist er nicht erreichbar, entscheide selbst und nenne es unter annahmen. Nie nach Passwörtern fragen.
- Melde den Stand bei jedem Phasenwechsel mit fortschritt_melden ("Baue den Shop-Dialog", "Teste im Playtest").
- Prüfe deine Arbeit mit den Prüfwerkzeugen aus CLAUDE.md, soweit installiert.
- Trag am Ende einen kurzen Eintrag in FORTSCHRITT.md ein: Datum, Aufgabe, was neu ist, wie man es ausprobiert.
- Was noch fehlt oder dir als sinnvoller nächster Schritt auffällt: folgeaufgaben (höchstens drei).""")
        if mit_studio:
            teile.append(f"""Roblox Studio ist über seinen MCP-Server angebunden (Werkzeuge mcp__{STUDIO_SERVER}__…):
- Prüfe zuerst mit list_roblox_studios, ob Studio offen ist. Wenn nicht: arbeite nur mit den Dateien.
- Wenn ja: teste im Playtest (start_stop_play, get_console_output, screen_capture, character_navigation),
  erkunde die Szene (search_game_tree, inspect_instance) und erzeuge 3D-Modelle/Materialien mit der Roblox-KI
  (generate_mesh, generate_material, generate_procedural_model). Beende Playtests immer wieder.
- Code gehört trotzdem als Datei nach src/ (Rojo überträgt ihn nach Studio) – nicht nur in Studio ändern.
  Änderungen an der Welt in Studio (Modelle, Gelände) in FORTSCHRITT.md notieren, sie stehen nicht in Git.
- insert_asset nur, wenn nötig: eingefügte Modelle auf Skripte prüfen und fremde Skripte entfernen.""")
        teile.append("Zum Schluss gibst du das strukturierte Ergebnis zurück. erfolg=false nur, wenn die Aufgabe nicht "
                     "sinnvoll umsetzbar ist – dann wird alles verworfen und zusammenfassung erklärt warum.")
        return "\n\n".join(teile)

    # --- Werkzeuge für den Entwickler -------------------------------------------------------------
    def _projekt_server(self, fragen: Fragen, fortschritt: Fortschritt) -> Any:
        from claude_agent_sdk import create_sdk_mcp_server, tool

        ki = self

        @tool("nutzer_fragen", "Kurze Rückfrage an den Nutzer (Jarvis stellt sie per Sprache). Nur für Entscheidungen, "
                               "die vom Nutzer abhängen (Spielidee, Stil, Prioritäten). Immer mit Vorschlag. Nie nach "
                               "Passwörtern oder Schlüsseln fragen.", {"frage": str, "vorschlag": str})
        async def nutzer_fragen(args: dict[str, Any]) -> dict[str, Any]:
            start = time.monotonic()
            try:
                antwort = await fragen(str(args.get("frage", "")), str(args.get("vorschlag", "")))
            finally:
                ki._wartezeit += time.monotonic() - start
            return {"content": [{"type": "text", "text": antwort}]}

        @tool("fortschritt_melden", "Kurzer Satz zum aktuellen Stand, z. B. 'Baue den Münz-Zähler'. Bei jedem "
                                    "Phasenwechsel aufrufen.", {"text": str})
        async def fortschritt_melden(args: dict[str, Any]) -> dict[str, Any]:
            fortschritt(_kurz(args.get("text", ""), 150))
            return {"content": [{"type": "text", "text": "ok"}]}

        return create_sdk_mcp_server(name=PROJEKT_SERVER, version="1.0.0", tools=[nutzer_fragen, fortschritt_melden])

    # --- Eine Claude-Sitzung ------------------------------------------------------------------------
    async def _lauf(self, prompt: str, *, rolle: str, ordner: Path, schema: dict[str, Any], modell: str | None,
                    aufwand: str | None, max_schritte: int, max_minuten: float, erlaubt: list[str],
                    werkzeuge: list[str] | None = None, mcp: dict[str, Any] | None = None,
                    verboten: list[str] | None = None, sitzung: str | None = None,
                    fortschritt: Fortschritt | None = None) -> KILauf:
        from claude_agent_sdk import (AssistantMessage, ClaudeAgentOptions, ClaudeSDKClient, ClaudeSDKError,
                                      HookMatcher, ResultMessage, ToolUseBlock)

        async def waechter(eingabe: Any, tool_use_id: str | None, kontext: Any) -> dict[str, Any]:
            grund = pruefe_projekt_werkzeug(eingabe.get("tool_name", ""), eingabe.get("tool_input") or {}, ordner)
            if grund:
                return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                               "permissionDecisionReason": grund}}
            return {}

        optionen: dict[str, Any] = dict(
            cwd=str(ordner),
            system_prompt={"type": "preset", "preset": "claude_code"},
            setting_sources=["project"],              # lädt die CLAUDE.md des Projekts
            permission_mode="dontAsk",
            allowed_tools=list(erlaubt),
            disallowed_tools=["AskUserQuestion", *(verboten or [])],
            mcp_servers=mcp or {},
            strict_mcp_config=True,
            hooks={"PreToolUse": [HookMatcher(matcher=None, hooks=[waechter])]},
            output_format={"type": "json_schema", "schema": schema},
            max_turns=max_schritte,
            resume=sitzung,
            env=_umgebung(),
        )
        if werkzeuge is not None:
            optionen["tools"] = list(werkzeuge)
        if modell:
            optionen["model"] = modell
        if aufwand:
            optionen["effort"] = aufwand

        start, warten_vorher = time.monotonic(), self._wartezeit
        zeit_um = False
        try:
            async with ClaudeSDKClient(ClaudeAgentOptions(**optionen)) as client:
                async def zeitwaechter() -> None:
                    nonlocal zeit_um
                    while True:
                        await asyncio.sleep(5)
                        if time.monotonic() - start - (self._wartezeit - warten_vorher) > max_minuten * 60:
                            zeit_um = True
                            try:
                                await client.interrupt()
                            except Exception:  # noqa: BLE001
                                pass
                            return

                uhr = asyncio.get_running_loop().create_task(zeitwaechter())
                try:
                    await client.query(prompt)
                    async for msg in client.receive_response():
                        if isinstance(msg, AssistantMessage) and fortschritt is not None:
                            for block in msg.content:
                                if isinstance(block, ToolUseBlock):
                                    fortschritt(beschreibe_werkzeug(block.name, block.input or {}))
                        elif isinstance(msg, ResultMessage):
                            return auswerten(msg, msg.session_id or sitzung, zeit_um)
                finally:
                    uhr.cancel()
        except ClaudeSDKError as e:
            return KILauf(False, "voruebergehend", f"Verbindung zu Claude verloren: {schwaerzen(e)}", sitzung)
        return KILauf(False, "voruebergehend", f"Der {rolle} hat ohne Ergebnis aufgehört.", sitzung)


def auswerten(msg: Any, sitzung: str | None, zeit_um: bool = False) -> KILauf:
    """Ordnet das Ende einer Claude-Sitzung ein (Erfolg, Nutzungslimit, vorübergehender Fehler …)."""
    text = (msg.result or "").strip()
    if not msg.is_error and msg.subtype in ("success", "error_max_structured_output_retries"):
        daten = msg.structured_output if isinstance(msg.structured_output, dict) else {}
        return KILauf(True, sitzung=sitzung, daten=daten, text=text)
    if msg.subtype == "error_max_turns" or zeit_um:
        return KILauf(False, "zeit", "Zeit- oder Schrittlimit erreicht.", sitzung)
    niedrig = text.lower()
    if "usage limit" in niedrig or "limit reached" in niedrig or "nutzungslimit" in niedrig:
        return KILauf(False, "limit", text, sitzung)
    status = getattr(msg, "api_error_status", None)
    if status in (408, 429, 500, 502, 503, 504, 529) or msg.subtype == "error_during_execution" \
            or "overloaded" in niedrig or "timeout" in niedrig:
        return KILauf(False, "voruebergehend", text or msg.subtype, sitzung)
    return KILauf(False, "fehler", schwaerzen(text or msg.subtype), sitzung)
