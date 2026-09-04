# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Regression tests: no PowerShell sink interpolates untrusted text into ``-Command``.

Two sinks used to build the command string with an f-string and drop the value
inside a single-quoted PowerShell literal, so a ``'`` closed the literal and the
rest of the value was parsed as PowerShell:

* ``rag.pptx_utils.convert_pptx_to_pdf`` — the *filename* of an uploaded deck,
  executed during indexing with no tool call and no confirmation;
* ``gaia_agent_chat``'s ``notify_desktop`` Windows fallback — the message and
  title the model chose.

Both now hand their values to the child through the environment, so the command
text is a module constant that no input can reach. These tests assert exactly
that: the payload never appears in the command, and the command is byte-for-byte
the constant.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

# A stem that breaks out of a single-quoted PowerShell literal. Kept identical
# across the cases so a partial fix cannot pass one and fail the other.
INJECTION = "quarterly'; Start-Process calc; '"


# ── C3: PPTX → PDF conversion (filename is the attacker-controlled value) ────


class TestPptxToPdfConversion:
    def _run_convert(self, tmp_path: Path, stem: str):
        """Call ``convert_pptx_to_pdf`` with subprocess + platform stubbed out.

        Returns the ``subprocess.run`` mock so the test can inspect the exact
        argv and environment the child would have been given.
        """
        from gaia.rag import pptx_utils

        pptx = tmp_path / f"{stem}.pptx"
        pptx.write_bytes(b"not really a pptx")

        with patch.object(pptx_utils.platform, "system", return_value="Windows"):
            with patch.object(pptx_utils.subprocess, "run") as mock_run:
                mock_run.return_value = subprocess.CompletedProcess(
                    args=[], returncode=1, stdout="", stderr=""
                )
                pptx_utils.convert_pptx_to_pdf(str(pptx), str(tmp_path))

        assert mock_run.call_count == 1
        return mock_run.call_args

    def test_quote_in_filename_cannot_alter_the_command(self, tmp_path):
        """A ``'`` in the deck name must not reach the PowerShell command text."""
        from gaia.rag import pptx_utils

        call = self._run_convert(tmp_path, INJECTION)
        argv = call.args[0]

        assert argv[:3] == ["powershell", "-NoProfile", "-Command"]
        ps_script = argv[3]

        # The whole point: the command is the constant, not a rendering of input.
        assert ps_script == pptx_utils.PPTX_TO_PDF_PS_SCRIPT
        assert "Start-Process" not in ps_script
        assert "quarterly" not in ps_script

    def test_path_is_handed_over_out_of_band_and_intact(self, tmp_path):
        """The path still reaches PowerShell — verbatim, via the environment."""
        from gaia.rag import pptx_utils

        call = self._run_convert(tmp_path, INJECTION)
        env = call.kwargs["env"]

        assert env[pptx_utils.PPTX_IN_ENV_VAR] == str((tmp_path / f"{INJECTION}.pptx"))
        assert env[pptx_utils.PDF_OUT_ENV_VAR] == str((tmp_path / f"{INJECTION}.pdf"))
        # Inheriting the parent env matters — PowerShell needs PATH/SystemRoot.
        assert "PATH" in {k.upper() for k in env}

    def test_benign_filename_still_converts_through_the_same_path(self, tmp_path):
        """The constant command must work for ordinary names too, so the fix
        cannot be 'reject anything interesting'."""
        from gaia.rag import pptx_utils

        call = self._run_convert(tmp_path, "Q3 review (final)")
        assert call.args[0][3] == pptx_utils.PPTX_TO_PDF_PS_SCRIPT
        assert call.kwargs["env"][pptx_utils.PPTX_IN_ENV_VAR].endswith(
            "Q3 review (final).pptx"
        )

    def test_script_constant_reads_both_paths_from_the_environment(self):
        from gaia.rag import pptx_utils

        script = pptx_utils.PPTX_TO_PDF_PS_SCRIPT
        assert f"$env:{pptx_utils.PPTX_IN_ENV_VAR}" in script
        assert f"$env:{pptx_utils.PDF_OUT_ENV_VAR}" in script
        assert "$ppt.Presentations.Open($in," in script
        assert "$pres.SaveAs($out, 32)" in script


# ── C3 (defense in depth): the upload path must not keep a "'" in the stem ───


class TestSanitizeStem:
    """``_sanitize_stem`` is what turns an uploaded filename into an on-disk
    name; it stripped ``<>:"/\\|?*`` but left ``'`` and backtick through."""

    @pytest.fixture(autouse=True)
    def _requires_ui_extras(self):
        # The documents router imports FastAPI; a framework-only env skips.
        pytest.importorskip("fastapi", reason="gaia[ui] extras not installed")

    def test_quote_and_backtick_are_replaced(self):
        from gaia.ui.routers.documents import _sanitize_stem

        out = _sanitize_stem(INJECTION)
        assert "'" not in out
        assert "`" not in out

    def test_existing_illegal_and_control_chars_still_replaced(self):
        from gaia.ui.routers.documents import _sanitize_stem

        assert _sanitize_stem('a<b>c:d"e/f\\g|h?i*j') == "a_b_c_d_e_f_g_h_i_j"
        assert _sanitize_stem("a\x00b\x1fc") == "a_b_c"

    def test_ordinary_names_survive(self):
        from gaia.ui.routers.documents import _sanitize_stem

        assert _sanitize_stem("Q3 Review (final) v2") == "Q3 Review (final) v2"


# ── C2: notify_desktop's Windows PowerShell fallback ─────────────────────────


class TestNotifyDesktopScript:
    """Scope the chat-wheel skip to this class only.

    A module-level ``importorskip`` raises ``Skipped`` during *collection* and
    takes the whole file with it — including the PPTX and ``_sanitize_stem``
    cases, which import no chat package and are the higher-severity half.
    """

    @pytest.fixture(autouse=True)
    def _requires_chat_wheel(self):
        pytest.importorskip(
            "gaia_agent_chat", reason="gaia-agent-chat wheel not installed in this env"
        )

    def test_script_constant_reads_both_values_from_the_environment(self):
        from gaia_agent_chat import agent as chat_agent

        script = chat_agent.NOTIFY_DESKTOP_PS_SCRIPT
        assert f"[string]$env:{chat_agent.NOTIFY_MESSAGE_ENV_VAR}" in script
        assert f"[string]$env:{chat_agent.NOTIFY_TITLE_ENV_VAR}" in script
        # No literal-string delimiter for a payload to close.
        assert "'" not in script

    def test_quote_in_message_cannot_alter_the_command(self, monkeypatch):
        """Drive the real tool body with a breakout message and inspect the argv."""
        from gaia_agent_chat import agent as chat_agent

        notify = _notify_desktop_tool()
        captured = {}

        class _FakePopen:
            def __init__(self, argv, **kwargs):
                captured["argv"] = argv
                captured["env"] = kwargs.get("env")

        # Force the PowerShell fallback: pretend plyer is absent, on Windows.
        monkeypatch.setattr(chat_agent.platform, "system", lambda: "Windows")
        monkeypatch.setitem(__import__("sys").modules, "plyer", None)
        monkeypatch.setattr(subprocess, "Popen", _FakePopen)

        payload = "hi'); Write-Host INJECTED; ('x"
        result = notify(title="t'); calc; ('", message=payload)

        assert result["status"] == "success"
        argv = captured["argv"]
        assert argv[:2] == ["powershell", "-NoProfile"]
        assert argv[-2] == "-Command"

        ps_script = argv[-1]
        assert ps_script == chat_agent.NOTIFY_DESKTOP_PS_SCRIPT
        assert "Write-Host" not in ps_script
        assert "INJECTED" not in ps_script
        assert "calc" not in ps_script

        env = captured["env"]
        assert env[chat_agent.NOTIFY_MESSAGE_ENV_VAR] == payload
        assert env[chat_agent.NOTIFY_TITLE_ENV_VAR] == "t'); calc; ('"


class TestNotifyDesktopIsGated:
    def test_notify_desktop_requires_confirmation(self):
        """Spawning a PowerShell child on a model's say-so needs a human in the
        loop, same as the other process-spawning tools."""
        from gaia.agents.base.agent import TOOLS_REQUIRING_CONFIRMATION, Agent

        assert "notify_desktop" in TOOLS_REQUIRING_CONFIRMATION
        assert "notify_desktop" in Agent.confirmation_required_tools()


# ── helpers ──────────────────────────────────────────────────────────────────


_MIXIN_REGISTRARS = (
    "register_shell_tools",
    "register_memory_tools",
    "register_rag_tools",
    "register_file_tools",
    "register_file_search_tools",
    "register_filesystem_tools",
    "register_file_io_tools",
    "register_scratchpad_tools",
    "register_browser_tools",
    "register_screenshot_tools",
    "_register_external_tools_conditional",
    "_register_loop_control_tools",
)


def _notify_desktop_tool():
    """Return ChatAgent's inner ``notify_desktop`` without building an agent.

    The tool is defined inside ``_register_tools``, so the only way to reach it
    is to run that method — but a real ``ChatAgent.__init__`` wants RAG,
    Lemonade and MCP. Running the body against a bare instance with the mixin
    registrars stubbed registers the agent's own ``@tool`` closures and nothing
    else, which keeps this hermetic.
    """
    from gaia_agent_chat.agent import ChatAgent, ChatAgentConfig

    from gaia.agents.base.tools import _TOOL_REGISTRY

    stub = object.__new__(ChatAgent)
    stub.config = ChatAgentConfig(prompt_profile="full")
    stub.tool_loader = None
    # Keep ``__del__`` quiet when the stub is collected.
    stub.observers = []
    stub._web_client = None
    stub._fs_index = None
    stub._scratchpad = None
    for name in _MIXIN_REGISTRARS:
        setattr(stub, name, lambda *a, **k: None)

    saved = dict(_TOOL_REGISTRY)
    try:
        ChatAgent._register_tools(stub)
        return _TOOL_REGISTRY["notify_desktop"]["function"]
    finally:
        _TOOL_REGISTRY.clear()
        _TOOL_REGISTRY.update(saved)
