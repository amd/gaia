# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Voice/style profile tools mixin for ``EmailTriageAgent`` (#1607).

``build_voice_profile`` samples the user's Sent mail through the mail
backend, derives a style profile (``voice_profile.analyze_sent_bodies``),
and persists it locally via ``action_store``. The agent's system prompt
picks the stored profile up on the next turn, so draft bodies composed for
``draft_reply``/``draft_forward`` match the user's own voice.

Both tools are local-only: reading Sent mail mutates nothing remote, and
the profile lives in the agent's SQLite ``state.db`` — no Sent content
leaves the device (derived features only are stored).
"""

from __future__ import annotations


from gaia_agent_email.tools.envelope import _envelope_err, _envelope_ok
from gaia_agent_email import action_store
from gaia_agent_email.gmail_backend import decode_message_body
from gaia_agent_email.verbose import log_tool_call
from gaia_agent_email.voice_profile import analyze_sent_bodies

from gaia.agents.base.tools import tool
from gaia.connectors.errors import ConnectorsError
from gaia.connectors.formatting import format_connector_error
from gaia.logger import get_logger

log = get_logger(__name__)

# Default Sent-mail sample. Big enough to smooth over one-off outliers,
# small enough to stay fast on first run (one get_message per sample).
DEFAULT_SAMPLE_SIZE = 50


def build_voice_profile_impl(
    gmail,
    db,
    *,
    mailbox: str,
    sample_size: int = DEFAULT_SAMPLE_SIZE,
    debug: bool = False,
) -> dict:
    with log_tool_call(
        "build_voice_profile",
        {"mailbox": mailbox, "sample_size": sample_size},
        debug=debug,
    ) as st:
        listing = gmail.list_messages(label_ids=["SENT"], max_results=sample_size)
        refs = listing.get("messages", [])
        if not refs:
            raise ValueError(
                f"no Sent messages found in mailbox '{mailbox}' — a voice "
                "profile needs Sent history to learn from. Send a few emails "
                "first, or connect a mailbox that has Sent mail."
            )
        bodies = []
        for ref in refs:
            msg = gmail.get_message(ref["id"])
            body, _attachments = decode_message_body(msg.get("payload") or {})
            if body.strip():
                bodies.append(body)
        profile = analyze_sent_bodies(bodies)
        action_store.save_voice_profile(db, mailbox=mailbox, profile=profile)
        st["result_summary"] = {"sample_count": profile["sample_count"]}
        return dict(profile, mailbox=mailbox)


class VoiceToolsMixin:
    """Registers ``build_voice_profile`` and ``clear_voice_profile``."""

    def _register_voice_tools(self) -> None:
        db = self
        agent = self
        debug_flag = bool(getattr(self.config, "debug", False))

        @tool
        def build_voice_profile(
            sample_size: int = DEFAULT_SAMPLE_SIZE, mailbox: str = ""
        ) -> str:
            """Learn the user's writing voice from their Sent mail.

            Samples recent Sent messages, derives a local style profile
            (greeting, sign-off, typical length, formality), and stores it
            on-device. Future draft bodies should match this profile. Reads
            mail only — sends nothing, mutates nothing remote.

            ``sample_size`` (optional, default 50) caps how many recent Sent
            messages are analyzed. ``mailbox`` (optional) selects which
            connected mailbox to learn from; defaults to the primary one.
            """
            try:
                if sample_size <= 0:
                    return _envelope_err(
                        f"sample_size must be positive, got {sample_size}"
                    )
                if not agent._backends:
                    return _envelope_err(
                        "no mailbox is connected — connect Gmail or Outlook "
                        "via `gaia connectors` first, then retry"
                    )
                provider = mailbox or next(iter(agent._backends))
                backend = agent._backends.get(provider)
                if backend is None:
                    return _envelope_err(
                        f"mailbox '{provider}' is not connected — connected "
                        f"mailboxes: {sorted(agent._backends)}"
                    )
                profile = build_voice_profile_impl(
                    backend,
                    db,
                    mailbox=provider,
                    sample_size=sample_size,
                    debug=debug_flag,
                )
                return _envelope_ok(profile)
            except ConnectorsError as exc:
                return _envelope_err(format_connector_error(exc))
            except Exception as exc:
                log.exception("email tool error: %s", type(exc).__name__)
                return _envelope_err(f"{type(exc).__name__}: {exc}")

        @tool
        def clear_voice_profile(mailbox: str = "") -> str:
            """Forget the learned voice profile.

            Removes the stored style profile so drafts go back to neutral
            phrasing. ``mailbox`` (optional) clears one mailbox's profile;
            default clears all.
            """
            try:
                action_store.delete_voice_profile(db, mailbox=mailbox or None)
                return _envelope_ok({"cleared": mailbox or "all"})
            except Exception as exc:
                log.exception("email tool error: %s", type(exc).__name__)
                return _envelope_err(f"{type(exc).__name__}: {exc}")
