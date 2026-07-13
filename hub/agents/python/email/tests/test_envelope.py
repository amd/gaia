# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Single-source-of-truth tests for the ``_envelope_ok``/``_envelope_err``
JSON envelope helpers (#1232 AC2).

``gaia_agent_email/tools/envelope.py`` must be the ONE place these two
helpers are defined. Every tool module that emits a JSON string result must
import (not redefine) them from there, and no module may keep a local copy.
"""

from __future__ import annotations

import ast
import json
from datetime import datetime
from pathlib import Path

import pytest

pytest.importorskip("gaia_agent_email")

CONSUMER_MODULE_NAMES = [
    "read_tools",
    "summarize_tools",
    "phishing_tools",
    "delete_tools",
    "reply_tools",
    "profile_tools",
    "voice_tools",
    "organize_tools",
    "schedule_tools",
    "preference_tools",
    "calendar_tools",
    "followup_tools",
]

_TOOLS_DIR = Path(__file__).resolve().parents[1] / "gaia_agent_email" / "tools"


# ---------------------------------------------------------------------------
# Output shape
# ---------------------------------------------------------------------------


def test_envelope_ok_shape():
    from gaia_agent_email.tools.envelope import _envelope_ok

    assert json.loads(_envelope_ok({"x": 1})) == {"ok": True, "data": {"x": 1}}


def test_envelope_ok_default_str_for_non_json_native_values():
    from gaia_agent_email.tools.envelope import _envelope_ok

    when = datetime(2026, 7, 13)
    parsed = json.loads(_envelope_ok({"when": when}))
    assert parsed["data"]["when"] == str(when)


def test_envelope_err_shape():
    from gaia_agent_email.tools.envelope import _envelope_err

    assert json.loads(_envelope_err("boom")) == {"ok": False, "error": "boom"}


# ---------------------------------------------------------------------------
# Wiring — every consumer must re-export the SAME function objects
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("module_name", CONSUMER_MODULE_NAMES)
def test_consumer_reexports_envelope_helpers(module_name):
    import importlib

    from gaia_agent_email.tools import envelope

    module = importlib.import_module(f"gaia_agent_email.tools.{module_name}")

    assert module._envelope_ok is envelope._envelope_ok, (
        f"{module_name}._envelope_ok must be the same object as "
        "envelope._envelope_ok (direct re-export, not a local copy)"
    )
    assert module._envelope_err is envelope._envelope_err, (
        f"{module_name}._envelope_err must be the same object as "
        "envelope._envelope_err (direct re-export, not a local copy)"
    )


# ---------------------------------------------------------------------------
# AST anti-regression — no module may keep a local definition
# ---------------------------------------------------------------------------


def test_no_local_envelope_definitions_remain():
    offenders = []
    for path in sorted(_TOOLS_DIR.glob("*.py")):
        if path.name in {"envelope.py", "__init__.py"}:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        local_defs = {
            node.name
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef)
            and node.name in {"_envelope_ok", "_envelope_err"}
        }
        if local_defs:
            offenders.append(f"{path.name}: {sorted(local_defs)}")

    assert not offenders, (
        "Found local _envelope_ok/_envelope_err definitions outside "
        f"envelope.py: {offenders}"
    )
