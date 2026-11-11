# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The frozen ``gaia`` carries every extra a command it lists needs.

v0.26.0-rc18 froze only ``[ui]``: ``gaia mcp serve``/``tui``/``code-index``
died on "No module named 'mcp'", ``gaia talk`` on a missing sounddevice, and the
Windows schedule notification fell back to a message box. A frozen app has no
pip, so the build has to carry them, and the smoke test has to start them.
"""

from __future__ import annotations

import ast
import importlib.util
import re
import sys
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[3]
CORE = REPO / "installer" / "core"
BUILD_CORE = REPO / ".github" / "workflows" / "build_core.yml"
README = REPO / "installer" / "README.md"
CLI = REPO / "src" / "gaia" / "cli.py"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(
        f"core_extras_{name}", CORE / f"{name}.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


freeze = _load("freeze")
smoke = _load("smoke_test")


def _setup_extras() -> dict:
    tree = ast.parse((REPO / "setup.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "setup":
            for kw in node.keywords:
                if kw.arg == "extras_require":
                    return ast.literal_eval(kw.value)
    raise AssertionError("no extras_require in setup.py")


def _installed_extras(text: str) -> tuple[str, ...]:
    match = re.search(r'-e "\.\[([^\]]+)\]" pyinstaller', text)
    assert match, "no freeze-environment install line found"
    return tuple(match.group(1).split(","))


def test_freeze_carries_the_extras_listed_commands_need():
    for extra in ("ui", "mcp", "talk", "notify"):
        assert extra in freeze.FREEZE_EXTRAS
    declared = _setup_extras()
    for extra in freeze.FREEZE_EXTRAS:
        assert extra in declared, f"setup.py declares no [{extra}] extra"
    assert any(r.startswith("plyer") for r in declared["notify"])


def test_the_freeze_job_installs_exactly_those_extras():
    steps = yaml.safe_load(BUILD_CORE.read_text(encoding="utf-8"))["jobs"]["freeze"][
        "steps"
    ]
    install = next(s for s in steps if "pyinstaller" in s.get("run", ""))
    assert _installed_extras(install["run"]) == freeze.FREEZE_EXTRAS
    assert _installed_extras(README.read_text(encoding="utf-8")) == (
        freeze.FREEZE_EXTRAS
    )
    assert _installed_extras(freeze.install_command()) == freeze.FREEZE_EXTRAS


def test_freeze_refuses_an_environment_without_them(tmp_path, monkeypatch):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text('<div id="root"></div>', encoding="utf-8")
    monkeypatch.setattr(freeze, "WEBUI_DIST", dist)
    absent = {"mcp", "sounddevice", "plyer"}
    monkeypatch.setattr(
        importlib.util,
        "find_spec",
        lambda name, _real=importlib.util.find_spec: (
            None if name in absent else _real(name) if name == "gaia" else object()
        ),
    )
    with pytest.raises(SystemExit) as exc:
        freeze._verify_freeze_env()
    message = str(exc.value)
    for module in absent:
        assert module in message
    assert '".[ui,mcp,talk,notify]"' in message


def test_freeze_collects_what_is_imported_by_name():
    assert "mcp" in freeze.COLLECT_SUBMODULES
    assert {"sounddevice", "plyer"} <= set(freeze.REQUIRED_MODULES)


def test_smoke_imports_the_new_modules_in_the_executable():
    for module in ("mcp.server", "gaia.audio.audio_recorder", "plyer"):
        assert module in smoke.BUNDLED_MODULES


def test_smoke_starts_every_stdio_mcp_server_the_cli_lists():
    commands = smoke.mcp_stdio_commands("http://127.0.0.1:9", Path("repo"))
    cli = CLI.read_text(encoding="utf-8")
    for args in commands.values():
        assert args[0] == "mcp"
        assert f'mcp_subparsers.add_parser(\n        "{args[1]}"' in cli
    assert {args[1] for args in commands.values()} == {"serve", "tui", "code-index"}
    assert "--stdio" in commands["gaia mcp serve"]
    assert "--stdio" in commands["gaia mcp tui"]


@pytest.mark.parametrize(
    "stdout, answered",
    [
        (
            '{"jsonrpc":"2.0","id":1,"result":{"protocolVersion":"2025-06-18",'
            '"serverInfo":{"name":"GAIA TUI","version":""}}}\n',
            True,
        ),
        ("Starting...\n" '{"jsonrpc":"2.0","id":1,"result":{"serverInfo":{}}}', True),
        ('{"jsonrpc":"2.0","id":1,"error":{"code":-32600,"message":"x"}}', False),
        ("Traceback (most recent call last):\nModuleNotFoundError: mcp", False),
        ("", False),
    ],
)
def test_smoke_recognises_an_initialize_answer(stdout, answered):
    assert smoke.answered_initialize(stdout) is answered


def test_smoke_talk_check_runs_against_this_checkout():
    """The script the smoke test hands the executable is valid where audio is."""
    pytest.importorskip("sounddevice")
    if sys.platform.startswith("linux"):
        pytest.skip("Linux wheels carry no PortAudio")
    exec(compile(smoke.TALK_AUDIO_CHECK, "<talk-check>", "exec"), {})
