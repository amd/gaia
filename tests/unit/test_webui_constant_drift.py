# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""The Agent UI's copies of backend constants, pinned to Python.

The webui can't import Python, so it keeps its own copy of each limit below,
read here from source. The browser pane's blocked-port list is one of them: a
port missing there is a GAIA service a web page can reach.
"""

import re
from pathlib import Path

from gaia.llm.lemonade_client import DEFAULT_CONTEXT_SIZE, DEFAULT_PORT
from gaia.mcp import ports as mcp_ports
from gaia.ui.database import PLACEHOLDER_TITLES
from gaia.ui.models import MAX_SESSION_TITLE_CHARS
from gaia.ui.routers.speech import MAX_SPEECH_CHARS
from gaia.ui.routers.system import _MIN_CONTEXT_SIZE

REPO = Path(__file__).resolve().parents[2]
WEBUI = "src/gaia/apps/webui"


def _only_match(rel: str, pattern: str) -> str:
    text = (REPO / rel).read_text(encoding="utf-8")
    found = re.findall(pattern, text)
    assert len(found) == 1, (
        f"{rel}: expected one match for {pattern!r}, found {len(found)}. "
        "If the line moved or was reworded, update the pattern here."
    )
    return found[0]


def _int_const(rel: str, name: str) -> int:
    return int(_only_match(rel, rf"const {name} = ([\d_]+);"))


def test_min_context_size_matches_python():
    ts = _int_const(f"{WEBUI}/src/utils/constants.ts", "MIN_CONTEXT_SIZE")
    assert ts == _MIN_CONTEXT_SIZE == DEFAULT_CONTEXT_SIZE, (
        f"webui MIN_CONTEXT_SIZE {ts} differs from "
        f"gaia.ui.routers.system._MIN_CONTEXT_SIZE {_MIN_CONTEXT_SIZE}; "
        "change both together."
    )


def test_max_session_title_chars_matches_python():
    ts = _int_const(f"{WEBUI}/src/utils/constants.ts", "MAX_SESSION_TITLE_CHARS")
    assert ts == MAX_SESSION_TITLE_CHARS, (
        f"webui MAX_SESSION_TITLE_CHARS {ts} differs from "
        f"gaia.ui.models.MAX_SESSION_TITLE_CHARS {MAX_SESSION_TITLE_CHARS}."
    )


def test_max_speech_chars_matches_python():
    ts = _int_const(f"{WEBUI}/src/services/speechApi.ts", "MAX_SPEECH_CHARS")
    assert ts == MAX_SPEECH_CHARS, (
        f"webui MAX_SPEECH_CHARS {ts} differs from "
        f"gaia.ui.routers.speech.MAX_SPEECH_CHARS {MAX_SPEECH_CHARS}."
    )


def test_placeholder_titles_match_python():
    rel = f"{WEBUI}/src/utils/sessionTitle.ts"
    body = _only_match(rel, r"const PLACEHOLDER_TITLES = new Set\(\[([^\]]*)\]\);")
    titles = re.findall(r"'([^']*)'", body)
    assert len(titles) == len(set(titles)), f"{rel} lists a title twice: {titles}"
    assert set(titles) == PLACEHOLDER_TITLES, (
        f"{rel} PLACEHOLDER_TITLES {sorted(titles)} differ from "
        f"gaia.ui.database.PLACEHOLDER_TITLES {sorted(PLACEHOLDER_TITLES)}."
    )


SERVICE_PORTS = f"{WEBUI}/services/browser-pane/service-ports.cjs"


def test_pane_blocks_lemonades_default_port():
    cjs = _int_const(SERVICE_PORTS, "LEMONADE_DEFAULT_PORT")
    assert cjs == DEFAULT_PORT, (
        f"{SERVICE_PORTS} LEMONADE_DEFAULT_PORT {cjs} differs from "
        f"gaia.llm.lemonade_client.DEFAULT_PORT {DEFAULT_PORT}: the browser pane "
        "would let pages reach Lemonade."
    )


def test_pane_blocks_every_mcp_port():
    body = _only_match(SERVICE_PORTS, r"const MCP_PORTS = \[([^\]]*)\];")
    cjs = sorted(int(p) for p in re.findall(r"\d+", body))
    python = sorted(
        value
        for name, value in vars(mcp_ports).items()
        if name.endswith("_PORT") and isinstance(value, int)
    )
    assert python, "gaia.mcp.ports defines no *_PORT constants; was it moved?"
    assert cjs == python, (
        f"{SERVICE_PORTS} MCP_PORTS {cjs} differ from the ports in "
        f"gaia/mcp/ports.py {python}: the browser pane would let pages reach "
        "a GAIA service."
    )
