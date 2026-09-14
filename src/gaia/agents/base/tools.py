# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Tool registry and decorator for agent tools.
"""

import inspect
import logging
import re
import threading
import types
import typing
from typing import Any, Callable, Dict, Optional

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Tool registry to store registered tools
_TOOL_REGISTRY: dict[str, dict] = {}
_SUPPORTED_TOOL_KWARGS = ("atomic", "display_label", "timeout")


class ToolCancelled(Exception):
    """Raised inside a tool body once the agent has abandoned the call."""

    def __init__(self, message: str = "tool call was cancelled after it timed out"):
        super().__init__(message)


# Per-worker cancellation flag, set by ``Agent._call_tool_bounded`` when a tool
# overruns its window. Python cannot kill a thread, so an abandoned worker runs
# to completion unless it opts in by checking this — and for a multi-minute tool
# that means a second job racing the first on the same hardware (#2600).
_cancellation = threading.local()


def set_tool_cancel_event(event: Optional[threading.Event]) -> None:
    """Bind *event* as the cancellation flag for the calling thread."""
    _cancellation.event = event


def tool_cancelled() -> bool:
    """True once the agent has stopped waiting for this tool.

    Long-running tools should poll this between stages and stop early. Anything
    that finishes well inside its timeout can ignore it.
    """
    event = getattr(_cancellation, "event", None)
    return event is not None and event.is_set()


def raise_if_cancelled() -> None:
    """Abort a tool body the agent has already given up on."""
    if tool_cancelled():
        raise ToolCancelled()


# Annotation -> registry type name. Anything absent stays "unknown", which
# downstream consumers read as "no declared type" rather than a contradiction.
_ANNOTATION_TYPES: dict[Any, str] = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
    list: "array",
    tuple: "array",
    set: "array",
    frozenset: "array",
    dict: "object",
}

_ARGS_HEADER_RE = re.compile(r"^[ \t]*(?:Args|Arguments|Parameters)[ \t]*:[ \t]*$")
_NEXT_SECTION_RE = re.compile(
    r"^[ \t]*(?:Returns?|Yields?|Raises|Examples?|Notes?|Attributes|Warns|"
    r"Warnings?|See Also|Todo)[ \t]*:"
)
_ARG_LINE_RE = re.compile(
    r"^[ \t]*(\*{0,2}[A-Za-z_]\w*)[ \t]*(?:\([^)]*\))?[ \t]*:(.*)$"
)


def _resolve_hints(func: Callable) -> Dict[str, Any]:
    """Resolve a function's annotations, evaluating PEP 563 string forms.

    ``inspect.signature()`` never evaluates postponed annotations (modules with
    ``from __future__ import annotations`` hand back the literal string
    ``"Optional[List[str]]"``), so every such module would otherwise fall
    through ``_infer_param_type`` to ``"unknown"``. Falls back to an empty dict
    when resolution itself raises (locally-scoped or forward-ref names that
    ``get_type_hints`` cannot see) so the caller can fall back to the raw
    ``param.annotation`` per-parameter.
    """
    try:
        return typing.get_type_hints(func)
    except Exception:
        return {}


def _infer_param_type(annotation: Any) -> str:
    """Map a parameter annotation onto a registry type name.

    Unwraps ``Optional[X]`` / ``X | None`` and generic aliases (``List[str]``,
    ``Dict[str, Any]``) so containers are advertised as ``array``/``object``
    instead of falling through to the ``string`` default in the JSON schema.
    """
    if annotation is inspect.Parameter.empty:
        return "unknown"

    origin = typing.get_origin(annotation)
    if origin is typing.Union or origin is types.UnionType:
        non_none = [a for a in typing.get_args(annotation) if a is not types.NoneType]
        # A union of two real types has no single JSON type to advertise.
        return _infer_param_type(non_none[0]) if len(non_none) == 1 else "unknown"
    if origin is not None:
        annotation = origin

    try:
        return _ANNOTATION_TYPES.get(annotation, "unknown")
    except TypeError:  # unhashable annotation (e.g. a bare literal)
        return "unknown"


def _parse_arg_descriptions(docstring: Optional[str]) -> Dict[str, str]:
    """Extract per-argument text from a Google-style ``Args:`` block.

    The model reads ``properties.<arg>.description`` at the moment it fills the
    argument slot; without this the constraint only exists in the bundled
    docstring prose (#3581).
    """
    if not docstring:
        return {}

    descriptions: Dict[str, str] = {}
    current: Optional[str] = None
    arg_indent: Optional[int] = None
    in_args = False

    for line in inspect.cleandoc(docstring).splitlines():
        if not in_args:
            in_args = bool(_ARGS_HEADER_RE.match(line))
            continue

        stripped = line.strip()
        if not stripped:
            continue
        if _NEXT_SECTION_RE.match(line):
            break

        expanded = line.expandtabs()
        indent = len(expanded) - len(expanded.lstrip())
        if arg_indent is None:
            arg_indent = indent
        if indent > arg_indent:
            if current:
                descriptions[current] = f"{descriptions[current]} {stripped}".strip()
            continue
        if indent < arg_indent:
            break

        match = _ARG_LINE_RE.match(line)
        if not match:
            current = None
            continue
        current = match.group(1).lstrip("*")
        descriptions[current] = match.group(2).strip()

    return descriptions


def tool(
    func: Callable | None = None,
    *,
    atomic: bool = False,
    display_label: str | None = None,
    timeout: float | None = None,
    **unexpected_kwargs: object,
) -> Callable:
    """
    Decorator to register a function as a tool.
    Similar to smolagents tool decorator but simpler.

    Supports both @tool and @tool(...) syntax for backward compatibility.

    Args:
        func: Function to register as a tool (when used as @tool)
        atomic: If True, marks this tool as atomic (can execute without multi-step planning)
        display_label: Optional user-facing label for UI progress strips
        timeout: Per-tool execution limit in seconds. Overrides the global
            ``GAIA_AGENT_TOOL_TIMEOUT`` default in ``Agent._execute_tool``. Set
            this on tools that legitimately run long (e.g. image generation that
            may download a model) so they aren't capped by the global default.
            ``None`` (the default) means "use the global default".

    Returns:
        The original function or decorator, unchanged
    """

    def decorator(f: Callable) -> Callable:
        if unexpected_kwargs:
            unexpected_name = next(iter(unexpected_kwargs))
            accepted = ", ".join(_SUPPORTED_TOOL_KWARGS)
            raise TypeError(
                f"@tool(...) got unexpected keyword argument {unexpected_name!r} "
                f"for tool {f.__name__!r}. Accepted: {accepted}."
            )

        # Extract function name and signature for the tool registry
        tool_name = f.__name__
        sig = inspect.signature(f)
        arg_descriptions = _parse_arg_descriptions(f.__doc__)
        hints = _resolve_hints(f)
        params = {}

        for name, param in sig.parameters.items():
            annotation = hints.get(name, param.annotation)
            param_info = {
                "type": _infer_param_type(annotation),
                "required": param.default == inspect.Parameter.empty,
            }

            description = arg_descriptions.get(name, "").strip()
            if description:
                param_info["description"] = description

            params[name] = param_info

        # Register the tool with atomic metadata
        _TOOL_REGISTRY[tool_name] = {
            "name": tool_name,
            "description": f.__doc__ or "",
            "parameters": params,
            "function": f,
            "atomic": atomic,
            "display_label": display_label,
            "timeout": timeout,
        }

        # Return the function unchanged
        return f

    # Support both @tool and @tool(...) syntax
    if func is not None:
        # Called as @tool without parentheses
        return decorator(func)
    else:
        # Called as @tool(...) with arguments - return the decorator
        return decorator


def get_tool_display_name(tool_name: str) -> str:
    """Return the display name for a tool, resolving MCP namespacing.

    MCP tools are registered under a prefixed key (``mcp_{server}_{tool}``) to
    avoid name conflicts.  Their ``display_name`` field preserves the original
    tool name together with the server origin, e.g. ``"read_file (myserver)"``,
    so console output remains meaningful.  Native tools carry no ``display_name``
    and are returned as-is.

    Args:
        tool_name: The internal tool name as stored in ``_TOOL_REGISTRY``
            (e.g. ``"mcp_myserver_read_file"`` or ``"read_file"``).

    Returns:
        The ``display_name`` when set (MCP tools), otherwise ``tool_name``.
    """
    tool = _TOOL_REGISTRY.get(tool_name)
    if not tool:
        return tool_name
    return tool.get("display_name", tool_name)  # type: ignore[no-any-return]


def get_tool_display_label(tool_name: str) -> str:
    """Return a user-facing label for the tool suitable for UI progress strips.

    Prefers the explicit `display_label` provided on the decorator, falls
    back to the registry `description`, then finally to the raw tool name.
    """
    tool = _TOOL_REGISTRY.get(tool_name)
    if not tool:
        return None  # type: ignore[return-value]
    return tool.get("display_label")  # type: ignore[return-value]


def get_tool_metadata(tool_name: str):
    """Return the full registry entry for a tool, or ``None`` if not found.

    This is the public accessor for ``_TOOL_REGISTRY``.  Consumers outside
    the agent base layer (e.g. the SSE handler) should use this instead of
    importing ``_TOOL_REGISTRY`` directly.
    """
    return _TOOL_REGISTRY.get(tool_name)
