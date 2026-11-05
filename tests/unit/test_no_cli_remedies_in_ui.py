# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Text the Agent UI can show must not tell the user to run a `gaia` command.

A desktop user has no terminal habit and often no ``gaia`` on PATH, so a
remedy like "Run `gaia init`" leaves them stuck (#6703). Errors the CLI shares
with the apps keep their command in a ``CLI_*`` hint that
:func:`gaia.llm.surface_hints.surface_wording` swaps in for the CLI only.

The scan covers the Agent UI backend, the LLM layer whose errors it relays,
and the webui's English strings. A hit outside the allowlist below fails.
"""

from __future__ import annotations

import argparse
import ast
import re
from pathlib import Path

import pytest

from gaia import cli
from gaia.llm import surface_hints
from gaia.llm.surface_hints import surface_wording

REPO = Path(__file__).resolve().parents[2]
SRC = REPO / "src" / "gaia"
EN_LOCALE = SRC / "apps" / "webui" / "src" / "i18n" / "locales" / "en.ts"
SCANNED_PACKAGES = ("ui", "llm")

# Whole modules that only a terminal ever prints.
CLI_ONLY_MODULES = {
    "llm/custom_models_cli.py": "`gaia model` subcommand handlers",
}

# Modules still to reword, on less-travelled paths. Shrink this, never grow it.
PENDING_MODULES = {
    "llm/lemonade_choice.py": "#6676 is rewriting this file; reword after (#6703)",
    "llm/lemonade_embedded.py": "#6703",
    "llm/lemonade_supervisor.py": "#6703",
    # The webui reads "gaia init" in the start hint as "show the Set up button"
    # (ErrorNotice.namesSetupCommand); swap that for a field first (#6703).
    "llm/lemonade_launcher.py": "#6703",
    "llm/model_fit.py": "#6703",
    "llm/custom_models.py": "#6703",
    "llm/gpu_memory.py": "#6703",
    "llm/local_only.py": "#6703",
    "llm/vlm_client.py": "#6703",
}

# (module, text inside the string) that may keep its command, and why.
ALLOWED = {
    # Never shown in the app: CLI-only callers, log lines, argv, the eval.
    ("llm/lemonade_client.py", "gaia init --force-models"): "CLI download progress",
    ("llm/lemonade_manager.py", "  gaia init"): "printed to the CLI's stderr",
    ("llm/gateway.py", "gaia gateway auth --no-remember"): "`gaia gateway auth`",
    ("llm/gateway.py", "Pass one to `gaia gateway auth`"): "`gaia gateway auth`",
    ("llm/gateway.py", "Pick another with `gaia gateway models`"): "CLI callers only",
    ("llm/gateway.py", "GAIA did not give it to Lemonade. Run"): "log line",
    ("llm/gateway.py", "almost certainly rejecting the key"): "log line",
    ("llm/gateway.py", "which means the gateway rejected it"): "log line",
    ("ui/setup_runner.py", "gaia init --profile gaia"): "argv",
    ("ui/setup_runner.py", "gaia init (pid"): "log line",
    ("ui/setup_runner.py", "gaia init --check --json` printed"): "names the step",
    ("ui/routers/chat.py", "gaia eval agent"): "an env var only the eval sets",
    ("ui/routers/sessions.py", "gaia hub install"): "an agent id the app never lists",
    ("ui/security.py", "gaia eval agent"): "an override only the eval sends",
    # Lines #6686 rewords; left alone here to keep the two diffs apart.
    ("ui/setup_plan.py", "or `gaia config set default_model"): "#6686",
    ("llm/lemonade_client.py", "check the gateway connection"): "#6686",
    ("llm/lemonade_client.py", "`gaia model add <link>`"): "#6686",
    ("en.ts", "or run `gaia init` in a terminal"): "#6686",
    # Still to reword, on less-travelled paths (#6703).
    ("llm/gateway.py", "re-run `gaia gateway install`"): "#6703",
    ("llm/lemonade_client.py", "stop it with `gaia kill`"): "#6703",
    ("llm/lemonade_client.py", "Stop it with `gaia kill`"): "#6703",
    ("llm/lemonade_client.py", "(run `gaia init`) and retry"): "#6703",
    ("llm/lemonade_client.py", "Lemonade Server with: gaia init"): "#6703",
    ("llm/lemonade_client.py", "without `--profile npu`"): "#6703",
    ("llm/lemonade_client.py", "`gaia config set default_device gpu`"): "#6703",
    ("llm/lemonade_client.py", "`gaia config set default_model"): "#6703",
    ("llm/lemonade_manager.py", "`gaia init --profile npu`"): "#6703",
    ("llm/lemonade_manager.py", "run `gaia init` to set up GPU"): "#6703",
    ("llm/lemonade_manager.py", "To fix: run `gaia init`"): "#6703",
}


def _subcommands() -> list:
    parser = cli.build_parser()
    for action in parser._actions:  # pylint: disable=protected-access
        if isinstance(action, argparse._SubParsersAction):  # pylint: disable=W0212
            return sorted(action.choices)
    raise AssertionError("gaia's parser has no subcommands; the scan has no target")


COMMAND = re.compile(
    r"(?<![\w./~-])gaia\s+(?:" + "|".join(map(re.escape, _subcommands())) + r")\b"
)


def _docstrings(tree: ast.AST) -> set:
    found = set()
    for node in ast.walk(tree):
        if isinstance(
            node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            body = node.body
            if body and isinstance(body[0], ast.Expr):
                if isinstance(body[0].value, ast.Constant):
                    found.add(id(body[0].value))
    return found


def _cli_constants(tree: ast.AST) -> set:
    """Strings assigned to ``CLI_*``/``SERVICE_*`` names: terminal wording."""
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id.startswith(("CLI_", "SERVICE_"))
            for t in node.targets
        ):
            found.update(id(n) for n in ast.walk(node.value))
    return found


def _python_hits(path: Path, rel: str) -> list:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    skip = _docstrings(tree) | _cli_constants(tree)
    hits = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in skip
            and COMMAND.search(node.value)
        ):
            hits.append((rel, node.lineno, node.value))
    return hits


def _locale_hits() -> list:
    lines = EN_LOCALE.read_text(encoding="utf-8").splitlines()
    return [
        ("en.ts", n, line) for n, line in enumerate(lines, 1) if COMMAND.search(line)
    ]


def _all_hits() -> list:
    hits = _locale_hits()
    for package in SCANNED_PACKAGES:
        for path in sorted((SRC / package).rglob("*.py")):
            rel = path.relative_to(SRC).as_posix()
            if rel not in CLI_ONLY_MODULES and rel not in PENDING_MODULES:
                hits.extend(_python_hits(path, rel))
    return hits


def _allowed(rel: str, text: str) -> bool:
    return any(rel == module and snippet in text for module, snippet in ALLOWED)


def test_ui_reachable_text_names_no_gaia_command():
    offenders = [
        f"{rel}:{line}: {text.strip()[:160]!r}"
        for rel, line, text in _all_hits()
        if not _allowed(rel, text)
    ]
    assert not offenders, (
        "Text the Agent UI can show names a `gaia` command, which a desktop "
        "user cannot run. Say where in the app to fix it instead (see "
        "gaia/llm/surface_hints.py for wording the CLI swaps back), or add a "
        "commented ALLOWED entry if the text is CLI-only:\n  " + "\n  ".join(offenders)
    )


def test_the_scan_sees_a_command():
    assert COMMAND.search("Run `gaia init` to install it.")
    assert COMMAND.search("gaia daemon status")
    assert not COMMAND.search("saved in ~/.gaia config.json")
    assert not COMMAND.search("GAIA init failed")


@pytest.mark.parametrize(
    "name",
    sorted(
        n
        for n in dir(surface_hints)
        if n.startswith(("TUI_", "AGENT_UI_", "APP_"))
        and isinstance(getattr(surface_hints, n), str)
    ),
)
def test_app_hints_name_no_command(name):
    assert not COMMAND.search(getattr(surface_hints, name))


@pytest.mark.parametrize(
    "hint, command",
    [
        (surface_hints.TUI_SETUP_HINT, "gaia init"),
        (surface_hints.TUI_GATEWAY_KEY_HINT, "gaia gateway auth"),
        (surface_hints.APP_RESTART_HINT, "gaia daemon status"),
        (surface_hints.APP_SERVICE_DOWN_HINT, "gaia daemon start"),
    ],
)
def test_the_cli_keeps_its_command(hint, command):
    assert command in surface_wording(f"It failed. {hint}", "cli")
    assert not COMMAND.search(surface_wording(f"It failed. {hint}", "agent_ui"))


def test_a_cli_startup_failure_prints_the_cli_command(monkeypatch, capsys):
    """The CLI must not tell a terminal user to close and reopen the app."""
    from gaia.llm import lemonade_client, lemonade_manager, lemonade_service

    def _fails(**_):
        raise lemonade_client.LemonadeClientError(
            f"The model server was not back within 60s. {surface_hints.APP_RESTART_HINT}"
        )

    monkeypatch.setattr(lemonade_service, "ensure_daemon_owns_lemonade", lambda: None)
    monkeypatch.setattr(lemonade_client, "resolve_ctx_size", lambda **_: 65536)
    monkeypatch.setattr(lemonade_manager.LemonadeManager, "ensure_ready", _fails)

    assert cli.initialize_lemonade_for_agent("chat") == (False, None)

    err = capsys.readouterr().err
    assert surface_hints.CLI_RESTART_HINT in err
    assert surface_hints.APP_RESTART_HINT not in err


def test_a_failed_key_replay_tells_an_app_user_where_to_fix_it(monkeypatch):
    pytest.importorskip("fastapi")
    from gaia.llm import cloud_keys
    from gaia.ui._chat_helpers import _replay_cloud_key_for_relay

    def _rejected(provider):
        raise cloud_keys.CloudKeyError(
            f"The gateway rejected the key. {surface_hints.TUI_GATEWAY_KEY_HINT}"
        )

    monkeypatch.setattr(cloud_keys, "ensure_authenticated", _rejected)
    emitted = []
    sse = type("SSE", (), {"_emit": lambda self, event: emitted.append(event)})()

    _replay_cloud_key_for_relay(sse, "amd.DeepSeek-V4.1-Flash")

    (event,) = emitted
    assert surface_hints.AGENT_UI_GATEWAY_KEY_HINT in event["message"]
    assert not COMMAND.search(event["message"])
    assert "/gateway" not in event["message"]
