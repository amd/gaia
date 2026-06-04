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

from gaia.agents.email.contract import (
    SCHEMA_VERSION,
    ActionItem,
    DraftReply,
    EmailCategory,
    EmailTriageRequest,
    EmailTriageResponse,
    EmailTriageResult,
)
from gaia.agents.email.spec_html import render_endpoint_spec_html


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


def test_all_four_category_values_present():
    html = _html()
    for cat in EmailCategory:
        assert cat.value in html, f"Category value {cat.value!r} missing from spec"


def test_category_urgent_present():
    assert EmailCategory.URGENT.value in _html()


def test_category_actionable_present():
    assert EmailCategory.ACTIONABLE.value in _html()


def test_category_informational_present():
    assert EmailCategory.INFORMATIONAL.value in _html()


def test_category_low_priority_present():
    assert EmailCategory.LOW_PRIORITY.value in _html()


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
