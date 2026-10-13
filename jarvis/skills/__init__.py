"""Fähigkeiten (Skills) von Jarvis.

Jede Datei in diesem Ordner (außer solchen, die mit "_" beginnen) wird beim Start
automatisch geladen. Eine Fähigkeit ist einfach eine Python-Funktion mit
Typ-Hinweisen und Docstring, markiert mit @skill:

    from typing import Annotated
    from jarvis.skills import skill

    @skill
    def wuerfeln(seiten: Annotated[int, "Anzahl der Seiten"] = 6) -> str:
        \"\"\"Würfelt eine Zahl zwischen 1 und `seiten`.\"\"\"
        import random
        return f"Gewürfelt: {random.randint(1, seiten)}"

Der Docstring ist die Beschreibung, die Jarvis' Gehirn sieht. Die Funktion darf
synchron oder `async` sein und sollte einen kurzen Text zurückgeben.
"""

from __future__ import annotations

import asyncio
import importlib
import inspect
import pkgutil
import traceback
import types
from dataclasses import dataclass
from typing import Annotated, Any, Callable, Union, get_args, get_origin, get_type_hints


@dataclass
class Skill:
    name: str
    beschreibung: str
    schema: dict[str, Any]
    funktion: Callable[..., Any]

    async def ausfuehren(self, args: dict[str, Any]) -> str:
        if inspect.iscoroutinefunction(self.funktion):
            result = await self.funktion(**args)
        else:
            result = await asyncio.to_thread(self.funktion, **args)
        return "Erledigt." if result is None else str(result)


_REGISTRY: dict[str, Skill] = {}


def _type_to_schema(tp: Any) -> dict[str, Any]:
    origin = get_origin(tp)
    if origin is Annotated:
        base, *meta = get_args(tp)
        schema = _type_to_schema(base)
        for m in meta:
            if isinstance(m, str):
                schema["description"] = m
        return schema
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


def skill(fn: Callable[..., Any] | None = None, *, name: str | None = None,
          beschreibung: str | None = None) -> Any:
    """Decorator: registriert eine Funktion als Fähigkeit von Jarvis."""

    def register(f: Callable[..., Any]) -> Callable[..., Any]:
        skill_name = name or f.__name__
        desc = beschreibung or inspect.getdoc(f) or ""
        if not desc:
            raise ValueError(f"Fähigkeit '{skill_name}' braucht einen Docstring als Beschreibung.")
        existing = _REGISTRY.get(skill_name)
        if existing and existing.funktion.__module__ != f.__module__:
            raise ValueError(
                f"Fähigkeit '{skill_name}' existiert doppelt "
                f"({existing.funktion.__module__} und {f.__module__})."
            )
        _REGISTRY[skill_name] = Skill(skill_name, desc, schema_from_function(f), f)
        return f

    return register(fn) if fn is not None else register


def make_skill(fn: Callable[..., Any], name: str | None = None) -> Skill:
    """Erzeugt ein Skill-Objekt, ohne es global zu registrieren (für Kern-Werkzeuge)."""
    return Skill(name or fn.__name__, inspect.getdoc(fn) or "", schema_from_function(fn), fn)


def load_skills(strict: bool = False) -> tuple[list[Skill], list[str]]:
    """Importiert alle Skill-Module. Gibt (Fähigkeiten, Fehlermeldungen) zurück.

    Mit strict=True wird beim ersten Fehler eine Exception geworfen (Selbsttest).
    """
    errors: list[str] = []
    for module in pkgutil.iter_modules(__path__):
        if module.name.startswith("_"):
            continue
        try:
            importlib.import_module(f"{__name__}.{module.name}")
        except Exception:
            if strict:
                raise
            errors.append(f"Fähigkeit '{module.name}' konnte nicht geladen werden:\n{traceback.format_exc()}")
    return list(_REGISTRY.values()), errors
