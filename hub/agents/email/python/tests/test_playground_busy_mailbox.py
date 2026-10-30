# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The playground must not tell a mailbox mid-save to reconnect.

``/v1/email/connectors`` marks such a mailbox ``busy`` with ``can_send: false``
because its scopes are unknown for the moment. The send picker has to read the
flag before it reads ``can_send``.
"""

from __future__ import annotations

from gaia_agent_email.playground_html import render_playground_html


def test_the_send_picker_says_busy_before_it_says_reconnect():
    html = render_playground_html()

    busy = html.index("busyById[sel.value]")
    reconnect = html.index("reconnect to enable sending")
    assert busy < reconnect, "a busy mailbox falls through to the reconnect note"
    assert "being saved by another GAIA operation" in html
    assert '(p.busy ? " · being saved"' in html
