# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Cross-compatibility between gaiacpp AutomationStore and Python automations."""

from __future__ import annotations

import json
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from gaia.automations import outbox, runner, store
from gaia.ui.routers import automations
from gaia.ui.sidecar.native_tools import (
    NATIVE_MUTATING_TOOLS,
    NATIVE_TOOL_LABELS,
    native_result_summary,
)

LOCAL = ("127.0.0.1", 50000)


@pytest.fixture(autouse=True)
def gaia_home(tmp_path, monkeypatch):
    monkeypatch.setenv("GAIA_HOME", str(tmp_path))
    monkeypatch.delenv("GAIA_CONFIG_DIR", raising=False)


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(automations.router)
    return TestClient(app, client=LOCAL, headers={"X-Gaia-UI": "1"})


def test_python_store_and_api_read_gaiacpp_record():
    now = time.time()
    payload = {
        "automations": [
            {
                "id": "ab12cd34",
                "name": "Call Sam",
                "kind": "reminder",
                "prompt": "",
                "say": "Call Sam",
                "every_seconds": 0,
                "next_at": now + 3600.0,
                "check": "",
                "quiet": "",
                "days": "",
                "paused": False,
                "created_at": now,
                "last_run_at": 0.0,
                "runs": 0,
                "last_output": "",
                "checked": False,
                "failures": 0,
                "last_alert": "",
            },
            {
                "id": "ef56ab78",
                "name": "Morning inbox",
                "kind": "summary",
                "prompt": "Summarize unread mail",
                "say": "",
                "every_seconds": 86400,
                "next_at": now + 7200.0,
                "check": "",
                "quiet": "22:00-07:00",
                "days": "mon,tue,wed,thu,fri",
                "paused": False,
                "created_at": now,
                "last_run_at": 0.0,
                "runs": 0,
                "last_output": "",
                "checked": False,
                "failures": 0,
                "last_alert": "",
            },
        ]
    }
    target = store.path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    items = store.load()
    assert [a.id for a in items] == ["ab12cd34", "ef56ab78"]
    assert items[0].kind == store.KIND_REMINDER
    assert items[0].say == "Call Sam"
    assert items[1].days == "mon,tue,wed,thu,fri"

    client = _client()
    rows = client.get("/api/automations").json()["automations"]
    assert [r["name"] for r in rows] == ["Call Sam", "Morning inbox"]

    paused = client.post("/api/automations/ef56ab78/pause").json()
    assert paused["paused"] is True

    class _Sink:
        def __init__(self):
            self.posts = []

        def post(self, text):
            self.posts.append(text)
            return True

        def start_turn(self, *args, **kwargs):
            raise AssertionError("say reminder must not start a turn")

        def check_env(self):
            return None

    sink = _Sink()
    ran = runner.run_due(sink, now + 3601.0)
    assert [a.id for a in ran] == ["ab12cd34"]
    assert sink.posts == ["⏰ **Call Sam**"]
    assert [a.id for a in store.load()] == ["ef56ab78"]


def test_outbox_drop_format_compatible_with_python_outbox():
    outbox.enqueue(
        outbox.Delivery(
            id="del12345",
            automation_id="ab12cd34",
            name="Call Sam",
            kind=outbox.KIND_POST,
            text="Call Sam",
            due_at=time.time(),
        )
    )
    assert len(outbox.pending()) == 1
    assert outbox.drop_for("ab12cd34") == 1
    assert outbox.pending() == []


def test_native_automation_tool_labels_and_summaries():
    expected_tools = {
        "list_automations",
        "create_automation",
        "edit_automation",
        "pause_automation",
        "resume_automation",
        "remove_automation",
    }
    for name in expected_tools:
        assert name in NATIVE_TOOL_LABELS
    assert {
        "create_automation",
        "edit_automation",
        "pause_automation",
        "resume_automation",
        "remove_automation",
    }.issubset(NATIVE_MUTATING_TOOLS)
    assert "list_automations" not in NATIVE_MUTATING_TOOLS

    assert (
        native_result_summary(
            "create_automation",
            {"status": "success", "message": "Reminder 'Call Sam' set for 17:00."},
        )
        == "Reminder 'Call Sam' set for 17:00."
    )
    assert (
        native_result_summary(
            "list_automations",
            {"status": "success", "count": 2, "automations": [{}, {}]},
        )
        == "2 automations"
    )
    assert (
        native_result_summary(
            "pause_automation",
            {"status": "success", "message": "Paused 'Call Sam'."},
        )
        == "Paused 'Call Sam'."
    )
