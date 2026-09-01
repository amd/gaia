# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
T-CLI: ``gaia connectors`` subcommand tests.

Covers the thin wrappers in ``src/gaia/connectors/cli.py`` that delegate
to ``gaia.connectors.api``. The actual flow / token / grant logic is
tested elsewhere; these tests verify wiring + output shape + exit codes.
"""

from __future__ import annotations

import json

import pytest

from gaia.connectors import cli as connections_cli
from gaia.connectors.providers import _registry


@pytest.fixture(autouse=True)
def fake_home(tmp_path, monkeypatch):
    """Isolated grants/mcp_servers dirs per test."""
    monkeypatch.setattr("gaia.connectors.grants.Path.home", lambda: tmp_path)
    monkeypatch.setattr("gaia.connectors.activations.Path.home", lambda: tmp_path)
    monkeypatch.setattr("gaia.connectors.mcp_server.Path.home", lambda: tmp_path)
    monkeypatch.setenv("GAIA_GOOGLE_CLIENT_ID", "test.apps.example")
    _registry.clear()
    yield


def _seed_google(account_email: str) -> None:
    """Helper: write a Google keyring blob (the source of truth for
    ``configured`` after the state.json removal)."""
    from gaia.connectors.providers import get as get_provider
    from gaia.connectors.store import save_connection

    save_connection(
        provider="google",
        account_email=account_email,
        refresh_token="seed",
        scopes=["s"],
        client_id_hash=get_provider("google").client_id_hash,
    )


def _run(*argv) -> tuple[int, str, str]:
    import sys
    from io import StringIO

    out = StringIO()
    err = StringIO()
    saved_out, saved_err = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = out, err
    try:
        rc = connections_cli.main(list(argv))
    except SystemExit as e:
        rc = e.code if isinstance(e.code, int) else 1
    finally:
        sys.stdout, sys.stderr = saved_out, saved_err
    return rc, out.getvalue(), err.getvalue()


class TestConnectSelfDocuments:
    """`gaia connectors connect google` with no client credentials must be
    self-documenting for a headless user (#2347) — the console setup steps and
    the exact commands, not a UI-only dead end."""

    def test_connect_without_client_creds_prints_setup_guide(self, monkeypatch):
        monkeypatch.delenv("GAIA_GOOGLE_CLIENT_ID", raising=False)
        monkeypatch.delenv("GAIA_GOOGLE_CLIENT_SECRET", raising=False)
        # Ensure no keyring-stored client creds resolve either.
        monkeypatch.setattr(
            "gaia.connectors.store.peek_provider_credentials", lambda pid: None
        )
        _registry.clear()

        rc, _out, err = _run("connectors", "connect", "google")

        assert rc == 3  # ConfigurationError exit code
        assert "not configured" in err
        assert "console.cloud.google.com" in err  # console steps
        assert "gaia connectors configure google --client-id" in err  # exact command
        # connect authorizes scopes and grants the agent in one flow (#2347).
        assert "gaia connectors connect google --scopes" in err
        assert "--grant-agent installed:email" in err
        assert "gmail.modify" in err  # copy-paste example has real scopes
        assert "amd-gaia.ai/docs/connectors/google" in err


class TestConnectGrantAgent:
    """`gaia connectors connect --grant-agent` folds connect + grant into one
    flow so the scopes can never drift (#2347 UX)."""

    @staticmethod
    def _install_fake_agent_registry(monkeypatch, declared_scopes):
        """Stand in for `AgentRegistry() -> .discover() -> register_installed_
        sidecars()` (#2603) with a fake that resolves ``installed:email`` to
        *declared_scopes* for ``google``, without importing gaia_agent_email
        or touching disk."""
        from dataclasses import dataclass, field
        from typing import List

        from gaia.connectors.providers.base import ConnectorRequirement

        @dataclass
        class FakeReg:
            namespaced_agent_id: str
            required_connections: List[ConnectorRequirement] = field(
                default_factory=list
            )

        class FakeAgentRegistry:
            def __init__(self, *_a, **_k):
                cr = ConnectorRequirement(connector_id="google", scopes=declared_scopes)
                self._regs = [
                    FakeReg(
                        namespaced_agent_id="installed:email",
                        required_connections=[cr],
                    )
                ]

            def discover(self):
                pass

            def list(self):
                return self._regs

        monkeypatch.setattr("gaia.agents.registry.AgentRegistry", FakeAgentRegistry)
        monkeypatch.setattr(
            "gaia.hub.installer.register_installed_sidecars",
            lambda registry: None,
        )

    def test_grant_agent_without_scopes_derives_declared_scopes(self, monkeypatch):
        declared = [
            "https://www.googleapis.com/auth/gmail.modify",
            "https://www.googleapis.com/auth/gmail.send",
        ]
        self._install_fake_agent_registry(monkeypatch, declared)

        captured = {}

        async def _fake_start(connector_id, *, scopes, grant_agents=None):
            captured["connector_id"] = connector_id
            captured["scopes"] = list(scopes)
            captured["grant_agents"] = grant_agents
            return {"flow_id": "F1", "authorization_url": "https://auth.example"}

        async def _fake_complete(flow_id):
            return {"account_email": "alice@example.com"}

        monkeypatch.setattr("gaia.connectors.api.start_authorization", _fake_start)
        monkeypatch.setattr(
            "gaia.connectors.api.complete_authorization", _fake_complete
        )

        rc, _out, err = _run(
            "connectors", "connect", "google", "--grant-agent", "installed:email"
        )
        assert rc == 0, err

        # The grant is exactly the agent's declared scopes — sorted, no `openid`.
        assert captured["grant_agents"] == {
            "installed:email": [
                "https://www.googleapis.com/auth/gmail.modify",
                "https://www.googleapis.com/auth/gmail.send",
            ]
        }
        # The authorize set is the UNION with the provider's default_scopes
        # (AC3/C3 regression guard) — `openid` must not be silently dropped,
        # and the derived/declared scopes must still be requested too.
        assert "openid" in captured["scopes"]
        assert "https://www.googleapis.com/auth/gmail.modify" in captured["scopes"]
        assert "https://www.googleapis.com/auth/gmail.send" in captured["scopes"]

    def test_grant_agent_passes_grant_agents_to_the_flow(self, monkeypatch):
        captured = {}

        async def _fake_start(connector_id, *, scopes, grant_agents=None):
            captured["connector_id"] = connector_id
            captured["scopes"] = list(scopes)
            captured["grant_agents"] = grant_agents
            return {"flow_id": "F1", "authorization_url": "https://auth.example"}

        async def _fake_complete(flow_id):
            return {"account_email": "alice@example.com"}

        monkeypatch.setattr("gaia.connectors.api.start_authorization", _fake_start)
        monkeypatch.setattr(
            "gaia.connectors.api.complete_authorization", _fake_complete
        )

        # --scopes is explicit here, so resolve_declared_scopes must never run.
        def _must_not_run(*_a, **_k):
            raise AssertionError(
                "resolve_declared_scopes must not run when --scopes is given"
            )

        monkeypatch.setattr(
            "gaia.connectors.api.resolve_declared_scopes", _must_not_run
        )

        rc, out, _err = _run(
            "connectors",
            "connect",
            "google",
            "--scopes",
            "https://www.googleapis.com/auth/gmail.modify",
            "--grant-agent",
            "installed:email",
        )
        assert rc == 0
        # The grant rides the SAME scopes as the connect — no drift possible.
        assert captured["grant_agents"] == {
            "installed:email": ["https://www.googleapis.com/auth/gmail.modify"]
        }
        assert "Connected as alice@example.com" in out
        assert "granted google → installed:email" in out

    @pytest.mark.allow_network
    def test_explicit_scope_outside_catalog_is_rejected_by_the_flow(self):
        bogus_scope = "https://www.googleapis.com/auth/not-in-catalog"

        rc, _out, err = _run("connectors", "connect", "google", "--scopes", bogus_scope)

        assert rc == 5
        assert bogus_scope in err

    def test_plain_connect_passes_no_grant_agents(self, monkeypatch):
        captured = {}

        async def _fake_start(connector_id, *, scopes, grant_agents=None):
            captured["grant_agents"] = grant_agents
            return {"flow_id": "F1", "authorization_url": "https://auth.example"}

        async def _fake_complete(flow_id):
            return {"account_email": "bob@example.com"}

        monkeypatch.setattr("gaia.connectors.api.start_authorization", _fake_start)
        monkeypatch.setattr(
            "gaia.connectors.api.complete_authorization", _fake_complete
        )

        rc, _out, _err = _run("connectors", "connect", "google", "--scopes", "s1")
        assert rc == 0
        assert captured["grant_agents"] is None

    def test_bare_connect_with_existing_grant_derives_declared_union(self, monkeypatch):
        """AC-8 (#2730): the command GAIA's own error text tells a user to
        run — `gaia connectors connect google` with NO flags — must not
        silently narrow an existing grant to identity-only scopes. With
        installed:email already granted, it authorizes
        declared ∪ default_scopes (7 scopes for google)."""
        TestConnectGrantAgent._install_fake_agent_registry(
            monkeypatch,
            [
                "https://www.googleapis.com/auth/gmail.modify",
                "https://www.googleapis.com/auth/gmail.send",
                "https://www.googleapis.com/auth/calendar.events",
                "https://www.googleapis.com/auth/calendar.readonly",
            ],
        )
        from gaia.connectors.grants import grant_agent

        grant_agent(
            "google",
            "installed:email",
            ["https://www.googleapis.com/auth/gmail.modify"],
        )

        captured = {}

        async def _fake_start(connector_id, *, scopes, grant_agents=None):
            captured["scopes"] = sorted(scopes)
            captured["grant_agents"] = grant_agents
            return {"flow_id": "F1", "authorization_url": "https://auth.example"}

        async def _fake_complete(flow_id):
            return {"account_email": "alice@example.com"}

        monkeypatch.setattr("gaia.connectors.api.start_authorization", _fake_start)
        monkeypatch.setattr(
            "gaia.connectors.api.complete_authorization", _fake_complete
        )

        rc, _out, err = _run("connectors", "connect", "google")
        assert rc == 0, err
        assert captured["scopes"] == sorted(
            {
                "openid",
                "email",
                "profile",
                "https://www.googleapis.com/auth/gmail.modify",
                "https://www.googleapis.com/auth/gmail.send",
                "https://www.googleapis.com/auth/calendar.events",
                "https://www.googleapis.com/auth/calendar.readonly",
            }
        )
        assert len(captured["scopes"]) == 7
        # Bare connect never grants — it only authorizes.
        assert captured["grant_agents"] is None

    def test_bare_connect_with_no_grants_defers_to_start_authorization(
        self, monkeypatch
    ):
        """No agent holds a grant for this connector, so the CLI has nothing
        to derive a scope union from. It must NOT guess a narrower list
        itself — it sends nothing and lets `start_authorization`'s own D0
        guard decide (raise on an existing connection, or fall back to
        default_scopes for a genuine first-time connect)."""
        captured = {}

        async def _fake_start(connector_id, *, scopes, grant_agents=None):
            captured["scopes"] = list(scopes)
            return {"flow_id": "F1", "authorization_url": "https://auth.example"}

        async def _fake_complete(flow_id):
            return {"account_email": "bob@example.com"}

        monkeypatch.setattr("gaia.connectors.api.start_authorization", _fake_start)
        monkeypatch.setattr(
            "gaia.connectors.api.complete_authorization", _fake_complete
        )

        rc, _out, err = _run("connectors", "connect", "google")
        assert rc == 0, err
        assert captured["scopes"] == []


class TestStatus:
    def test_status_empty(self):
        # list/status shows catalog entries; google is always in the catalog
        rc, out, _err = _run("connectors", "status")
        assert rc == 0
        assert "google" in out
        assert "not configured" in out

    def test_status_seeded(self):
        _seed_google("alice@example.com")
        rc, out, _err = _run("connectors", "status")
        assert rc == 0
        assert "alice@example.com" in out
        assert "google" in out

    def test_status_json(self):
        sentinel_token = "TOKEN-MUST-NOT-LEAK-12345"
        rc, out, _err = _run("connectors", "status", "--json")
        assert rc == 0
        rows = json.loads(out)
        assert any(row["id"] == "google" for row in rows)
        # Credentials must not appear in the output.
        assert sentinel_token not in out
        assert "refresh_token" not in out


class TestGrants:
    def test_grants_grant_then_list(self):
        rc, _out, _err = _run(
            "connectors",
            "grants",
            "grant",
            "google",
            "builtin:chat",
            "--scopes",
            "gmail.readonly",
        )
        assert rc == 0

        rc2, out2, _err2 = _run("connectors", "grants", "list", "google")
        assert rc2 == 0
        assert "builtin:chat" in out2
        assert "gmail.readonly" in out2

    def test_grants_revoke(self):
        _run(
            "connectors",
            "grants",
            "grant",
            "google",
            "builtin:chat",
            "--scopes",
            "gmail.readonly",
        )
        rc, _out, _err = _run(
            "connectors", "grants", "revoke", "google", "builtin:chat"
        )
        assert rc == 0
        rc2, out2, _err2 = _run("connectors", "grants", "list", "google")
        assert "No grants" in out2 or "builtin:chat" not in out2

    def test_grants_list_empty_default_provider(self):
        rc, out, _err = _run("connectors", "grants", "list")
        assert rc == 0
        assert "No grants" in out


class TestConfigure:
    """``gaia connectors configure google --client-id … --client-secret …`` (#1084).

    The flags persist the OAuth *client* credentials to the same keyring slot the
    Google provider resolves from (``store.peek_provider_credentials("google")``),
    completing OAuth config WITHOUT the Agent UI and WITHOUT any live OAuth/network
    step. The actual browser login stays a separate ``gaia connectors connect``.
    """

    def test_client_id_secret_persist_to_provider_store(self, monkeypatch):
        # No env creds — the persisted keyring blob must be the sole source the
        # provider reads from afterward.
        monkeypatch.delenv("GAIA_GOOGLE_CLIENT_ID", raising=False)
        monkeypatch.delenv("GAIA_GOOGLE_CLIENT_SECRET", raising=False)

        rc, out, _err = _run(
            "connectors",
            "configure",
            "google",
            "--client-id",
            "cli-id.apps.googleusercontent.com",
            "--client-secret",
            "GOCSPX-cli",
        )
        assert rc == 0
        assert "Configured google" in out

        # Landed in the exact store the provider resolves from.
        from gaia.connectors.store import peek_provider_credentials

        creds = peek_provider_credentials("google")
        assert creds == {
            "client_id": "cli-id.apps.googleusercontent.com",
            "client_secret": "GOCSPX-cli",
        }

        # And the provider actually picks them up on next construction.
        from gaia.connectors.providers import get as get_provider

        prov = get_provider("google")
        assert prov.client_id == "cli-id.apps.googleusercontent.com"
        assert prov.client_secret == "GOCSPX-cli"

    def test_client_id_secret_does_not_start_oauth_flow(self, monkeypatch):
        # AC: no live OAuth/network. The credential-persist path must NOT invoke
        # the PKCE flow starter (which opens a browser + loopback server).
        monkeypatch.delenv("GAIA_GOOGLE_CLIENT_ID", raising=False)
        called = {"start": False}

        def _boom(*_a, **_k):
            called["start"] = True
            raise AssertionError("start_authorization must not run on configure")

        monkeypatch.setattr("gaia.connectors.flow.start_authorization", _boom)

        rc, _out, _err = _run(
            "connectors",
            "configure",
            "google",
            "--client-id",
            "id.apps.googleusercontent.com",
            "--client-secret",
            "GOCSPX-x",
        )
        assert rc == 0
        assert called["start"] is False

    def test_secret_not_echoed_to_stdout(self, monkeypatch):
        monkeypatch.delenv("GAIA_GOOGLE_CLIENT_ID", raising=False)
        rc, out, _err = _run(
            "connectors",
            "configure",
            "google",
            "--client-id",
            "id.apps.googleusercontent.com",
            "--client-secret",
            "GOCSPX-super-secret",
        )
        assert rc == 0
        assert "GOCSPX-super-secret" not in out

    def test_client_id_without_secret_is_usage_error(self):
        rc, _out, err = _run(
            "connectors",
            "configure",
            "google",
            "--client-id",
            "id.apps.googleusercontent.com",
        )
        assert rc == 2
        assert "client-secret" in err

    def test_client_secret_without_id_is_usage_error(self):
        rc, _out, err = _run(
            "connectors",
            "configure",
            "google",
            "--client-secret",
            "GOCSPX-x",
        )
        assert rc == 2
        assert "client-id" in err

    def test_keyring_failure_surfaces_as_connectors_error(self, monkeypatch):
        # Fail-loudly: a keyring write failure must propagate as a
        # ConnectorsError (exit 5), never a silent success.
        from gaia.connectors.errors import ConnectorsError

        def _boom(*_a, **_k):
            raise ConnectorsError("Keyring set_password failed: backend locked")

        monkeypatch.setattr("gaia.connectors.store.save_provider_credentials", _boom)
        rc, _out, err = _run(
            "connectors",
            "configure",
            "google",
            "--client-id",
            "id.apps.googleusercontent.com",
            "--client-secret",
            "GOCSPX-x",
        )
        assert rc == 5
        assert "Connectors error" in err

    def test_unknown_connector_returns_exit_1(self):
        rc, _out, err = _run(
            "connectors",
            "configure",
            "does-not-exist",
            "--client-id",
            "id.apps.googleusercontent.com",
            "--client-secret",
            "GOCSPX-x",
        )
        assert rc == 1
        assert "unknown connector" in err

    def test_client_id_with_set_is_usage_error(self):
        rc, _out, err = _run(
            "connectors",
            "configure",
            "google",
            "--client-id",
            "id.apps.googleusercontent.com",
            "--client-secret",
            "GOCSPX-x",
            "--set",
            "FOO=bar",
        )
        assert rc == 2
        assert "--set" in err or "--json" in err

    def test_microsoft_and_microsoft_work_keep_distinct_credentials(self):
        # A1 (CRITICAL) driven through the real CLI entry point: configuring
        # BOTH Microsoft connectors must never let one clobber the other's
        # stored client id — this is the exact failure oauth_provider_ref
        # collision would produce.
        rc1, out1, _ = _run(
            "connectors",
            "configure",
            "microsoft",
            "--client-id",
            "personal-client-id",
            "--client-secret",
            "unused-secret-a",
        )
        rc2, out2, _ = _run(
            "connectors",
            "configure",
            "microsoft_work",
            "--client-id",
            "work-client-id",
            "--client-secret",
            "unused-secret-b",
        )
        assert rc1 == 0 and "Configured microsoft" in out1
        assert rc2 == 0 and "Configured microsoft_work" in out2

        from gaia.connectors.store import peek_provider_credentials

        assert peek_provider_credentials("microsoft")["client_id"] == (
            "personal-client-id"
        )
        assert peek_provider_credentials("microsoft_work")["client_id"] == (
            "work-client-id"
        )

        from gaia.connectors.providers import get as get_provider

        personal = get_provider("microsoft")
        work = get_provider("microsoft_work")
        assert personal.client_id == "personal-client-id"
        assert work.client_id == "work-client-id"
        assert personal.tenant == "consumers"
        assert work.tenant == "organizations"


class TestConfigureSecretlessPublicClient:
    """#1638: ``configure`` must not demand --client-secret for providers
    whose token endpoint rejects one (Microsoft/Entra public PKCE clients).
    Google's requirement is unchanged.
    """

    def test_configure_microsoft_without_secret_succeeds(self, monkeypatch):
        monkeypatch.delenv("GAIA_MICROSOFT_CLIENT_ID", raising=False)
        monkeypatch.delenv("GAIA_MICROSOFT_CLIENT_SECRET", raising=False)

        rc, out, _err = _run(
            "connectors",
            "configure",
            "microsoft",
            "--client-id",
            "cli-test-guid",
        )
        assert rc == 0
        assert "Configured microsoft" in out

        from gaia.connectors.store import peek_provider_credentials

        creds = peek_provider_credentials("microsoft")
        # Exact equality, not truthiness: a loose `not creds["client_secret"]`
        # check would pass for both "" and the None this fix must prevent.
        assert creds == {"client_id": "cli-test-guid", "client_secret": ""}

    def test_configure_google_without_secret_still_exits_2(self):
        rc, _out, err = _run(
            "connectors",
            "configure",
            "google",
            "--client-id",
            "id.apps.googleusercontent.com",
        )
        assert rc == 2
        assert "client-secret" in err
        assert "Google" in err or "google" in err

    def test_client_secret_without_id_still_exits_2(self):
        # Unchanged regardless of provider: --client-secret alone is a
        # usage error.
        rc, _out, err = _run(
            "connectors",
            "configure",
            "microsoft",
            "--client-secret",
            "should-not-be-sent",
        )
        assert rc == 2
        assert "client-id" in err

    def test_configure_then_provider_resolves_without_env(self, monkeypatch):
        """The actual payoff (#1638): after `configure microsoft
        --client-id`, the provider resolves the id with no env var set —
        proven at the provider-construction seam, not through `connect`
        (which mocks start_authorization and would pass either way)."""
        monkeypatch.delenv("GAIA_MICROSOFT_CLIENT_ID", raising=False)
        monkeypatch.delenv("GAIA_MICROSOFT_CLIENT_SECRET", raising=False)

        rc, _out, _err = _run(
            "connectors", "configure", "microsoft", "--client-id", "cli-test-guid"
        )
        assert rc == 0

        _registry.clear()
        from gaia.connectors.providers import get as get_provider

        assert get_provider("microsoft").client_id == "cli-test-guid"

    def test_cli_reads_the_shared_constant_not_a_copy(self, monkeypatch):
        """Structural guard: the CLI must consult
        oauth_pkce.PROVIDERS_REQUIRING_CLIENT_SECRET directly, not a copy
        of the rule, so flipping the shared constant moves both providers'
        behavior together."""
        from gaia.connectors import oauth_pkce

        monkeypatch.setattr(
            oauth_pkce, "PROVIDERS_REQUIRING_CLIENT_SECRET", frozenset({"microsoft"})
        )
        assert _run("connectors", "configure", "microsoft", "--client-id", "x")[0] == 2
        assert _run("connectors", "configure", "google", "--client-id", "y")[0] == 0

    def test_help_no_longer_implies_a_universal_secret_requirement(self):
        rc, out, _err = _run("connectors", "configure", "--help")
        assert rc == 0
        assert "requires --client-secret" not in out
        assert "google" in out.lower()


class TestDisconnect:
    def test_disconnect_idempotent(self):
        rc, _out, _err = _run("connectors", "disconnect", "google")
        # Idempotent — works even when nothing to disconnect.
        assert rc == 0


class TestMissingSubcommand:
    def test_no_subcommand_returns_exit_2(self):
        rc, _out, _err = _run("connectors")
        assert rc == 2


class TestActivations:
    """Activations apply to MCP-server connectors only (#1005). All tests
    here use ``mcp-github`` (a real MCP catalog entry); OAuth rejection
    is covered by :class:`TestActivationsRejectOauth` below.
    """

    def test_activations_list_empty(self):
        rc, out, _err = _run("connectors", "activations", "list", "mcp-github")
        assert rc == 0
        assert "No activations" in out

    def test_activate_with_explicit_scopes_auto_grants(self):
        rc, out, _err = _run(
            "connectors",
            "activations",
            "activate",
            "mcp-github",
            "builtin:chat",
            "--scopes",
            "use",
        )
        assert rc == 0
        assert "Auto-granted" in out
        assert "Activated mcp-github for builtin:chat" in out

        # The grant landed too — visible via the grants subcommand.
        rc2, out2, _err2 = _run("connectors", "grants", "list", "mcp-github")
        assert rc2 == 0
        assert "builtin:chat" in out2
        assert "use" in out2

        # List shows the activation.
        rc3, out3, _err3 = _run("connectors", "activations", "list", "mcp-github")
        assert rc3 == 0
        assert "builtin:chat: active" in out3

    def test_activate_without_grant_or_scopes_returns_exit_3(self):
        # ConfigurationError → exit code 3 per the shared error-class table.
        rc, _out, err = _run(
            "connectors", "activations", "activate", "mcp-github", "builtin:chat"
        )
        assert rc == 3
        assert "Configuration error" in err

    def test_activate_existing_grant_no_auto_grant_message(self):
        _run(
            "connectors",
            "grants",
            "grant",
            "mcp-github",
            "builtin:chat",
            "--scopes",
            "use",
        )
        rc, out, _err = _run(
            "connectors", "activations", "activate", "mcp-github", "builtin:chat"
        )
        assert rc == 0
        assert "Auto-granted" not in out
        assert "Activated mcp-github for builtin:chat" in out

    def test_deactivate_preserves_grant(self):
        _run(
            "connectors",
            "activations",
            "activate",
            "mcp-github",
            "builtin:chat",
            "--scopes",
            "use",
        )
        rc, out, _err = _run(
            "connectors", "activations", "deactivate", "mcp-github", "builtin:chat"
        )
        assert rc == 0
        assert "Deactivated" in out

        # Grant survives.
        rc2, out2, _err2 = _run("connectors", "grants", "list", "mcp-github")
        assert "builtin:chat" in out2
        assert "use" in out2

        # No active rows.
        rc3, out3, _err3 = _run("connectors", "activations", "list", "mcp-github")
        assert "No activations" in out3 or "builtin:chat: active" not in out3

    def test_deactivate_idempotent(self):
        rc, _out, _err = _run(
            "connectors", "activations", "deactivate", "mcp-github", "builtin:chat"
        )
        assert rc == 0

    def test_activations_list_json(self):
        _run(
            "connectors",
            "activations",
            "activate",
            "mcp-github",
            "builtin:chat",
            "--scopes",
            "use",
        )
        rc, out, _err = _run(
            "connectors", "activations", "list", "mcp-github", "--json"
        )
        assert rc == 0
        listing = json.loads(out)
        assert listing == {"mcp-github": {"builtin:chat": True}}

    def test_activations_no_subcommand_returns_exit_2(self):
        rc, _out, _err = _run("connectors", "activations")
        assert rc == 2


class TestActivationsRejectOauth:
    """#1005 follow-up — activations gate MCP tool visibility only.

    OAuth connectors like ``google`` have no MCP tool surface — their
    per-agent access is controlled by grants. The CLI must reject the
    write with the standard ConfigurationError exit code (3) so users
    don't end up with a ledger entry nothing reads.
    """

    def test_activate_on_oauth_connector_returns_exit_3(self):
        rc, _out, err = _run(
            "connectors",
            "activations",
            "activate",
            "google",
            "builtin:chat",
            "--scopes",
            "openid",
        )
        assert rc == 3
        assert "MCP-server" in err

    def test_deactivate_on_oauth_connector_returns_exit_3(self):
        rc, _out, err = _run(
            "connectors", "activations", "deactivate", "google", "builtin:chat"
        )
        assert rc == 3
        assert "MCP-server" in err
