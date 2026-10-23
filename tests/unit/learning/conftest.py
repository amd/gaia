# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Shared fixtures for the nightly-learning tests: an isolated learning home,
real chat and memory stores seeded with a small history, and a scripted
teacher."""

import json
from pathlib import Path

import pytest


@pytest.fixture
def lhome(tmp_path, monkeypatch):
    home = tmp_path / "learning"
    monkeypatch.setenv("GAIA_LEARNING_HOME", str(home))
    monkeypatch.setenv("GAIA_TRAIN_HOME", str(tmp_path / "train"))
    return home


class ScriptedTeacher:
    """Answers synthesis prompts with JSON built from the fact in the prompt,
    and any other prompt with a fixed sentence."""

    def __init__(self, bad_json_for=()):
        self.prompts = []
        self.bad_json_for = bad_json_for

    def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        if prompt.startswith("Fact: "):
            # The probe check: answer from the fact, as a good teacher would.
            return prompt.split("\n", 1)[0]
        if "What it must learn" not in prompt:
            return "A general answer."
        fact = prompt.split("):\n", 1)[1].split("\n\n", 1)[0]
        if any(b in fact for b in self.bad_json_for):
            return "Sorry, I can't help with that."
        first = fact.split()[1] if len(fact.split()) > 1 else fact
        return (
            "```json\n"
            + json.dumps(
                {
                    "examples": [
                        {"question": f"Q{i} about {first}?", "answer": f"{fact} (#{i})"}
                        for i in range(5)
                    ],
                    "probes": [
                        {
                            "question": f"Probe about {first}?",
                            "must_mention": [first, "INVENTED"],
                        },
                    ],
                }
            )
            + "\n```"
        )


@pytest.fixture
def teacher():
    return ScriptedTeacher()


@pytest.fixture
def stores(tmp_path):
    """Real ChatDatabase and MemoryStore files with one preference, one
    concept, one sensitive memory, a correction and a slow workflow."""
    from gaia.agents.base.memory_store import MemoryStore
    from gaia.ui.database import ChatDatabase

    chat_path = tmp_path / "chat" / "gaia_chat.db"
    chat_path.parent.mkdir(parents=True)
    mem_path = tmp_path / "memory.db"
    chat = ChatDatabase(str(chat_path))
    mem = MemoryStore(db_path=mem_path)
    mem.store("preference", "Jordan wants times in UTC with a Z suffix.", source="user")
    mem.store(
        "fact", "Bluefleet is the Halcyon staging cluster in us-east-2.", source="user"
    )
    mem.store(
        "fact",
        "Jordan's bank PIN hint is the dog's birthday.",
        source="user",
        sensitive=True,
    )

    s = chat.create_session(title="cache")["id"]
    chat.add_message(s, "user", "What port does the Halcyon cache use?")
    chat.add_message(s, "assistant", "Probably 8080.")
    chat.add_message(s, "user", "No, it's 7443 over TLS.")
    chat.add_message(
        s, "assistant", "Right: the Halcyon cache listens on Port 7443 over TLS."
    )

    s = chat.create_session(title="build")["id"]
    chat.add_message(s, "user", "Is the nightly build green?")
    mem.log_tool_call(
        "m1",
        "read_file",
        {"file_path": "C:/x.txt"},
        "",
        success=False,
        error="File not found",
    )
    mem.log_tool_call(
        "m1",
        "query_specific_file",
        {"file_path": "C:/b.md", "query": "nightly"},
        "green",
        success=True,
    )
    chat.add_message(
        s,
        "assistant",
        "Nightly #4521 is green.",
        agent_steps=[
            {
                "type": "tool",
                "tool": "read_file",
                "detail": "",
                "result": "",
                "success": False,
            },
            {
                "type": "tool",
                "tool": "query_specific_file",
                "detail": "",
                "result": "",
                "success": True,
            },
        ],
    )
    mem.close()
    chat.close()
    return {"chat": chat_path, "memory": mem_path}


@pytest.fixture
def since_epoch():
    from datetime import datetime, timezone

    return datetime(2000, 1, 1, tzinfo=timezone.utc)


def write_gguf(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"GGUF" + b"\0" * 32)
    return path
