"""Das Gehirn: eine dauerhafte Claude-Sitzung über das Claude Agent SDK.

Das SDK steuert Claude Code im Hintergrund. Es nutzt deine normale Claude-Code-
Anmeldung (Abo) oder – falls gesetzt – die Umgebungsvariable ANTHROPIC_API_KEY.
Jarvis' Fähigkeiten werden dem Gehirn als Werkzeuge (MCP-Tools) angeboten.
"""

from __future__ import annotations

import json
import platform
from collections.abc import AsyncIterator
from typing import Any

from jarvis import memory, ui
from jarvis.config import DATA_DIR, ROOT_DIR
from jarvis.skills import Skill

SESSION_FILE = DATA_DIR / "sitzung.json"
MCP_SERVER_NAME = "jarvis"


def tool_id(skill_name: str) -> str:
    return f"mcp__{MCP_SERVER_NAME}__{skill_name}"


def build_system_prompt(cfg: dict[str, Any], extra_notes: list[str] | None = None) -> str:
    anrede = cfg["nutzer"]["anrede"] or "Sir"
    name = cfg["nutzer"]["name"]
    wer = f"{name} (Anrede: \"{anrede}\")" if name else f"deinen Nutzer (Anrede: \"{anrede}\")"
    prompt = f"""Du bist J.A.R.V.I.S. – der persönliche KI-Assistent von {wer}, frei nach Iron Man.
Du läufst lokal auf dem PC des Nutzers ({platform.system()} {platform.release()}).

So sprichst du:
- Auf Deutsch, höflich, souverän, mit trockenem britischem Butler-Humor. Sprich den Nutzer mit "{anrede}" an (nicht in jedem Satz).
- Deine Antworten werden laut vorgelesen. Halte sie kurz: meist ein bis drei Sätze.
- Kein Markdown, keine Listen, keine Emojis, keine Codeblöcke, keine URLs vorlesen. Schreibe Zahlen und Abkürzungen so, dass sie gut klingen.
- Die Eingaben kommen oft aus einer Spracherkennung und können Hör-Fehler enthalten. Interpretiere sie sinnvoll.

Was du kannst:
- Nutze deine Werkzeuge, um Dinge auf dem PC zu erledigen, die Uhrzeit zu kennen, dir Dinge zu merken usw.
- Für aktuelle Informationen aus dem Internet kannst du die Websuche nutzen (falls verfügbar).

Selbstverbesserung – dein wichtigstes Merkmal:
- Du kannst deinen eigenen Programmcode mit Claude Code verändern. Wenn der Nutzer möchte, dass du etwas Neues kannst,
  dich anders verhältst, eine Einstellung änderst (z. B. Stimme, Tempo, Aktivierungswort) oder ein Fehler behoben wird,
  rufe das Werkzeug `selbst_verbessern` auf.
- Formuliere den Auftrag dafür ausführlich und präzise auf Deutsch: was genau gewünscht ist, wie es sich verhalten soll,
  und alle Details, die der Nutzer genannt hat. Der Entwickler kann den Nutzer nicht fragen.
- Ist der Wunsch zu vage, stelle zuerst eine kurze Rückfrage.
- Wenn du etwas nicht kannst, sag es ehrlich und biete an, dir die Fähigkeit selbst beizubringen.
- Die Umsetzung läuft im Hintergrund und dauert meist ein paar Minuten. Sag dem Nutzer kurz Bescheid; er kann
  währenddessen weiter mit dir reden. Danach startest du dich automatisch neu.
- Mit `aenderungsverlauf` siehst du, was zuletzt an dir geändert wurde, mit `letzte_aenderung_rueckgaengig` machst du
  die letzte Änderung rückgängig.
"""
    facts = memory.facts()
    if facts:
        prompt += "\nDas weißt du über den Nutzer (aus deinem Gedächtnis):\n" + "\n".join(f"- {f}" for f in facts) + "\n"
    for note in extra_notes or []:
        prompt += f"\nHinweis: {note}\n"
    return prompt


class Brain:
    def __init__(self, cfg: dict[str, Any], skills: list[Skill], extra_notes: list[str] | None = None):
        self.cfg = cfg
        self.skills = skills
        self.extra_notes = extra_notes or []
        self._client: Any = None
        self.session_id: str | None = None

    # --- Aufbau -------------------------------------------------------------
    def build_options(self, resume: str | None = None) -> Any:
        from claude_agent_sdk import ClaudeAgentOptions, create_sdk_mcp_server, tool

        sdk_tools = [tool(s.name, s.beschreibung, s.schema)(_make_handler(s)) for s in self.skills]
        server = create_sdk_mcp_server(name=MCP_SERVER_NAME, version="1.0.0", tools=sdk_tools)

        g = self.cfg["gehirn"]
        builtin = ["WebSearch", "WebFetch"] if g.get("websuche", True) else []
        options = dict(
            system_prompt=build_system_prompt(self.cfg, self.extra_notes),
            mcp_servers={MCP_SERVER_NAME: server},
            strict_mcp_config=True,
            tools=builtin,                                   # keine Datei-/Shell-Werkzeuge im Gespräch
            allowed_tools=builtin + [tool_id(s.name) for s in self.skills],
            permission_mode="dontAsk",                       # nie nachfragen, Unerlaubtes ablehnen
            setting_sources=[],                              # CLAUDE.md ist für den Entwickler, nicht fürs Gespräch
            cwd=str(ROOT_DIR),
            resume=resume,
        )
        if g.get("modell"):
            options["model"] = g["modell"]
        if g.get("aufwand"):
            options["effort"] = g["aufwand"]
        return ClaudeAgentOptions(**options)

    async def connect(self) -> None:
        from claude_agent_sdk import ClaudeSDKClient

        resume = self._load_session() if self.cfg["gehirn"].get("gespraech_fortsetzen", True) else None
        if resume:
            try:
                self._client = ClaudeSDKClient(self.build_options(resume=resume))
                await self._client.connect()
                self.session_id = resume
                return
            except Exception as e:  # noqa: BLE001
                ui.info(f"Altes Gespräch konnte nicht fortgesetzt werden ({e}) – starte neu.")
                await self._safe_disconnect()
        self._client = ClaudeSDKClient(self.build_options())
        await self._client.connect()

    async def disconnect(self) -> None:
        await self._safe_disconnect()

    async def _safe_disconnect(self) -> None:
        if self._client is not None:
            try:
                await self._client.disconnect()
            except Exception:  # noqa: BLE001
                pass
            self._client = None

    # --- Gespräch -------------------------------------------------------------
    async def ask(self, text: str) -> AsyncIterator[str]:
        """Schickt eine Nachricht und liefert die Antwort Stück für Stück (Textblöcke)."""
        from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock, ToolUseBlock

        if self._client is None:
            await self.connect()
        try:
            await self._client.query(text)
            async for msg in self._client.receive_response():
                if isinstance(msg, AssistantMessage):
                    for block in msg.content:
                        if isinstance(block, TextBlock) and block.text.strip():
                            yield block.text.strip()
                        elif isinstance(block, ToolUseBlock):
                            ui.info(f"⚙ {block.name.removeprefix(f'mcp__{MCP_SERVER_NAME}__')}")
                elif isinstance(msg, ResultMessage):
                    if msg.session_id:
                        self.session_id = msg.session_id
                        self._save_session()
                    if msg.is_error:
                        raise RuntimeError(msg.result or msg.subtype)
        except Exception:
            # Verbindung beim nächsten Mal neu aufbauen (das Gespräch wird dabei fortgesetzt).
            await self._safe_disconnect()
            raise

    async def reset(self) -> None:
        """Beginnt ein frisches Gespräch (vergisst den Gesprächsverlauf, nicht das Gedächtnis)."""
        await self._safe_disconnect()
        self.session_id = None
        SESSION_FILE.unlink(missing_ok=True)
        await self.connect()

    # --- Sitzung merken (damit das Gespräch Neustarts übersteht) --------------
    def _load_session(self) -> str | None:
        try:
            return json.loads(SESSION_FILE.read_text(encoding="utf-8")).get("session_id")
        except (OSError, json.JSONDecodeError):
            return None

    def _save_session(self) -> None:
        SESSION_FILE.parent.mkdir(exist_ok=True)
        SESSION_FILE.write_text(json.dumps({"session_id": self.session_id}), encoding="utf-8")


def _make_handler(s: Skill):
    async def handler(args: dict[str, Any]) -> dict[str, Any]:
        try:
            text = await s.ausfuehren(args or {})
            return {"content": [{"type": "text", "text": text}]}
        except Exception as e:  # noqa: BLE001 – Fehler als Werkzeug-Ergebnis zurückgeben
            ui.warn(f"Fähigkeit '{s.name}' fehlgeschlagen: {e}")
            return {"content": [{"type": "text", "text": f"Fehler: {e}"}], "is_error": True}

    return handler
