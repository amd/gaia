# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""The default model name and Lemonade ports, pinned across languages.

Python owns both: ``DEFAULT_MODEL_NAME`` in ``gaia.llm.lemonade_client`` and
``DETECT_PORTS`` in ``gaia.llm.lemonade_choice``. The Agent UI, the TUI and the
native agent can't import them, so each keeps one copy, read here from source.
"""

import re
from pathlib import Path

import pytest

from gaia.llm.lemonade_choice import DETECT_PORTS
from gaia.llm.lemonade_client import DEFAULT_MODEL_NAME

REPO = Path(__file__).resolve().parents[2]

# (file, pattern whose one group is the copy of DEFAULT_MODEL_NAME)
MODEL_COPIES = [
    (
        "src/gaia/apps/webui/src/utils/constants.ts",
        r"export const DEFAULT_MODEL_NAME = '([^']+)';",
    ),
    ("tui/internal/lemonade/serverinfo.go", r'const DefaultChatModel = "([^"]+)"'),
    ("cpp/agents/gaiacpp/main.cpp", r'\{"modelId", "([^"]+)"\}'),
    ("cpp/agents/gaiacpp/sidecar.cpp", r'defaultModel\.empty\(\) \? "([^"]+)"'),
]


def _only_match(rel: str, pattern: str) -> str:
    text = (REPO / rel).read_text(encoding="utf-8")
    found = re.findall(pattern, text)
    assert len(found) == 1, (
        f"{rel}: expected one match for {pattern!r}, found {len(found)}. "
        "If the line moved or was reworded, update the pattern here."
    )
    return found[0]


@pytest.mark.parametrize("rel,pattern", MODEL_COPIES, ids=[c[0] for c in MODEL_COPIES])
def test_default_model_name_matches_python(rel, pattern):
    assert _only_match(rel, pattern) == DEFAULT_MODEL_NAME, (
        f"{rel} names a different default model than "
        "gaia.llm.lemonade_client.DEFAULT_MODEL_NAME; change both together."
    )


def test_tui_lemonade_ports_match_python():
    rel = "tui/internal/lemonade/connection.go"
    body = _only_match(rel, r"var LocalPorts = \[\]string\{([^}]*)\}")
    go_ports = tuple(int(p) for p in re.findall(r'"(\d+)"', body))
    assert go_ports == tuple(DETECT_PORTS), (
        f"{rel} LocalPorts {go_ports} differ from "
        f"gaia.llm.lemonade_choice.DETECT_PORTS {tuple(DETECT_PORTS)}; "
        "change both together."
    )
