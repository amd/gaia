# SPDX-License-Identifier: MIT
"""A shared Lemonade provider must not return another call's reasoning."""

from threading import Event, Thread

from gaia.llm.providers.lemonade import LemonadeProvider


class _InterleavingMessage(dict):
    """Pause call A after it saves its reasoning and before it builds an answer."""

    def __init__(self, reasoning, ready, continue_call):
        super().__init__(content="", reasoning_content=reasoning)
        self._ready = ready
        self._continue_call = continue_call

    def get(self, key, default=None):
        if key == "content":
            self._ready.set()
            assert self._continue_call.wait(timeout=2), "second call did not finish"
        return super().get(key, default)


class _RaceBackend:
    def __init__(self, ready, continue_call):
        self._ready = ready
        self._continue_call = continue_call

    @staticmethod
    def cloud_model_provider(_model):
        return None

    def chat_completions(self, *, messages, **_kwargs):
        request = messages[-1]["content"]
        if request == "A":
            return {
                "choices": [
                    {
                        "message": _InterleavingMessage(
                            "reasoning from A", self._ready, self._continue_call
                        ),
                        "finish_reason": "stop",
                    }
                ]
            }
        assert request == "B"
        return {
            "choices": [
                {
                    "message": {"content": "answer from B"},
                    "finish_reason": "stop",
                }
            ]
        }


def test_shared_provider_keeps_reasoning_fallback_local_to_its_call():
    """Call B may reset shared diagnostics without changing call A's answer."""
    ready = Event()
    continue_call = Event()
    provider = LemonadeProvider(model="Gemma-4-E4B-it-GGUF")
    provider._backend = _RaceBackend(ready, continue_call)
    answers = {}

    def call_a():
        answers["A"] = provider.chat([{"role": "user", "content": "A"}])

    thread = Thread(target=call_a)
    thread.start()
    assert ready.wait(timeout=2), "call A did not reach its fallback"

    answers["B"] = provider.chat([{"role": "user", "content": "B"}])
    continue_call.set()
    thread.join(timeout=2)

    assert not thread.is_alive()
    assert answers == {"A": "reasoning from A", "B": "answer from B"}


class _ScriptedBackend:
    """Answer each request from a script keyed by its last user message."""

    def __init__(self, script):
        self._script = script

    @staticmethod
    def cloud_model_provider(_model):
        return None

    def chat_completions(self, *, messages, **_kwargs):
        message, finish_reason = self._script[messages[-1]["content"]]
        return {"choices": [{"message": message, "finish_reason": finish_reason}]}


def test_both_racing_calls_get_their_own_reasoning_fallback():
    """Two fallbacks interleaved on one provider never swap answers."""
    ready = Event()
    continue_call = Event()
    provider = LemonadeProvider(model="Gemma-4-E4B-it-GGUF")
    race = _RaceBackend(ready, continue_call)
    provider._backend = race
    answers = {}

    def call_a():
        answers["A"] = provider.chat([{"role": "user", "content": "A"}])

    thread = Thread(target=call_a)
    thread.start()
    assert ready.wait(timeout=2), "call A did not reach its fallback"

    provider._backend = _ScriptedBackend(
        {"B": ({"content": "", "reasoning_content": "reasoning from B"}, "stop")}
    )
    answers["B"] = provider.chat([{"role": "user", "content": "B"}])
    continue_call.set()
    thread.join(timeout=2)

    assert not thread.is_alive()
    assert answers == {"A": "reasoning from A", "B": "reasoning from B"}


def test_truncated_reply_never_borrows_an_earlier_calls_reasoning():
    """A length cut-off stays empty even right after a reasoning fallback."""
    provider = LemonadeProvider(model="Gemma-4-E4B-it-GGUF")
    provider._backend = _ScriptedBackend(
        {
            "first": ({"content": "", "reasoning_content": "earlier"}, "stop"),
            "cut": ({"content": ""}, "length"),
            "cut-thinking": (
                {"content": "", "reasoning_content": "half a thought"},
                "length",
            ),
            "empty": ({"content": ""}, "stop"),
        }
    )

    assert provider.chat([{"role": "user", "content": "first"}]) == "earlier"
    assert provider.get_last_reasoning() is None
    assert provider.chat([{"role": "user", "content": "cut"}]) == ""
    assert provider.chat([{"role": "user", "content": "cut-thinking"}]) == ""
    assert provider.get_last_reasoning() == "half a thought"
    assert provider.chat([{"role": "user", "content": "empty"}]) == ""
    assert provider.get_last_reasoning() is None
