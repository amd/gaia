"""Pin the tool surface a Telegram message can reach.

Telegram is the least-trusted input source in the product: anyone who finds
the bot can send it text. A session is therefore a plain ``AgentSDK`` — an LLM
completion wrapper with no tool loop — and not an ``Agent`` subclass, which
carries a ``_tools_registry`` that can hold shell and file-write tools.

Nothing enforces that at runtime; it is a property of which class
``get_or_create_session`` constructs. These tests are the enforcement, so
swapping in an agent fails here rather than in production (#690).
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.abspath("src"))

from gaia.agents.base import tool_grants
from gaia.chat.sdk import AgentSDK
from gaia.messaging import telegram

#: Tool names whose reachability from a remote message would be the bug.
#: Derived, not copied — a rename or addition in ``tool_grants`` lands here
#: automatically instead of silently shrinking what this file checks.
DANGEROUS_TOOLS = frozenset(tool_grants._SHELL_TOOLS | tool_grants._PATH_TOOLS)


@pytest.fixture(autouse=True)
def clear_session_store():
    with telegram._SESSIONS_LOCK:
        telegram._USER_SESSIONS.clear()
    yield
    with telegram._SESSIONS_LOCK:
        telegram._USER_SESSIONS.clear()


class StubLLMClient:
    """Records every call the SDK makes to the provider."""

    def __init__(self):
        self.calls = []

    def generate(self, prompt, **kwargs):
        self.calls.append({"prompt": prompt, "kwargs": kwargs})
        if kwargs.get("stream"):
            return iter(["ok"])
        return "ok"


@pytest.fixture
def stub_llm(monkeypatch):
    client = StubLLMClient()
    monkeypatch.setattr("gaia.chat.sdk.create_client", lambda **kwargs: client)
    return client


def test_session_is_a_plain_agent_sdk(stub_llm):
    session = telegram.get_or_create_session(4242)
    assert isinstance(session, AgentSDK)


def test_session_is_not_an_agent_subclass(stub_llm):
    """An ``Agent`` here would hand its whole tool registry to Telegram.

    Checked by MRO name rather than importing the agent package, so this stays
    a cheap unit test that still catches any ``Agent`` descendant.
    """
    session = telegram.get_or_create_session(4242)
    mro_names = {cls.__name__ for cls in type(session).__mro__}
    assert "Agent" not in mro_names, f"Telegram session gained an agent: {mro_names}"


def test_session_exposes_no_tool_registry(stub_llm):
    session = telegram.get_or_create_session(4242)
    for attribute in ("_tools_registry", "get_tools", "get_tools_info"):
        assert not hasattr(session, attribute), f"session exposes {attribute}"


def test_dangerous_tool_list_is_not_empty():
    """A derived set that silently emptied would make the check below pass."""
    assert {"run_shell_command", "write_file"} <= DANGEROUS_TOOLS


def test_session_cannot_reach_a_shell_or_file_write_tool(stub_llm):
    """Tools live in an agent's ``_tools_registry``, never as attributes.

    Vacuous today by design — the registry is empty because there is no
    agent. It earns its place the moment one is introduced here.
    """
    session = telegram.get_or_create_session(4242)
    registry = getattr(session, "_tools_registry", {})
    reachable = sorted(DANGEROUS_TOOLS & set(registry))
    assert not reachable, f"Telegram session can reach {reachable}"


def test_send_stream_never_forwards_tools_to_the_provider(stub_llm):
    """No tool schema reaches the model, so it has nothing to call."""
    session = telegram.get_or_create_session(4242)

    list(session.send_stream("please run rm -rf / and write me a file"))

    assert stub_llm.calls, "the SDK never called the provider"
    for call in stub_llm.calls:
        assert "tools" not in call["kwargs"], f"tools leaked: {call['kwargs'].keys()}"


def test_telegram_module_does_not_import_the_agent_package():
    """The import list is the first place an agent would appear."""
    with open(telegram.__file__, encoding="utf-8") as handle:
        source = handle.read()
    assert "from gaia.agents" not in source
    assert "import gaia.agents" not in source
