# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
HTML spec generator for the Email Triage Agent REST endpoints (issue #1263).

``render_endpoint_spec_html()`` returns a single self-contained HTML page
documenting the three email REST endpoints and the frozen #1262 contract
request/response shapes. It derives field rows directly from the contract
pydantic models so the spec stays in sync with the contract automatically.

No external assets — inline CSS only. No LLM, no network calls.
"""

from __future__ import annotations

import html as _html_lib
from typing import Any, List, Tuple, Type

from pydantic import BaseModel

from gaia.agents.email.contract import (
    SCHEMA_VERSION,
    ActionItem,
    DraftReply,
    EmailAddress,
    EmailCategory,
    EmailMessage,
    EmailTriageRequest,
    EmailTriageResponse,
    EmailTriageResult,
    SingleEmailInput,
    ThreadInput,
)

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_INLINE_CSS = """
body {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto,
               Helvetica, Arial, sans-serif;
  background: #0f1117;
  color: #e0e0e0;
  margin: 0;
  padding: 2rem;
  line-height: 1.6;
}
h1 {
  color: #ed6b36;
  font-size: 2rem;
  margin-bottom: 0.25rem;
}
.subtitle {
  color: #888;
  font-size: 0.95rem;
  margin-bottom: 2.5rem;
}
h2 {
  color: #d97742;
  margin-top: 2.5rem;
  margin-bottom: 0.5rem;
  font-size: 1.4rem;
}
h3 {
  color: #c0bfe0;
  margin-top: 1.5rem;
  margin-bottom: 0.4rem;
  font-size: 1.1rem;
}
.endpoint-block {
  background: #1a1d27;
  border: 1px solid #2d2f3e;
  border-radius: 8px;
  padding: 1.5rem;
  margin-bottom: 2rem;
}
.method-badge {
  display: inline-block;
  background: #4e6aff;
  color: #fff;
  font-size: 0.78rem;
  font-weight: 700;
  padding: 0.2rem 0.6rem;
  border-radius: 4px;
  letter-spacing: 0.05em;
  margin-right: 0.75rem;
  vertical-align: middle;
}
.path {
  font-family: "SF Mono", "Fira Code", "Consolas", monospace;
  font-size: 1.05rem;
  color: #a8d8a8;
  vertical-align: middle;
}
.desc {
  color: #aaa;
  font-size: 0.93rem;
  margin-top: 0.5rem;
}
table {
  width: 100%;
  border-collapse: collapse;
  margin-top: 0.75rem;
  font-size: 0.9rem;
}
th {
  text-align: left;
  color: #888;
  font-weight: 600;
  border-bottom: 1px solid #2d2f3e;
  padding: 0.4rem 0.6rem;
}
td {
  padding: 0.4rem 0.6rem;
  border-bottom: 1px solid #1e2030;
  vertical-align: top;
}
td:first-child {
  font-family: "SF Mono", "Fira Code", "Consolas", monospace;
  color: #8fd8ff;
  white-space: nowrap;
}
td:nth-child(2) {
  color: #d8a870;
}
td:nth-child(3) {
  color: #aaa;
}
.required-badge {
  display: inline-block;
  font-size: 0.72rem;
  background: #6a3030;
  color: #ff9a9a;
  padding: 0.05rem 0.4rem;
  border-radius: 3px;
  margin-left: 0.4rem;
  vertical-align: middle;
}
.optional-badge {
  display: inline-block;
  font-size: 0.72rem;
  background: #2a3040;
  color: #8899bb;
  padding: 0.05rem 0.4rem;
  border-radius: 3px;
  margin-left: 0.4rem;
  vertical-align: middle;
}
.version-badge {
  display: inline-block;
  background: #2a3040;
  color: #8899bb;
  font-size: 0.8rem;
  padding: 0.2rem 0.75rem;
  border-radius: 4px;
  margin-left: 1rem;
  vertical-align: middle;
}
.category-list {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem;
  margin-top: 0.5rem;
}
.category-tag {
  background: #2a3040;
  color: #a8d8a8;
  border: 1px solid #3a4555;
  border-radius: 4px;
  padding: 0.2rem 0.75rem;
  font-family: "SF Mono", "Fira Code", "Consolas", monospace;
  font-size: 0.85rem;
}
.model-section {
  background: #151821;
  border: 1px solid #252838;
  border-radius: 6px;
  padding: 1rem 1.25rem;
  margin-top: 1rem;
}
.footer {
  margin-top: 3rem;
  color: #555;
  font-size: 0.8rem;
  border-top: 1px solid #2d2f3e;
  padding-top: 1rem;
}
"""


def _esc(text: str) -> str:
    return _html_lib.escape(str(text))


def _type_label(field_info: Any) -> str:
    """Best-effort human-readable type label from a pydantic FieldInfo."""
    annotation = getattr(field_info, "annotation", None)
    if annotation is None:
        return "any"
    # Use __name__ when available; fall back to repr for generics.
    name = getattr(annotation, "__name__", None) or repr(annotation)
    # Simplify common generic forms
    name = (
        name.replace("typing.", "")
        .replace("Optional[", "")
        .replace("]", "")
        .replace("List[", "list[")
    )
    return name


def _required_badge(field_info: Any) -> str:
    has_default = (
        field_info.default is not None or field_info.default_factory is not None
    )
    if has_default or getattr(field_info, "is_required", lambda: True)() is False:
        return '<span class="optional-badge">optional</span>'
    return '<span class="required-badge">required</span>'


def _model_table(model: Type[BaseModel], title: str) -> str:
    rows: List[str] = []
    for name, info in model.model_fields.items():
        desc = (info.description or "").strip()
        type_label = _type_label(info)
        badge = _required_badge(info)
        rows.append(
            f"<tr>"
            f"<td>{_esc(name)}{badge}</td>"
            f"<td>{_esc(type_label)}</td>"
            f"<td>{_esc(desc)}</td>"
            f"</tr>"
        )

    table_html = (
        "<table>"
        "<thead><tr>"
        "<th>Field</th><th>Type</th><th>Description</th>"
        "</tr></thead>"
        "<tbody>" + "".join(rows) + "</tbody>"
        "</table>"
    )
    return f'<div class="model-section"><h3>{_esc(title)}</h3>{table_html}</div>'


def _category_list_html() -> str:
    tags = "".join(
        f'<span class="category-tag">{_esc(cat.value)}</span>' for cat in EmailCategory
    )
    return f'<div class="category-list">{tags}</div>'


def _endpoint_block(
    path: str,
    description: str,
    request_sections: List[Tuple[str, Type[BaseModel]]],
    response_sections: List[Tuple[str, Type[BaseModel]]],
    extra_html: str = "",
) -> str:
    req_html = "".join(_model_table(m, t) for t, m in request_sections)
    resp_html = "".join(_model_table(m, t) for t, m in response_sections)
    return (
        f'<div class="endpoint-block">'
        f'<span class="method-badge">POST</span>'
        f'<span class="path">{_esc(path)}</span>'
        f'<p class="desc">{_esc(description)}</p>'
        f"{extra_html}"
        f"<h3>Request body</h3>{req_html}"
        f"<h3>Response body</h3>{resp_html}"
        f"</div>"
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def render_endpoint_spec_html() -> str:
    """Return a self-contained HTML page documenting the Email Triage API.

    The page is built entirely from the frozen #1262 contract models —
    field names and descriptions are derived at call time so the spec
    stays in sync with contract changes automatically. No external assets;
    all CSS is inlined.
    """
    triage_extra = (
        "<p class='desc'>"
        "<strong>Payload discriminator:</strong> set <code>payload.kind</code> "
        'to <code>"single"</code> for a single message or '
        '<code>"thread"</code> for a conversation thread.</p>'
        "<h3>Category values (EmailCategory)</h3>"
        + _category_list_html()
        + _model_table(SingleEmailInput, "SingleEmailInput (kind: single)")
        + _model_table(ThreadInput, "ThreadInput (kind: thread)")
        + _model_table(EmailMessage, "EmailMessage")
        + _model_table(EmailAddress, "EmailAddress")
    )

    triage_block = (
        f'<div class="endpoint-block">'
        f'<span class="method-badge">POST</span>'
        f'<span class="path">/v1/email/triage</span>'
        f'<p class="desc">Triage a single email or a full thread. '
        f"Accepts the frozen #1262 EmailTriageRequest and returns "
        f"a structured EmailTriageResponse — category, spam/phishing signals, "
        f"a plain-text summary, extracted action items, and an optional draft reply. "
        f"No mail is read or sent; this analyses only the payload in the request.</p>"
        f"<h3>Request envelope</h3>"
        f"{_model_table(EmailTriageRequest, 'EmailTriageRequest')}"
        f"<h3>Payload shapes</h3>"
        f"{triage_extra}"
        f"<h3>Response envelope</h3>"
        f"{_model_table(EmailTriageResponse, 'EmailTriageResponse')}"
        f"{_model_table(EmailTriageResult, 'EmailTriageResult')}"
        f"{_model_table(DraftReply, 'DraftReply (optional)')}"
        f"{_model_table(ActionItem, 'ActionItem')}"
        f"</div>"
    )

    draft_block = (
        '<div class="endpoint-block">'
        '<span class="method-badge">POST</span>'
        '<span class="path">/v1/email/draft</span>'
        '<p class="desc">Propose a reply and obtain a single-use confirmation '
        "token bound to the exact (to, subject, body) payload. "
        "Echo the token to POST /v1/email/send to authorize sending.</p>"
        "<h3>Request fields</h3>"
        "<div class='model-section'>"
        "<table><thead><tr><th>Field</th><th>Type</th><th>Description</th></tr></thead>"
        "<tbody>"
        "<tr><td>to <span class='required-badge'>required</span></td><td>list[EmailAddress]</td><td>Proposed recipients (non-empty).</td></tr>"
        "<tr><td>subject <span class='required-badge'>required</span></td><td>str</td><td>Proposed subject line.</td></tr>"
        "<tr><td>body <span class='required-badge'>required</span></td><td>str</td><td>Proposed reply body.</td></tr>"
        "</tbody></table>"
        "</div>"
        "<h3>Response fields</h3>"
        "<div class='model-section'>"
        "<table><thead><tr><th>Field</th><th>Type</th><th>Description</th></tr></thead>"
        "<tbody>"
        "<tr><td>draft <span class='required-badge'>required</span></td><td>DraftReply</td><td>The proposed reply (to / subject / body).</td></tr>"
        "<tr><td>confirmation_token <span class='required-badge'>required</span></td><td>str</td><td>Echo to POST /v1/email/send. Single-use; bound to this exact payload.</td></tr>"
        "</tbody></table>"
        "</div>"
        "</div>"
    )

    send_block = (
        '<div class="endpoint-block">'
        '<span class="method-badge">POST</span>'
        '<span class="path">/v1/email/send</span>'
        '<p class="desc">Send a reply — gated on explicit confirmation (#1264). '
        "The confirmation gate fires FIRST: a request without a valid, "
        "payload-bound confirmation token is rejected with HTTP 403 before any "
        "backend call. Emails are never sent without explicit confirmation.</p>"
        "<h3>Request fields</h3>"
        "<div class='model-section'>"
        "<table><thead><tr><th>Field</th><th>Type</th><th>Description</th></tr></thead>"
        "<tbody>"
        "<tr><td>to <span class='required-badge'>required</span></td><td>list[EmailAddress]</td><td>Recipients (non-empty).</td></tr>"
        "<tr><td>subject <span class='required-badge'>required</span></td><td>str</td><td>Subject line.</td></tr>"
        "<tr><td>body <span class='required-badge'>required</span></td><td>str</td><td>Reply body.</td></tr>"
        "<tr><td>confirmation_token <span class='optional-badge'>optional</span></td><td>str</td><td>Confirmation token from POST /v1/email/draft. A send without a valid token for this exact payload is rejected (403).</td></tr>"
        "</tbody></table>"
        "</div>"
        "<h3>Response fields</h3>"
        "<div class='model-section'>"
        "<table><thead><tr><th>Field</th><th>Type</th><th>Description</th></tr></thead>"
        "<tbody>"
        "<tr><td>sent_id <span class='required-badge'>required</span></td><td>str</td><td>Provider message id of the sent email.</td></tr>"
        "<tr><td>to <span class='required-badge'>required</span></td><td>list[EmailAddress]</td><td>Recipients the message was sent to.</td></tr>"
        "<tr><td>subject <span class='required-badge'>required</span></td><td>str</td><td>Subject of the sent message.</td></tr>"
        "<tr><td>sent <span class='required-badge'>required</span></td><td>bool</td><td>Always true on success.</td></tr>"
        "</tbody></table>"
        "</div>"
        "</div>"
    )

    body = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Email Triage Agent — Endpoint Spec</title>
<style>
{_INLINE_CSS}
</style>
</head>
<body>
<h1>Email Triage Agent
  <span class="version-badge">Contract schema_version: {_esc(SCHEMA_VERSION)}</span>
</h1>
<p class="subtitle">
  REST endpoint specification derived from the frozen #1262 contract
  (<code>gaia.agents.email.contract</code>).
  Field descriptions are sourced directly from the pydantic models and stay
  in sync with the contract automatically.
</p>

<h2>Endpoints</h2>

{triage_block}

{draft_block}

{send_block}

<div class="footer">
  GAIA Email Triage Agent &mdash; schema_version {_esc(SCHEMA_VERSION)} &mdash; amd-gaia.ai
</div>
</body>
</html>"""
    return body


__all__ = ["render_endpoint_spec_html"]
