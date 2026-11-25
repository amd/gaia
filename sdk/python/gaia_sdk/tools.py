# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Turning a Python function into a tool the engine can offer the model."""

from __future__ import annotations

import inspect
import re
import typing
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional, Union

_JSON_TYPES = {str: "string", int: "integer", float: "number", bool: "boolean"}
_NONE = type(None)


class ToolRefusal(Exception):
    """Raise from a tool that ran and said no.

    The model gets ``{"status": "refused", "message": ...}`` as an ordinary
    result to explain. Any other exception is reported to it as a failed call.
    """

    def __init__(self, message: str, **fields: Any):
        if not isinstance(message, str) or not message.strip():
            raise ValueError("ToolRefusal needs the reason as a non-empty string.")
        super().__init__(message)
        self.message = message
        self.fields = fields

    def as_result(self) -> Dict[str, Any]:
        return {**self.fields, "status": "refused", "message": self.message}


@dataclass(frozen=True)
class Tool:
    """A function plus the name, description and schema the model is shown."""

    name: str
    description: str
    parameters: Dict[str, Any]
    function: Callable[..., Any]
    #: How many times the tool may run in one turn; None for no limit.
    max_calls_per_turn: Optional[int] = None
    #: After a successful call, no other tool runs this turn.
    terminal: bool = False

    def declaration(self) -> Dict[str, Any]:
        declared: Dict[str, Any] = {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
        }
        if self.max_calls_per_turn is not None:
            declared["max_calls_per_turn"] = self.max_calls_per_turn
        if self.terminal:
            declared["terminal"] = True
        return declared


def _schema_for(annotation: Any, where: str) -> Dict[str, Any]:
    if annotation is inspect.Parameter.empty:
        raise TypeError(
            f"{where} has no type hint. The model is told each argument's type, "
            "so annotate it (str, int, float, bool, list[...] or dict)."
        )
    origin = typing.get_origin(annotation)
    args = typing.get_args(annotation)
    if origin is Union and _NONE in args:
        rest = [a for a in args if a is not _NONE]
        if len(rest) == 1:
            return _schema_for(rest[0], where)
    if origin is typing.Literal:
        return {"type": _JSON_TYPES.get(type(args[0]), "string"), "enum": list(args)}
    if annotation in _JSON_TYPES:
        return {"type": _JSON_TYPES[annotation]}
    if annotation is list or origin is list:
        schema: Dict[str, Any] = {"type": "array"}
        if args:
            schema["items"] = _schema_for(args[0], where)
        return schema
    if annotation is dict or origin is dict:
        return {"type": "object"}
    raise TypeError(
        f"{where} has type {annotation!r}, which has no JSON form. Use str, int, "
        "float, bool, list[...], dict, Optional[...] or Literal[...]."
    )


def _arg_descriptions(doc: str) -> Dict[str, str]:
    """``name: text`` lines under an ``Args:`` heading."""
    found: Dict[str, str] = {}
    in_args = False
    for line in doc.splitlines():
        stripped = line.strip()
        if stripped in ("Args:", "Arguments:", "Parameters:"):
            in_args = True
            continue
        if in_args:
            if not stripped or (stripped.endswith(":") and " " not in stripped):
                in_args = False
                continue
            match = re.match(r"(\w+)\s*(?:\([^)]*\))?:\s*(.+)", stripped)
            if match:
                found[match.group(1)] = match.group(2)
    return found


def tool(
    function: Optional[Callable[..., Any]] = None,
    *,
    name: Optional[str] = None,
    description: Optional[str] = None,
    max_calls_per_turn: Optional[int] = None,
    terminal: bool = False,
) -> Any:
    """Make a function a tool. Use as ``@tool`` or ``@tool(name=..., description=...)``.

    The description is the docstring's first paragraph, and each argument's
    type comes from its type hint, so both are required.

    Args:
        max_calls_per_turn: How many times the tool may run in one turn,
            whatever its arguments. A call past the limit is not run: the
            model is told so and asked to answer from the result it has.
        terminal: After a successful call, no other tool runs this turn, so
            the model's next reply is its answer. For a tool like a handoff.
    """
    if max_calls_per_turn is not None and (
        isinstance(max_calls_per_turn, bool)
        or not isinstance(max_calls_per_turn, int)
        or max_calls_per_turn < 1
    ):
        raise TypeError(
            "max_calls_per_turn must be a positive integer or None, got "
            f"{max_calls_per_turn!r}."
        )
    if not isinstance(terminal, bool):
        raise TypeError(f"terminal must be True or False, got {terminal!r}.")

    def build(f: Callable[..., Any]) -> Tool:
        doc = inspect.getdoc(f) or ""
        summary = description or doc.split("\n\n")[0].replace("\n", " ").strip()
        if not summary:
            raise TypeError(
                f"Tool {f.__name__!r} has no description. Give it a docstring: "
                "it is what the model reads to decide when to call it."
            )
        described = _arg_descriptions(doc)
        properties: Dict[str, Any] = {}
        required = []
        for parameter in inspect.signature(f).parameters.values():
            if parameter.kind in (parameter.VAR_POSITIONAL, parameter.VAR_KEYWORD):
                raise TypeError(
                    f"Tool {f.__name__!r} takes *args or **kwargs, which the model "
                    "cannot be told about. Name each argument."
                )
            hints = typing.get_type_hints(f)
            schema = _schema_for(
                hints.get(parameter.name, parameter.annotation),
                f"Argument {parameter.name!r} of tool {f.__name__!r}",
            )
            if parameter.name in described:
                schema["description"] = described[parameter.name]
            properties[parameter.name] = schema
            if parameter.default is inspect.Parameter.empty:
                required.append(parameter.name)
        return Tool(
            name=name or f.__name__,
            description=summary,
            parameters={
                "type": "object",
                "properties": properties,
                "required": required,
            },
            function=f,
            max_calls_per_turn=max_calls_per_turn,
            terminal=terminal,
        )

    return build(function) if function is not None else build
