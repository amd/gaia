# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Every field a turn saves on its steps comes back when the chat is reopened.

The response model ignores fields it doesn't declare, so a step field saved but
not declared there vanishes on reload: "Get the vision model" did, and the
finished answer pointed at a button that was gone.
"""

import pytest
from fastapi.testclient import TestClient

from gaia.ui.server import create_app

# One of each field the chat stream saves on a step.
SAVED_STEPS = [
    {
        "id": 1,
        "type": "tool",
        "label": "Using answer_question_about_image",
        "tool": "answer_question_about_image",
        "detail": "red.png",
        "narration": "Reading the image",
        "active": False,
        "success": False,
        "result": "The vision model isn't downloaded",
        "needsModel": "vision",
        "timestamp": 100,
        "latencyMs": 12.5,
    },
    {
        "id": 2,
        "type": "tool",
        "label": "Using git_log",
        "tool": "git_log",
        "mcpServer": "git",
        "mcpServerLabel": "Git",
        "mcpTool": "git_log",
        "mcpOutput": {"text": "abc123 first commit", "truncated": False},
        "active": False,
        "success": True,
        "declined": True,
        "timestamp": 101,
    },
    {
        "id": 3,
        "type": "tool",
        "label": "Using run_shell_command",
        "tool": "run_shell_command",
        "commandOutput": {"command": "dir", "stdout": "a.txt", "return_code": 0},
        "fileList": {"files": [{"name": "a.txt"}], "total": 1},
        "render": "email_pre_scan",
        "data": {"count": 1},
        "timedOut": True,
        "active": False,
        "timestamp": 102,
    },
    {
        "id": 4,
        "type": "plan",
        "label": "Created plan",
        "planSteps": ["Look", "Answer"],
        "planNarrations": ["Looking", "Answering"],
        "currentStep": 1,
        "active": False,
        "success": True,
        "timestamp": 103,
    },
    {
        "id": 5,
        "type": "policy_alert",
        "label": "Policy blocked drop_table",
        "tool": "drop_table",
        "decision": "BLOCK",
        "reason": "Destructive",
        "ruleIds": ["r1"],
        "policyVersion": "v1",
        "receiptId": "rcpt_1",
        "active": False,
        "success": False,
        "timestamp": 104,
    },
]


@pytest.fixture
def client(tmp_path, monkeypatch):
    home = (tmp_path / "home").resolve()
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    app = create_app(db_path=":memory:")
    return app, TestClient(app)


def _reopened_steps(client):
    app, http = client
    db = app.state.db
    session = db.create_session(model="M-GGUF", agent_type="gaia")
    db.add_message(session["id"], "user", "What colour is the attached image?")
    db.add_message(
        session["id"], "assistant", "I can't read it yet.", agent_steps=SAVED_STEPS
    )
    resp = http.get(f"/api/sessions/{session['id']}/messages")
    assert resp.status_code == 200
    answer = next(m for m in resp.json()["messages"] if m["role"] == "assistant")
    return answer["agent_steps"]


def test_the_vision_offer_survives_a_reload(client):
    steps = _reopened_steps(client)

    assert steps[0]["needsModel"] == "vision"


@pytest.mark.parametrize("index", range(len(SAVED_STEPS)))
def test_every_saved_step_field_comes_back(client, index):
    returned = _reopened_steps(client)[index]

    for key, value in SAVED_STEPS[index].items():
        assert key in returned, f"step field {key!r} is saved but dropped on reload"
        if isinstance(value, dict):
            assert {k: returned[key][k] for k in value} == value, key
        else:
            assert returned[key] == value, key
