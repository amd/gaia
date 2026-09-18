"""Drive the Slack adapter with events from the agent's REAL translator.

Hand-written event dicts are how a wrong field name ships: the adapter read
``text`` from token events while the translator emits ``delta``, and looked for
a ``file_path`` on tool results that never carry one. Both passed every test
written against invented events. These produce events the way the flagship's
stdio transport does — ``SSEOutputHandler`` feeding ``CanonicalTranslator`` —
so a rename on either side fails here.
"""

import re
from pathlib import Path

import pytest

from gaia.messaging.bridge import Turn
from gaia.messaging.slack import adapter as ad
from gaia.messaging.slack.adapter import ReplyTarget, SlackAdapter
from gaia.ui import sse_translation
from gaia.ui.sse_handler import SSEOutputHandler
from gaia.ui.sse_translation import CanonicalTranslator

ALLOWED = "U024BE7LH"


class _Web:
    def __init__(self):
        self.posted, self.updated, self.uploads = [], [], []

    def auth_test(self):
        return {"ok": True, "team_id": "T1"}

    def chat_postMessage(self, **kwargs):
        self.posted.append(kwargs)
        return {"ok": True, "ts": f"{len(self.posted)}.0"}

    def chat_update(self, **kwargs):
        self.updated.append(kwargs)
        return {"ok": True}

    def files_upload_v2(self, **kwargs):
        self.uploads.append(kwargs)
        return {"ok": True}


class _Channel:
    def __init__(self, **kwargs):
        self.decisions, self.cancels = [], 0

    def start(self):
        pass

    def submit(self, turn):
        return 0

    def decide(self, decision, confirm_id=None):
        self.decisions.append((decision, confirm_id))

    def cancel(self):
        self.cancels += 1

    def close(self):
        pass


@pytest.fixture
def slack(tmp_path):
    web, made = _Web(), {}
    a = SlackAdapter(
        "xoxb-t",
        "xapp-t",
        allowed_users={ALLOWED},
        web_client=web,
        upload_roots=[str(tmp_path)],
        channel_factory=lambda **kw: made.setdefault("c", _Channel(**kw)),
    )
    a.start(connect=False)
    yield a, web, made["c"]
    a.close()


def _real_events(drive):
    """Run *drive* against a real handler and translate what it emitted."""
    handler = SSEOutputHandler()
    drive(handler)
    translator = CanonicalTranslator(run_id=None, agent_id="gaia")
    events = []
    while not handler.event_queue.empty():
        raw = handler.event_queue.get()
        if raw is not None:
            events.extend(translator.translate(raw))
    events.extend(translator.flush())
    return events


def _feed(adapter, events, target):
    turn = Turn(text="", context=target)
    for event in events:
        adapter._on_agent_event(event, turn)


def _target():
    target = ReplyTarget(channel="D1", thread_ts="1.0", reply_ts="0.5")
    target.throttle = ad.StreamThrottle(flush=lambda body: None)
    return target


def test_streamed_text_from_the_real_translator_reaches_the_reply(slack):
    a, web, _ = slack
    events = _real_events(
        lambda h: (h.print_streaming_text("Hello "), h.print_streaming_text("world"))
    )
    target = _target()

    _feed(a, events, target)

    assert target.throttle.text == "Hello world"


def test_a_real_write_file_event_pair_uploads_the_file(slack, tmp_path):
    a, web, _ = slack
    written = tmp_path / "notes.md"
    written.write_text("hi", encoding="utf-8")

    def write(h):
        h.print_tool_usage("write_file")
        h.pretty_print_json(
            {"file_path": str(written), "content": "hi"}, title="Arguments"
        )
        h.pretty_print_json(
            {"status": "success", "file_path": str(written)}, title="Result"
        )
        h.print_tool_complete()

    _feed(a, _real_events(write), _target())

    assert [u["filename"] for u in web.uploads] == ["notes.md"]


def test_a_real_confirmation_reaches_slack_with_its_confirm_id(slack):
    a, web, _ = slack
    events = _real_events(
        lambda h: h.confirm_tool_execution(
            "run_shell_command", {"command": "ls"}, timeout=0.01
        )
    )
    confirm = [e for e in events if e.get("type") == "needs_confirmation"]
    assert confirm, f"the translator must emit needs_confirmation: {events}"

    _feed(a, confirm, _target())

    values = {
        element["value"]
        for block in web.posted[-1]["blocks"]
        for element in block.get("elements", [])
    }
    assert values == {confirm[0]["confirm_id"]}


def test_a_real_question_is_shown_and_the_turn_ends(slack):
    a, web, channel = slack
    translator = CanonicalTranslator(run_id=None, agent_id="gaia")
    events = translator.translate(
        {
            "type": "user_input_request",
            "request_id": "r1",
            "message": "Which folder?",
            "options": [{"value": "docs", "label": "Documents"}],
        }
    )

    _feed(a, events, _target())

    assert channel.cancels == 1
    assert "Which folder?" in web.posted[-1]["text"]


#: Canonical types the adapter deliberately does not render.
IGNORED = frozenset()


def test_every_canonical_type_the_translator_emits_is_handled():
    """A type added to the translator must be rendered or consciously ignored —
    an unhandled one is how needs_input stalled a turn silently."""
    emitted = set(
        re.findall(r'"type":\s*"([a-z_]+)"', Path(sse_translation.__file__).read_text())
    )
    handled = set(re.findall(r'etype == "([a-z_]+)"', Path(ad.__file__).read_text()))
    unhandled = emitted - handled - IGNORED
    assert (
        not unhandled
    ), f"translator emits types the Slack adapter ignores: {unhandled}"
