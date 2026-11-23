# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The design reminder hook speaks once per session, and only for UI source."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
HOOK = REPO_ROOT / "scripts" / "ui_design_reminder.sh"
BASH = shutil.which("bash")

# On Windows only Git Bash runs it; System32's bash.exe is the WSL launcher.
pytestmark = pytest.mark.skipif(
    BASH is None or (sys.platform == "win32" and "system32" in BASH.lower()),
    reason="the hook runs under bash (Git Bash on Windows)",
)


def _run(tmp_path, file_path, session_id="s1"):
    payload = {
        "session_id": session_id,
        "tool_name": "Edit",
        "tool_input": {"file_path": file_path, "old_string": "a", "new_string": "b"},
    }
    done = subprocess.run(
        [BASH, str(HOOK)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env={**os.environ, "TMPDIR": str(tmp_path)},
        check=True,
    )
    return done.stdout


@pytest.mark.parametrize(
    "file_path",
    [
        "/repo/src/gaia/apps/webui/src/components/ChatView.tsx",
        "/repo/src/gaia/apps/webui/src/styles/index.css",
        "/repo/src/gaia/apps/webui/main.cjs",
        "/repo/tui/internal/ui/chat.go",
        r"C:\Users\me\gaia\tui\internal\ui\chat.go",
    ],
)
def test_a_ui_edit_gets_the_design_reminder(tmp_path, file_path):
    out = json.loads(_run(tmp_path, file_path))["hookSpecificOutput"]
    assert out["hookEventName"] == "PostToolUse"
    assert "ui-ux-pro-max" in out["additionalContext"]
    assert "gaia-design-language.mdx" in out["additionalContext"]


@pytest.mark.parametrize(
    "file_path",
    [
        "/repo/src/gaia/apps/webui/src/components/__tests__/ChatView.test.tsx",
        "/repo/src/gaia/apps/webui/visual/rtl.visual.ts",
        "/repo/tui/internal/ui/chat_test.go",
        "/repo/src/gaia/ui/server.py",
        "/repo/docs/guides/chat.mdx",
    ],
)
def test_tests_and_other_files_stay_quiet(tmp_path, file_path):
    assert _run(tmp_path, file_path) == ""


def test_the_reminder_comes_once_per_session(tmp_path):
    ui = "/repo/src/gaia/apps/webui/src/components/ChatView.tsx"
    assert _run(tmp_path, ui, "a")
    assert _run(tmp_path, "/repo/tui/internal/ui/chat.go", "a") == ""
    assert _run(tmp_path, ui, "b")


def test_settings_wire_the_hook_to_file_edits():
    settings = json.loads((REPO_ROOT / ".claude" / "settings.json").read_text())
    entries = settings["hooks"]["PostToolUse"]
    wired = [
        e
        for e in entries
        if any("ui_design_reminder.sh" in h["command"] for h in e["hooks"])
    ]
    assert wired and set(wired[0]["matcher"].split("|")) >= {"Edit", "Write"}
