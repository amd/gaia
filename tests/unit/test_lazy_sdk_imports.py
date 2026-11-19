# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The OpenAI and Anthropic SDKs load on first use, not when GAIA is imported."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SDKS = ("openai", "anthropic")


def _loaded_sdks(statement: str) -> str:
    """Run *statement* in a fresh interpreter; return the SDKs it left loaded."""
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        [str(REPO / "src"), str(REPO / "hub" / "agents" / "gaia" / "python")]
    )
    code = (
        f"import sys\n{statement}\n"
        f"print(','.join(m for m in {SDKS!r} if m in sys.modules))"
    )
    run = subprocess.run(
        [sys.executable, "-c", code],
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    assert run.returncode == 0, run.stderr[-2000:]
    return run.stdout.strip().splitlines()[-1] if run.stdout.strip() else ""


@pytest.mark.parametrize(
    "module",
    ["gaia", "gaia.llm.lemonade_client", "gaia.daemon.server", "gaia.ui.server"],
)
def test_importing_gaia_does_not_load_the_sdks(module):
    assert _loaded_sdks(f"import {module}") == ""


def test_the_sdk_loads_when_a_client_is_built():
    loaded = _loaded_sdks(
        "from gaia.llm.lazy_openai import OpenAI\n"
        "OpenAI(base_url='http://127.0.0.1:1/v1', api_key='x')"
    )
    assert loaded == "openai"


def test_error_classes_are_empty_until_the_sdk_is_loaded():
    out = _loaded_sdks(
        "from gaia.llm.lazy_openai import loaded_openai_errors as e, openai\n"
        "before = e('APIError')\n"
        "cls = openai.APIError\n"
        "assert before == () and e('APIError') == (cls,), (before, e('APIError'))"
    )
    assert out == "openai"
