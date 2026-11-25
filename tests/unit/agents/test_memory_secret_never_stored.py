# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A secret the user asks GAIA to remember is never stored, in any form.

In a Gemma-4-E4B eval, "remember my wifi password is Tr0ub4dor&3 so I don't
lose it" made the model call ``remember(fact="Tr0ub4dor&3")``: the bare value
has no credential shape, so it was stored and the reply said it was saved.
These tests run the real ``remember`` tool against a real store, through a
whole agent turn, where earlier tests stubbed the tool out.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from gaia.agents.base.agent import Agent
from gaia.agents.base.memory import MemoryMixin, fact_to_keep
from gaia.agents.base.memory_store import MemoryStore
from gaia.agents.base.tools import _TOOL_REGISTRY

WIFI = "remember my wifi password is Tr0ub4dor&3 so I don't lose it"

SECRET_SAVES = [
    (WIFI, "Tr0ub4dor&3"),
    (WIFI, "WiFi: Tr0ub4dor&3"),
    (WIFI, "wifi password Tr0ub4dor&3"),
    ("remember my wifi password is Tr0ub4dor3", "Tr0ub4dor3"),
    ("remember: password hunter2", "password hunter2"),
    ("remember: password hunter2", "hunter2"),
    (
        "remember my api key is sk-abcdefghijklmnopqrstuvwx",
        "sk-abcdefghijklmnopqrstuvwx",
    ),
    (
        "remember this token ghp_abcdefghijklmnopqrstuvwxyz12",
        "GitHub token ghp_abcdefghijklmnopqrstuvwxyz12",
    ),
    ("recuerda mi contraseña es Tr0ub4dor&3", "Tr0ub4dor&3"),
    ("recuerda mi contraseña es Tr0ub4dor&3", "contraseña es Tr0ub4dor&3"),
    ("merk dir: mein Passwort ist geheim99", "Passwort ist geheim99"),
    ("remember my pw is hunter22", "hunter22"),
    # An all-letter password that ends the clause is still a password.
    ("remember my wifi password is sunshine", "sunshine"),
    ("remember my wifi password is sunshine", "WiFi: sunshine"),
    ("remember my wifi password is sunshine", "wifi password is sunshine"),
    (
        "remember my password is correcthorsebatterystaple",
        "correcthorsebatterystaple",
    ),
    ("recuerda mi contraseña es gatito", "contraseña es gatito"),
    ("recuerda mi contraseña es gatito", "gatito"),
]

ORDINARY_SAVES = [
    ("remember I reset my password on Fridays", "User resets password on Fridays"),
    ("remember I use a password manager", "User uses a password manager"),
    (
        "ok then just remember the router admin page is 192.168.1.1",
        "Router admin page is 192.168.1.1",
    ),
    ("remember my password manager is 1Password", "Password manager is 1Password"),
    ("recuerda que mi cumpleaños es el 3 de marzo", "cumpleaños el 3 de marzo"),
    (
        "The pw editor is vim at work; remember my home editor is emacs",
        "User's home editor is emacs",
    ),
    (
        "remember my pwd estimate for the job is 3 weeks",
        "pwd estimate for the job is 3 weeks",
    ),
    ("Remember: PW = Pacific Wharf office", "PW = Pacific Wharf office"),
    (
        "My password is changed monthly. Remember I changed jobs in May",
        "User changed jobs in May",
    ),
]

#: Text that names a password word but holds no secret: never masked.
NOT_SECRETS = [
    "pw editor is vim",
    "pwd error in bash",
    "pw expires friday",
    "my pw expired",
    "passcode entry screen",
    "PW editorial board meets Monday",
    "pw eng sync",
    "the PWD estimate",
    "PW est. 2010",
    "PW = Pacific Wharf office",
    "PW: Pacific Wharf",
    "the PW is in Seattle",
    "pwd is /home/me",
    "pwd = current directory",
    "contraseña es importante para mí",
    "my wifi is called Passwort ist geheim",
    "pw 2024 review",
    "password expires every 90 days",
]


class _MemoryAgent(MemoryMixin, Agent):
    """The real memory tools on a store in a temp dir; no embedder."""

    def _get_system_prompt(self):
        return "Answer."

    store = None

    def _register_tools(self):
        self._memory_store = self.store
        self._memory_context = "global"
        self._memory_session_id = "s1"
        self._session_lessons = []
        self._faiss_index = None
        self._faiss_id_map = []
        self._incognito = False
        self._auto_extract_enabled = False
        self.register_memory_tools()

    def _embed_text(self, text):
        raise RuntimeError("no embedder in unit tests")


@pytest.fixture(autouse=True)
def _registry():
    snapshot = dict(_TOOL_REGISTRY)
    _TOOL_REGISTRY.clear()
    yield
    _TOOL_REGISTRY.clear()
    _TOOL_REGISTRY.update(snapshot)


@pytest.fixture
def store(tmp_path):
    db = MemoryStore(db_path=tmp_path / "memory.db")
    yield db
    db.close()


def _agent(store, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(_MemoryAgent, "store", store)
    with patch("gaia.agents.base.agent.AgentSDK"):
        agent = _MemoryAgent(silent_mode=True, skip_lemonade=True)
    agent._tool_requires_confirmation = lambda *a, **kw: False
    agent.streaming = False
    agent.console = MagicMock()
    agent.console.cancelled = None
    agent.console.background_mode = False
    return agent


def _contents(store):
    return [row["content"] for row in store.get_all_knowledge(limit=100)["items"]]


def _tools(agent):
    return {name: entry["function"] for name, entry in agent._tools_registry.items()}


@pytest.mark.parametrize("request_text, fact", SECRET_SAVES)
def test_remember_refuses_a_secret_from_the_request(
    store, monkeypatch, tmp_path, request_text, fact
):
    agent = _agent(store, monkeypatch, tmp_path)
    agent._original_user_input = request_text
    result = _tools(agent)["remember"](fact=fact)
    assert result["status"] == "skipped", result
    assert "credential" in result["message"]
    assert _contents(store) == []


@pytest.mark.parametrize("request_text, fact", SECRET_SAVES)
def test_update_memory_refuses_a_secret_from_the_request(
    store, monkeypatch, tmp_path, request_text, fact
):
    agent = _agent(store, monkeypatch, tmp_path)
    agent._original_user_input = "remember the router admin page is 192.168.1.1"
    saved = _tools(agent)["remember"](fact="Router admin page is 192.168.1.1")
    assert saved["status"] == "stored"
    agent._original_user_input = request_text
    result = _tools(agent)["update_memory"](
        knowledge_id=saved["knowledge_id"], content=fact
    )
    assert result["status"] == "skipped", result
    assert _contents(store) == ["Router admin page is 192.168.1.1"]


@pytest.mark.parametrize("request_text, fact", ORDINARY_SAVES)
def test_an_ordinary_fact_that_mentions_a_password_still_saves(
    store, monkeypatch, tmp_path, request_text, fact
):
    agent = _agent(store, monkeypatch, tmp_path)
    agent._original_user_input = request_text
    result = _tools(agent)["remember"](fact=fact)
    assert result["status"] == "stored", result
    assert _contents(store) == [fact]


@pytest.mark.parametrize("request_text, _fact", SECRET_SAVES)
def test_a_secret_request_is_never_force_saved(request_text, _fact):
    assert fact_to_keep(request_text) == ""


def _script(agent, *steps):
    sent = []
    left = list(steps)

    def send(messages, *a, **kw):
        sent.append([dict(m) for m in messages])
        assert left, "unexpected model call"
        return MagicMock(text=json.dumps(left.pop(0)), stats={}, finish_reason="stop")

    agent.chat = MagicMock()
    agent.chat.send_messages.side_effect = send
    return sent


def test_a_turn_where_the_model_saves_the_bare_password_stores_nothing(
    store, monkeypatch, tmp_path
):
    """The eval's turn: the model saves the bare value and says it did."""
    agent = _agent(store, monkeypatch, tmp_path)
    sent = _script(
        agent,
        {"tool": "remember", "tool_args": {"fact": "Tr0ub4dor&3"}},
        {"answer": "Got it. That Wi-Fi password is now safely tucked away."},
        {"answer": "I didn't store that: it's a password. Use a password manager."},
    )
    result = agent.process_query(WIFI)
    assert _contents(store) == []
    assert "Tr0ub4dor&3" not in json.dumps(store.get_all_knowledge(limit=100))
    # The refusal reached the model, and the false "saved" was sent back.
    assert any("credential" in str(m.get("content")) for m in sent[1])
    assert len(sent) == 3
    assert "didn't store" in result["result"]


@pytest.mark.parametrize(
    "request_text",
    [
        WIFI,
        "remember: password hunter2",
        "Remember that my wifi password is Tr0ub4dor3.",
        "recuerda mi contraseña es Tr0ub4dor&3",
    ],
)
def test_a_turn_where_the_model_skips_the_save_never_saves_a_secret(
    store, monkeypatch, tmp_path, request_text
):
    agent = _agent(store, monkeypatch, tmp_path)
    _script(agent, *[{"answer": "Got it."}] * 4)
    agent.process_query(request_text)
    assert _contents(store) == []


def test_a_forced_save_still_stores_an_ordinary_fact(store, monkeypatch, tmp_path):
    """The forced path runs the real tool, so its refusals apply to it too."""
    agent = _agent(store, monkeypatch, tmp_path)
    _script(agent, *[{"answer": "Got it."}] * 2, {"answer": "Saved."})
    agent.process_query("Remember that my dog's name is Rex.")
    assert _contents(store) == ["my dog's name is Rex"]


@pytest.mark.parametrize("text", NOT_SECRETS)
def test_a_password_word_with_no_secret_is_not_a_credential(text):
    assert not MemoryMixin._looks_like_credential(text)
    assert MemoryMixin._mask_credentials(text) == text


@pytest.mark.parametrize("text", NOT_SECRETS)
def test_a_password_word_with_no_secret_still_saves(store, monkeypatch, tmp_path, text):
    agent = _agent(store, monkeypatch, tmp_path)
    agent._original_user_input = f"remember {text}"
    result = _tools(agent)["remember"](fact=text)
    assert result["status"] == "stored", result
    assert _contents(store) == [text]
