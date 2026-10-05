# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Naming the working directory keeps each request extending the one before.

Outside a project the system prompt says where relative paths resolve. The
working directory is fixed for the process, so the line must not change the
prompt from one turn to the next.
"""

from __future__ import annotations

import os

import test_prompt_prefix_stability as stability
from test_prompt_prefix_stability import flagship  # noqa: F401 - fixture


def test_working_directory_line_stays_fixed_across_turns(flagship):
    agent, model, doc = flagship
    stability._turn(agent, model, "Hi, I'm Sam.", ["Hi Sam."])
    stability._turn(agent, model, "What is in docs/notes.md?", ["No such file."])

    assert len(model.requests) == 2
    system = [request[0][0]["content"] for request in model.requests]
    assert f"Working directory: {os.getcwd()}" in system[0]
    assert str(doc.parent) in system[0]
    assert system[0] == system[1]
    before, after = (stability._render(request) for request in model.requests)
    assert after.startswith(before)
