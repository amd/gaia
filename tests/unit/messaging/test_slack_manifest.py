"""The Slack app manifest and the tokens pasted back.

The manifest is what the user is asked to approve, so its contents are a
security surface, not configuration trivia: an extra scope in here is an extra
thing GAIA can do in their workspace.
"""

import json
from urllib.parse import parse_qs, urlparse

import pytest

from gaia.messaging.slack import manifest as m


def _manifest_from_url(url):
    return json.loads(parse_qs(urlparse(url).query)["manifest_json"][0])


# ----------------------------------------------------------------------
# Manifest contents
# ----------------------------------------------------------------------


def test_socket_mode_is_on():
    """Without it the app needs a public URL, which a local agent cannot have."""
    assert m.build_manifest()["settings"]["socket_mode_enabled"] is True


def test_interactivity_is_on():
    """Tool approvals are buttons; without interactivity every click is dropped
    and every gated tool sits until it times out and denies."""
    assert m.build_manifest()["settings"]["interactivity"]["is_enabled"] is True


def test_only_direct_messages_are_subscribed():
    """Channel events would make the bot reachable by everyone in a channel."""
    assert m.build_manifest()["settings"]["event_subscriptions"]["bot_events"] == [
        "message.im"
    ]


def test_scopes_are_exactly_the_documented_set():
    """A scope added without a reason is a scope the user cannot evaluate."""
    assert set(m.build_manifest()["oauth_config"]["scopes"]["bot"]) == set(m.BOT_SCOPES)


@pytest.mark.parametrize(
    "forbidden",
    [
        "channels:history",
        "channels:read",
        "groups:history",
        "chat:write.public",
        "admin",
        "users:read.email",
    ],
)
def test_no_channel_or_admin_scope_is_requested(forbidden):
    assert forbidden not in m.build_manifest()["oauth_config"]["scopes"]["bot"]


def test_app_name_flows_into_the_bot_user():
    built = m.build_manifest("Acme Agent")
    assert built["display_information"]["name"] == "Acme Agent"
    assert built["features"]["bot_user"]["display_name"] == "Acme Agent"


def test_bot_is_not_advertised_as_always_online():
    """The bridge starts on demand; a green dot over a dead bridge is a lie."""
    assert m.build_manifest()["features"]["bot_user"]["always_online"] is False


# ----------------------------------------------------------------------
# The create-app URL
# ----------------------------------------------------------------------


def test_create_url_carries_a_manifest_slack_can_parse():
    """The URL is the whole one-click story — if it does not round-trip, the
    user lands on an empty form and fills it in by hand."""
    url = m.create_app_url()
    assert url.startswith(m.CREATE_APP_ENDPOINT)
    assert _manifest_from_url(url) == m.build_manifest()


def test_create_url_asks_for_the_new_app_dialog():
    assert parse_qs(urlparse(m.create_app_url()).query)["new_app"] == ["1"]


def test_create_url_survives_a_name_with_url_metacharacters():
    url = m.create_app_url("A&B agent?")
    assert _manifest_from_url(url)["display_information"]["name"] == "A&B agent?"


# ----------------------------------------------------------------------
# Token shape
# ----------------------------------------------------------------------


def test_well_formed_tokens_pass():
    m.validate_tokens("xoxb-real", "xapp-real")


def test_tokens_are_accepted_with_surrounding_whitespace():
    """Pasting from Slack's UI routinely brings a trailing newline."""
    m.validate_tokens("  xoxb-real\n", "\txapp-real ")


def test_swapped_tokens_name_the_swap():
    """The single most common setup mistake. Saying 'invalid token' twice
    leaves the user re-copying the same two strings."""
    with pytest.raises(m.TokenFormatError) as excinfo:
        m.validate_tokens("xapp-oops", "xoxb-oops")
    assert "goes in the other field" in str(excinfo.value)


def test_a_missing_bot_token_names_where_to_find_it():
    with pytest.raises(m.TokenFormatError) as excinfo:
        m.validate_tokens("", "xapp-real")
    assert "Install to Workspace" in str(excinfo.value)


def test_a_missing_app_token_names_the_scope_it_needs():
    with pytest.raises(m.TokenFormatError) as excinfo:
        m.validate_tokens("xoxb-real", "")
    assert "connections:write" in str(excinfo.value)


def test_setup_steps_are_not_promised_as_automatic():
    """Claiming 'automatic' strands the user on a settings page. The steps must
    read as steps."""
    joined = " ".join(m.SETUP_STEPS).lower()
    assert "paste" in joined
    assert "keyring" in joined, "the user should know where the tokens land"
