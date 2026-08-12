# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""``GaiaAgent`` — the flagship general-purpose agent.

This is the agent a new user meets first: conversation, document Q&A over their
own files, data exploration, web research, and a memory that persists across
sessions — extended by skills rather than by shipping a new agent per task.

**It composes ``ChatAgent`` rather than forking it.** ChatAgent's ``doc`` profile
already carries the RAG prompt, smart document discovery, cross-turn session
persistence, memory v2, and MCP. Duplicating that to get a flagship would mean
maintaining two copies of the hardest-won prompt in the repo. What GaiaAgent adds
is *breadth*: the capability flags ChatAgent leaves off by default, plus a bundled
skill library.

Why breadth is a requirement and not a preference
-------------------------------------------------
``tools_required`` in a ``SKILL.md`` is **advisory** — the loader logs at INFO when
a declared tool is absent and loads the skill anyway. So a skill dropped into an
agent that lacks its tools does not fail at load; it fails mid-run when the model
calls a tool that was never registered. A general-purpose skill host therefore has
to carry the union of what its skills can ask for, or the failure surfaces to the
user as a broken answer instead of a clear refusal. The starter pack's needs map
directly onto the flags below:

    document-brief   -> RAG            (the ``doc`` prompt profile)
    data-explore     -> scratchpad     (``enable_scratchpad``)
    research-report  -> browser + file (``enable_browser`` + ``enable_filesystem``)
    check-in         -> memory         (on by default in the base agent)
    github-triage    -> MCP connector  (inherited from ChatAgent)

Skills are discovered from the bundled ``skills/`` directory (highest-precedence
root) and declared in ``gaia-agent.yaml``. Following the email agent's precedent
(#2848), **no skill set loads by default** — the manifest ships its
``default_skill_set`` commented out until an eval measures the prompt-token cost.
Skills are opt-in via ``--skill-set``, ``GAIA_SKILL_SET``, or — mid-session — the
skill-library tools in :mod:`gaia_agent.skill_tools`, which let the model
discover, install, load, and unload skills on demand without a restart. Those
tools never load anything on their own, so the out-of-the-box prompt budget is
unchanged.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar, List, Optional

from gaia_agent.skill_tools import SkillLibraryToolsMixin
from gaia_agent_chat.agent import ChatAgent, ChatAgentConfig

#: Bundled skills ship inside the package so they survive both the wheel and the
#: frozen sidecar; as ``SKILL_DIRS`` they outrank a same-named user or Claude Code copy.
_SKILLS_DIR = Path(__file__).resolve().parent / "skills"

_MANIFEST_CANDIDATES = (
    # Packaged: staged into the package (frozen sidecar --add-data, wheel package-data).
    Path(__file__).resolve().parent / "gaia-agent.yaml",
    # Source checkout / editable install: the canonical hub artifact.
    Path(__file__).resolve().parent.parent / "gaia-agent.yaml",
)

#: Env override for the active skill set, mirroring the email agent's channel.
SKILL_SET_ENV = "GAIA_SKILL_SET"


def _locate_agent_manifest() -> Optional[str]:
    """Absolute path to this package's ``gaia-agent.yaml``, or ``None``.

    Returning ``None`` rather than raising keeps an unpackaged checkout usable:
    the agent still runs, it just has no declarative skill sets. A *missing but
    declared* manifest is what the base class treats as an error.
    """
    for candidate in _MANIFEST_CANDIDATES:
        if candidate.is_file():
            return str(candidate)
    return None


@dataclass
class GaiaAgentConfig(ChatAgentConfig):
    """Flagship defaults: ChatAgent's ``doc`` profile with the breadth flags on.

    Every field here exists on :class:`ChatAgentConfig` already — this only
    changes defaults, so anything ChatAgent accepts still works.
    """

    # "full" — NOT "doc". This is the load-bearing line of the whole package.
    # ChatAgent registers tools from ``ProfileSpec.tool_groups``, which is keyed
    # on the profile alone; the ``enable_*`` flags below do NOT feed tool
    # registration. Setting profile="doc" with all three flags on yields RAG and
    # files but ZERO scratchpad and ZERO browser tools — measured, not assumed —
    # so data-explore and research-report would load and then die mid-run.
    # "full" is the only spec whose tool_groups are the union this agent needs:
    # doc_rag + file_fs + data_scratch + web_browse + full_screenshot.
    prompt_profile: str = "full"

    # Kept explicit even though "full" already implies them: these gate mixin
    # *construction* (indexes, DB handles, HTTP session) in __init__, separately
    # from the profile's tool registration.
    enable_filesystem: bool = True
    enable_scratchpad: bool = True
    enable_browser: bool = True

    # Which declared skill set to load. None = load nothing (the #2848 default);
    # resolution order is explicit arg -> env -> manifest default.
    skill_set: Optional[str] = None

    # Image generation stays off: it pulls a second resident model, and evicting
    # the chat model to draw a picture is not a trade a document agent should
    # make silently.
    enable_sd_tools: bool = False

    rag_documents: List[str] = field(default_factory=list)

    # ChatAgent defaults this to ``[Path.cwd()]``, which is wrong for a sidecar:
    # the daemon launches it with cwd = the package directory, so the agent ends
    # up sandboxed to its own source tree and refuses to read the user's files.
    # Measured: "read ~/Documents/notes.txt" fails with "not in allowed paths".
    #
    # The user's home is the honest scope for a personal document agent — it is
    # what "ask questions about my files" means — and it stays a real boundary
    # (system directories, other users, and program files are still refused).
    # Override with ``allowed_paths=[...]`` to narrow it.
    allowed_paths: Optional[List[str]] = field(
        default_factory=lambda: [str(Path.home())]
    )


class GaiaAgent(SkillLibraryToolsMixin, ChatAgent):
    """The flagship GAIA agent — conversation, documents, data, web, and skills."""

    SKILL_DIRS: ClassVar[List[str]] = [str(_SKILLS_DIR)]
    SKILL_MANIFEST: ClassVar[Optional[str]] = _locate_agent_manifest()

    # Installing a skill writes third-party code under ~/.gaia/skills and
    # removing one deletes it, so both are gated the way file mutation is.
    CONFIRMATION_REQUIRED_TOOLS: ClassVar[frozenset] = frozenset(
        {"install_skill", "remove_skill"}
    )

    def __init__(self, config: Optional[GaiaAgentConfig] = None, **kwargs):
        super().__init__(config=config or GaiaAgentConfig(**kwargs))

    def _register_tools(self) -> None:
        """ChatAgent's profile tools, plus runtime access to the skill library.

        Skill-library tools go first: ChatAgent's registration ends with
        ``_snapshot_tools()``, and anything registered after that snapshot is
        absent from this instance's registry.
        """
        self.register_skill_library_tools()
        super()._register_tools()

    def select_skill_set(self) -> Optional[str]:
        """Resolve which declared skill set to load at startup.

        Explicit config wins, then ``GAIA_SKILL_SET``, then the manifest default
        (which ships unset). Returning ``None`` means load no skills — the base
        class treats that as a deliberate choice, not a missing value.
        """
        explicit = getattr(self.config, "skill_set", None)
        if explicit:
            return explicit
        return os.environ.get(SKILL_SET_ENV) or None


__all__ = ["GaiaAgent", "GaiaAgentConfig", "SKILL_SET_ENV"]
