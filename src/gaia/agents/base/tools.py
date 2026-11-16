# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Tool registry and decorator for agent tools.
"""

import contextvars
import inspect
import re
import types
import typing
from contextlib import contextmanager
from typing import Any, Callable, Dict, Iterator, List, Mapping, Optional

# Set up logging via the project logger; importing this module must not
# reconfigure the root logger.
from gaia.logger import get_logger

# Re-exported for existing callers of this module.
# pylint: disable=unused-import
from gaia.tool_cancellation import (  # noqa: F401
    AbandonedWorkerLogFilter,
    ToolCancelled,
    raise_if_cancelled,
    set_tool_cancel_event,
    tool_cancelled,
)

logger = get_logger(__name__)

# Tool registry to store registered tools
_TOOL_REGISTRY: dict[str, dict] = {}
_SUPPORTED_TOOL_KWARGS = (
    "atomic",
    "display_label",
    "timeout",
    "timeout_arg",
    "preflight",
    "dedup_key",
    "registry",
    "override",
)

# Names registered so far in the agent tool build under way, or None outside one.
_BUILD_NAMES: contextvars.ContextVar[Optional[set[str]]] = contextvars.ContextVar(
    "gaia_tool_build_names", default=None
)


class DuplicateToolError(ValueError):
    """Two different functions tried to register one tool name."""


@contextmanager
def tool_build() -> Iterator[None]:
    """Scope one agent's tool registration, so a name collision inside it raises.

    The global registry outlives any one agent: a tool left by an earlier
    agent's build is replaced, not a collision.
    """
    token = _BUILD_NAMES.set(set())
    try:
        yield
    finally:
        _BUILD_NAMES.reset(token)


def _tool_source(f: Callable) -> str:
    f = inspect.unwrap(f)
    return f"{getattr(f, '__module__', '?')}.{getattr(f, '__qualname__', repr(f))}"


def _tool_location(f: Callable) -> str:
    code = getattr(inspect.unwrap(f), "__code__", None)
    if code is None:
        return _tool_source(f)
    return f"{_tool_source(f)} ({code.co_filename}:{code.co_firstlineno})"


def _same_tool(a: Callable, b: Callable) -> bool:
    """True for one ``def``, however many closures a registrar has made of it."""
    code_a = getattr(inspect.unwrap(a), "__code__", None)
    code_b = getattr(inspect.unwrap(b), "__code__", None)
    if code_a is not None and code_b is not None:
        return code_a is code_b
    return _tool_source(a) == _tool_source(b)


def _check_name_free(
    tool_name: str, f: Callable, destination: Mapping[str, Any], instance_local: bool
) -> None:
    """Raise when *tool_name* already belongs to a different tool in this tool set."""
    existing = destination.get(tool_name)
    existing_function = existing.get("function") if existing else None
    if existing_function is None or _same_tool(existing_function, f):
        return
    build = _BUILD_NAMES.get()
    if not instance_local and (build is None or tool_name not in build):
        return
    raise DuplicateToolError(
        f"Tool name {tool_name!r} is already registered by "
        f"{_tool_location(existing_function)}, and {_tool_location(f)} would "
        "silently replace it. Rename one of them, or mark the one that should "
        "win with @tool(override=True)."
    )


# Every model call re-sends the schema of every offered tool, so this text is
# billed on each step of each turn. Enforced by `python util/lint.py
# --tool-descriptions`; measure with `python util/tool_schema_tokens.py`.
MAX_TOOL_DESCRIPTION_CHARS = 500
MAX_TOOL_PARAM_DESCRIPTION_CHARS = 160

# Named exceptions to MAX_TOOL_DESCRIPTION_CHARS, not a general escape hatch.
# Both are already trimmed to safety-relevant facts only (no examples, no
# rationale) — the overage is what it costs to state which shell operators
# run vs. refuse, and the exact skill-correction contract, without which the
# model cannot use either tool safely.
TOOL_DESCRIPTION_ALLOWANCES = {
    "run_shell_command": 600,
    "remember_skill_lesson": 450,
}


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


def _array_items(annotation: Any) -> Dict[str, Any]:
    """The ``items`` schema of a list annotation: ``List[str]`` -> string."""
    origin = typing.get_origin(annotation)
    if origin is typing.Union or origin is types.UnionType:
        non_none = [a for a in typing.get_args(annotation) if a is not types.NoneType]
        return _array_items(non_none[0]) if len(non_none) == 1 else {}
    args = [a for a in typing.get_args(annotation) if a is not Ellipsis]
    element = _infer_param_type(args[0]) if len(args) == 1 else "unknown"
    return {} if element == "unknown" else {"type": element}


def _literal_choices(annotation: Any) -> Optional[list]:
    """The values of a ``Literal[...]`` (or ``Optional[Literal[...]]``), else None."""
    origin = typing.get_origin(annotation)
    if origin is typing.Union or origin is types.UnionType:
        non_none = [a for a in typing.get_args(annotation) if a is not types.NoneType]
        return _literal_choices(non_none[0]) if len(non_none) == 1 else None
    if origin is typing.Literal:
        return list(typing.get_args(annotation))
    return None


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


def _schema_description(docstring: Optional[str]) -> str:
    """Return the docstring text the tool schema ships, minus the ``Args:`` block.

    Every ``Args:`` entry already rides in ``properties.<arg>.description``, so
    leaving it here bills the same text twice on every model call. Leading
    indentation goes too — the raw ``__doc__`` carries the source indent of
    every continuation line.

    The ``Args:`` block ends where :func:`_parse_arg_descriptions` stops reading
    it: at the next section header, or at the first line that dedents out of the
    block. Keep the two boundaries identical or text falls between them.
    """
    if not docstring:
        return ""

    kept: list[str] = []
    in_args = False
    arg_indent: Optional[int] = None

    for line in inspect.cleandoc(docstring).splitlines():
        if in_args:
            if _NEXT_SECTION_RE.match(line):
                in_args = False
            elif not line.strip():
                continue
            else:
                expanded = line.expandtabs()
                indent = len(expanded) - len(expanded.lstrip())
                if arg_indent is None:
                    arg_indent = indent
                if indent >= arg_indent:
                    continue
                in_args = False
        elif _ARGS_HEADER_RE.match(line):
            in_args = True
            arg_indent = None
            continue
        kept.append(line)

    return "\n".join(kept).strip()


def validate_dedup_key(
    tool_name: str, dedup_key: Any, param_names: typing.Iterable[str]
) -> tuple[str, ...]:
    """Check a tool's ``dedup_key`` and return it as a tuple of argument names.

    Raises:
        TypeError: ``dedup_key`` is not a non-empty list of strings.
        ValueError: It names an argument the tool does not take.
    """
    if (
        not isinstance(dedup_key, (list, tuple))
        or not dedup_key
        or not all(isinstance(name, str) for name in dedup_key)
    ):
        raise TypeError(
            f"dedup_key for tool {tool_name!r} must be a non-empty list of "
            f"argument names, got {dedup_key!r}."
        )
    params = list(param_names)
    unknown = [name for name in dedup_key if name not in params]
    if unknown:
        raise ValueError(
            f"dedup_key for tool {tool_name!r} names unknown argument(s) "
            f"{', '.join(map(repr, unknown))}. Parameters: "
            f"{', '.join(params) or 'none'}."
        )
    return tuple(dedup_key)


def tool(
    func: Callable | None = None,
    *,
    atomic: bool = False,
    display_label: str | None = None,
    timeout: float | None = None,
    timeout_arg: str | None = None,
    preflight: Callable[[Dict[str, Any]], Optional[Dict[str, Any]]] | None = None,
    dedup_key: list[str] | tuple[str, ...] | None = None,
    registry: dict[str, dict] | None = None,
    override: bool = False,
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
        timeout_arg: Name of the tool's own parameter that sets how long the
            call may run (e.g. ``"timeout"``). The agent then waits at least
            that long plus a grace period before abandoning the call, so a
            tool asked to run for 15 minutes is not cut off at the default.
        preflight: Called with the call's arguments before the confirmation
            prompt; returns the refusal the call has already earned, or
            ``None``. The tool body must enforce the same rule itself, since
            state can change while a prompt waits.
        dedup_key: Names of the arguments the repeat-call guard compares.
            Calls that agree on these count as repeats even when other
            arguments (e.g. a free-text ``reason``) differ. ``None`` (the
            default) compares every argument.

        registry: An explicit instance-local destination. When omitted, register
            globally for existing callers. Passing an empty mapping is honored;
            no entry is ever written to the global registry in this mode.
        override: Replace a different tool already registered under this name
            in the same tool set. Without it that raises ``DuplicateToolError``;
            re-registering the same function is always allowed.

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
        if not isinstance(override, bool):
            raise TypeError(
                f"@tool(override=...) must be True or False for tool "
                f"{f.__name__!r}, got {override!r}."
            )

        # Extract function name and signature for the tool registry
        tool_name = f.__name__
        sig = inspect.signature(f)
        if timeout_arg is not None and timeout_arg not in sig.parameters:
            raise TypeError(
                f"@tool(timeout_arg={timeout_arg!r}) names no parameter of tool "
                f"{tool_name!r}. Parameters: {', '.join(sig.parameters) or 'none'}."
            )
        dedup = (
            None
            if dedup_key is None
            else validate_dedup_key(tool_name, dedup_key, sig.parameters)
        )
        arg_descriptions = _parse_arg_descriptions(f.__doc__)
        hints = _resolve_hints(f)
        params = {}

        for name, param in sig.parameters.items():
            annotation = hints.get(name, param.annotation)
            param_info = {
                "type": _infer_param_type(annotation),
                "required": param.default == inspect.Parameter.empty,
            }
            if param_info["type"] == "array":
                param_info["items"] = _array_items(annotation)
            choices = _literal_choices(annotation)
            if choices:
                # An enum lets the server's tool-call grammar rule out bad values.
                param_info["type"] = _infer_param_type(type(choices[0]))
                param_info["enum"] = choices

            description = arg_descriptions.get(name, "").strip()
            if description:
                param_info["description"] = description

            params[name] = param_info

        # Register the tool with atomic metadata
        destination = _TOOL_REGISTRY if registry is None else registry
        if not override:
            _check_name_free(tool_name, f, destination, registry is not None)
        build = _BUILD_NAMES.get()
        if build is not None and registry is None:
            build.add(tool_name)
        destination[tool_name] = {
            "name": tool_name,
            "description": _schema_description(f.__doc__),
            "parameters": params,
            "function": f,
            "atomic": atomic,
            "display_label": display_label,
            "timeout": timeout,
            "timeout_arg": timeout_arg,
            "preflight": preflight,
            "dedup_key": dedup,
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


def bare_tool_name(tool_name: str) -> str:
    """Return *tool_name* without its ``<skill-name>/`` namespace.

    A skill's tools register as ``<skill>/<tool>`` (``rss-digest/fetch_rss``).
    Checks that classify a tool by name — the confirmation set, "does this
    write a file" — read the bare name, so a skill tool called ``write_file``
    is held to the same rule as the core ``write_file``. Core and MCP names
    carry no ``/`` and come back unchanged.
    """
    return tool_name.rpartition("/")[2]


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


def get_tool_display_label(tool_name: str) -> Optional[str]:
    """Return the tool's ``display_label`` for UI progress strips, or None.

    None when the tool isn't registered or declares no ``display_label``;
    callers pick their own fallback.
    """
    tool = _TOOL_REGISTRY.get(tool_name)
    if not tool:
        return None
    return tool.get("display_label")


#: What a long tool call is doing, in the user's words, while it streams.
_TOOL_CALL_PROGRESS_LABELS = {
    "edit_file": "Writing a file edit",
    "edit_python_file": "Writing a file edit",
    "write_file": "Writing a file",
    "write_python_file": "Writing a file",
    "write_markdown_file": "Writing a file",
    "run_python": "Writing Python code",
    "run_shell_command": "Writing a command",
}


def tool_call_progress_message(tool: Optional[str], chars: int) -> str:
    """Progress line for a tool call still streaming: what it is, never what it says."""
    label = _TOOL_CALL_PROGRESS_LABELS.get(tool or "") or (
        f"Preparing {tool}" if tool else "Preparing a tool call"
    )
    return f"{label} — {chars:,} characters so far"


def get_tool_metadata(tool_name: str):
    """Return the full registry entry for a tool, or ``None`` if not found.

    This is the public accessor for ``_TOOL_REGISTRY``.  Consumers outside
    the agent base layer (e.g. the SSE handler) should use this instead of
    importing ``_TOOL_REGISTRY`` directly.
    """
    return _TOOL_REGISTRY.get(tool_name)


#: Categories a tool can have besides a ``KNOWN_TOOLS`` mixin name.
TOOL_CATEGORY_MCP = "mcp"
TOOL_CATEGORY_SKILL = "skill"
TOOL_CATEGORY_AGENT = "agent"


def tool_category(tool_name: str, entry: Mapping[str, Any]) -> str:
    """Where a registered tool comes from, for grouping it in a UI.

    Args:
        tool_name: The tool's registry key.
        entry: Its registry entry.

    Returns:
        ``"mcp"`` for a tool an MCP server provides, ``"skill"`` for one a
        skill registered (``<skill>/<tool>``), the ``KNOWN_TOOLS`` name of the
        mixin that defines it (``"rag"``, ``"file_io"``, ...), else
        ``"agent"``: a tool the agent class defines itself.
    """
    from gaia.agents.registry import KNOWN_TOOLS

    if entry.get("_mcp_server") is not None:
        return TOOL_CATEGORY_MCP
    if "/" in tool_name:
        return TOOL_CATEGORY_SKILL
    module = getattr(entry.get("function"), "__module__", None)
    for name, (module_path, _class_name) in KNOWN_TOOLS.items():
        if module == module_path:
            return name
    return TOOL_CATEGORY_AGENT


def describe_registry(
    registry: Mapping[str, Mapping[str, Any]],
) -> List[Dict[str, str]]:
    """Name, description and category of every tool in *registry*, in its order.

    Args:
        registry: A tool registry (``_TOOL_REGISTRY`` or an agent's snapshot).

    Returns:
        One ``{"name", "description", "category"}`` dict per tool. The
        description is the one the model is given.
    """
    return [
        {
            "name": name,
            "description": entry.get("description") or "",
            "category": tool_category(name, entry),
        }
        for name, entry in list(registry.items())
    ]
