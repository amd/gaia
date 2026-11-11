# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Which tools a session offers the model, and in what form.

A local model reads every tool it is offered before it can answer, and any change
to ``tools=`` makes Gemma 4 and Qwen3.6 re-read the whole conversation. So a
session starts on a small CORE list, keeps the full list primed on a slot of its
own, and moves to that list at most once:

* **Core** — a usage-based always-on set. Most turns need nothing else.
* **Jump** — the first need outside the list moves the session to every
  registered tool, and the list then stays put. A tool the user names, a recalled
  procedure's tools, a tool that out-scores the whole list on the query, or a
  ``search_tools`` hit each trigger it, before the model can misfire: llama.cpp
  builds its tool grammar from this list, so an undeclared tool is silently
  rewritten into a declared one rather than refused.
* **Skill tools** — a skill's tools join the end of the list without a jump, and
  are the one thing that can leave it: unloading the skill removes them.

Nothing is evicted. :func:`thin_tool_schema` keeps each entry short, and
:func:`format_tool_docs` hands out what it leaves out, on request.

The registry stays the source of truth for all registered tools and for
execution; the loader only picks what the prompt renderers surface. It never
imports ``MemoryMixin``: the embedding function(s) are injected by the host
agent. An embedding failure session-disables the loader with a loud log, and the
caller offers the full registry.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from dataclasses import dataclass
from typing import Callable, Dict, FrozenSet, Iterable, List, Optional, Sequence, Tuple

import numpy as np

logger = logging.getLogger(__name__)

# Inclusive cosine a tool outside the list must reach to trigger a jump. Scores
# alone cannot separate chat from tasks ("how's it going?" scored 0.452, "Make a
# thunder sound effect." 0.659, on embeddinggemma-300m, but "Search the web" only
# 0.345), so it must also beat every tool in the list by a margin: small talk
# scores about the same on everything.
DEFAULT_THRESHOLD = 0.40
JUMP_MARGIN = 0.03

# ``search_tools`` returns nothing below this: an answer of "no match" must stay
# possible, or the model keeps calling whatever came back first.
SEARCH_FLOOR = 0.20


def dynamic_tools_env_override() -> Optional[bool]:
    """Parse the ``GAIA_DYNAMIC_TOOLS`` override, or ``None`` when it is unset.

    Returns the parsed boolean (truthy set ``1``/``true``/``yes``/``on``,
    case-insensitive) when the env var is set, else ``None`` to signal "no
    override — fall back to the persisted/config value". Lives in the core
    tool-loader module so the UI settings router and the (hub-packaged) chat
    agent share one truthy set without the router depending on the chat wheel.
    """
    raw = os.getenv("GAIA_DYNAMIC_TOOLS")
    if raw is None:
        return None
    return raw.strip().lower() in ("1", "true", "yes", "on")


#: Longest description a tool keeps in the fixed ``tools=`` list, in characters.
THIN_DESCRIPTION_CHARS = 90

_FIRST_SENTENCE = re.compile(r"(.+?[.!?])(?:\s|$)")


def thin_tool_schema(schema: dict) -> dict:
    """Return *schema* reduced to what a stable ``tools=`` entry must carry.

    Every parameter stays declared: llama.cpp builds its tool-call grammar from
    this list, so an undeclared name or argument is not rejected but silently
    rewritten or dropped (``search_web`` became ``search_past_conversations``,
    "lines 10 to 20" read the whole file). The prose moves to
    :func:`format_tool_docs`.
    """
    function = schema["function"]
    text = " ".join((function.get("description") or "").split())
    match = _FIRST_SENTENCE.match(text)
    summary = match.group(1) if match else text
    if len(summary) > THIN_DESCRIPTION_CHARS:
        summary = summary[:THIN_DESCRIPTION_CHARS].rsplit(" ", 1)[0]

    parameters = function.get("parameters") or {}
    properties = {}
    for name, prop in (parameters.get("properties") or {}).items():
        properties[name] = {k: v for k, v in prop.items() if k != "description"}
    thin_parameters = {
        "type": "object",
        "properties": properties,
        "required": list(parameters.get("required") or []),
    }
    return {
        "type": "function",
        "function": {
            "name": function["name"],
            "description": summary,
            "parameters": thin_parameters,
        },
    }


def format_tool_docs(schema: dict) -> str:
    """Render a full tool schema as one ``[Tool docs: name]`` text block."""
    function = schema["function"]
    parameters = function.get("parameters") or {}
    required = set(parameters.get("required") or [])
    lines = [
        f"[Tool docs: {function['name']}]",
        (function.get("description") or "").strip(),
    ]
    properties = parameters.get("properties") or {}
    if properties:
        lines.append("Parameters:")
    for name, prop in properties.items():
        need = "required" if name in required else "optional"
        detail = f"- {name} ({prop.get('type', 'string')}, {need})"
        if prop.get("enum"):
            detail += f" one of {', '.join(str(v) for v in prop['enum'])}"
        if prop.get("description"):
            detail += f": {prop['description']}"
        lines.append(detail)
    return "\n".join(line for line in lines if line)


@dataclass(frozen=True)
class ToolBundle:
    """A named group of related tools.

    The union of CORE and every bundle must cover a profile's registry, which is
    how a new tool forces a conscious decision instead of shipping unclassified.

    Parameters
    ----------
    name:
        Bundle identifier (e.g. ``"rag_query"``, ``"file_edit"``).
    members:
        Frozenset of tool names that belong together.
    description:
        One-line summary.
    """

    name: str
    members: FrozenSet[str]
    description: str = ""


class ToolLoader:
    """Decides which registered tools appear in the prompt for a session.

    The loader does **not** modify ``_TOOL_REGISTRY`` or gate execution; it
    returns the name list the agent renders into its prompt. ``None`` from
    :meth:`select` means "session-disabled — offer the full registry" (the loud
    path on embedder failure).
    """

    def __init__(
        self,
        core_tools: Sequence[str],
        bundles: Sequence[ToolBundle],
        embed_fn: Callable[[str], "np.ndarray"],
        *,
        embed_batch_fn: Optional[Callable[[Sequence[str]], "np.ndarray"]] = None,
        threshold: float = DEFAULT_THRESHOLD,
        optional_tools: Sequence[str] = (),
    ) -> None:
        """Build a tool loader.

        Args:
            core_tools: Tool names every session starts with.
            bundles: Groups that, with CORE, must cover the registry.
            embed_fn: Single-text embedder returning an L2-normalized vector.
            embed_batch_fn: Optional batched embedder taking a list of texts and
                returning a 2-D array (one row per text). Preferred for the
                one-shot tool-doc embedding; falls back to per-doc ``embed_fn``.
            threshold: Inclusive cosine a tool outside the list must reach, and
                beat every listed tool by :data:`JUMP_MARGIN`.
            optional_tools: Configured names that a healthy registry may
                legitimately lack — environment-conditional tools, or tools a
                subclass contributes that the base profile does not. Exempt from
                :meth:`validate_registry` only; every other name there still
                fails loudly, and the list simply omits an absent tool.
        """
        self._core: FrozenSet[str] = frozenset(core_tools)
        self._bundles: List[ToolBundle] = list(bundles)
        self._optional: FrozenSet[str] = frozenset(optional_tools)
        self._embed_fn = embed_fn
        self._embed_batch_fn = embed_batch_fn
        self._threshold = float(threshold)

        # Content-keyed embedding cache: (name, sha256(doc_text)) -> vector.
        # Survives reset_session() — embeddings depend only on tool docs.
        self._embed_cache: Dict[Tuple[str, str], "np.ndarray"] = {}

        # Per-session state (cleared by reset_session()).
        self._added: List[str] = []
        self._jump_reason: Optional[str] = None
        self._turn = 0
        self._session_disabled = False

    # ── public API ───────────────────────────────────────────────────────

    @property
    def session_disabled(self) -> bool:
        """True once an embedding failure has disabled selection for the session."""
        return self._session_disabled

    @property
    def turns(self) -> int:
        """Turns selected for in this session."""
        return self._turn

    @property
    def jumped(self) -> bool:
        """True once this session has moved to the full list."""
        return self._jump_reason is not None

    def validate_registry(
        self, registry: Dict[str, dict], withheld: Iterable[str] = ()
    ) -> None:
        """Raise if CORE or any bundle names a tool absent from *registry*.

        Names in ``optional_tools`` are exempt — they are absent by construction
        on some installs (env-gated backends, subclass-only mixins), so treating
        them as drift would fail a healthy agent. So are *withheld* names: tools
        the agent's model is not offered (:mod:`gaia.agents.base.model_features`).

        Checks the config→registry direction only (no dead/misspelled CORE or
        bundle names). The host agent calls this once on first activation so
        genuine drift fails loudly at runtime. The reverse direction (a
        *registry* tool not covered by CORE/bundles) is the CI bundle test's job
        — it compares both sets exactly.
        """
        names = set(registry) | self._optional | set(withheld)
        missing_core = sorted(self._core - names)
        missing_bundle = sorted({m for b in self._bundles for m in b.members} - names)
        if missing_core or missing_bundle:
            raise ValueError(
                "ToolLoader configuration references tools missing from the "
                f"registry. Missing CORE: {missing_core or 'none'}; "
                f"missing bundle members: {missing_bundle or 'none'}. "
                "Add them to a bundle (or CORE) in tool_bundles.py, or correct "
                "the name — every registered tool must be accounted for."
            )

    def warm(self, registry: Dict[str, dict]) -> List[str]:
        """Build the tool embeddings now; return the list a new session starts on.

        Touches no turn state, so the first real turn selects exactly as it would
        have. Raises if the embedder fails: the caller is warming up on purpose
        and must hear about it.
        """
        if self._session_disabled:
            return []
        self._ensure_tool_embeddings(registry)
        return self.offered(registry)

    def offered(self, registry: Dict[str, dict]) -> List[str]:
        """The tool list as it stands, in the order the model sees it.

        Registry order, so two agents with the same tools send the same bytes:
        the list a warm-up primed is the list the first turn sends.
        """
        if self.jumped:
            return list(registry)
        listed = [name for name in registry if name in self._core]
        return listed + [
            name for name in self._added if name in registry and name not in self._core
        ]

    def select(
        self,
        query: str,
        registry: Dict[str, dict],
        *,
        skill_tools: Optional[Sequence[str]] = None,
        requested_tools: Optional[Sequence[str]] = None,
    ) -> Optional[List[str]]:
        """Settle this turn's list and return it, or ``None``.

        ``None`` is the session-disabled signal (embedder down): the caller must
        offer the full registry.

        Args:
            query: The selection query (previous + current user message).
            registry: The live tool registry (source of truth for execution).
            skill_tools: Tools the turn's active skills need. They join the list
                without a jump. Names absent from *registry* are dropped.
            requested_tools: Tools the turn cannot do without: named by the user,
                or used by a recalled procedure. One outside the list is a jump.
        """
        if self._session_disabled:
            return None
        self._turn += 1
        if skill_tools:
            self.admit_tools(skill_tools, registry)

        scores: Dict[str, float] = {}
        if not self.jumped:
            listed = set(self.offered(registry))
            if any(n in registry and n not in listed for n in requested_tools or ()):
                self.jump("requested")
            else:
                try:
                    tool_vecs = self._ensure_tool_embeddings(registry)
                    qvec = self._embed_fn(query)
                except Exception as exc:  # noqa: BLE001 — disabled + re-surfaced loudly
                    self._session_disabled = True
                    logger.warning(
                        "[ToolLoader] embedding service unreachable — dynamic tool "
                        "loading disabled for this session (all tools will be shown; "
                        "check that Lemonade is running and reload to re-enable). "
                        "Reason: %s",
                        exc,
                    )
                    return None
                scores = {n: float(np.dot(qvec, v)) for n, v in tool_vecs.items()}
                outside = max(
                    (s for n, s in scores.items() if n not in listed), default=None
                )
                inside = max(
                    (s for n, s in scores.items() if n in listed), default=-1.0
                )
                if (
                    outside is not None
                    and outside >= self._threshold
                    and outside >= inside + JUMP_MARGIN
                ):
                    self.jump("retrieval")

        offered = self.offered(registry)
        self._log_selection(query, scores, offered)
        return offered

    def jump(self, reason: str) -> None:
        """Move this session to the full list. Later calls change nothing."""
        if self.jumped:
            return
        self._jump_reason = reason
        logger.info(
            "TOOL_LOADER %s",
            json.dumps({"turn": self._turn, "event": "jump", "reason": reason}),
        )

    def admit_tools(self, names: Sequence[str], registry: Dict[str, dict]) -> List[str]:
        """Add a skill's exact *names* to the end of the list; return the list.

        Names absent from *registry* are dropped rather than raised: a skill may
        name a tool this agent does not have, and the loader must not invent
        capability.
        """
        for name in names:
            if name in registry and name not in self._added:
                self._added.append(name)
        return self.offered(registry)

    def remove_tools(
        self, names: Sequence[str], registry: Dict[str, dict]
    ) -> List[str]:
        """Take an unloaded skill's *names* off the list; return the list."""
        gone = set(names)
        self._added = [name for name in self._added if name not in gone]
        return self.offered(registry)

    def rank(
        self, query: str, docs: Dict[str, str], limit: int
    ) -> List[Tuple[str, float]]:
        """The *limit* best ``(name, score)`` of *docs* for *query*, best first.

        Scores below :data:`SEARCH_FLOOR` are left out. Raises if the embedder
        fails: a search tool must say so rather than report no matches.
        """
        names = list(docs)
        if not names or limit <= 0:
            return []
        vecs = self._embed_docs(docs)
        qvec = self._embed_fn(query)
        scored = [(name, float(np.dot(qvec, vecs[name]))) for name in names]
        scored.sort(key=lambda item: (-item[1], item[0]))
        return [item for item in scored[:limit] if item[1] >= SEARCH_FLOOR]

    def tool_docs(self, registry: Dict[str, dict]) -> Dict[str, str]:
        """``name -> text`` for every registered tool, as selection embeds it."""
        return {name: self._doc_text(name, info) for name, info in registry.items()}

    def reset_session(self) -> None:
        """Clear per-session state for a new conversation.

        The content-keyed embedding cache survives — embeddings depend only on
        the tool docs, not the conversation.
        """
        if self._turn > 0:
            logger.info(
                "TOOL_LOADER_SESSION %s",
                json.dumps({"turns": self._turn, "jump": self._jump_reason}),
            )
        self._added.clear()
        self._jump_reason = None
        self._turn = 0
        self._session_disabled = False

    # ── internals ────────────────────────────────────────────────────────

    def _ensure_tool_embeddings(
        self, registry: Dict[str, dict]
    ) -> Dict[str, "np.ndarray"]:
        """Return ``name -> vector`` for every registry tool, embedding lazily."""
        return self._embed_docs(self.tool_docs(registry))

    def _embed_docs(self, docs: Dict[str, str]) -> Dict[str, "np.ndarray"]:
        """Return ``name -> vector`` for *docs*, embedding what is not cached.

        Uses the content-keyed cache so unchanged docs are embedded once per
        process. Missing docs are batched into a single embed call when an
        ``embed_batch_fn`` was provided, else embedded per-doc.
        """
        keys: Dict[str, Tuple[str, str]] = {
            name: (name, _sha256(text)) for name, text in docs.items()
        }

        missing = [name for name, key in keys.items() if key not in self._embed_cache]
        if missing:
            if self._embed_batch_fn is not None:
                vecs = self._embed_batch_fn([docs[n] for n in missing])
                if len(vecs) != len(missing):
                    # A warming-up embedder can answer 200 with fewer vectors
                    # than texts; a bare zip would silently drop entries and
                    # surface later as a misleading KeyError.
                    raise RuntimeError(
                        f"embedding batch returned {len(vecs)} vectors for "
                        f"{len(missing)} texts — embedder still loading?"
                    )
                for name, vec in zip(missing, vecs):
                    self._embed_cache[keys[name]] = np.asarray(vec, dtype=np.float32)
            else:
                for name in missing:
                    self._embed_cache[keys[name]] = np.asarray(
                        self._embed_fn(docs[name]), dtype=np.float32
                    )

        return {name: self._embed_cache[keys[name]] for name in docs}

    @staticmethod
    def _doc_text(name: str, info: dict) -> str:
        """The text embedded for a tool: ``"{name}: {first line of description}"``."""
        description = ""
        for line in (info.get("description") or "").splitlines():
            if line.strip():
                description = line.strip()
                break
        return f"{name}: {description}" if description else name

    def _log_selection(
        self, query: str, scores: Dict[str, float], offered: List[str]
    ) -> None:
        """Emit one structured ``TOOL_LOADER`` INFO line (tuning data)."""
        logger.info(
            "TOOL_LOADER %s",
            json.dumps(
                {
                    "turn": self._turn,
                    "query_sha": _sha256(query)[:12],
                    "threshold": self._threshold,
                    "scores": {k: round(v, 4) for k, v in scores.items()},
                    "jump": self._jump_reason,
                    "loaded": offered,
                }
            ),
        )


def _sha256(text: str) -> str:
    """Hex SHA-256 of *text* (UTF-8)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
