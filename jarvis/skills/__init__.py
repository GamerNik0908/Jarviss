"""Funktionen von Jarvis.

Jede Datei (oder jedes Paket) in diesem Ordner ist eine Funktion und wird beim Start automatisch geladen
(Ausnahme: Namen, die mit "_" beginnen). Die erste Zeile des Modul-Docstrings ist der Name der Funktion
für den Nutzer, z. B. "Telegram: Nachrichten zwischen Handy und Jarvis".

Bausteine (Details in jarvis/api.py und CLAUDE.md):

    from typing import Annotated
    from jarvis.api import skill, dienst

    @skill
    def wuerfeln(seiten: Annotated[int, "Anzahl der Seiten"] = 6) -> str:
        \"\"\"Würfelt eine Zahl zwischen 1 und `seiten`.\"\"\"
        ...

    @skill(nachfragen="immer", frage="Soll ich {datei} wirklich drucken?")
    def drucken(datei: str) -> str: ...

    @dienst
    async def empfang() -> None:          # läuft dauerhaft im Hintergrund
        while True:
            ...
"""

from __future__ import annotations

import asyncio
import importlib
import inspect
import pkgutil
import re
import string
import sys
import traceback
import types
from dataclasses import dataclass, field
from typing import Annotated, Any, Callable, Literal, Union, get_args, get_origin, get_type_hints

from jarvis.einrichtung import funktion_von_modul
from jarvis.sicherheit import NACHFRAGEN

NAME_MUSTER = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


@dataclass
class Skill:
    name: str
    beschreibung: str
    schema: dict[str, Any]
    funktion: Callable[..., Any]
    nachfragen: str = "nie"          # "nie" | "wenn_fremd" | "immer"
    frage: str = ""                  # Bestätigungsfrage, darf {parameter} enthalten
    fremde_inhalte: bool = False     # Ergebnis enthält Inhalte Dritter (Webseiten, fremde Nachrichten …)

    @property
    def modul(self) -> str:
        return self.funktion.__module__

    @property
    def gruppe(self) -> str:
        return funktion_von_modul(self.modul)

    async def ausfuehren(self, args: dict[str, Any]) -> str:
        if inspect.iscoroutinefunction(self.funktion):
            result = await self.funktion(**args)
        else:
            result = await asyncio.to_thread(self.funktion, **args)
        return "Erledigt." if result is None else str(result)

    def frage_text(self, args: dict[str, Any]) -> str:
        if self.frage:
            try:
                return self.frage.format(**{**_defaults(self.funktion), **args})
            except (KeyError, IndexError, ValueError):
                pass
        details = ", ".join(f"{k}={v!r}" for k, v in args.items())
        return f"Darf ich '{self.name}' ausführen ({details})?"


@dataclass
class Dienst:
    name: str
    funktion: Callable[[], Any]
    beschreibung: str = ""

    @property
    def modul(self) -> str:
        return self.funktion.__module__

    @property
    def gruppe(self) -> str:
        return funktion_von_modul(self.modul)


@dataclass
class FunktionInfo:
    name: str
    titel: str
    skills: list[Skill] = field(default_factory=list)
    dienste: list[Dienst] = field(default_factory=list)


_REGISTRY: dict[str, Skill] = {}
_DIENSTE: dict[str, Dienst] = {}
_HINWEISE: dict[str, str] = {}


def _defaults(fn: Callable[..., Any]) -> dict[str, Any]:
    return {n: p.default for n, p in inspect.signature(fn).parameters.items()
            if p.default is not inspect.Parameter.empty}


def _type_to_schema(tp: Any) -> dict[str, Any]:
    origin = get_origin(tp)
    if origin is Annotated:
        base, *meta = get_args(tp)
        schema = _type_to_schema(base)
        for m in meta:
            if isinstance(m, str):
                schema["description"] = m
        return schema
    if origin is Literal:
        werte = list(get_args(tp))
        return {"type": "string" if all(isinstance(w, str) for w in werte) else "integer", "enum": werte}
    if origin is Union or isinstance(tp, types.UnionType):
        non_none = [a for a in get_args(tp) if a is not type(None)]
        if len(non_none) == 1:
            return _type_to_schema(non_none[0])
        return {"anyOf": [_type_to_schema(a) for a in non_none]}
    if tp is str:
        return {"type": "string"}
    if tp is bool:
        return {"type": "boolean"}
    if tp is int:
        return {"type": "integer"}
    if tp is float:
        return {"type": "number"}
    if tp is list or origin is list:
        args = get_args(tp)
        return {"type": "array", "items": _type_to_schema(args[0])} if args else {"type": "array"}
    if tp is dict or origin is dict:
        return {"type": "object"}
    return {"type": "string"}


def schema_from_function(fn: Callable[..., Any]) -> dict[str, Any]:
    """Baut ein JSON-Schema aus den Parametern und Typ-Hinweisen einer Funktion."""
    hints = get_type_hints(fn, include_extras=True)
    properties: dict[str, Any] = {}
    required: list[str] = []
    for name, param in inspect.signature(fn).parameters.items():
        if param.kind in (param.VAR_POSITIONAL, param.VAR_KEYWORD):
            continue
        properties[name] = _type_to_schema(hints.get(name, str))
        if param.default is inspect.Parameter.empty:
            required.append(name)
        else:
            properties[name]["default"] = param.default
    return {"type": "object", "properties": properties, "required": required}


def _pruefe_frage(name: str, frage: str, fn: Callable[..., Any]) -> None:
    params = set(inspect.signature(fn).parameters)
    for _, feld, _, _ in string.Formatter().parse(frage):
        if feld and feld.split(".")[0].split("[")[0] not in params:
            raise ValueError(f"Fähigkeit '{name}': Platzhalter {{{feld}}} in frage ist kein Parameter")


def skill(fn: Callable[..., Any] | None = None, *, name: str | None = None, beschreibung: str | None = None,
          nachfragen: str = "nie", frage: str = "", fremde_inhalte: bool = False) -> Any:
    """Decorator: macht eine Funktion zu einem Werkzeug, das Jarvis' Gehirn aufrufen kann.

    nachfragen="immer"      -> Jarvis fragt den Nutzer vor JEDER Ausführung (Drucken, Nachrichten an andere,
                               Löschen, Käufe, Geräte mit Hitze/Motor). frage= ist der Bestätigungstext.
    nachfragen="wenn_fremd" -> fragt nur, wenn der Auftrag nicht direkt vom Nutzer kommt oder im selben Zug
                               fremde Inhalte gelesen wurden (dauerhafte Änderungen, Programme starten).
    fremde_inhalte=True     -> das Ergebnis enthält Inhalte Dritter (Webseiten, fremde Nachrichten, E-Mails).
    """

    def register(f: Callable[..., Any]) -> Callable[..., Any]:
        skill_name = name or f.__name__
        desc = beschreibung or inspect.getdoc(f) or ""
        if not desc:
            raise ValueError(f"Fähigkeit '{skill_name}' braucht einen Docstring als Beschreibung.")
        if not NAME_MUSTER.match(skill_name):
            raise ValueError(f"Ungültiger Name '{skill_name}' (nur a-z, A-Z, 0-9, _ und -; keine Umlaute)")
        if nachfragen not in NACHFRAGEN:
            raise ValueError(f"Fähigkeit '{skill_name}': nachfragen muss eins von {NACHFRAGEN} sein")
        if frage:
            _pruefe_frage(skill_name, frage, f)
        existing = _REGISTRY.get(skill_name)
        if existing and existing.modul != f.__module__:
            raise ValueError(f"Fähigkeit '{skill_name}' existiert doppelt ({existing.modul} und {f.__module__}).")
        _REGISTRY[skill_name] = Skill(skill_name, desc, schema_from_function(f), f, nachfragen, frage,
                                      fremde_inhalte)
        return f

    return register(fn) if fn is not None else register


def dienst(fn: Callable[[], Any] | None = None, *, name: str | None = None) -> Any:
    """Decorator: eine `async def` ohne Parameter, die dauerhaft im Hintergrund läuft (Nachrichten empfangen,
    Ordner überwachen, Druckstatus abfragen …). Jarvis startet sie nach dem Start, startet sie nach Abstürzen
    neu und beendet sie beim Herunterfahren. Fehlt eine Einrichtung (FehlendeEinrichtung), wartet sie, bis
    der Wert eingerichtet ist."""

    def register(f: Callable[[], Any]) -> Callable[[], Any]:
        dienst_name = name or f.__name__
        if not inspect.iscoroutinefunction(f):
            raise TypeError(f"Dienst '{dienst_name}' muss eine 'async def' sein")
        pflicht = [p for p in inspect.signature(f).parameters.values() if p.default is inspect.Parameter.empty]
        if pflicht:
            raise TypeError(f"Dienst '{dienst_name}' darf keine Pflicht-Parameter haben")
        if not NAME_MUSTER.match(dienst_name):
            raise ValueError(f"Ungültiger Dienstname '{dienst_name}'")
        existing = _DIENSTE.get(dienst_name)
        if existing and existing.modul != f.__module__:
            raise ValueError(f"Dienst '{dienst_name}' existiert doppelt ({existing.modul} und {f.__module__}).")
        _DIENSTE[dienst_name] = Dienst(dienst_name, f, (inspect.getdoc(f) or "").split("\n")[0])
        return f

    return register(fn) if fn is not None else register


def hinweis_registrieren(modul: str, text: str) -> None:
    _HINWEISE[modul] = text.strip()


def hinweise() -> list[str]:
    return list(_HINWEISE.values())


def dienste() -> list[Dienst]:
    return list(_DIENSTE.values())


def make_skill(fn: Callable[..., Any], name: str | None = None, *, nachfragen: str = "nie",
               frage: str = "") -> Skill:
    """Erzeugt ein Skill-Objekt, ohne es global zu registrieren (für Kern-Werkzeuge)."""
    return Skill(name or fn.__name__, inspect.getdoc(fn) or "", schema_from_function(fn), fn, nachfragen, frage)


def _titel(modulname: str) -> str:
    modul = sys.modules.get(modulname)
    doc = (getattr(modul, "__doc__", None) or "").strip()
    return doc.split("\n")[0].strip() if doc else modulname.rsplit(".", 1)[-1]


def funktionen() -> dict[str, FunktionInfo]:
    """Alle geladenen Funktionen, gruppiert nach Modul/Paket in jarvis/skills/."""
    gruppen: dict[str, FunktionInfo] = {}
    for s in _REGISTRY.values():
        g = gruppen.setdefault(s.gruppe, FunktionInfo(s.gruppe, _titel(f"{__name__}.{s.gruppe}")))
        g.skills.append(s)
    for d in _DIENSTE.values():
        g = gruppen.setdefault(d.gruppe, FunktionInfo(d.gruppe, _titel(f"{__name__}.{d.gruppe}")))
        g.dienste.append(d)
    return gruppen


def verfuegbare_module() -> list[str]:
    return [m.name for m in pkgutil.iter_modules(__path__) if not m.name.startswith("_")]


def load_skills(strict: bool = False, aus: set[str] | frozenset[str] = frozenset()) -> tuple[list[Skill], list[str]]:
    """Importiert alle Funktions-Module (außer ausgeschalteten). Gibt (Fähigkeiten, Fehlermeldungen) zurück.

    Mit strict=True wird beim ersten Fehler eine Exception geworfen (Selbsttest).
    """
    errors: list[str] = []
    for modulname in verfuegbare_module():
        if modulname in aus:
            continue
        try:
            importlib.import_module(f"{__name__}.{modulname}")
        except Exception:
            if strict:
                raise
            errors.append(f"Funktion '{modulname}' konnte nicht geladen werden:\n{traceback.format_exc()}")
    return list(_REGISTRY.values()), errors
