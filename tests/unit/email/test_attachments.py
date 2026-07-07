# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Attachment handling (#1542, schema 2.2).

Read path: a message with attachments exposes their metadata through the
read tools (via ``decode_message_body``). Draft/send path: the reply tools
accept attachments and pass them to the backend send call — asserted
against the recorded call shape (boundary validity, not just invocation)
and against the actual RFC 2822 / Graph payloads the live backends build.
"""

from __future__ import annotations

import base64
import email
import mailbox
from email.message import EmailMessage as MimeMessage
from pathlib import Path

import pytest

pytest.importorskip("gaia_agent_email")

from gaia_agent_email.gmail_backend import _build_rfc822, _pad_b64  # noqa: E402
from gaia_agent_email.outlook_backend import _build_graph_message  # noqa: E402
from gaia_agent_email.tools.read_tools import get_message_impl  # noqa: E402
from gaia_agent_email.tools.reply_tools import (  # noqa: E402
    _load_attachment_files,
    draft_reply_impl,
    send_now_impl,
)

from tests.fixtures.email.fake_gmail import FakeGmailBackend  # noqa: E402

PDF_BYTES = b"%PDF-1.4 fake report content for attachment tests"


def _mbox_with_attachment(tmp_path: Path) -> Path:
    """One-message mbox: text body + a PDF attachment."""
    msg = MimeMessage()
    msg["From"] = "Alice Example <alice@example.com>"
    msg["To"] = "user@example.com"
    msg["Subject"] = "Q3 report attached"
    msg["Date"] = "Mon, 01 Jun 2026 10:00:00 +0000"
    msg["Message-ID"] = "<report-1@example.com>"
    msg.set_content("Please review the attached report before Friday.")
    msg.add_attachment(
        PDF_BYTES,
        maintype="application",
        subtype="pdf",
        filename="q3-report.pdf",
    )
    path = tmp_path / "inbox.mbox"
    mb = mailbox.mbox(str(path))
    mb.add(msg)
    mb.flush()
    mb.close()
    return path


class _DBStub:
    """Absorbs the action-store audit writes the impls make."""

    def execute_query(self, *args, **kwargs):
        return []


@pytest.fixture
def db(monkeypatch):
    from gaia_agent_email import action_store

    monkeypatch.setattr(action_store, "record_draft", lambda *a, **k: None)
    monkeypatch.setattr(action_store, "mark_draft_sent", lambda *a, **k: None)
    return _DBStub()


# ---------------------------------------------------------------------------
# Read path — metadata exposure
# ---------------------------------------------------------------------------


def test_read_exposes_attachment_metadata(tmp_path):
    backend = FakeGmailBackend(_mbox_with_attachment(tmp_path))
    (mid,) = backend._messages.keys()

    out = get_message_impl(backend, message_id=mid)

    atts = out["attachments"]
    assert len(atts) == 1
    att = atts[0]
    assert att["filename"] == "q3-report.pdf"
    assert att["mime_type"] == "application/pdf"
    assert att["size_bytes"] == len(PDF_BYTES)
    # The fake synthesizes a deterministic Gmail-style attachmentId handle.
    assert att["attachment_id"], "read path must expose the provider handle"
    # The body itself still decodes — the attachment part must not leak in.
    assert "attached report" in out["body"]


def test_triage_gmail_message_exposes_attachment_metadata(tmp_path):
    from gaia_agent_email.api_routes import EmailTriageService

    backend = FakeGmailBackend(_mbox_with_attachment(tmp_path))
    (mid,) = backend._messages.keys()

    result = EmailTriageService().triage_gmail_message(
        backend.get_message(mid), principal_email="user@example.com"
    )

    assert [a.filename for a in result.attachments] == ["q3-report.pdf"]
    assert result.attachments[0].mime_type == "application/pdf"
    assert result.attachments[0].size_bytes == len(PDF_BYTES)


# ---------------------------------------------------------------------------
# Draft/send path — attachments reach the backend send call
# ---------------------------------------------------------------------------


def test_send_now_impl_passes_attachments_to_backend_send(tmp_path, db):
    backend = FakeGmailBackend(_mbox_with_attachment(tmp_path))
    atts = [
        {"filename": "q3.pdf", "mime_type": "application/pdf", "content": PDF_BYTES}
    ]

    out = send_now_impl(
        backend,
        db,
        to="bob@example.com",
        subject="Report",
        body="See attached.",
        attachments=atts,
    )

    assert out["sent"] is True
    assert out["attachments"] == ["q3.pdf"]
    (call,) = [c for c in backend._transport.calls if c[0] == "send_message"]
    assert call[1]["attachments"] == atts


def test_draft_reply_impl_passes_attachments_to_create_draft(tmp_path, db):
    backend = FakeGmailBackend(_mbox_with_attachment(tmp_path))
    (mid,) = backend._messages.keys()
    atts = [
        {"filename": "q3.pdf", "mime_type": "application/pdf", "content": PDF_BYTES}
    ]

    out = draft_reply_impl(
        backend, db, message_id=mid, body="Here it is.", attachments=atts
    )

    assert out["attachments"] == ["q3.pdf"]
    (call,) = [c for c in backend._transport.calls if c[0] == "create_draft"]
    assert call[1]["attachments"] == atts


# ---------------------------------------------------------------------------
# Tool-side file loading — fail loudly, never a silent skip
# ---------------------------------------------------------------------------


def test_load_attachment_files_happy_path(tmp_path):
    f = tmp_path / "notes.csv"
    f.write_bytes(b"a,b\n1,2\n")
    (att,) = _load_attachment_files(str(f))
    assert att["filename"] == "notes.csv"
    assert att["mime_type"] == "text/csv"
    assert att["content"] == b"a,b\n1,2\n"


def test_load_attachment_files_empty_string_is_none():
    assert _load_attachment_files("") is None


def test_load_attachment_files_missing_file_raises(tmp_path):
    with pytest.raises(ValueError, match="not found"):
        _load_attachment_files(str(tmp_path / "nope.pdf"))


def test_load_attachment_files_unknown_extension_raises(tmp_path):
    f = tmp_path / "blob.zzz9"
    f.write_bytes(b"data")
    with pytest.raises(ValueError, match="MIME type"):
        _load_attachment_files(str(f))


def test_load_attachment_files_empty_file_raises(tmp_path):
    f = tmp_path / "empty.pdf"
    f.write_bytes(b"")
    with pytest.raises(ValueError, match="empty"):
        _load_attachment_files(str(f))


def test_load_attachment_files_oversize_raises(tmp_path, monkeypatch):
    from gaia_agent_email.tools import reply_tools

    monkeypatch.setattr(reply_tools, "_MAX_ATTACHMENT_BYTES", 8)
    f = tmp_path / "big.pdf"
    f.write_bytes(b"123456789")
    with pytest.raises(ValueError, match="maximum"):
        _load_attachment_files(str(f))


# ---------------------------------------------------------------------------
# Live-backend payload shapes (the contract at the provider boundary)
# ---------------------------------------------------------------------------


def test_build_rfc822_with_attachment_is_valid_multipart():
    raw = _build_rfc822(
        to="bob@example.com",
        subject="Report",
        body="See attached.",
        attachments=[
            {
                "filename": "q3.pdf",
                "mime_type": "application/pdf",
                "content": PDF_BYTES,
            }
        ],
    )
    parsed = email.message_from_bytes(base64.urlsafe_b64decode(_pad_b64(raw)))
    assert parsed.get_content_type() == "multipart/mixed"
    parts = parsed.get_payload()
    assert parts[0].get_content_type() == "text/plain"
    assert "See attached." in parts[0].get_payload()
    att = parts[1]
    assert att.get_content_type() == "application/pdf"
    assert att.get_filename() == "q3.pdf"
    # Round-trip: the transferred base64 decodes back to the exact bytes.
    assert att.get_payload(decode=True) == PDF_BYTES


def test_build_rfc822_without_attachments_stays_single_part():
    raw = _build_rfc822(to="bob@example.com", subject="Hi", body="Plain.")
    parsed = email.message_from_bytes(base64.urlsafe_b64decode(_pad_b64(raw)))
    assert not parsed.is_multipart()
    assert parsed.get_content_type() == "text/plain"


def test_build_rfc822_rejects_header_injection_in_filename():
    with pytest.raises(ValueError, match="filename"):
        _build_rfc822(
            to="bob@example.com",
            subject="Hi",
            body="x",
            attachments=[
                {
                    "filename": 'evil"\r\nBcc: victim@example.com',
                    "mime_type": "application/pdf",
                    "content": b"x",
                }
            ],
        )


def test_build_graph_message_maps_file_attachments():
    msg = _build_graph_message(
        to="bob@example.com",
        subject="Report",
        body="See attached.",
        attachments=[
            {
                "filename": "q3.pdf",
                "mime_type": "application/pdf",
                "content": PDF_BYTES,
            }
        ],
    )
    (att,) = msg["attachments"]
    assert att["@odata.type"] == "#microsoft.graph.fileAttachment"
    assert att["name"] == "q3.pdf"
    assert att["contentType"] == "application/pdf"
    assert base64.b64decode(att["contentBytes"]) == PDF_BYTES


def test_build_graph_message_rejects_oversize_attachment():
    with pytest.raises(ValueError, match="3 MB"):
        _build_graph_message(
            to="bob@example.com",
            subject="Big",
            body="x",
            attachments=[
                {
                    "filename": "huge.bin.pdf",
                    "mime_type": "application/pdf",
                    "content": b"\0" * (3 * 1024 * 1024 + 1),
                }
            ],
        )
