# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Naming the working directory keeps each request extending the one before.

Outside a project the session context, at the start of the first user
message, says where relative paths resolve. The working directory is fixed
for the process, so the line must not change the request from turn to turn.
"""

from __future__ import annotations

import os

import test_prompt_prefix_stability as stability


def test_working_directory_line_stays_fixed_across_turns(flagship):
    agent, model, doc = flagship
    stability._turn(agent, model, "Hi, I'm Sam.", ["Hi Sam."])
    stability._turn(
        agent,
        model,
        "What is in docs/notes.md?",
        ["Want me to open it and summarise it?"],
    )

    assert len(model.requests) == 2
    system = [request[0][0]["content"] for request in model.requests]
    opening = stability._first_user_text(model.requests[0])
    assert f"Working directory: {os.getcwd()}" in opening
    assert str(doc.parent) in opening
    assert str(doc.parent) not in system[0]
    assert system[0] == system[1]
    stability._assert_each_extends_the_last(model.requests)
