# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Tests for the email agent HTML spec generator (issue #1263).

The spec must:
- Be a self-contained HTML page (no external script/link assets)
- Document all 3 REST endpoints
- Render key contract field names sourced from the contract models
- Show SCHEMA_VERSION
- Include the four category values from EmailCategory
"""

from __future__ import annotations

import re

# EmailTriageAgent ships as the standalone gaia-agent-email wheel (#1102);
# skip when a framework-only env lacks it.
import pytest  # noqa: E402

pytest.importorskip("gaia_agent_email")  # noqa: E402
from gaia_agent_email.contract import (
    SCHEMA_VERSION,
    ActionItem,
    DraftReply,
    EmailCategory,
    EmailMessage,
    EmailTriageRequest,
    EmailTriageResponse,
    EmailTriageResult,
)
from gaia_agent_email.spec_html import (
    _required_badge,
    _type_label,
    render_endpoint_spec_html,
)


def _html() -> str:
    """Call once and cache in tests that need the output multiple times."""
    return render_endpoint_spec_html()


# ---------------------------------------------------------------------------
# Basic well-formedness
# ---------------------------------------------------------------------------


def test_returns_nonempty_string():
    html = _html()
    assert isinstance(html, str)
    assert len(html) > 0


def test_starts_with_doctype():
    html = _html()
    assert html.strip().lower().startswith("<!doctype html")


def test_contains_html_root_element():
    html = _html()
    assert "<html" in html.lower()


# ---------------------------------------------------------------------------
# Endpoint paths
# ---------------------------------------------------------------------------


def test_triage_endpoint_present():
    assert "/v1/email/triage" in _html()


def test_draft_endpoint_present():
    assert "/v1/email/draft" in _html()


def test_send_endpoint_present():
    assert "/v1/email/send" in _html()


def test_init_endpoint_present():
    # Readiness preflight (#1795) must be documented on the spec page.
    html = _html()
    assert "/v1/email/init" in html
    assert "InitResponse" in html
    # The GET method badge must render (init is the only GET endpoint shown).
    assert ">GET<" in html


def test_provision_verb_documented():
    # The POST provisioning verb (#1795 follow-up) streams progress and is not in
    # the JSON OpenAPI, so the HTML spec is where it must be documented.
    html = _html()
    assert "stream terminal-style progress" in html.lower()
    assert "text/plain" in html


# ---------------------------------------------------------------------------
# Authentication (#1706) — the caller-auth posture must be documented on the
# spec page so integrators know the sidecar requires a per-session token.
# ---------------------------------------------------------------------------


def test_authentication_section_present():
    html = _html()
    assert "Authentication" in html
    assert "GAIA_EMAIL_SIDECAR_TOKEN" in html
    assert "Authorization: Bearer" in html


def test_authentication_documents_status_codes():
    html = _html()
    # Token-missing 401, rebinding Host 400, drive-by Origin 403.
    assert "401" in html
    assert "400" in html
    assert "403" in html


# ---------------------------------------------------------------------------
# Contract field names — sourced from the models so a contract change that
# drops a field will break this test, not slip through silently.
# ---------------------------------------------------------------------------


def test_request_field_schema_version_present():
    # EmailTriageRequest has schema_version
    assert "schema_version" in EmailTriageRequest.model_fields
    assert "schema_version" in _html()


def test_result_field_category_present():
    assert "category" in EmailTriageResult.model_fields
    assert "category" in _html()


def test_result_field_is_phishing_present():
    assert "is_phishing" in EmailTriageResult.model_fields
    assert "is_phishing" in _html()


def test_result_field_summary_present():
    assert "summary" in EmailTriageResult.model_fields
    assert "summary" in _html()


def test_result_field_draft_present():
    assert "draft" in EmailTriageResult.model_fields
    assert "draft" in _html()


def test_result_field_action_items_present():
    assert "action_items" in EmailTriageResult.model_fields
    assert "action_items" in _html()


def test_response_field_request_kind_present():
    assert "request_kind" in EmailTriageResponse.model_fields
    assert "request_kind" in _html()


def test_draft_reply_field_to_present():
    assert "to" in DraftReply.model_fields
    assert "to" in _html()


def test_draft_reply_field_subject_present():
    assert "subject" in DraftReply.model_fields
    assert "subject" in _html()


def test_draft_reply_field_body_present():
    assert "body" in DraftReply.model_fields
    assert "body" in _html()


def test_action_item_field_description_present():
    assert "description" in ActionItem.model_fields
    assert "description" in _html()


# ---------------------------------------------------------------------------
# Category values — sourced from EmailCategory enum
# ---------------------------------------------------------------------------


def test_all_category_values_present():
    html = _html()
    for cat in EmailCategory:
        assert cat.value in html, f"Category value {cat.value!r} missing from spec"


def test_category_urgent_present():
    assert EmailCategory.URGENT.value in _html()


def test_category_needs_response_present():
    assert EmailCategory.NEEDS_RESPONSE.value in _html()


def test_category_fyi_present():
    assert EmailCategory.FYI.value in _html()


def test_category_promotional_present():
    assert EmailCategory.PROMOTIONAL.value in _html()


def test_category_personal_present():
    assert EmailCategory.PERSONAL.value in _html()


# ---------------------------------------------------------------------------
# Schema version
# ---------------------------------------------------------------------------


def test_schema_version_rendered():
    assert SCHEMA_VERSION in _html()


# ---------------------------------------------------------------------------
# Self-contained — no external assets
# ---------------------------------------------------------------------------


def test_no_external_script_src():
    html = _html()
    # Any <script src="http..."> or <script src="//..."> is forbidden
    external_script = re.search(r'<script[^>]+src=["\']https?://', html, re.IGNORECASE)
    assert external_script is None, "Found external script src in spec HTML"


def test_no_external_link_href():
    html = _html()
    external_link = re.search(r'<link[^>]+href=["\']https?://', html, re.IGNORECASE)
    assert external_link is None, "Found external link href in spec HTML"


def test_no_external_protocol_relative_script():
    html = _html()
    proto_relative = re.search(r'<script[^>]+src=["\']\/\/', html, re.IGNORECASE)
    assert proto_relative is None, "Found protocol-relative script src in spec HTML"


# ---------------------------------------------------------------------------
# Required / optional badges — must reflect pydantic's is_required(), not a
# default-is-None heuristic (which mislabels every required field).
# ---------------------------------------------------------------------------


def _row_badge(html: str, field_name: str) -> str:
    """Return 'required' or 'optional' for the FIRST rendered row of
    ``field_name``, or '' if no badge is found.
    """
    m = re.search(
        rf"<td>{re.escape(field_name)}" rf'<span class="(required|optional)-badge">',
        html,
    )
    return m.group(1) if m else ""


def test_required_badge_helper_marks_required_field_required():
    # category has no default (PydanticUndefined) → is_required() is True.
    badge = _required_badge(EmailTriageResult.model_fields["category"])
    assert "required" in badge
    assert "optional" not in badge


def test_required_badge_helper_marks_optional_field_optional():
    # thread_id is Optional[str] with default None → is_required() is False.
    badge = _required_badge(EmailMessage.model_fields["thread_id"])
    assert "optional" in badge
    assert ">required<" not in badge


def test_required_badge_helper_marks_default_factory_field_optional():
    # action_items uses default_factory; its default is PydanticUndefined but
    # is_required() is False — the old `default is not None` heuristic got this
    # wrong. Guard against regression.
    badge = _required_badge(EmailTriageResult.model_fields["action_items"])
    assert "optional" in badge


def test_required_field_renders_required_badge_in_html():
    # A known-required field (category) must show the 'required' badge.
    assert _row_badge(_html(), "category") == "required"


def test_optional_field_renders_optional_badge_in_html():
    # A known-optional field unique to one model (is_spam, only in
    # EmailTriageResult, default False) must show the 'optional' badge.
    # (thread_id is ambiguous — it's required on ThreadInput but optional on
    # EmailMessage — so it's a poor page-level probe.)
    assert _row_badge(_html(), "is_spam") == "optional"


def test_send_response_sent_field_is_optional_in_html():
    # EmailSendResponse.sent has default=True → optional, not required.
    # (Regression guard for the hand-coded table that marked it 'required'.)
    assert _row_badge(_html(), "sent") == "optional"


# ---------------------------------------------------------------------------
# Type labels — generics must render cleanly (no truncated `list[str`).
# ---------------------------------------------------------------------------


def test_type_label_renders_list_generic_cleanly():
    # EmailMessage.to is List[EmailAddress] → 'list[EmailAddress]', not 'List'
    # or a truncated 'list[EmailAddress' (the bracket-strip ordering bug).
    label = _type_label(EmailMessage.model_fields["to"])
    assert label == "list[EmailAddress]"


def test_type_label_unwraps_optional():
    # thread_id is Optional[str] → the value type 'str', not 'Optional'.
    label = _type_label(EmailMessage.model_fields["thread_id"])
    assert label == "str"


def test_no_truncated_list_label_in_html():
    # The whole page must never contain a truncated 'list[Something' without a
    # closing bracket (the _type_label bracket-ordering bug). Every 'list['
    # generic that opens must close with ']'. The inner may be a single type
    # (list[EmailAddress]) or a union (list[SingleEmailInput | ThreadInput]),
    # so we require a ']' before the next '<' (HTML tag) — never an unclosed run.
    html = _html()
    for m in re.finditer(r"list\[([^\]<]*)(.?)", html):
        assert m.group(2) == "]", f"Unclosed list[ label near: {m.group(0)!r}"


# ---------------------------------------------------------------------------
# /draft and /send sections are derived from the real route models, so their
# model class names appear in the page (they would not if hand-coded).
# ---------------------------------------------------------------------------


def test_draft_section_derived_from_route_models():
    html = _html()
    assert "EmailDraftRequest" in html
    assert "EmailDraftResponse" in html
    assert "confirmation_token" in html


def test_send_section_derived_from_route_models():
    html = _html()
    assert "EmailSendRequest" in html
    assert "EmailSendResponse" in html
    assert "sent_id" in html


# ---------------------------------------------------------------------------
# write_and_open_spec — the single shared write/open helper used by the CLI.
# ---------------------------------------------------------------------------


def test_write_and_open_spec_writes_file_and_returns_path(tmp_path, monkeypatch):
    import gaia_agent_email.spec_html as spec_mod

    opened = []
    monkeypatch.setattr(spec_mod.webbrowser, "open", lambda uri: opened.append(uri))

    dest = tmp_path / "spec.html"
    returned = spec_mod.write_and_open_spec(str(dest))

    assert returned == dest.resolve()
    assert dest.exists()
    content = dest.read_text(encoding="utf-8")
    assert content.strip().lower().startswith("<!doctype html")
    # The browser was opened with this file's URI (no silent no-op).
    assert opened == [dest.resolve().as_uri()]


def test_write_and_open_spec_default_path_constant(monkeypatch):
    import gaia_agent_email.spec_html as spec_mod

    # Default path lives under ~/.gaia/email/ and is exposed as a constant the
    # CLI prints; assert the shape without writing to the real home dir.
    assert spec_mod.DEFAULT_SPEC_PATH.name == "endpoint-spec.html"
    assert spec_mod.DEFAULT_SPEC_PATH.parent.name == "email"
