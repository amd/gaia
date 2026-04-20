# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""``CoderAgent`` — the base class for gaia-coder (§5.1).

She does NOT inherit from :class:`gaia.agents.base.Agent`. That base is
designed for GAIA product agents (chat, blender, jira, etc.) with a generic
"call tools, return answer" ReAct loop. ``gaia-coder``'s work is
fundamentally different: long-lived daemon lifecycle, durable queues,
self-governance, multi-pass review, editable ReAct graph. A generic base
cannot carry that weight without a dozen forced overrides — so she has her
own base, built from first principles.

Phase 1 scaffolding: this module defines the class surface and holds the
canonical references to the default loop, her identity document, and her
two living architecture files. The runtime loop runner, mixin resolution,
and durable-queue plumbing land in later phases per
``docs/plans/coder-agent.mdx``.

Composition (not inheritance) from the GAIA agent base is intentionally
allowed and encouraged for well-designed pieces:

* ``@tool`` decorator from :mod:`gaia.agents.base.tools` — the
  tool-registration pattern is solid and worth reusing.
* :class:`gaia.agents.base.console.AgentConsole` — standardised CLI output
  (silent mode, colour, progress bars).
* :class:`gaia.security.PathValidator` — sandboxed filesystem access.

None of those imports are pulled in at module import time during Phase 1
— the subsystems that need them will import at use-site to keep the
skeleton cheap and the dependency surface obvious.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from gaia.coder.loop import DEFAULT_LOOP, Loop

# ---------------------------------------------------------------------------
# Canonical filesystem anchors for identity / architecture docs (§4.6, §6.5).
# ---------------------------------------------------------------------------

#: Absolute path to ``GAIA.md`` — the always-present identity document
#: (principles, persona, working-style) injected into every system prompt.
GAIA_MD_PATH: Path = Path(__file__).resolve().parent / "GAIA.md"

#: Absolute path to ``ARCHITECTURE.md`` — how *she* is composed (mixins,
#: state machine, invariants). Updated on every self-edit that changes
#: composition.
ARCHITECTURE_MD_PATH: Path = Path(__file__).resolve().parent / "ARCHITECTURE.md"

#: Absolute path to ``PROJECT_MAP.md`` — how the *project she is building*
#: (``amd/gaia``) is composed. Updated continuously from the RAG index.
PROJECT_MAP_MD_PATH: Path = Path(__file__).resolve().parent / "PROJECT_MAP.md"


@dataclass
class CoderAgentConfig:
    """Minimal construction-time configuration for :class:`CoderAgent`.

    Phase 1 holds only the values the skeleton needs to be constructible in
    tests. Later phases extend this with model selection, prompt-caching
    flags, tier, EM identity, auto-merge classes, etc. — all of which are
    already specified in ``docs/plans/coder-agent.mdx`` and parked in
    ``~/.gaia/coder/*.toml`` per §3.2, §4.1, §6.6, §6.7.

    Attributes:
        repo_root: Absolute path to the bound ``amd/gaia`` checkout. If
            ``None``, the agent is running in "unbound" mode (tests, docs,
            CI smoke runs) and may not write to any repo path.
        loop: The ReAct graph she runs. Defaults to
            :data:`gaia.coder.loop.DEFAULT_LOOP`. Supplied explicitly for
            tests that want to exercise a narrower loop.
    """

    repo_root: Optional[Path] = None
    loop: Loop = DEFAULT_LOOP


class CoderAgent:
    """Base class for ``gaia-coder`` (§5.1).

    Phase 1 exposes the minimum surface needed by downstream tasks
    (stores, mixins) to import from a stable location:

    * :attr:`loop` — the active :class:`~gaia.coder.loop.Loop`.
    * :attr:`gaia_md_path` / :attr:`architecture_md_path` /
      :attr:`project_map_md_path` — absolute paths to her three living
      documents (§4.6 + §6.5).
    * :meth:`identity_document` / :meth:`architecture_document` /
      :meth:`project_map_document` — readers that return the *current*
      on-disk content (important: these files are expected to mutate at
      runtime in later phases, so every caller reads them on demand
      rather than at init).

    No behaviour beyond reading the canonical paths is wired up yet.
    Constructing the class is cheap; no network, no LLM, no side
    effects.

    Example::

        from gaia.coder import CoderAgent

        agent = CoderAgent()
        print(agent.loop.version)          # 1
        print(agent.identity_document())   # contents of GAIA.md
    """

    #: Default loop every instance uses unless the caller supplies one.
    DEFAULT_LOOP: Loop = DEFAULT_LOOP

    def __init__(self, config: Optional[CoderAgentConfig] = None) -> None:
        self._config = config or CoderAgentConfig()

    # ------------------------------------------------------------------
    # Core properties
    # ------------------------------------------------------------------

    @property
    def config(self) -> CoderAgentConfig:
        """Return the construction-time configuration."""
        return self._config

    @property
    def loop(self) -> Loop:
        """Return the ReAct graph this instance is running."""
        return self._config.loop

    @property
    def gaia_md_path(self) -> Path:
        """Absolute path to her identity document (``GAIA.md``)."""
        return GAIA_MD_PATH

    @property
    def architecture_md_path(self) -> Path:
        """Absolute path to her composition map (``ARCHITECTURE.md``)."""
        return ARCHITECTURE_MD_PATH

    @property
    def project_map_md_path(self) -> Path:
        """Absolute path to her project map (``PROJECT_MAP.md``)."""
        return PROJECT_MAP_MD_PATH

    # ------------------------------------------------------------------
    # Document readers
    # ------------------------------------------------------------------

    def identity_document(self) -> str:
        """Return the current contents of ``GAIA.md``.

        Raises:
            FileNotFoundError: If the file is missing. Fail-loudly per the
                repo ``CLAUDE.md`` rule — the identity document is
                always-present; absence is a bug, not a fallback case.
        """
        return self.gaia_md_path.read_text(encoding="utf-8")

    def architecture_document(self) -> str:
        """Return the current contents of ``ARCHITECTURE.md``.

        Raises:
            FileNotFoundError: If the file is missing.
        """
        return self.architecture_md_path.read_text(encoding="utf-8")

    def project_map_document(self) -> str:
        """Return the current contents of ``PROJECT_MAP.md``.

        Raises:
            FileNotFoundError: If the file is missing.
        """
        return self.project_map_md_path.read_text(encoding="utf-8")

    # ------------------------------------------------------------------
    # Phase-1 placeholders
    # ------------------------------------------------------------------

    def run_once(self, *_args: Any, **_kwargs: Any) -> None:
        """Execute a single heartbeat tick through the ReAct graph.

        Phase 1 placeholder. The real implementation lands in Phase 3
        alongside the loop runner, event bridge, and durable queues.
        """
        raise NotImplementedError(
            "CoderAgent.run_once is a Phase 3 deliverable — see "
            "docs/plans/coder-agent.mdx §5.1 for the full state machine "
            "and §15.3 for the canonical loop.py contract."
        )


__all__ = [
    "ARCHITECTURE_MD_PATH",
    "CoderAgent",
    "CoderAgentConfig",
    "GAIA_MD_PATH",
    "PROJECT_MAP_MD_PATH",
]
