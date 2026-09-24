"""Pin the adapter's Slack calls against the real ``slack_sdk``.

``test_slack_adapter.py`` drives a ``FakeWeb`` that accepts any keyword it is
handed, so every assertion there proves only "we called it" — never "the call
would be accepted". That is the boundary-validity gap CLAUDE.md names, and on a
third-party HTTP client it is exactly where a rename lands: ``files.upload`` was
retired in favour of ``files_upload_v2`` with a different signature, and a fake
would have kept reporting success right through the change.

These tests introspect the installed SDK. They cannot prove Slack's *server*
accepts a payload — only an end-to-end run against a real workspace does that —
but they do prove the adapter is not calling a method that no longer exists or
passing a keyword that was removed.
"""

import inspect

import pytest

slack_sdk = pytest.importorskip(
    "slack_sdk", reason="slack-sdk is an optional extra: pip install 'amd-gaia[slack]'"
)

from slack_sdk import WebClient  # noqa: E402
from slack_sdk.socket_mode import SocketModeClient  # noqa: E402
from slack_sdk.socket_mode.response import SocketModeResponse  # noqa: E402


def _params(method):
    return set(inspect.signature(method).parameters)


# ----------------------------------------------------------------------
# WebClient
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "method", ["auth_test", "chat_postMessage", "chat_update", "files_upload_v2"]
)
def test_the_adapter_only_calls_methods_that_exist(method):
    assert hasattr(
        WebClient, method
    ), f"WebClient has no {method}; the adapter calls it"


def test_post_accepts_every_keyword_the_adapter_sends():
    assert {"channel", "text", "blocks", "thread_ts"} <= _params(
        WebClient.chat_postMessage
    )


def test_update_accepts_every_keyword_the_adapter_sends():
    assert {"channel", "ts", "text", "blocks"} <= _params(WebClient.chat_update)


def test_upload_accepts_every_keyword_the_adapter_sends():
    """``files_upload_v2``, not the retired ``files.upload``. Its keywords
    differ from the old call's, which is the whole reason this is pinned."""
    assert {
        "channel",
        "file",
        "filename",
        "initial_comment",
        "thread_ts",
    } <= _params(WebClient.files_upload_v2)


# ----------------------------------------------------------------------
# Socket Mode
# ----------------------------------------------------------------------


def test_socket_client_takes_the_app_token_and_a_web_client():
    assert {"app_token", "web_client"} <= _params(SocketModeClient.__init__)


def test_socket_client_exposes_the_listener_list_the_adapter_appends_to():
    client = SocketModeClient(app_token="xapp-not-a-real-token")
    assert isinstance(client.socket_mode_request_listeners, list)


def test_the_ack_carries_an_envelope_id():
    """Slack retries anything unacked within three seconds, and a turn takes far
    longer — a broken ack turns one question into three."""
    assert "envelope_id" in _params(SocketModeResponse.__init__)


# ----------------------------------------------------------------------
# The adapter against a real WebClient
# ----------------------------------------------------------------------


def test_the_adapter_builds_a_real_web_client_from_the_bot_token():
    from gaia.messaging.slack.adapter import SlackAdapter

    adapter = SlackAdapter("xoxb-not-a-real-token", "xapp-x", allowed_users={"U1"})
    client = adapter._build_web_client()
    assert isinstance(client, WebClient)
    assert client.token == "xoxb-not-a-real-token"
