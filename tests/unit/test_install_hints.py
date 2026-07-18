# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Contract tests for the shared agent-not-installed message helper (#2240).

Today ~20 call sites hand-roll their own "agent not installed" text, and two
of the phrases they use don't work: ``pip install gaia-agent-<id>`` (no such
package is published) and ``pip install "amd-gaia[agents]"`` (an unsatisfiable
extra that makes pip/uv silently *downgrade* a working install). This module
locks in the contract for the replacement: a single helper,
``gaia.install_hints.agent_not_installed_message``, that only ever emits
advice that works today.

``gaia.install_hints`` does not exist yet — every test below is expected to
fail with ``ModuleNotFoundError`` until it is implemented. That is the
intended red-phase state.

Interface assumed (documented here since no implementation exists to read):

    def agent_not_installed_message(
        agent_id: str,
        *,
        package: str,
        retry_command: str,
        also_see: str | None = None,
    ) -> str

``also_see`` is this test file's assumed mechanism for the chat-specific
requirement that the message point at ``gaia chat --ui`` as where custom
agents run today. If the real implementation instead special-cases
``agent_id == "chat"`` internally (no ``also_see`` kwarg), only
``test_chat_message_mentions_agent_ui_and_does_not_imply_a_routing_decision``
needs adjusting — the rest of this file does not depend on that kwarg.

Two tests assume the implementation calls the qualified
``importlib.metadata.version(...)`` / raises via qualified
``importlib.metadata.PackageNotFoundError`` (i.e. ``import importlib.metadata``,
not ``from importlib.metadata import version``) so that patching
``importlib.metadata.version`` takes effect — mirroring the qualified
``importlib.util.find_spec(...)`` style mandated for Increment 3. If the real
helper does a bare ``from ... import version`` instead, these two tests will
fail because the patch silently has no effect (the real installed amd-gaia
version leaks through) rather than because of missing pinning logic; switch
the import style to qualified access to fix it.
"""

from __future__ import annotations

import importlib.metadata
import subprocess  # nosec B404 - fixed argv, no shell
import sys
from unittest.mock import patch

import pytest


def _import_helper():
    from gaia.install_hints import agent_not_installed_message

    return agent_not_installed_message


def test_message_is_version_pinned_never_a_bare_main_ref():
    """The git-install command must pin @v{version}, never a bare main ref."""
    agent_not_installed_message = _import_helper()
    with patch("importlib.metadata.version", return_value="9.9.9"):
        msg = agent_not_installed_message(
            "chat", package="chat agent", retry_command="gaia chat"
        )
    assert "@v9.9.9" in msg
    assert "@main" not in msg
    assert "#main" not in msg
    assert "/main#" not in msg


def test_message_shape_is_the_git_subdirectory_install():
    """The exact working install command must appear, subdirectory-scoped."""
    agent_not_installed_message = _import_helper()
    with patch("importlib.metadata.version", return_value="9.9.9"):
        msg = agent_not_installed_message(
            "chat", package="chat agent", retry_command="gaia chat"
        )
    assert (
        "git+https://github.com/amd/gaia.git@v9.9.9#subdirectory=hub/agents/python/chat"
        in msg
    )


def test_message_never_mentions_the_broken_bracket_extra():
    agent_not_installed_message = _import_helper()
    with patch("importlib.metadata.version", return_value="9.9.9"):
        msg = agent_not_installed_message(
            "sd", package="sd agent", retry_command="gaia sd"
        )
    assert "amd-gaia[agents]" not in msg


def test_message_never_recommends_a_bare_pip_install_of_the_unpublished_wheel():
    agent_not_installed_message = _import_helper()
    with patch("importlib.metadata.version", return_value="9.9.9"):
        msg = agent_not_installed_message(
            "docker", package="docker agent", retry_command="gaia docker"
        )
    assert "pip install gaia-agent-" not in msg
    assert "uv pip install gaia-agent-" not in msg


def test_message_mentions_the_retry_command_verbatim():
    agent_not_installed_message = _import_helper()
    with patch("importlib.metadata.version", return_value="9.9.9"):
        msg = agent_not_installed_message(
            "jira", package="jira agent", retry_command="gaia jira"
        )
    assert "gaia jira" in msg


def test_package_not_found_error_fails_loudly_not_silently_unpinned():
    """A source checkout with no installed amd-gaia metadata must raise, not
    silently emit an unpinned URL (fail-loudly, CLAUDE.md)."""
    agent_not_installed_message = _import_helper()
    with patch(
        "importlib.metadata.version",
        side_effect=importlib.metadata.PackageNotFoundError("amd-gaia"),
    ):
        with pytest.raises(RuntimeError) as excinfo:
            agent_not_installed_message(
                "chat", package="chat agent", retry_command="gaia chat"
            )
    message = str(excinfo.value)
    assert message, "the raised error must carry an actionable message"
    # No unpinned fallback URL of any kind.
    assert "git+https://github.com/amd/gaia.git#" not in message


def test_chat_message_mentions_agent_ui_and_does_not_imply_a_routing_decision():
    """The chat message must state the fact plainly, not sound like GAIA chose
    the built-in agent over a custom one (the reporter's actual misreading)."""
    agent_not_installed_message = _import_helper()
    with patch("importlib.metadata.version", return_value="9.9.9"):
        msg = agent_not_installed_message(
            "chat",
            package="chat agent",
            retry_command="gaia chat",
            also_see="gaia chat --ui",
        )
    lowered = msg.lower()
    assert "prefers" not in lowered
    assert "instead of your" not in lowered
    assert "chose" not in lowered
    assert "gaia chat --ui" in msg


def test_install_hints_module_has_no_heavy_top_level_imports():
    """gaia.install_hints must be importable without pulling in the agent
    stack (RAG/SD/VLM/MCP/torch/faiss/fastapi) — it is used from lightweight
    consumers like gaia.mcp.servers.docker_mcp and gaia.eval.* (#2240).

    This runs in a fresh subprocess so an already-populated sys.modules in
    the current test process (e.g. from importing other gaia submodules
    earlier in the run) can't mask a real regression.
    """
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys\n"
            "before = set(sys.modules)\n"
            "import gaia.install_hints\n"
            "after = set(sys.modules) - before\n"
            "print(','.join(sorted(after)))\n",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, (
        "importing gaia.install_hints failed:\n" + result.stderr
    )
    newly_imported = (
        set(result.stdout.strip().split(",")) if result.stdout.strip() else set()
    )
    heavy_prefixes = (
        "torch",
        "faiss",
        "fastapi",
        "sentence_transformers",
        "transformers",
        "mcp",
        "uvicorn",
        "gaia.agents",
        "gaia.rag",
        "gaia.sd",
        "gaia.vlm",
        "gaia.apps",
    )
    offenders = {m for m in newly_imported if m.startswith(heavy_prefixes)}
    assert (
        not offenders
    ), f"gaia.install_hints pulled in heavy modules at import time: {offenders}"
