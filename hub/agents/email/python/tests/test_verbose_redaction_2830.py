# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Email-address redaction in ``verbose.py``'s structured log lines (#2830).

``_redact`` (``verbose.py``) matched MFA-shaped digit runs, long URLs, and
JWT-shaped tokens, but never an email address -- ``.`` and ``@`` break the
40-char contiguous run the token pattern requires. The ``gaia_agent_email``
logger propagates to the root handler ``GaiaLogger`` attaches unconditionally
-- writing to ``~/.gaia/gaia.log``, which ``gaia diagnostics`` bundles by
default and the docs tell users to attach to a (public) GitHub issue. A
``search_messages`` query containing a contact's address (``from:
alice@example.com``) would put it on a path to public disclosure the moment
increment 3 (#2830) starts logging the effective query.

The pattern mirrors ``agent.py``'s ``_AUTONOMY_ERROR_EMAIL_RE`` (#2625/C5) --
the same package's already-adversarially-reviewed answer to this exact
threat model. Not imported from ``agent.py``: ``agent.py`` imports the
``tools/*`` mixins, which import ``verbose.py``, so importing the other
direction would be circular.

Hermetic: no Lemonade, no network.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# parents[0] = tests/, [1] = python/, [2] = email/, [3] = agents/, [4] = hub/,
# [5] = repo-root
_REPO_ROOT = Path(__file__).resolve().parents[5]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

pytest.importorskip("gaia_agent_email")

from gaia_agent_email.verbose import _redact, log_tool_call  # noqa: E402


def test_redacts_bare_email_address():
    assert _redact("contact alice@example.com about the invoice") == (
        "contact [REDACTED] about the invoice"
    )


def test_redacts_email_inside_a_gmail_query_string():
    assert _redact('from:"alice@example.com" newer_than:14d') == (
        'from:"[REDACTED]" newer_than:14d'
    )


def test_redacts_email_nested_in_dict_and_list_args():
    assert _redact({"query": "to:bob@example.org", "tags": ["cc:eve@x.com"]}) == {
        "query": "to:[REDACTED]",
        "tags": ["cc:[REDACTED]"],
    }


def test_non_email_strings_pass_through_untouched():
    assert _redact("newer_than:14d is:unread") == "newer_than:14d is:unread"


def test_existing_redaction_patterns_still_work_alongside_the_new_one():
    # MFA-shaped digit run + an email in the same string -- both redacted,
    # neither pattern's addition regresses the other.
    assert _redact("code 123456 sent to alice@example.com") == (
        "code [REDACTED] sent to [REDACTED]"
    )


def test_log_tool_call_redacts_an_email_in_tool_args(caplog):
    with caplog.at_level("INFO", logger="gaia_agent_email"):
        with log_tool_call(
            "search_messages", {"query": 'from:"alice@example.com" newer_than:14d'}
        ):
            pass
    call_record = next(r for r in caplog.records if r.stage == "tool_call")
    assert "alice@example.com" not in str(call_record.tool_args)
