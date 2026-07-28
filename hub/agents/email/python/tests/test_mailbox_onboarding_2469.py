# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Agent-led mailbox onboarding — detection and the conversation (#2469).

Two things are worth defending here, and they are the two the old behaviour got
wrong:

1. **The four broken states are told apart.** "Can't reach your mailbox" is four
   different problems with four different fixes — and the cheapest of them
   (``agent_not_granted``) needs no browser at all. Collapsing them means asking
   the user for an OAuth round-trip they did not need.
2. **Each state opens with a DIFFERENT question.** A generic "connect your
   mailbox?" for a mailbox that is already connected is how a user loses trust
   in the thing offering to help.

The conversation is scripted in Python, so it is asserted exactly — no LLM in
this file.
"""

from __future__ import annotations

import json
import time

import pytest
from conftest import FakeAgent as _FakeAgent
from conftest import ScriptedConsole as _ScriptedConsole
from gaia_agent_email import mailbox_state as ms
from gaia_agent_email import question as q
from gaia_agent_email.tools import onboarding_tools as ob

GMAIL_SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.send",
]


# ---------------------------------------------------------------------------
# Fakes — shared with every onboarding test module, see conftest.py.
# ---------------------------------------------------------------------------


def _connection(scopes=None, email="kalin@example.com", error=None):
    entry = {
        "provider": "google",
        "account_email": email,
        "scopes": list(GMAIL_SCOPES if scopes is None else scopes),
        "connected_at": 1,
    }
    if error:
        entry["error"] = error
    return entry


@pytest.fixture()
def connectors(monkeypatch):
    """Drive every connector call the flow makes, with no keyring or network."""

    state = {
        "connection": None,
        "granted": False,
        "token_error": None,
        "grants": [],
        "configured": [],
        "completed": [],
        "client_id": "abc.apps.googleusercontent.com",
        "client_secret": "s3cret",
        "timeouts": [],
    }

    def get_connection(provider):
        return state["connection"] if provider == "google" else None

    def check_agent_grant(provider, agent_id, scopes):
        return state["granted"]

    def get_access_token_sync(**kwargs):
        if state["token_error"] is not None:
            raise state["token_error"]
        return "token"

    def grant_agent(provider, agent_id, scopes):
        state["grants"].append((provider, agent_id, tuple(scopes)))

    class _Provider:
        provider_id = "google"

        @property
        def client_id(self):
            return state["client_id"]

        @property
        def client_secret(self):
            return state["client_secret"]

    def get_provider(provider_id):
        from gaia.connectors.errors import ConfigurationError

        if not state["client_id"]:
            raise ConfigurationError("GAIA_GOOGLE_CLIENT_ID is not set")
        return _Provider()

    async def configure(connector_id, config):
        state["configured"].append((connector_id, config))
        return {"flow_id": "flow-1", "authorization_url": "https://accounts/auth"}

    async def complete_authorization(flow_id):
        state["completed"].append(flow_id)
        return {
            "provider": "google",
            "account_email": "kalin@example.com",
            "scopes": list(GMAIL_SCOPES),
        }

    def run_sync(coro, *, timeout=30.0):
        import asyncio

        # Assert the BUDGET, not just the call. The default 30s is shorter than
        # the 120s browser sign-in wait, so a caller that leaves it at the
        # default abandons a flow that then succeeds — and the mailbox lands
        # connected-but-ungranted. A stub that dropped this kwarg is what let
        # that ship.
        state["timeouts"].append(timeout)
        return asyncio.run(coro)

    monkeypatch.setattr("gaia.connectors.api.get_connection", get_connection)
    monkeypatch.setattr("gaia.connectors.grants.check_agent_grant", check_agent_grant)
    monkeypatch.setattr(
        "gaia.connectors.api.get_access_token_sync", get_access_token_sync
    )
    monkeypatch.setattr("gaia.connectors.grants.grant_agent", grant_agent)
    monkeypatch.setattr("gaia.connectors.providers.get", get_provider)
    monkeypatch.setattr("gaia.connectors.handler.configure", configure)
    monkeypatch.setattr(
        "gaia.connectors.flow.complete_authorization", complete_authorization
    )
    monkeypatch.setattr("gaia.connectors._loop.run_sync", run_sync)
    return state


def _run(agent, provider=""):
    return json.loads(ob._setup_mailbox_access(agent, provider))


def _questions(agent):
    return [a["message"] for a in agent.console.asked]


# ---------------------------------------------------------------------------
# Detection — the four states are told apart
# ---------------------------------------------------------------------------


def test_detects_not_connected(connectors):
    connectors["connection"] = None
    assert ms.inspect_provider("google")["state"] == ms.STATE_NOT_CONNECTED


def test_detects_missing_scopes(connectors):
    connectors["connection"] = _connection(scopes=[GMAIL_SCOPES[0]])
    state = ms.inspect_provider("google")
    assert state["state"] == ms.STATE_MISSING_SCOPES
    assert state["missing_scopes"] == [GMAIL_SCOPES[1]]


def test_detects_agent_not_granted(connectors):
    connectors["connection"] = _connection()
    connectors["granted"] = False
    assert ms.inspect_provider("google")["state"] == ms.STATE_NOT_GRANTED


def test_detects_reauth_required_only_via_the_live_probe(connectors):
    """The failure the user actually hit: stored credentials that stopped working.

    Every local check passes; only the token refresh reveals it — which is why
    ``probe=False`` cannot see it and the default is on.
    """
    from gaia.connectors.errors import ConnectionRevokedError

    connectors["connection"] = _connection()
    connectors["granted"] = True
    connectors["token_error"] = ConnectionRevokedError("google")

    assert ms.inspect_provider("google", probe=False)["state"] == ms.STATE_OK
    assert ms.inspect_provider("google")["state"] == ms.STATE_REAUTH_REQUIRED


def test_detects_ok(connectors):
    connectors["connection"] = _connection()
    connectors["granted"] = True
    state = ms.inspect_provider("google")
    assert state["state"] == ms.STATE_OK
    assert state["account_email"] == "kalin@example.com"


def test_missing_oauth_client_is_not_reported_as_missing_scopes(connectors):
    """A connection whose OAuth client is gone reports no scopes at all.

    Reading that as "missing scopes" sends the user to fix the wrong thing.
    """
    connectors["connection"] = _connection(scopes=[], email="", error="configuration")
    assert ms.inspect_provider("google")["state"] == ms.STATE_REAUTH_REQUIRED


# ---------------------------------------------------------------------------
# The conversation — a DIFFERENT opening question per state
# ---------------------------------------------------------------------------


def test_already_usable_asks_nothing(connectors):
    connectors["connection"] = _connection()
    connectors["granted"] = True
    agent = _FakeAgent()

    out = _run(agent)

    assert out["ok"] is True
    assert out["data"]["changed"] is False
    assert agent.console.asked == [], "a working mailbox must not be interrupted"


def test_not_granted_is_fixed_locally_with_no_browser(connectors):
    """The cheap fix: connected already, just not allowed. No OAuth at all."""
    connectors["connection"] = _connection()
    connectors["granted"] = False
    agent = _FakeAgent(answers=["yes"])

    # After the grant, the state is healthy.
    def after_grant(provider, agent_id, scopes):
        connectors["granted"] = True

    import gaia.connectors.grants as grants

    original = grants.grant_agent
    grants.grant_agent = lambda p, a, s: (original(p, a, s), after_grant(p, a, s))[0]
    try:
        out = _run(agent)
    finally:
        grants.grant_agent = original

    assert out["ok"] is True and out["data"]["changed"] is True
    asked = _questions(agent)
    assert len(asked) == 1, asked
    assert "already connected" in asked[0]
    assert "haven't been allowed" in asked[0]
    # The cheap path is advertised as cheap.
    labels = agent.console.asked[0]["options"]
    assert "no browser" in labels[0]["description"]
    assert connectors["configured"] == [], "granting must not start an OAuth flow"
    assert connectors["grants"], "the grant was never written"


def test_success_message_quotes_the_terminal_probe_not_a_mid_flow_value(
    connectors, monkeypatch
):
    """#2590 AC5: success is reported only after the TERMINAL
    inspect_provider(probe=True) call. Plant a differing mid-flow value (the
    account email the INITIAL survey() saw) and assert the final message
    quotes the LAST call's value, not a cached earlier one."""
    connectors["connection"] = _connection(email="stale@example.com")
    connectors["granted"] = False
    agent = _FakeAgent(answers=["yes"])

    calls = {"n": 0}
    real_get_connection = connectors["connection"]

    def get_connection(provider):
        if provider != "google":
            return None
        calls["n"] += 1
        # First call: the INITIAL ms.survey(probe=True) inside _setup_flow.
        # Every call after: the grant has landed and the account is now the
        # REAL one — the terminal inspect_provider() call must see this.
        if calls["n"] == 1:
            return real_get_connection
        return _connection(email="current@example.com")

    monkeypatch.setattr("gaia.connectors.api.get_connection", get_connection)

    def after_grant(provider, agent_id, scopes):
        connectors["granted"] = True

    import gaia.connectors.grants as grants

    original = grants.grant_agent
    grants.grant_agent = lambda p, a, s: (original(p, a, s), after_grant(p, a, s))[0]
    try:
        out = _run(agent)
    finally:
        grants.grant_agent = original

    assert out["ok"] is True
    assert out["data"]["account_email"] == "current@example.com"
    assert "current@example.com" in out["data"]["message"]
    assert "stale@example.com" not in out["data"]["message"]


def test_reauth_required_says_the_sign_in_stopped_working(connectors):
    from gaia.connectors.errors import ConnectionRevokedError

    connectors["connection"] = _connection()
    connectors["granted"] = True
    connectors["token_error"] = ConnectionRevokedError("google")
    agent = _FakeAgent(answers=["no"])

    out = _run(agent)

    asked = _questions(agent)
    assert len(asked) == 1
    assert "stopped working" in asked[0]
    assert out["data"]["declined"] is True
    assert connectors["configured"] == [], "declining must change nothing"


def test_missing_scopes_names_the_missing_permission(connectors):
    connectors["connection"] = _connection(scopes=[GMAIL_SCOPES[0]])
    agent = _FakeAgent(answers=["no"])

    _run(agent)

    asked = _questions(agent)[0]
    assert "doesn't cover everything I need" in asked
    # Plain language, not a scope URL.
    assert "Send email on your behalf" in asked
    assert "googleapis.com" not in asked


def test_nothing_connected_asks_which_mailbox_first(connectors):
    connectors["connection"] = None
    agent = _FakeAgent(answers=["no"])

    out = _run(agent)

    asked = _questions(agent)
    assert len(asked) == 1
    assert "Which one should I connect?" in asked[0]
    values = [o["value"] for o in agent.console.asked[0]["options"]]
    assert values == ["google", "microsoft", "no"]
    assert out["data"]["declined"] is True


def test_the_four_states_open_with_four_different_questions(connectors):
    """The point of the whole exercise, asserted in one place."""
    from gaia.connectors.errors import ConnectionRevokedError

    openings = {}

    scenarios = {
        ms.STATE_NOT_CONNECTED: dict(connection=None, granted=False, token_error=None),
        ms.STATE_NOT_GRANTED: dict(
            connection=_connection(), granted=False, token_error=None
        ),
        ms.STATE_MISSING_SCOPES: dict(
            connection=_connection(scopes=[GMAIL_SCOPES[0]]),
            granted=True,
            token_error=None,
        ),
        ms.STATE_REAUTH_REQUIRED: dict(
            connection=_connection(),
            granted=True,
            token_error=ConnectionRevokedError("google"),
        ),
    }
    for name, setup in scenarios.items():
        connectors.update(setup)
        agent = _FakeAgent(answers=["no"])
        _run(agent)
        openings[name] = _questions(agent)[0]

    assert len(set(openings.values())) == 4, openings


# ---------------------------------------------------------------------------
# The OAuth path, and the limits it cannot prompt away
# ---------------------------------------------------------------------------


def test_oauth_flow_grants_the_agent_in_the_same_pass(connectors):
    connectors["connection"] = None
    agent = _FakeAgent(answers=["google", "yes"])

    def after(*_):
        connectors["connection"] = _connection()
        connectors["granted"] = True

    import gaia.connectors.grants as grants

    original = grants.grant_agent
    grants.grant_agent = lambda p, a, s: (original(p, a, s), after())[0]
    try:
        out = _run(agent)
    finally:
        grants.grant_agent = original

    assert out["ok"] is True and out["data"]["changed"] is True
    _, config = connectors["configured"][0]
    # Connecting without granting is the dead end this feature exists to remove.
    assert config["grant_agents"] == {ms.AGENT_ID: GMAIL_SCOPES}
    assert connectors["completed"] == ["flow-1"]
    # The sign-in wait must outlast the flow's own 120s bound.
    assert max(connectors["timeouts"]) > 120
    # The copy-paste fallback is offered before the 2-minute wait, not after.
    assert any("https://accounts/auth" in m for m in agent.console.info)


def test_missing_oauth_client_is_explained_before_it_is_asked_for(connectors):
    """The honest limit: the user still supplies their own client id + secret."""
    connectors["connection"] = None
    connectors["client_id"] = ""
    connectors["client_secret"] = ""
    agent = _FakeAgent(answers=["google", "yes", "yes", "my-id", "my-secret"])

    def after(*_):
        connectors["connection"] = _connection()
        connectors["granted"] = True

    import gaia.connectors.grants as grants

    original = grants.grant_agent
    grants.grant_agent = lambda p, a, s: (original(p, a, s), after())[0]
    try:
        out = _run(agent)
    finally:
        grants.grant_agent = original

    asked = _questions(agent)
    explain = [a for a in asked if "OAuth client ID and secret" in a]
    assert explain, asked
    assert "cannot do for you" in explain[0]
    assert "amd-gaia.ai" in explain[0], "the user is told where to go"

    # The secret is asked for with the sensitive flag, so the surface can mask it.
    secret_q = [a for a in agent.console.asked if "client secret" in a["message"]]
    assert secret_q and secret_q[0]["sensitive"] is True
    assert [a["sensitive"] for a in agent.console.asked if a is not secret_q[0]] == [
        False
    ] * (len(agent.console.asked) - 1)

    _, config = connectors["configured"][0]
    assert config["client_id"] == "my-id"
    assert config["client_secret"] == "my-secret"
    assert out["data"]["changed"] is True


def test_declining_the_client_credentials_changes_nothing(connectors):
    connectors["connection"] = None
    connectors["client_id"] = ""
    agent = _FakeAgent(answers=["google", "yes", "no"])

    out = _run(agent)

    assert out["ok"] is True and out["data"]["declined"] is True
    assert connectors["configured"] == []
    assert "amd-gaia.ai" in out["data"]["message"]


def test_a_fix_that_did_not_fix_it_is_reported_as_failure(connectors):
    """Never say "connected!" about a mailbox that still does not work."""
    connectors["connection"] = _connection()
    connectors["granted"] = False  # stays false: the grant silently did nothing
    agent = _FakeAgent(answers=["yes"])

    out = _run(agent)

    assert out["ok"] is False
    assert "still isn't usable" in out["error"]


def test_connected_but_grant_failed_is_reported_honestly_not_as_nothing_changed(
    connectors,
):
    """#2590: save_connection runs before the grant commits. If the grant
    write fails the connection IS persisted — the generic catch-all used to
    say "Nothing was changed", which is false. It must say what actually
    happened: connected, not yet permitted."""
    from gaia.connectors.errors import GrantAfterConnectError

    connectors["connection"] = None

    async def failing_complete_authorization(flow_id):
        raise GrantAfterConnectError(
            "google", ms.AGENT_ID, reason="disk full"
        )

    import gaia.connectors.flow as flow_mod

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(flow_mod, "complete_authorization", failing_complete_authorization)
    try:
        agent = _FakeAgent(answers=["google", "yes"])
        out = _run(agent)
    finally:
        monkeypatch.undo()

    assert out["ok"] is True
    assert out["data"]["changed"] is True
    assert "connected" in out["data"]["message"].lower()
    assert "nothing was changed" not in out["data"]["message"].lower()
    assert "disk full" in out["data"]["message"]


# ---------------------------------------------------------------------------
# Surfaces that cannot ask
# ---------------------------------------------------------------------------


def test_a_surface_that_cannot_ask_says_so(connectors):
    class _Mute:
        pass

    class _MuteAgent:
        console = _Mute()

    connectors["connection"] = None
    with pytest.raises(q.InputUnsupportedError) as exc:
        ob._setup_mailbox_access(_MuteAgent(), "")
    assert "cannot ask questions during a run" in str(exc.value)


def test_a_caller_that_cannot_answer_is_refused_immediately(connectors):
    """A one-shot / CLI run must fail fast, not park until the question expires.

    The SSE handler is present on EVERY /query run, so its presence proves
    nothing about whether anyone is watching — only the caller knows, and it
    says so with ``can_answer_questions``. Getting this wrong turns an
    actionable error into a 240-second silence that reads as a hang.
    """
    connectors["connection"] = None
    agent = _FakeAgent(answers=["google", "yes"], can_answer_questions=False)

    with pytest.raises(q.InputUnsupportedError) as exc:
        ob._setup_mailbox_access(agent, "")
    assert "can't take an answer mid-task" in str(exc.value)
    assert agent.console.asked == [], "nothing should have been asked"
    assert connectors["configured"] == []


def test_an_unreadable_store_is_not_offered_a_browser_sign_in(connectors):
    """A locked keychain is not "not connected" — do not walk them through OAuth."""

    def boom(provider):
        raise RuntimeError("keychain is locked")

    monkey = pytest.MonkeyPatch()
    monkey.setattr("gaia.connectors.api.get_connection", boom)
    try:
        agent = _FakeAgent(answers=["yes"])
        with pytest.raises(RuntimeError) as exc:
            ob._setup_mailbox_access(agent, "google")
    finally:
        monkey.undo()
    assert "could not read" in str(exc.value).lower()
    assert agent.console.asked == [], "no browser sign-in may be offered"


def test_a_flaky_probe_is_not_reported_as_revoked_credentials(connectors):
    """A network blip must not send a healthy mailbox through a re-auth."""
    connectors["connection"] = _connection()
    connectors["granted"] = True
    connectors["token_error"] = OSError("temporary failure in name resolution")

    state = ms.inspect_provider("google")
    assert state["state"] == ms.STATE_ERROR
    assert state["state"] != ms.STATE_REAUTH_REQUIRED


def test_probe_reported_missing_scopes_reach_the_question(connectors):
    """The scopes the LIVE probe names must survive into what the user is told."""
    from gaia.connectors.errors import AuthRequiredError

    connectors["connection"] = _connection()
    connectors["granted"] = True
    connectors["token_error"] = AuthRequiredError(
        AuthRequiredError.Reason.CONNECTION_MISSING_SCOPES,
        provider="google",
        missing_scopes=[GMAIL_SCOPES[1]],
    )

    state = ms.inspect_provider("google")
    assert state["state"] == ms.STATE_MISSING_SCOPES
    assert state["missing_scopes"] == [GMAIL_SCOPES[1]]

    agent = _FakeAgent(answers=["no"])
    _run(agent)
    assert "Send email on your behalf" in _questions(agent)[0]


def test_an_unanswered_question_stops_the_flow_loudly(connectors):
    connectors["connection"] = None
    agent = _FakeAgent(answers=[])  # nothing ever answers

    with pytest.raises(q.InputUnansweredError):
        ob._setup_mailbox_access(agent, "")
    assert connectors["configured"] == []


# ---------------------------------------------------------------------------
# Under the daemon the sidecar holds no credential of its own (#2154)
# ---------------------------------------------------------------------------


@pytest.fixture()
def forwarded(monkeypatch):
    """Boot the sidecar in daemon forwarded-credentials mode."""
    from gaia_agent_email import forwarded_credentials as fc

    monkeypatch.setenv(fc.FORWARDED_MODE_ENV_VAR, "1")
    fc.reset()
    yield fc
    fc.reset()


def test_forwarded_mode_reads_usability_from_the_forwarded_token(
    connectors, forwarded
):
    """A keyring that looks healthy is NOT proof this process can use it.

    Probing the keyring here would call a mailbox usable that the sidecar
    structurally cannot touch — the exact "works on my machine" trap.
    """
    connectors["connection"] = _connection()
    connectors["granted"] = True

    assert ms.inspect_provider("google")["state"] == ms.STATE_REAUTH_REQUIRED

    forwarded.set_forwarded(
        "google",
        access_token="tok",
        scopes=GMAIL_SCOPES,
        expires_at=time.time() + 3600,
        account_email="kalin@example.com",
    )
    assert ms.inspect_provider("google")["state"] == ms.STATE_OK


def test_forwarded_mode_reports_a_pending_handover_not_a_failure(
    connectors, forwarded
):
    """Connecting under the daemon succeeds even though the token lags behind."""
    connectors["connection"] = None
    agent = _FakeAgent(answers=["google", "yes"])

    def after(*_):
        connectors["connection"] = _connection()
        connectors["granted"] = True

    import gaia.connectors.grants as grants

    original = grants.grant_agent
    grants.grant_agent = lambda p, a, s: (original(p, a, s), after())[0]
    try:
        out = _run(agent)
    finally:
        grants.grant_agent = original

    assert out["ok"] is True, out
    assert out["data"]["changed"] is True
    assert out["data"]["handover_pending"] is True
    assert "try your request again shortly" in out["data"]["message"]
