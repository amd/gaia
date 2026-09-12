"""The Slack adapter's guard rails and rendering.

This bridge drives the REAL flagship agent — shell, file writes, the lot — so
the authorization tests here are the ones that decide whether the feature is
shippable. Unlike Telegram's adapter, which reaches a tool-less ``AgentSDK``,
nothing structural stops a message from reaching a dangerous tool: only the
allowlist, the DM rule, the workspace pin, and the confirmation gate do.
"""

import pytest

from gaia.messaging.bridge import DECISION_ALLOW, DECISION_ALWAYS, DECISION_DENY
from gaia.messaging.slack import adapter as ad
from gaia.messaging.slack.adapter import (
    ReplyTarget,
    SlackAdapter,
    SlackAllowlistError,
    confirmation_blocks,
)

ALLOWED = "U024BE7LH"
STRANGER = "U999NOPE"
TEAM = "T01TEAM"


class FakeWeb:
    """Records every Slack API call instead of making one."""

    def __init__(self, team_id=TEAM):
        self.posted = []
        self.updated = []
        self.uploads = []
        self._team_id = team_id
        self._ts = 0

    def auth_test(self):
        return {
            "ok": True,
            "team_id": self._team_id,
            "team": "Acme",
            "user": "gaia",
            "user_id": "U0BOT",
        }

    def chat_postMessage(self, **kwargs):
        self._ts += 1
        ts = f"{self._ts}.000"
        self.posted.append({**kwargs, "ts": ts})
        return {"ok": True, "ts": ts}

    def chat_update(self, **kwargs):
        self.updated.append(kwargs)
        return {"ok": True}

    def files_upload_v2(self, **kwargs):
        self.uploads.append(kwargs)
        return {"ok": True}


class FakeChannel:
    """Stands in for AgentChannel — records turns and decisions."""

    def __init__(self, **kwargs):
        self.on_event = kwargs.get("on_event")
        self.on_busy = kwargs.get("on_busy")
        self.turns = []
        self.decisions = []
        self.started = False
        self.closed = False

    def start(self):
        self.started = True

    def submit(self, turn):
        self.turns.append(turn)
        return 0

    def decide(self, decision, confirm_id=None):
        self.decisions.append((decision, confirm_id))

    def close(self):
        self.closed = True


@pytest.fixture
def adapter():
    """A started adapter wired to fakes, with its channel reachable."""
    web = FakeWeb()
    made = {}

    def factory(**kwargs):
        made["channel"] = FakeChannel(**kwargs)
        return made["channel"]

    a = SlackAdapter(
        bot_token="xoxb-t",
        app_token="xapp-t",
        allowed_users={ALLOWED},
        web_client=web,
        channel_factory=factory,
    )
    a.start(connect=False)
    return a, web, made["channel"]


def dm_event(user=ALLOWED, text="hello", team=TEAM, **extra):
    return {
        "team_id": team,
        "event": {
            "type": "message",
            "channel_type": "im",
            "channel": "D1",
            "user": user,
            "text": text,
            "ts": "111.000",
            **extra,
        },
    }


# ----------------------------------------------------------------------
# Construction guards
# ----------------------------------------------------------------------


@pytest.mark.parametrize("empty", [set(), None, [], frozenset()])
def test_empty_allowlist_is_refused_at_construction(empty):
    """An adapter that should serve nobody must never exist."""
    with pytest.raises(SlackAllowlistError) as excinfo:
        SlackAdapter("xoxb-t", "xapp-t", allowed_users=empty)
    assert "--allowed-users" in str(excinfo.value)


def test_omitted_allowlist_is_refused():
    with pytest.raises(SlackAllowlistError):
        SlackAdapter("xoxb-t", "xapp-t")


def test_refusal_is_a_valueerror_for_callers_catching_the_base():
    with pytest.raises(ValueError):
        SlackAdapter("xoxb-t", "xapp-t")


def test_a_string_allowlist_is_rejected_rather_than_split_into_characters():
    """'U024BE7LH' would become {'U','0','2',...} and deny everyone."""
    with pytest.raises(TypeError) as excinfo:
        SlackAdapter("xoxb-t", "xapp-t", allowed_users=ALLOWED)
    assert "U024BE7LH" in str(excinfo.value)


def test_the_refusal_explains_why_a_workspace_is_not_an_allowlist():
    with pytest.raises(SlackAllowlistError) as excinfo:
        SlackAdapter("xoxb-t", "xapp-t", allowed_users=set())
    message = str(excinfo.value)
    assert "read your files" in message
    assert "Everyone in the workspace" in message


# ----------------------------------------------------------------------
# Authorization on the message path
# ----------------------------------------------------------------------


def test_an_allowlisted_member_reaches_the_agent(adapter):
    a, web, channel = adapter
    a._handle_event(dm_event())
    assert [t.text for t in channel.turns] == ["hello"]


def test_a_stranger_never_reaches_the_agent(adapter):
    a, web, channel = adapter
    a._handle_event(dm_event(user=STRANGER))
    assert channel.turns == []
    assert any(ad.UNAUTHORIZED_REPLY in p["text"] for p in web.posted)


def test_a_message_with_no_user_is_refused(adapter):
    a, web, channel = adapter
    a._handle_event(dm_event(user=None))
    assert channel.turns == []


def test_a_message_from_another_workspace_is_dropped(adapter):
    """A token installed elsewhere must not be served, even for an id that
    happens to match the allowlist."""
    a, web, channel = adapter
    a._handle_event(dm_event(team="T_OTHER"))
    assert channel.turns == []
    assert web.posted == [], "a foreign workspace gets no reply at all"


def test_a_channel_message_is_ignored(adapter):
    """Anyone in a channel can address a bot in it."""
    a, web, channel = adapter
    event = dm_event()
    event["event"]["channel_type"] = "channel"
    a._handle_event(event)
    assert channel.turns == []


@pytest.mark.parametrize("channel_type", ["group", "mpim", "channel", None])
def test_only_direct_messages_are_served(adapter, channel_type):
    a, web, channel = adapter
    event = dm_event()
    event["event"]["channel_type"] = channel_type
    a._handle_event(event)
    assert channel.turns == []


def test_the_bots_own_message_does_not_start_a_turn(adapter):
    """Answering your own reply is an infinite loop with an LLM at the bottom."""
    a, web, channel = adapter
    a._handle_event(dm_event(bot_id="B1"))
    assert channel.turns == []


@pytest.mark.parametrize(
    "subtype", ["bot_message", "message_changed", "message_deleted"]
)
def test_edits_and_deletions_do_not_start_a_turn(adapter, subtype):
    a, web, channel = adapter
    a._handle_event(dm_event(subtype=subtype))
    assert channel.turns == []


def test_an_empty_message_asks_for_a_question_instead_of_running_a_turn(adapter):
    a, web, channel = adapter
    a._handle_event(dm_event(text="   "))
    assert channel.turns == []
    assert any("empty message" in p["text"] for p in web.posted)


def test_allowed_predicate_admits_nobody_when_emptied_after_construction(adapter):
    """Defence in depth behind the constructor's refusal."""
    a, _, _ = adapter
    a.allowed_users.clear()
    assert a._allowed(ALLOWED) is False


# ----------------------------------------------------------------------
# Authorization on the button path
# ----------------------------------------------------------------------


def _click(action_id=ad.ACTION_ALLOW, user=ALLOWED, team=TEAM, value="c1"):
    return {
        "user": {"id": user},
        "team": {"id": team},
        "actions": [{"action_id": action_id, "value": value}],
    }


def test_an_allowlisted_member_can_approve(adapter):
    a, web, channel = adapter
    a._handle_interactive(_click())
    assert channel.decisions == [(DECISION_ALLOW, "c1")]


def test_a_stranger_cannot_approve_a_tool(adapter):
    """The buttons are visible to anyone who can see the conversation; a
    workspace admin reading it must not be able to run a command on the
    owner's machine."""
    a, web, channel = adapter
    a._handle_interactive(_click(user=STRANGER))
    assert channel.decisions == []


def test_a_click_from_another_workspace_is_dropped(adapter):
    a, web, channel = adapter
    a._handle_interactive(_click(team="T_OTHER"))
    assert channel.decisions == []


@pytest.mark.parametrize(
    "action_id, expected",
    [
        (ad.ACTION_ALLOW, DECISION_ALLOW),
        (ad.ACTION_ALWAYS, DECISION_ALWAYS),
        (ad.ACTION_DENY, DECISION_DENY),
    ],
)
def test_each_button_maps_to_its_decision(adapter, action_id, expected):
    a, web, channel = adapter
    a._handle_interactive(_click(action_id=action_id))
    assert channel.decisions == [(expected, "c1")]


def test_an_unrecognised_action_is_ignored(adapter):
    """A button from some other app's message must not answer our prompt."""
    a, web, channel = adapter
    a._handle_interactive(_click(action_id="someone_elses_button"))
    assert channel.decisions == []


def test_answering_replaces_the_buttons_with_the_decision(adapter):
    """Live buttons after the turn moved on look like they still do something."""
    a, web, channel = adapter
    target = ReplyTarget(channel="D1", thread_ts="111.000")
    a._ask_confirmation(
        target,
        {"action": "run a shell command", "summary": "ls", "confirm_id": "c1"},
    )
    a._handle_interactive(_click(value="c1"))
    assert web.updated, "the confirmation message must be edited"
    assert "Allowed" in web.updated[-1]["text"]
    assert web.updated[-1]["blocks"] == []


# ----------------------------------------------------------------------
# The confirmation prompt
# ----------------------------------------------------------------------


def test_confirmation_buttons_carry_the_confirm_id(adapter):
    """Without it a late click resolves whatever prompt replaced this one."""
    a, web, channel = adapter
    target = ReplyTarget(channel="D1", thread_ts="111.000")
    a._ask_confirmation(
        target, {"action": "write a file", "summary": "/tmp/x", "confirm_id": "c7"}
    )
    blocks = web.posted[-1]["blocks"]
    values = {e["value"] for e in blocks[1]["elements"]}
    assert values == {"c7"}


def test_always_allow_is_offered_only_with_a_scope():
    """Offering it without one grants far more than the user believes."""
    with_scope = confirmation_blocks("run", "ls -la", "ls")
    without = confirmation_blocks("run", "ls -la", "")
    assert ad.ACTION_ALWAYS in {e["action_id"] for e in with_scope[1]["elements"]}
    assert ad.ACTION_ALWAYS not in {e["action_id"] for e in without[1]["elements"]}


def test_deny_gated_tools_refuses_without_ever_showing_a_button():
    """The read-only posture: a gated tool is denied, not offered."""
    web = FakeWeb()
    made = {}
    a = SlackAdapter(
        "xoxb-t",
        "xapp-t",
        allowed_users={ALLOWED},
        web_client=web,
        deny_gated_tools=True,
        channel_factory=lambda **kw: made.setdefault("c", FakeChannel(**kw)),
    )
    a.start(connect=False)
    target = ReplyTarget(channel="D1", thread_ts="111.000")
    a._ask_confirmation(target, {"action": "run a shell command", "confirm_id": "c1"})
    assert made["c"].decisions == [(DECISION_DENY, "c1")]
    assert all("blocks" not in p for p in web.posted)
    assert any("read-only" in p["text"] for p in web.posted)


# ----------------------------------------------------------------------
# Rendering
# ----------------------------------------------------------------------


def _target_for(adapter_tuple):
    a, web, channel = adapter_tuple
    a._handle_event(dm_event())
    return channel.turns[-1]


def test_the_final_answer_replaces_the_streamed_text(adapter):
    a, web, channel = adapter
    turn = _target_for(adapter)
    channel.on_event({"type": "token", "text": "partial"}, turn)
    channel.on_event({"type": "final", "answer": "the whole answer"}, turn)
    assert any(u["text"] == "the whole answer" for u in web.updated)


def test_an_error_is_rendered_as_a_warning(adapter):
    a, web, channel = adapter
    turn = _target_for(adapter)
    channel.on_event({"type": "error", "detail": "Lemonade is not running"}, turn)
    assert any("Lemonade is not running" in u["text"] for u in web.updated)


def test_a_status_does_not_overwrite_text_that_already_streamed(adapter):
    """Replacing a partial answer with 'Working…' is a visible regression."""
    a, web, channel = adapter
    turn = _target_for(adapter)
    channel.on_event({"type": "token", "text": "some answer"}, turn)
    before = len(web.updated)
    channel.on_event({"type": "status", "message": "Thinking harder"}, turn)
    assert len(web.updated) == before


def test_the_tools_used_are_listed_after_the_answer(adapter):
    a, web, channel = adapter
    turn = _target_for(adapter)
    channel.on_event({"type": "tool_call", "tool": "query_documents"}, turn)
    channel.on_event({"type": "final", "answer": "done"}, turn)
    assert any("query_documents" in p["text"] for p in web.posted)


def test_a_long_answer_is_truncated_rather_than_rejected(adapter):
    """Past 4000 characters chat.update rejects the edit and the reply vanishes."""
    a, web, channel = adapter
    turn = _target_for(adapter)
    channel.on_event({"type": "final", "answer": "x" * 9000}, turn)
    body = web.updated[-1]["text"]
    assert len(body) <= ad.SLACK_MESSAGE_LIMIT + 60
    assert "truncated" in body


def test_a_queued_message_tells_the_sender_it_is_waiting(adapter):
    """Silence while a previous turn runs reads as a broken bot."""
    a, web, channel = adapter
    turn = _target_for(adapter)
    a._on_busy(turn, 2)
    assert any("Queued" in u["text"] for u in web.updated)


# ----------------------------------------------------------------------
# Uploading files back
# ----------------------------------------------------------------------


def test_a_file_the_agent_wrote_under_an_upload_root_is_sent_back(tmp_path):
    web = FakeWeb()
    made = {}
    a = SlackAdapter(
        "xoxb-t",
        "xapp-t",
        allowed_users={ALLOWED},
        web_client=web,
        upload_roots=[str(tmp_path)],
        channel_factory=lambda **kw: made.setdefault("c", FakeChannel(**kw)),
    )
    a.start(connect=False)
    written = tmp_path / "summary.md"
    written.write_text("hello", encoding="utf-8")
    target = ReplyTarget(channel="D1", thread_ts="1.0")
    a._maybe_upload(
        target, {"type": "tool_result", "data": {"file_path": str(written)}}
    )
    assert [u["filename"] for u in web.uploads] == ["summary.md"]


def test_a_file_outside_the_upload_roots_is_refused(tmp_path):
    """The write was approved; shipping it off the machine was not."""
    web = FakeWeb()
    a = SlackAdapter(
        "xoxb-t",
        "xapp-t",
        allowed_users={ALLOWED},
        web_client=web,
        upload_roots=[str(tmp_path / "allowed")],
        channel_factory=lambda **kw: FakeChannel(**kw),
    )
    a.start(connect=False)
    (tmp_path / "allowed").mkdir()
    secret = tmp_path / "elsewhere.txt"
    secret.write_text("private", encoding="utf-8")
    a._maybe_upload(
        ReplyTarget(channel="D1", thread_ts="1.0"),
        {"data": {"file_path": str(secret)}},
    )
    assert web.uploads == []


def test_a_path_escaping_an_upload_root_with_dotdot_is_refused(tmp_path):
    """The check resolves the path first, so ../ cannot walk out of a root."""
    web = FakeWeb()
    root = tmp_path / "allowed"
    root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("private", encoding="utf-8")
    a = SlackAdapter(
        "xoxb-t",
        "xapp-t",
        allowed_users={ALLOWED},
        web_client=web,
        upload_roots=[str(root)],
        channel_factory=lambda **kw: FakeChannel(**kw),
    )
    a.start(connect=False)
    a._maybe_upload(
        ReplyTarget(channel="D1", thread_ts="1.0"),
        {"data": {"file_path": str(root / ".." / "outside.txt")}},
    )
    assert web.uploads == []


def test_the_same_file_is_not_uploaded_twice_in_one_turn(tmp_path):
    web = FakeWeb()
    a = SlackAdapter(
        "xoxb-t",
        "xapp-t",
        allowed_users={ALLOWED},
        web_client=web,
        upload_roots=[str(tmp_path)],
        channel_factory=lambda **kw: FakeChannel(**kw),
    )
    a.start(connect=False)
    written = tmp_path / "a.txt"
    written.write_text("x", encoding="utf-8")
    target = ReplyTarget(channel="D1", thread_ts="1.0")
    for _ in range(3):
        a._maybe_upload(target, {"data": {"file_path": str(written)}})
    assert len(web.uploads) == 1


def test_an_oversized_file_is_skipped(tmp_path, monkeypatch):
    web = FakeWeb()
    a = SlackAdapter(
        "xoxb-t",
        "xapp-t",
        allowed_users={ALLOWED},
        web_client=web,
        upload_roots=[str(tmp_path)],
        channel_factory=lambda **kw: FakeChannel(**kw),
    )
    a.start(connect=False)
    written = tmp_path / "big.bin"
    written.write_bytes(b"x" * 128)
    monkeypatch.setattr(ad, "MAX_UPLOAD_BYTES", 16)
    a._maybe_upload(
        ReplyTarget(channel="D1", thread_ts="1.0"),
        {"data": {"file_path": str(written)}},
    )
    assert web.uploads == []


def test_a_tool_result_without_a_file_path_uploads_nothing(adapter):
    a, web, channel = adapter
    a._maybe_upload(
        ReplyTarget(channel="D1", thread_ts="1.0"),
        {"type": "tool_result", "data": {"status": "success"}},
    )
    assert web.uploads == []


def test_a_missing_file_is_not_uploaded(tmp_path):
    web = FakeWeb()
    a = SlackAdapter(
        "xoxb-t",
        "xapp-t",
        allowed_users={ALLOWED},
        web_client=web,
        upload_roots=[str(tmp_path)],
        channel_factory=lambda **kw: FakeChannel(**kw),
    )
    a.start(connect=False)
    a._maybe_upload(
        ReplyTarget(channel="D1", thread_ts="1.0"),
        {"data": {"file_path": str(tmp_path / "gone.txt")}},
    )
    assert web.uploads == []


# ----------------------------------------------------------------------
# Workspace pinning
# ----------------------------------------------------------------------


def test_verify_pins_the_workspace_from_auth_test():
    web = FakeWeb(team_id="T_REAL")
    a = SlackAdapter("xoxb-t", "xapp-t", allowed_users={ALLOWED}, web_client=web)
    assert a.team_id is None
    a.verify()
    assert a.team_id == "T_REAL"


def test_verify_refuses_a_token_slack_rejects():
    class Rejecting(FakeWeb):
        def auth_test(self):
            return {"ok": False, "error": "invalid_auth"}

    a = SlackAdapter(
        "xoxb-bad", "xapp-t", allowed_users={ALLOWED}, web_client=Rejecting()
    )
    with pytest.raises(RuntimeError) as excinfo:
        a.verify()
    assert "invalid_auth" in str(excinfo.value)
    assert "gaia slack setup" in str(excinfo.value), "name the remedy"


def test_an_explicit_team_id_is_not_overwritten_by_verify():
    web = FakeWeb(team_id="T_FROM_TOKEN")
    a = SlackAdapter(
        "xoxb-t",
        "xapp-t",
        allowed_users={ALLOWED},
        team_id="T_PINNED",
        web_client=web,
    )
    a.verify()
    assert a.team_id == "T_PINNED"


def test_using_the_adapter_before_start_names_the_fix():
    """Under ``python -O`` an assert would vanish and this would surface as
    'NoneType has no attribute submit' from inside a Slack event handler."""
    a = SlackAdapter("xoxb-t", "xapp-t", allowed_users={ALLOWED}, web_client=FakeWeb())
    with pytest.raises(RuntimeError) as excinfo:
        a._agent()
    assert "gaia slack start" in str(excinfo.value)
