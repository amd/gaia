# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Fix-contract tests for the ChatAgent ProfileSpec refactor (#2323).

Unlike ``test_profilespec_characterization.py`` (which pins TODAY's behavior
so it never regresses), every test in this module targets a specific
known-broken behavior on current main that a later refactor increment is
expected to fix:

* manifest honesty (``gaia-agent.yaml`` / ``__init__.py`` tools_count drift)
* the ``notify_desktop`` Windows PowerShell-fallback error being masked as a
  generic "plyer not installed" message
* five silently-swallowed ``except Exception: pass`` sites that should log
  with context instead of going dark
* eager RAG/SessionManager construction on profiles that never need them
* ``_register_tools()`` re-entrancy
* ``allowed_paths`` confinement staying consistent across the agent,
  ``PathValidator``, and ``RAGConfig``

Most of these tests are EXPECTED TO FAIL on current main — that failure IS
the point; they flip to green once the corresponding fix increment lands. Do
not "fix" a failing assertion here by weakening it — if a test surprisingly
already passes (documented per-test below), keep it as a regression guard
instead of deleting it.
"""

from __future__ import annotations

import logging
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

# ChatAgent ships as the standalone gaia-agent-chat wheel (#1102); skip the
# whole module when a framework-only env lacks it.
pytest.importorskip("gaia_agent_chat")

from gaia_agent_chat.agent import ChatAgent, ChatAgentConfig  # noqa: E402

from tests.unit.test_profilespec_characterization import (  # noqa: E402
    build_agent_for_row,
    chat_agent_build_context,
)

CHAT_PKG_DIR = (
    Path(__file__).resolve().parents[2] / "hub" / "agents" / "chat" / "python"
)
YAML_PATH = CHAT_PKG_DIR / "gaia-agent.yaml"


# ── 1. Manifest honesty ─────────────────────────────────────────────────────


def test_manifest_interfaces_are_honest():
    """``gaia-agent.yaml`` claims capabilities ChatAgent doesn't wire up.

    ChatAgent has no API-server route wiring of its own (``api_server``) and
    is an MCP CLIENT, not a server (``mcp_server`` — ``MCPClientMixin``).
    Expected to FAIL on current main (both are ``true``).
    """
    manifest = yaml.safe_load(YAML_PATH.read_text(encoding="utf-8"))
    interfaces = manifest["interfaces"]
    assert interfaces["api_server"] is False, (
        "gaia-agent.yaml declares interfaces.api_server: true, but ChatAgent "
        "ships no API-server route wiring of its own — this over-claims a "
        "capability the package doesn't have."
    )
    assert interfaces["mcp_server"] is False, (
        "gaia-agent.yaml declares interfaces.mcp_server: true, but ChatAgent "
        "consumes MCP servers (MCPClientMixin) rather than serving one — this "
        "over-claims a capability the package doesn't have."
    )


def test_yaml_top_level_tools_count_matches_default_profile_registry():
    """gaia-agent.yaml's top-level ``tools_count`` should track the real
    registry size for the package's default configuration.

    Judgment call: the yaml's ``entry_class: ChatAgent`` with no profile
    override defaults to ``ChatAgentConfig.prompt_profile == "full"``, so
    "the real introspected size for the default construction" is the "full"
    profile's registered tool count — not the hand-typed literal ``0``.
    """
    manifest = yaml.safe_load(YAML_PATH.read_text(encoding="utf-8"))
    _, tools = build_agent_for_row("full")
    real = len(tools)
    assert manifest["tools_count"] == real, (
        f"gaia-agent.yaml top-level tools_count={manifest['tools_count']!r} "
        f"does not match the real default-profile ('full') registry size "
        f"({real}). This is stale hand-typed drift."
    )


@pytest.mark.parametrize(
    "build_fn_name,profile,extra_kwargs",
    [
        ("build_chat", "chat", {}),
        ("build_doc", "doc", {}),
        # build_file's real factory passes extra={"enable_filesystem": True};
        # included for faithfulness even though the "file" profile already
        # unconditionally registers filesystem tools regardless of the flag.
        ("build_file", "file", {"enable_filesystem": True}),
    ],
)
def test_registration_tools_count_matches_real_registry(
    build_fn_name, profile, extra_kwargs
):
    """``__init__.py``'s ``build_*().tools_count`` must equal the REAL
    introspected registry size for that profile, not a hand-maintained
    literal that can silently drift as tools are added/removed.
    """
    import gaia_agent_chat as pkg

    registration = getattr(pkg, build_fn_name)()
    _, tools = build_agent_for_row(profile, **extra_kwargs)
    real = len(tools)
    assert registration.tools_count == real, (
        f"gaia_agent_chat.{build_fn_name}().tools_count="
        f"{registration.tools_count!r} does not match the real {profile!r} "
        f"registry size ({real}). Introspect the registry instead of "
        "hand-maintaining this literal."
    )


# ── 2. notify_desktop: Windows real-error passthrough ──────────────────────
# Also covers the logging assertion for agent.py:1592-1593 (see module intro
# — subsumed here rather than duplicated in a separate logging test).


def test_notify_desktop_windows_real_error_not_masked_as_plyer_missing(caplog):
    """When the Windows PowerShell fallback itself raises, the tool must
    surface THAT failure — not the generic "plyer not installed" message
    (which is only accurate when plyer truly isn't installed, not when the
    fallback errored for an unrelated reason).

    Expected to FAIL on current main: the bare ``except Exception: pass`` at
    agent.py:1592-1593 swallows the fallback's real exception, and the
    generic message is returned regardless of the *actual* failure reason —
    with nothing logged either.
    """
    with chat_agent_build_context("doc") as agent:
        agent._register_tools()
        notify_desktop = agent._tools_registry["notify_desktop"]["function"]

    caplog.set_level(logging.DEBUG, logger="gaia_agent_chat.agent")
    with (
        patch("platform.system", return_value="Windows"),
        patch("subprocess.Popen", side_effect=RuntimeError("ps blocked")),
    ):
        result = notify_desktop("Title", "Body")

    assert result["status"] == "error"
    assert result["error"] != "plyer not installed. Run: pip install plyer", (
        "notify_desktop masked a real PowerShell-fallback failure ('ps "
        "blocked') behind the generic plyer-missing message."
    )
    assert "ps blocked" in result["error"], (
        f"expected the real underlying failure surfaced in the error message, "
        f"got: {result['error']!r}"
    )
    assert "ps blocked" in caplog.text, (
        "expected the swallowed PowerShell-fallback exception (agent.py:"
        "1592-1593) to be logged with context; caplog captured nothing "
        "referencing it."
    )


# ── 3. Silent-swallow logging (the remaining four sites) ──────────────────


def test_lite_agent_screenshot_registration_failure_is_logged(caplog):
    """``ChatAgentLite._register_tools()``'s bare except around
    ``register_screenshot_tools()`` (lite_agent.py:51-58) currently discards
    the exception with no log record. Expected to FAIL on current main.
    """
    from gaia_agent_chat.lite_agent import ChatAgentLite, ChatAgentLiteConfig

    agent = ChatAgentLite.__new__(ChatAgentLite)
    agent.config = ChatAgentLiteConfig()

    caplog.set_level(logging.DEBUG, logger="gaia_agent_chat.lite_agent")
    with patch.object(
        ChatAgentLite,
        "register_screenshot_tools",
        side_effect=RuntimeError("screenshot registration boom"),
    ):
        agent._register_tools()

    assert "screenshot registration boom" in caplog.text, (
        "expected a log record referencing the swallowed exception from "
        "lite_agent.py:51-58 — caplog captured nothing."
    )


def test_reset_command_tool_loader_failure_is_logged(caplog, monkeypatch):
    """The interactive ``/reset`` command's tool-loader-reset swallow
    (app.py:313-325) currently discards the exception with no log record.
    Drives ``interactive_mode`` through ``input()`` -> "/reset" -> "/quit"
    with a MagicMock agent. Expected to FAIL on current main.
    """
    from gaia_agent_chat import app as app_module

    mock_agent = MagicMock()
    mock_agent.tool_loader.reset_session.side_effect = RuntimeError(
        "reset command tool loader boom"
    )

    monkeypatch.setattr("builtins.input", MagicMock(side_effect=["/reset", "/quit"]))
    caplog.set_level(logging.DEBUG, logger="gaia_agent_chat.app")
    app_module.interactive_mode(mock_agent)

    assert "reset command tool loader boom" in caplog.text, (
        "expected a log record referencing the swallowed exception from "
        "app.py:313-325 (/reset command) — caplog captured nothing."
    )


def test_bootstrap_session_tool_loader_failure_is_logged(caplog, monkeypatch):
    """The bootstrap-session tool-loader-reset swallow in ``main()``
    (app.py:1006-1011) currently discards the exception with no log record.
    ``ChatAgent`` itself is replaced with a MagicMock factory so this never
    touches Lemonade/RAG; ``interactive_mode`` is stubbed to a no-op so the
    test exercises only the bootstrap path. Expected to FAIL on current main.
    """
    from gaia_agent_chat import app as app_module

    mock_agent = MagicMock()
    mock_agent.current_session = None  # forces the "create initial session" branch
    mock_agent.tool_loader.reset_session.side_effect = RuntimeError(
        "bootstrap tool loader boom"
    )

    monkeypatch.setattr(sys, "argv", ["gaia-chat"])
    caplog.set_level(logging.DEBUG, logger="gaia_agent_chat.app")
    with (
        patch.object(app_module, "ChatAgent", return_value=mock_agent),
        patch.object(app_module, "interactive_mode"),
    ):
        result = app_module.main()

    assert result == 0
    assert "bootstrap tool loader boom" in caplog.text, (
        "expected a log record referencing the swallowed exception from "
        "app.py:1006-1011 (bootstrap session) — caplog captured nothing."
    )


def test_main_finally_stop_watching_failure_is_logged(caplog, monkeypatch):
    """``main()``'s ``finally: agent.stop_watching()`` swallow
    (app.py:1069-1073) currently discards the exception with no log record.
    Expected to FAIL on current main.
    """
    from gaia_agent_chat import app as app_module

    mock_agent = MagicMock()
    mock_agent.current_session = MagicMock(session_id="s1")
    mock_agent.stop_watching.side_effect = RuntimeError("stop watching boom")

    monkeypatch.setattr(sys, "argv", ["gaia-chat"])
    caplog.set_level(logging.DEBUG, logger="gaia_agent_chat.app")
    with (
        patch.object(app_module, "ChatAgent", return_value=mock_agent),
        patch.object(app_module, "interactive_mode"),
    ):
        app_module.main()

    assert "stop watching boom" in caplog.text, (
        "expected a log record referencing the swallowed exception from "
        "app.py:1069-1073 (finally: agent.stop_watching()) — caplog "
        "captured nothing."
    )


def test_list_windows_per_window_failure_logged_at_debug(caplog):
    """``list_windows()``'s per-window Windows swallow (agent.py:1628-1629)
    currently discards the exception with no log record. Must log at DEBUG
    specifically once fixed — window titles can carry sensitive text
    (document names, email subjects), so this must never surface at a
    higher default-visible level. Expected to FAIL on current main.
    """
    with chat_agent_build_context("doc") as agent:
        agent._register_tools()
        list_windows = agent._tools_registry["list_windows"]["function"]

    class _FakeWindow:
        def window_text(self):
            raise RuntimeError("window enum boom")

    fake_desktop = MagicMock()
    fake_desktop.windows.return_value = [_FakeWindow()]
    fake_pywinauto = types.ModuleType("pywinauto")
    fake_pywinauto.Desktop = MagicMock(return_value=fake_desktop)

    caplog.set_level(logging.DEBUG, logger="gaia_agent_chat.agent")
    with (
        patch.dict(sys.modules, {"pywinauto": fake_pywinauto}),
        patch("platform.system", return_value="Windows"),
    ):
        result = list_windows()

    assert result["status"] == "success"
    debug_records = [r for r in caplog.records if "window enum boom" in r.getMessage()]
    assert debug_records, (
        "expected a log record referencing the swallowed exception from "
        "agent.py:1628-1629 — caplog captured nothing."
    )
    assert all(r.levelno == logging.DEBUG for r in debug_records), (
        "the swallowed per-window exception must log at DEBUG (window titles "
        "can carry sensitive text) — got a higher level instead."
    )


# ── 4. RAG/SessionManager not built eagerly for profiles that don't need it ─


def test_chat_profile_does_not_build_rag_eagerly():
    """The "chat" profile never surfaces RAG tools (`_register_tools()`
    returns before `register_rag_tools()`), so building a full `RAGSDK`
    for it is pure waste. Expected to FAIL on current main — `self.rag =
    RAGSDK(rag_config)` at agent.py:283 runs unconditionally, regardless of
    `prompt_profile`.
    """
    with (
        patch("gaia_agent_chat.agent.RAGSDK") as rag_sdk_cls,
        patch("gaia_agent_chat.agent.RAGConfig"),
        patch("gaia_agent_chat.agent.SessionManager"),
    ):
        ChatAgent(ChatAgentConfig(prompt_profile="chat", silent_mode=True))

    rag_sdk_cls.assert_not_called()


# ── 5. _register_tools() re-entrancy ───────────────────────────────────────


def test_register_tools_is_idempotent_on_repeated_calls():
    """Calling `_register_tools()` twice on the same agent instance must
    produce an identical tool set and system prompt both times.

    NOTE: this may already PASS on current main (determined empirically, not
    assumed) — if so, it's a regression guard for the refactor rather than a
    fix-contract: the ProfileSpec table must not introduce non-idempotent
    registration (e.g. via a mutable per-call side effect).
    """
    with chat_agent_build_context("doc") as agent:
        agent._register_tools()
        tools_first = sorted(agent._tools_registry.keys())
        prompt_first = agent._get_system_prompt()

        agent._register_tools()
        tools_second = sorted(agent._tools_registry.keys())
        prompt_second = agent._get_system_prompt()

    assert tools_first == tools_second, (
        "calling _register_tools() twice on the same agent instance produced "
        "a different tool set the second time."
    )
    assert prompt_first == prompt_second, (
        "calling _register_tools() twice on the same agent instance changed "
        "the composed system prompt the second time."
    )


# ── 6. allowed_paths confinement stays consistent ──────────────────────────


def test_allowed_paths_default_none_stays_consistent(tmp_path, monkeypatch):
    """Freezes today's `allowed_paths=None` wiring across `ChatAgent`,
    `PathValidator`, and the `RAGConfig` call — so a later increment that
    moves *when* RAG reads this value can't silently change what it resolves
    to. `HOME` is redirected to `tmp_path` so a dev box's real
    `~/.gaia/cache/allowed_paths.json` can never leak extra roots in.
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    expected_cwd = Path.cwd()

    with (
        patch("gaia_agent_chat.agent.RAGSDK"),
        patch("gaia_agent_chat.agent.RAGConfig") as rag_config_cls,
        patch("gaia_agent_chat.agent.SessionManager"),
    ):
        agent = ChatAgent(
            ChatAgentConfig(prompt_profile="chat", silent_mode=True, allowed_paths=None)
        )

    assert agent.allowed_paths == [expected_cwd]
    assert agent.path_validator.allowed_paths == {expected_cwd.resolve()}
    _, rag_kwargs = rag_config_cls.call_args
    assert rag_kwargs.get("allowed_paths") is None


def test_allowed_paths_explicit_stays_consistent(tmp_path, monkeypatch):
    """Same as above, for an explicit `allowed_paths=[...]` override."""
    monkeypatch.setenv("HOME", str(tmp_path))
    explicit_dir = tmp_path / "sub"
    explicit_dir.mkdir()

    with (
        patch("gaia_agent_chat.agent.RAGSDK"),
        patch("gaia_agent_chat.agent.RAGConfig") as rag_config_cls,
        patch("gaia_agent_chat.agent.SessionManager"),
    ):
        agent = ChatAgent(
            ChatAgentConfig(
                prompt_profile="chat",
                silent_mode=True,
                allowed_paths=[str(explicit_dir)],
            )
        )

    assert agent.allowed_paths == [explicit_dir.resolve()]
    assert agent.path_validator.allowed_paths == {explicit_dir.resolve()}
    _, rag_kwargs = rag_config_cls.call_args
    assert rag_kwargs.get("allowed_paths") == [str(explicit_dir)]
