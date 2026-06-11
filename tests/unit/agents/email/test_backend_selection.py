# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Backend-selection wiring tests (#1275).

The Email Triage Agent must operate on either Gmail or a personal Outlook.com
mailbox depending on which provider the user connected — WITHOUT breaking the
shipped Gmail path. Selection is owned by ``EmailAgentConfig`` (the config /
factory seam) so the agent stays provider-agnostic.

Two axes are covered:
  1. ``EmailAgentConfig.resolve_mail_backend()`` returns the right live backend
     for ``mail_provider`` ("google" -> Gmail, "microsoft" -> Outlook), and an
     injected test/eval backend always wins (the eval seam).
  2. ``EmailTriageAgent`` wires ``self._gmail`` through that resolver and
     declares a ``microsoft`` connector requirement alongside the untouched
     ``google`` one.

No network or OAuth — backends are constructed but never called.
"""

from __future__ import annotations

# EmailTriageAgent ships as the standalone gaia-agent-email wheel (#1102);
# skip when a framework-only env lacks it.
import pytest  # noqa: E402

pytest.importorskip("gaia_agent_email")  # noqa: E402
from gaia_agent_email.calendar_backend import CalendarBackend, LiveCalendarBackend
from gaia_agent_email.config import ConfigurationError, EmailAgentConfig
from gaia_agent_email.gmail_backend import GmailBackend, LiveGmailBackend
from gaia_agent_email.outlook_backend import LiveOutlookBackend
from gaia_agent_email.outlook_calendar_backend import LiveOutlookCalendarBackend


class TestResolveMailBackend:
    def test_default_provider_is_google_gmail(self):
        cfg = EmailAgentConfig()
        backend = cfg.resolve_mail_backend()
        assert isinstance(backend, LiveGmailBackend)

    def test_microsoft_provider_resolves_to_outlook(self):
        cfg = EmailAgentConfig(mail_provider="microsoft")
        backend = cfg.resolve_mail_backend()
        assert isinstance(backend, LiveOutlookBackend)

    def test_microsoft_backend_satisfies_gmail_protocol(self):
        # Interchangeability: the Outlook backend the selector hands back must
        # satisfy the same Protocol the tools depend on.
        cfg = EmailAgentConfig(mail_provider="microsoft")
        assert isinstance(cfg.resolve_mail_backend(), GmailBackend)

    def test_injected_gmail_backend_wins_over_provider(self):
        sentinel = object()
        cfg = EmailAgentConfig(mail_provider="google", gmail_backend=sentinel)
        assert cfg.resolve_mail_backend() is sentinel

    def test_injected_outlook_backend_wins_for_microsoft(self):
        sentinel = object()
        cfg = EmailAgentConfig(mail_provider="microsoft", outlook_backend=sentinel)
        assert cfg.resolve_mail_backend() is sentinel

    def test_unknown_provider_raises_actionable(self):
        cfg = EmailAgentConfig(mail_provider="yahoo")
        with pytest.raises(ConfigurationError) as exc:
            cfg.resolve_mail_backend()
        msg = str(exc.value)
        assert "yahoo" in msg
        # Names the supported providers so the error is actionable.
        assert "google" in msg and "microsoft" in msg

    def test_provider_is_case_insensitive(self):
        cfg = EmailAgentConfig(mail_provider="Microsoft")
        assert isinstance(cfg.resolve_mail_backend(), LiveOutlookBackend)


class TestAgentWiring:
    def _agent(self, **cfg_kwargs):
        from gaia_agent_email.agent import EmailTriageAgent

        # Inject fake backends so no live token/HTTP path is hit during
        # construction; we only assert the wiring picked the right one.
        return EmailTriageAgent

    def test_agent_routes_microsoft_to_outlook_backend(self, tmp_path, monkeypatch):
        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
        from gaia_agent_email.agent import EmailTriageAgent

        outlook_sentinel = object()
        cfg = EmailAgentConfig(
            mail_provider="microsoft",
            outlook_backend=outlook_sentinel,
            db_path=str(tmp_path / "state.db"),
            calendar_backend=object(),  # avoid live calendar token construction
        )
        agent = EmailTriageAgent(config=cfg)
        assert agent._gmail is outlook_sentinel

    def test_agent_keeps_gmail_as_default(self, tmp_path, monkeypatch):
        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
        from gaia_agent_email.agent import EmailTriageAgent

        gmail_sentinel = object()
        cfg = EmailAgentConfig(
            gmail_backend=gmail_sentinel,
            db_path=str(tmp_path / "state.db"),
            calendar_backend=object(),
        )
        agent = EmailTriageAgent(config=cfg)
        assert agent._gmail is gmail_sentinel

    def test_required_connectors_include_microsoft_and_google(self):
        from gaia_agent_email.agent import EmailTriageAgent

        ids = {c.connector_id for c in EmailTriageAgent.REQUIRED_CONNECTORS}
        # Gmail must still be declared (don't break the shipped connector) and
        # Microsoft must be added so a microsoft-connected user is grant-checked.
        assert "google" in ids
        assert "microsoft" in ids

    def test_microsoft_requirement_requests_graph_mail_scopes(self):
        from gaia_agent_email.agent import EmailTriageAgent

        ms = next(
            c
            for c in EmailTriageAgent.REQUIRED_CONNECTORS
            if c.connector_id == "microsoft"
        )
        assert any("graph.microsoft.com/Mail" in s for s in ms.scopes)


class TestResolveMailBackends:
    """Plural resolver (#1603 Phase 2): ``mail_provider`` becomes a FILTER.

    ``resolve_mail_backends()`` returns ``[(provider, backend), ...]`` for every
    CONNECTED mailbox the filter admits — so a both-connected user triages both,
    and a single-mailbox user gets exactly that one. It is connector-derived
    (consults ``connected_mailbox_providers``); the singular ``resolve_mail_backend``
    stays connector-agnostic for the existing eval seam.

    Fail-loud: a filter naming an unconnected provider, or nothing connected,
    raises ``ConfigurationError`` — never silently picks one.
    """

    def test_none_filter_both_connected_returns_two_in_order(self, monkeypatch):
        monkeypatch.setattr(
            "gaia_agent_email.config.connected_mailbox_providers",
            lambda: ["google", "microsoft"],
        )
        cfg = EmailAgentConfig(mail_provider=None)
        pairs = cfg.resolve_mail_backends()
        providers = [p for p, _ in pairs]
        assert providers == ["google", "microsoft"]
        assert isinstance(dict(pairs)["google"], LiveGmailBackend)
        assert isinstance(dict(pairs)["microsoft"], LiveOutlookBackend)

    def test_none_filter_one_connected_returns_one(self, monkeypatch):
        monkeypatch.setattr(
            "gaia_agent_email.config.connected_mailbox_providers",
            lambda: ["microsoft"],
        )
        cfg = EmailAgentConfig(mail_provider=None)
        pairs = cfg.resolve_mail_backends()
        assert [p for p, _ in pairs] == ["microsoft"]
        assert isinstance(pairs[0][1], LiveOutlookBackend)

    def test_explicit_filter_selects_only_that_provider(self, monkeypatch):
        # Both connected, but the session explicitly chose google → just google.
        monkeypatch.setattr(
            "gaia_agent_email.config.connected_mailbox_providers",
            lambda: ["google", "microsoft"],
        )
        cfg = EmailAgentConfig(mail_provider="google")
        pairs = cfg.resolve_mail_backends()
        assert [p for p, _ in pairs] == ["google"]
        assert isinstance(pairs[0][1], LiveGmailBackend)

    def test_explicit_filter_unconnected_raises_actionable(self, monkeypatch):
        # Session selected microsoft but only google is connected → fail loud.
        monkeypatch.setattr(
            "gaia_agent_email.config.connected_mailbox_providers",
            lambda: ["google"],
        )
        cfg = EmailAgentConfig(mail_provider="microsoft")
        with pytest.raises(ConfigurationError) as exc:
            cfg.resolve_mail_backends()
        msg = str(exc.value)
        assert "microsoft" in msg
        # Names what IS connected so the user can course-correct.
        assert "google" in msg

    def test_nothing_connected_raises_actionable(self, monkeypatch):
        monkeypatch.setattr(
            "gaia_agent_email.config.connected_mailbox_providers",
            lambda: [],
        )
        cfg = EmailAgentConfig(mail_provider=None)
        with pytest.raises(ConfigurationError) as exc:
            cfg.resolve_mail_backends()
        msg = str(exc.value)
        assert "connect" in msg.lower()

    def test_injected_backend_honored_per_provider(self, monkeypatch):
        # The eval seam: an injected backend for the connected provider wins
        # over building a live one.
        monkeypatch.setattr(
            "gaia_agent_email.config.connected_mailbox_providers",
            lambda: ["google"],
        )
        sentinel = object()
        cfg = EmailAgentConfig(mail_provider=None, gmail_backend=sentinel)
        pairs = cfg.resolve_mail_backends()
        assert pairs == [("google", sentinel)]

    def test_injected_outlook_backend_honored_for_microsoft(self, monkeypatch):
        monkeypatch.setattr(
            "gaia_agent_email.config.connected_mailbox_providers",
            lambda: ["microsoft"],
        )
        sentinel = object()
        cfg = EmailAgentConfig(mail_provider=None, outlook_backend=sentinel)
        pairs = cfg.resolve_mail_backends()
        assert pairs == [("microsoft", sentinel)]

    def test_singular_resolver_unchanged_default_is_gmail(self):
        # D1 must NOT regress the connector-agnostic singular seam: default
        # (mail_provider=None) still builds Gmail without any connectivity mock.
        cfg = EmailAgentConfig()
        assert isinstance(cfg.resolve_mail_backend(), LiveGmailBackend)


class TestResolveCalendarBackend:
    """Calendar-backend selection (#1276), mirroring ``resolve_mail_backend``.

    The shipped Google calendar path must stay the default; ``microsoft`` must
    route to the Outlook calendar backend; an injected backend always wins.
    """

    def test_default_provider_is_google_calendar(self):
        cfg = EmailAgentConfig()
        backend = cfg.resolve_calendar_backend()
        assert isinstance(backend, LiveCalendarBackend)

    def test_microsoft_provider_resolves_to_outlook_calendar(self):
        cfg = EmailAgentConfig(calendar_provider="microsoft")
        backend = cfg.resolve_calendar_backend()
        assert isinstance(backend, LiveOutlookCalendarBackend)

    def test_microsoft_calendar_satisfies_calendar_protocol(self):
        cfg = EmailAgentConfig(calendar_provider="microsoft")
        assert isinstance(cfg.resolve_calendar_backend(), CalendarBackend)

    def test_injected_calendar_backend_wins_over_provider(self):
        sentinel = object()
        cfg = EmailAgentConfig(calendar_provider="microsoft", calendar_backend=sentinel)
        assert cfg.resolve_calendar_backend() is sentinel

    def test_unknown_calendar_provider_raises_actionable(self):
        cfg = EmailAgentConfig(calendar_provider="yahoo")
        with pytest.raises(ConfigurationError) as exc:
            cfg.resolve_calendar_backend()
        msg = str(exc.value)
        assert "yahoo" in msg
        assert "google" in msg and "microsoft" in msg

    def test_calendar_provider_is_case_insensitive(self):
        cfg = EmailAgentConfig(calendar_provider="Microsoft")
        assert isinstance(cfg.resolve_calendar_backend(), LiveOutlookCalendarBackend)

    def test_calendar_provider_defaults_to_mail_provider_when_unset(self):
        # Convenience: a microsoft-only user who set mail_provider="microsoft"
        # should get the Outlook calendar too without separately setting
        # calendar_provider. (Default field value tracks mail_provider.)
        cfg = EmailAgentConfig(mail_provider="microsoft")
        assert isinstance(cfg.resolve_calendar_backend(), LiveOutlookCalendarBackend)


class TestAgentCalendarWiring:
    def test_agent_routes_microsoft_to_outlook_calendar(self, tmp_path, monkeypatch):
        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
        from gaia_agent_email.agent import EmailTriageAgent

        cfg = EmailAgentConfig(
            mail_provider="microsoft",
            calendar_provider="microsoft",
            outlook_backend=object(),  # avoid live mail token construction
            db_path=str(tmp_path / "state.db"),
        )
        agent = EmailTriageAgent(config=cfg)
        assert isinstance(agent._calendar, LiveOutlookCalendarBackend)

    def test_agent_keeps_google_calendar_as_default(self, tmp_path, monkeypatch):
        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
        from gaia_agent_email.agent import EmailTriageAgent

        cfg = EmailAgentConfig(
            gmail_backend=object(),
            db_path=str(tmp_path / "state.db"),
        )
        agent = EmailTriageAgent(config=cfg)
        assert isinstance(agent._calendar, LiveCalendarBackend)

    def test_injected_calendar_backend_still_wins_in_agent(self, tmp_path, monkeypatch):
        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
        from gaia_agent_email.agent import EmailTriageAgent

        cal_sentinel = object()
        cfg = EmailAgentConfig(
            gmail_backend=object(),
            calendar_backend=cal_sentinel,
            db_path=str(tmp_path / "state.db"),
        )
        agent = EmailTriageAgent(config=cfg)
        assert agent._calendar is cal_sentinel
