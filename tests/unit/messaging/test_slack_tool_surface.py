"""Pin the security posture a Slack message runs under.

Slack differs from Telegram on the axis that matters. Telegram's adapter reaches
a tool-less ``AgentSDK``, so no shell tool exists to be reached and
``test_telegram_tool_surface.py`` pins exactly that. This bridge deliberately
drives the full flagship agent — reading your files and running commands is the
feature — so the safety argument is not "the tools are absent" but "the
dangerous ones are gated, and nothing in this path can ungate them".

These tests are that argument, written down. Each one fails if a change makes a
dangerous tool reachable without a decision the user consciously made.
"""

import inspect

from gaia.agents.base import agent as base_agent
from gaia.messaging import bridge
from gaia.messaging.slack import adapter as ad

#: Tool names whose unattended execution from a remote message would be the bug.
#: Read from the base agent rather than copied, so a tool added to the gate
#: lands here automatically instead of silently shrinking what this file checks.
GATED_TOOLS = frozenset(base_agent.TOOLS_REQUIRING_CONFIRMATION)


def test_the_dangerous_tools_are_gated_by_the_base_agent():
    """The whole posture rests on this set being non-empty and containing the
    obvious offenders. If the base agent stops gating shell, every other test
    in this file is testing nothing."""
    assert {"run_shell_command", "write_file", "edit_file"} <= GATED_TOOLS


def test_a_gated_tool_pauses_the_turn_rather_than_running():
    """``needs_confirmation`` is the event that makes approval possible at all."""
    assert bridge.NEEDS_CONFIRMATION == "needs_confirmation"
    assert bridge.NEEDS_CONFIRMATION not in bridge.TERMINAL_EVENTS


def test_the_bridge_has_no_bypass_control():
    """``gaia_agent.stdio`` accepts a ``bypass`` verb that disarms the gate for
    the whole session. A remote surface must not be able to send it: an
    allowlisted user is trusted to approve one tool, not to switch the gate off
    for every later one."""
    source = inspect.getsource(bridge)
    # The module documents the absence; what must not appear is a control
    # message that actually carries the verb.
    assert '"bypass"' not in source
    assert "CONTROL_BYPASS" not in source


def test_the_adapter_cannot_build_a_raw_control_message():
    """The adapter's only channel to the agent is ``decide()``, which accepts
    the three decisions and nothing else.

    Checked structurally rather than by searching for the word: the module
    docstring explains that bypass is unreachable, and a text search would
    either trip over that sentence or be defeated by rewording it. What matters
    is that no code here builds a control dict of its own — if it did, it could
    put any verb in it, including the one that disarms the gate.
    """
    import ast

    tree = ast.parse(inspect.getsource(ad))
    referenced = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)} | {
        node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
    }
    assert "CONTROL_KEY" not in referenced
    assert "CONTROL_TOOL_DECISION" not in referenced
    assert "CONTROL_BYPASS" not in referenced


def test_the_adapter_reaches_the_agent_only_through_decide():
    """Every write to the agent goes through the one method that validates."""
    import ast

    tree = ast.parse(inspect.getsource(ad))
    channel_calls = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Attribute)
        and node.func.value.attr == "_channel"
    }
    assert channel_calls <= {"decide", "start", "close", "submit"}, channel_calls


def test_the_adapter_only_ever_sends_the_three_real_decisions():
    """A fourth value would be downgraded to a deny by the agent, which looks
    to the user like their approval was ignored."""
    assert set(ad._ACTION_DECISIONS.values()) == bridge.VALID_DECISIONS


def test_every_button_maps_to_a_decision_the_agent_accepts():
    for action_id, decision in ad._ACTION_DECISIONS.items():
        assert decision in bridge.VALID_DECISIONS, action_id


def test_the_manifest_subscribes_to_no_channel_events():
    """A channel subscription makes the agent addressable by everyone in the
    channel, which the allowlist alone would then have to carry."""
    from gaia.messaging.slack import manifest

    events = manifest.build_manifest()["settings"]["event_subscriptions"]["bot_events"]
    assert events == ["message.im"]
    assert not any("channel" in event for event in events)


def test_both_message_and_button_paths_check_the_allowlist():
    """Slack routes events and interactions to different handlers. Guarding only
    one is the trap Telegram's ``require_allowed`` exists for — a command update
    never reaches the message handler."""
    for handler in (ad.SlackAdapter._handle_event, ad.SlackAdapter._handle_interactive):
        source = inspect.getsource(handler)
        assert "_allowed(" in source, f"{handler.__name__} does not check the allowlist"
        assert (
            "_same_workspace(" in source
        ), f"{handler.__name__} does not pin the workspace"


def test_an_upload_root_outside_the_agents_reach_is_still_enforced(tmp_path):
    """Approving a write is not approving a transfer off the machine."""
    source = inspect.getsource(ad.SlackAdapter._maybe_upload)
    assert "upload_roots" in source
    assert "resolve()" in source, "the path must be resolved before it is compared"


def test_the_refusal_message_never_names_who_is_allowed():
    """A stranger who found the bot learns only that they are not allowed."""
    assert "U0" not in ad.UNAUTHORIZED_REPLY
    assert "allowlist" not in ad.UNAUTHORIZED_REPLY.lower()
