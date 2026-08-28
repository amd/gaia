import os
import sys

import pytest

sys.path.insert(0, os.path.abspath("src"))

from gaia.messaging import telegram


@pytest.fixture(autouse=True)
def clear_session_store():
    with telegram._SESSIONS_LOCK:
        telegram._USER_SESSIONS.clear()
    yield
    with telegram._SESSIONS_LOCK:
        telegram._USER_SESSIONS.clear()


@pytest.fixture
def stub_agent_sdk(monkeypatch):
    class StubAgentSDK:
        def __init__(self, config):
            self.config = config
            self.history = []

    monkeypatch.setattr(telegram, "AgentSDK", StubAgentSDK)
    return StubAgentSDK


def test_sessions_reuse(stub_agent_sdk):
    s1 = telegram.get_or_create_session(1001)
    s2 = telegram.get_or_create_session(1001)
    assert s1 is s2


def test_sessions_are_isolated_by_user(stub_agent_sdk):
    first = telegram.get_or_create_session(1001)
    second = telegram.get_or_create_session(2002)

    first.history.append("first-user-message")

    assert first is not second
    assert second.history == []
