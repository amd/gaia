# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A plain ``Agent`` can change model mid-conversation and ask the user things.

``switch_model`` (all or nothing, conversation kept), the follow/rollback hooks
a subclass extends, the ``request_user_input`` mixin with its "an unanswered
question needs a person" rule, and ``on_cut`` for a history that outgrew the
window. No Lemonade or LLM: the chat client and the model list are stubs.
"""

import dataclasses
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from gaia.agents.base import history as history_module
from gaia.agents.base import model_switch
from gaia.agents.base.agent import Agent
from gaia.agents.base.call_risk import NEEDS_PERSON_ARG
from gaia.agents.base.tools import _TOOL_REGISTRY, tool
from gaia.agents.tools.user_input_tools import (
    NO_QUESTION_CHANNEL,
    UserInputToolsMixin,
)
from gaia.llm.lemonade_client import DEFAULT_MODEL_NAME

OTHER = "Qwen3.5-4B-GGUF"
CLOUD = "fireworks.accounts/fireworks/models/glm-4p6"


class _PlainAgent(Agent):
    def _get_system_prompt(self) -> str:
        return "test"

    def _register_tools(self) -> None:
        pass


class _AskingAgent(Agent, UserInputToolsMixin):
    def _get_system_prompt(self) -> str:
        return "test"

    def _register_tools(self) -> None:
        self.register_user_input_tools()

        @tool
        def write_note(text: str) -> dict:
            """Write a note.

            Args:
                text: The note.
            """
            return {"status": "success"}


@pytest.fixture(autouse=True)
def clean_registry():
    snapshot = dict(_TOOL_REGISTRY)
    yield
    _TOOL_REGISTRY.clear()
    _TOOL_REGISTRY.update(snapshot)


def _agent(cls=_PlainAgent, replies=()):
    with patch("gaia.agents.base.agent.AgentSDK"):
        agent = cls(silent_mode=True, skip_lemonade=True, model_id=DEFAULT_MODEL_NAME)
    agent.streaming = False
    pending = list(replies)
    chat = MagicMock()
    chat.get_stats.return_value = {}
    chat.config = SimpleNamespace(
        use_claude=False,
        model=DEFAULT_MODEL_NAME,
        claude_model="claude-sonnet-5",
        base_url="http://localhost:13305/api/v1",
        system_prompt=None,
    )
    chat.llm_client = SimpleNamespace(name="old")
    chat.send_messages.side_effect = lambda *_, **__: SimpleNamespace(
        text=pending.pop(0), stats={}
    )
    agent.chat = chat
    return agent


@pytest.fixture
def lemonade(monkeypatch):
    """Lemonade lists three models; every client built is recorded."""
    built = []
    listed = {name: {"id": name} for name in (DEFAULT_MODEL_NAME, OTHER, CLOUD)}

    def create(**kwargs):
        built.append(kwargs)
        return SimpleNamespace(name=kwargs["model"])

    monkeypatch.setattr(model_switch, "_lemonade_models", lambda base_url: listed)
    monkeypatch.setattr(model_switch, "create_client", create)
    monkeypatch.setattr("gaia.llm.local_only.cloud_blocked", lambda: False)
    return built


# ---------------------------------------------------------------------------
# switch_model
# ---------------------------------------------------------------------------


def test_a_switch_moves_the_client_the_model_and_the_prompt(lemonade):
    agent = _agent()
    agent.rebuild_system_prompt = MagicMock()

    shown = agent.switch_model(OTHER)

    assert shown == OTHER
    assert agent.model_id == OTHER
    assert agent.chat.config.model == OTHER
    assert agent.chat.llm_client.name == OTHER
    assert lemonade[0]["model"] == OTHER
    agent.rebuild_system_prompt.assert_called_once_with()


def test_the_conversation_survives_a_switch(lemonade):
    agent = _agent(replies=["Paris.", "About 2.1 million."])
    agent.start_session_history()
    agent.process_query("Capital of France?")

    agent.switch_model(CLOUD)
    agent.process_query("Its population?")

    assert agent.model_id == CLOUD
    assert "Capital of France?" in str(agent.chat.send_messages.call_args_list[1])


def test_a_switch_to_claude_builds_a_claude_client(lemonade):
    agent = _agent()

    assert agent.switch_model("claude-sonnet-5") == "Sonnet 5"

    assert lemonade[0]["use_claude"] is True
    assert agent._use_claude is True
    assert agent.model_id == "claude-sonnet-5"
    assert agent._inference_location().provider == "claude"


def test_an_unlisted_model_is_refused_and_nothing_moves(lemonade):
    agent = _agent()
    old = agent.chat.llm_client

    with pytest.raises(RuntimeError, match="Unknown Lemonade model 'nope-GGUF'"):
        agent.switch_model("nope-GGUF")

    assert agent.model_id == DEFAULT_MODEL_NAME
    assert agent.chat.llm_client is old
    assert lemonade == []


@pytest.mark.parametrize("target", [None, "", "   ", 7])
def test_a_missing_model_id_is_refused_by_name(lemonade, target):
    agent = _agent()

    with pytest.raises(ValueError, match="switch_model needs a model id"):
        agent.switch_model(target)

    assert lemonade == []


def test_a_cloud_model_is_refused_while_local_only_is_on(lemonade, monkeypatch):
    monkeypatch.setattr("gaia.llm.local_only.cloud_blocked", lambda: True)
    agent = _agent()

    with pytest.raises(RuntimeError, match="Local only is on"):
        agent.switch_model(CLOUD)

    assert agent.model_id == DEFAULT_MODEL_NAME


def test_a_switch_that_fails_half_way_is_rolled_back(lemonade):
    agent = _agent()
    old = agent.chat.llm_client
    agent.rebuild_system_prompt = MagicMock(side_effect=ValueError("bad prompt"))

    with pytest.raises(RuntimeError, match="rolled back to the previous model"):
        agent.switch_model(OTHER)

    assert agent.model_id == DEFAULT_MODEL_NAME
    assert agent.chat.config.model == DEFAULT_MODEL_NAME
    assert agent.chat.llm_client is old


class _TwoClientAgent(_PlainAgent):
    """Keeps a second, per-model setting the way a subclass would."""

    def follow_model_switch(self) -> None:
        self.summary_model = self.model_id

    def _snapshot_model_followers(self):
        return {
            **super()._snapshot_model_followers(),
            "summary_model": self.summary_model,
        }

    def _restore_model_followers(self, state):
        state = dict(state)
        self.summary_model = state.pop("summary_model")
        super()._restore_model_followers(state)


def test_a_subclass_follows_the_switch_through_its_hook(lemonade):
    agent = _agent(_TwoClientAgent)
    agent.summary_model = DEFAULT_MODEL_NAME

    agent.switch_model(OTHER)

    assert agent.summary_model == OTHER


def test_a_rollback_undoes_what_the_subclass_followed(lemonade):
    agent = _agent(_TwoClientAgent)
    agent.summary_model = DEFAULT_MODEL_NAME
    agent.rebuild_system_prompt = MagicMock(side_effect=ValueError("bad prompt"))

    with pytest.raises(RuntimeError):
        agent.switch_model(OTHER)

    assert agent.summary_model == DEFAULT_MODEL_NAME


# ---------------------------------------------------------------------------
# request_user_input
# ---------------------------------------------------------------------------


def _ask(**kwargs):
    return _TOOL_REGISTRY["request_user_input"]["function"](**kwargs)


class _Console(MagicMock):
    """A console whose user answers *reply* (``""`` is silence)."""


def _asking_agent(reply, answers_questions=True):
    agent = _agent(_AskingAgent)
    console = MagicMock()
    console.answers_questions = answers_questions
    console.request_user_input_blocking.return_value = reply
    agent.console = console
    return agent


def test_the_users_answer_comes_back_to_the_model():
    agent = _asking_agent("the blue one")

    assert _ask(message="Which one?", choices=["blue", "red"]) == "the blue one"
    assert agent._unanswered_question is None
    asked = agent.console.request_user_input_blocking.call_args.kwargs
    assert asked["message"] == "Which one?"
    assert asked["choices"] == ["blue", "red"]


def test_silence_is_never_returned_as_the_default_answer():
    agent = _asking_agent("")

    result = _ask(message="Delete them?", default_if_no_response="yes")

    assert result.startswith("__NO_RESPONSE__")
    assert result != "yes"
    assert agent._unanswered_question == "Delete them?"


def test_a_console_that_cannot_answer_sends_the_question_to_the_reply():
    agent = _asking_agent("ignored", answers_questions=False)

    assert _ask(message="Which repo?") == NO_QUESTION_CHANNEL
    agent.console.request_user_input_blocking.assert_not_called()


def test_a_change_after_an_unanswered_question_needs_a_person():
    agent = _asking_agent("")
    _ask(message="Overwrite it?")

    shown = agent.confirmation_args_for_call("write_file", {"file_path": "a.txt"})
    read = agent.confirmation_args_for_call(
        "run_shell_command", {"command": "git status"}
    )

    assert shown[NEEDS_PERSON_ARG] is True
    assert NEEDS_PERSON_ARG not in read


def test_an_answered_question_marks_nothing():
    agent = _asking_agent("yes")
    _ask(message="Overwrite it?")

    shown = agent.confirmation_args_for_call("write_file", {"file_path": "a.txt"})

    assert NEEDS_PERSON_ARG not in shown


def test_the_next_turn_starts_with_no_unanswered_question():
    agent = _asking_agent("")
    _ask(message="Overwrite it?")
    agent.chat.send_messages.side_effect = lambda *_, **__: SimpleNamespace(
        text="Done.", stats={}
    )

    agent.process_query("next request")

    assert agent._unanswered_question is None


def test_the_mixin_is_listed_for_composing_by_name():
    from gaia.agents.registry import KNOWN_TOOLS

    assert KNOWN_TOOLS["user_input"] == (
        "gaia.agents.tools.user_input_tools",
        "UserInputToolsMixin",
    )


# ---------------------------------------------------------------------------
# on_cut
# ---------------------------------------------------------------------------


def test_on_cut_reports_turns_that_left_the_models_view(monkeypatch):
    agent = _agent(replies=["one " * 40, "two " * 40, "three " * 40])
    cuts = []
    agent.start_session_history(on_cut=cuts.append)
    monkeypatch.setattr(history_module, "history_budget", lambda agent, query: 120)

    for question in ("first?", "second?", "third?"):
        agent.process_query(question)

    assert cuts, "a history over budget must report what it hid"
    assert cuts[-1].turns >= 1
    assert len(agent.conversation_history.turns(agent)) == 3  # nothing is deleted


def test_no_on_cut_is_fine():
    agent = _agent(replies=["one", "two"])
    agent.start_session_history()

    agent.process_query("first?")
    agent.process_query("second?")

    assert len(agent.conversation_history.turns(agent)) == 2


# ---------------------------------------------------------------------------
# A subclass's own ``config`` is never written by a switch
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class _Settings:
    model_id: str = "mine"
    use_claude: bool = False
    claude_model: str = "mine"


def test_a_switch_leaves_a_subclass_config_alone(lemonade):
    agent = _agent()
    agent.config = _Settings()

    agent.switch_model(OTHER)

    assert agent.model_id == OTHER
    assert agent.config == _Settings()


def test_a_rollback_with_a_frozen_config_still_raises_the_documented_error(lemonade):
    agent = _agent()
    agent.config = _Settings()
    agent.rebuild_system_prompt = MagicMock(side_effect=ValueError("bad prompt"))

    with pytest.raises(RuntimeError, match="rolled back to the previous model"):
        agent.switch_model(OTHER)

    assert agent.model_id == DEFAULT_MODEL_NAME


def test_a_failed_claude_switch_puts_the_claude_model_back(lemonade):
    agent = _agent()
    agent._claude_model = "claude-haiku-4-5"
    agent.rebuild_system_prompt = MagicMock(side_effect=ValueError("bad prompt"))

    with pytest.raises(RuntimeError):
        agent.switch_model("claude-opus-5")

    assert agent._claude_model == "claude-haiku-4-5"
    assert agent._use_claude is False


# ---------------------------------------------------------------------------
# request_user_input where nobody can type
# ---------------------------------------------------------------------------


def test_a_console_with_no_question_hook_and_no_terminal_does_not_wait(monkeypatch):
    agent = _agent(_AskingAgent)
    agent.console = SimpleNamespace()  # no request_user_input_blocking
    monkeypatch.setattr(
        "gaia.agents.tools.user_input_tools.stdin_is_interactive", lambda: False
    )
    monkeypatch.setattr(
        "builtins.input", lambda *_: pytest.fail("input() would hang on a pipe")
    )

    assert _ask(message="Which repo?") == NO_QUESTION_CHANNEL
    assert agent._unanswered_question == "Which repo?"


def test_at_a_terminal_the_answer_is_read_from_it(monkeypatch):
    agent = _agent(_AskingAgent)
    agent.console = SimpleNamespace()
    monkeypatch.setattr(
        "gaia.agents.tools.user_input_tools.stdin_is_interactive", lambda: True
    )
    monkeypatch.setattr("builtins.input", lambda *_: " the red one ")

    assert _ask(message="Which one?") == "the red one"


def test_the_tool_is_grouped_under_its_mixin_like_every_other():
    from gaia.agents.base.tools import tool_category

    _agent(_AskingAgent)

    assert (
        tool_category("request_user_input", _TOOL_REGISTRY["request_user_input"])
        == "user_input"
    )
