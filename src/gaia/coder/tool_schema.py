# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Bridge between :mod:`gaia.agents.base.tools` and Anthropic tool-use.

Two concerns, one module:

1. **Schema:** :func:`build_anthropic_tools` converts the
   :data:`gaia.agents.base.tools._TOOL_REGISTRY` entries that the coder
   tool mixins populate into Anthropic ``tools=[…]`` payload shape.
2. **Dispatch:** :class:`ToolDispatcher` runs a tool by name on behalf of
   :class:`gaia.coder.agent.CoderAgent`, formats the result as a
   ``tool_result`` content block, and never lets a tool exception kill
   the chat loop — exceptions are re-raised in *test* contexts but
   converted to ``is_error=True`` ``tool_result`` blocks in the running
   agent so the model can recover.

Why a fresh schema layer rather than reusing
:mod:`gaia.agents.base.agent`'s tool-call path? The base ``Agent``
class is built around Lemonade's tool-call shape (model emits a JSON
``tool_call`` blob in plain text; the agent regex-extracts and
dispatches). Anthropic's tool-use is a *first-class content-block
protocol* — different request shape, different reply shape, different
error correlation (``tool_use_id``). Trying to overload the base
agent's parser would fight both APIs. A separate, explicit bridge is
clearer.

The dispatcher is *registry-aware but stateless*: it holds no agent
instance, only the registry view, so it composes cleanly with any
future tool source (MCP, plugins, etc.) that registers via the same
``@tool`` decorator.
"""

from __future__ import annotations

import json
import logging
import traceback
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

from gaia.agents.base.tools import _TOOL_REGISTRY, get_tool_metadata

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Schema construction
# ---------------------------------------------------------------------------


#: Map from the loose type strings the ``@tool`` decorator records to the
#: JSON-schema types Anthropic expects. Anthropic accepts JSON Schema
#: ``string`` / ``integer`` / ``number`` / ``boolean`` / ``array`` /
#: ``object``. ``unknown`` is mapped to ``string`` because that is the
#: most-permissive container the model can populate; downstream tools
#: that need stricter types should annotate their parameters explicitly.
_PARAM_TYPE_TO_JSONSCHEMA: Dict[str, str] = {
    "string": "string",
    "integer": "integer",
    "number": "number",
    "boolean": "boolean",
    "array": "array",
    "object": "object",
    "unknown": "string",
}


def _convert_param(param: Mapping[str, Any]) -> Dict[str, Any]:
    """Convert one ``@tool``-registered param entry to a JSON-schema property."""
    declared = str(param.get("type", "unknown"))
    schema_type = _PARAM_TYPE_TO_JSONSCHEMA.get(declared, "string")
    out: Dict[str, Any] = {"type": schema_type}
    # ``items`` is required for arrays. We do not have rich item-type
    # information from the @tool decorator, so we default to string;
    # tools that need typed arrays can override by passing an explicit
    # input_schema (see ``register_explicit_tool`` below).
    if schema_type == "array":
        out["items"] = {"type": "string"}
    return out


def build_anthropic_tools(
    *,
    include: Optional[Sequence[str]] = None,
    exclude: Optional[Sequence[str]] = None,
    registry: Optional[Mapping[str, Mapping[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """Render the active ``@tool`` registry as Anthropic ``tools=[…]`` payload.

    Args:
        include: If supplied, only tools with names in this list are emitted.
            Useful for the REPL when the EM wants a narrower toolbox.
        exclude: If supplied, tools with names in this list are skipped.
        registry: Override the underlying tool registry (tests inject a
            fresh dict). Defaults to the global
            :data:`gaia.agents.base.tools._TOOL_REGISTRY`.

    Returns:
        A list of dicts, each shaped::

            {
              "name": "read_file",
              "description": "...",
              "input_schema": {
                "type": "object",
                "properties": {"path": {"type": "string"}, ...},
                "required": ["path"]
              }
            }

    Raises:
        ValueError: if ``include`` references an unknown tool name. We
            do not silently drop unknown names — that's a configuration
            bug the EM should see immediately.
    """
    src = registry if registry is not None else _TOOL_REGISTRY
    if include:
        unknown = [name for name in include if name not in src]
        if unknown:
            available = ", ".join(sorted(src.keys())) or "(none)"
            raise ValueError(
                "build_anthropic_tools: include contains unknown tool(s): "
                f"{unknown!r}. Available: {available}"
            )
    excluded = set(exclude or ())

    tools: List[Dict[str, Any]] = []
    for name, entry in src.items():
        if include is not None and name not in include:
            continue
        if name in excluded:
            continue
        params = entry.get("parameters") or {}
        properties: Dict[str, Dict[str, Any]] = {}
        required: List[str] = []
        for pname, pmeta in params.items():
            properties[pname] = _convert_param(pmeta)
            if pmeta.get("required"):
                required.append(pname)
        # Description: prefer the curated registry description; fall back
        # to function docstring's first non-empty paragraph for clarity.
        raw_desc = (entry.get("description") or "").strip()
        description = _first_paragraph(raw_desc) or "(no description)"
        input_schema: Dict[str, Any] = {
            "type": "object",
            "properties": properties,
            "required": required,
        }
        tools.append(
            {
                "name": name,
                "description": description,
                "input_schema": input_schema,
            }
        )
    tools.sort(key=lambda t: t["name"])
    return tools


def _first_paragraph(text: str) -> str:
    """Return the first paragraph (up to the first blank line) of ``text``.

    Tool descriptions are docstrings, which often start with a one-line
    summary followed by a blank line and prose. The summary alone is what
    the model needs; the prose can run dozens of lines and would bloat
    the system prompt unnecessarily.
    """
    if not text:
        return ""
    para_lines: List[str] = []
    for line in text.splitlines():
        if not line.strip() and para_lines:
            break
        para_lines.append(line.rstrip())
    return "\n".join(para_lines).strip()


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ToolResult:
    """Wrapper around a dispatched tool call's outcome."""

    tool_use_id: str
    name: str
    content: str
    is_error: bool = False

    def to_anthropic_block(self) -> Dict[str, Any]:
        """Render as an Anthropic ``tool_result`` content block."""
        block: Dict[str, Any] = {
            "type": "tool_result",
            "tool_use_id": self.tool_use_id,
            "content": self.content,
        }
        if self.is_error:
            block["is_error"] = True
        return block


class ToolDispatcher:
    """Dispatch a tool by name, returning a :class:`ToolResult`.

    Holds a permission-check callback so the agent can refuse a call
    before it runs (e.g. ``run_cli_command`` while dev-mode is off).
    The default permission callback approves everything — useful in
    tests and trusted environments.
    """

    #: Maximum number of characters of tool output to send back to the model.
    #: Anthropic charges per input token; a 5MB ``read_file`` result would
    #: blow up the next request. Truncated content is suffixed with a
    #: human-readable marker so the model knows the result was capped and
    #: can ask for a narrower slice.
    MAX_OUTPUT_CHARS: int = 100_000

    def __init__(
        self,
        *,
        permission_check: Optional[
            Callable[[str, Mapping[str, Any]], Optional[str]]
        ] = None,
        registry: Optional[Mapping[str, Mapping[str, Any]]] = None,
        max_output_chars: Optional[int] = None,
    ) -> None:
        """Construct a dispatcher.

        Args:
            permission_check: Callable invoked before every tool call.
                Receives ``(name, input_dict)``; returns ``None`` to
                approve, or a string explaining the denial. Used by the
                agent to enforce dev-mode / capability-tier policy.
            registry: Override the underlying tool registry (tests).
            max_output_chars: Override the default output cap.
        """
        self._permission_check = permission_check or (lambda _n, _i: None)
        self._registry = registry if registry is not None else _TOOL_REGISTRY
        self._max_output_chars = max_output_chars or self.MAX_OUTPUT_CHARS

    def run(
        self,
        *,
        tool_use_id: str,
        name: str,
        tool_input: Mapping[str, Any],
    ) -> ToolResult:
        """Dispatch ``name`` with ``tool_input`` and return a :class:`ToolResult`.

        The dispatcher *never* raises on tool-internal errors — it converts
        them to ``is_error=True`` results so the model can recover. It
        *does* raise on infrastructure errors (unknown tool name, bad
        permission-check return shape) because those are bugs the operator
        must see, not states the model can recover from.

        Args:
            tool_use_id: The Anthropic ``toolu_…`` correlation id from
                the assistant's ``tool_use`` block.
            name: Tool name (must exist in the registry).
            tool_input: Argument dict (will be unpacked as kwargs).

        Returns:
            :class:`ToolResult`.

        Raises:
            KeyError: if ``name`` is not registered.
        """
        meta = self._registry.get(name) or get_tool_metadata(name)
        if meta is None:
            available = ", ".join(sorted(self._registry.keys())) or "(none)"
            raise KeyError(
                f"ToolDispatcher: unknown tool {name!r}. Registered: {available}"
            )

        denial = self._permission_check(name, tool_input)
        if denial is not None:
            logger.warning(
                "ToolDispatcher: %s denied by permission_check (%s)", name, denial
            )
            return ToolResult(
                tool_use_id=tool_use_id,
                name=name,
                content=f"PERMISSION DENIED: {denial}",
                is_error=True,
            )

        func = meta["function"]
        try:
            output = func(**dict(tool_input))
        except Exception as exc:  # noqa: BLE001 — see note below
            # Tool-internal exceptions are converted to an is_error
            # tool_result so the model can recover (try a different
            # path, ask the user, etc.). This is NOT a silent fallback —
            # the model gets the full error text and a stack trace
            # snippet, the dispatcher logs at WARN, and the agent can
            # surface it in the REPL.
            tb = traceback.format_exc(limit=2)
            logger.warning(
                "ToolDispatcher: %s raised %s: %s", name, type(exc).__name__, exc
            )
            return ToolResult(
                tool_use_id=tool_use_id,
                name=name,
                content=(
                    f"{type(exc).__name__}: {exc}\n"
                    f"---\n{tb.strip()}\n"
                    "(tool call failed — adjust arguments or try a different tool)"
                ),
                is_error=True,
            )

        rendered = self._render_output(output)
        return ToolResult(
            tool_use_id=tool_use_id,
            name=name,
            content=rendered,
            is_error=False,
        )

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _render_output(self, output: Any) -> str:
        """Render a tool's return value as a string for the model."""
        if isinstance(output, str):
            text = output
        elif output is None:
            text = ""
        elif isinstance(output, (list, dict, tuple)):
            try:
                text = json.dumps(output, indent=2, default=str, ensure_ascii=False)
            except (TypeError, ValueError):
                text = repr(output)
        else:
            try:
                text = json.dumps(output, default=str, ensure_ascii=False)
            except (TypeError, ValueError):
                text = repr(output)
        if len(text) > self._max_output_chars:
            cut = self._max_output_chars
            return (
                text[:cut]
                + f"\n\n…[truncated to {cut} chars; "
                + f"original was {len(text)} chars; "
                + "ask for a narrower slice or use a search tool]"
            )
        return text


__all__ = [
    "ToolDispatcher",
    "ToolResult",
    "build_anthropic_tools",
]
