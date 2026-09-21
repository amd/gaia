# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""What the flagship asks a mailbox connection for, and when.

Two lists per provider, and the difference between them is the whole point: a
new consent requests read only, while an existing connection that already
carries a broader scope is used as-is rather than forcing a browser reconnect.
"""

import pytest

from gaia.agents.tools._email.scopes import (
    DECLARED_SCOPES,
    GOOGLE_CONNECTOR_ID,
    MICROSOFT_CONNECTOR_ID,
    READ_CAPABLE_SCOPES,
    SCOPE_GMAIL_FULL_MAILBOX,
    SCOPE_GMAIL_MODIFY,
    SCOPE_GMAIL_READONLY,
    SCOPE_MAIL_READ,
    SCOPE_MAIL_READWRITE,
    resolve_request_scope,
)


@pytest.mark.parametrize(
    "connection,granted,expected",
    [
        # Only readonly anywhere -> request readonly.
        ([SCOPE_GMAIL_READONLY], [SCOPE_GMAIL_READONLY], SCOPE_GMAIL_READONLY),
        # A pre-existing modify connection: request modify, so the connection
        # scope gate passes and the user never sees a browser.
        ([SCOPE_GMAIL_MODIFY], [SCOPE_GMAIL_MODIFY], SCOPE_GMAIL_MODIFY),
        # Both available -> the narrowest wins.
        (
            [SCOPE_GMAIL_READONLY, SCOPE_GMAIL_MODIFY],
            [SCOPE_GMAIL_READONLY, SCOPE_GMAIL_MODIFY],
            SCOPE_GMAIL_READONLY,
        ),
        # Ledger row narrower than the connection -> the overlap.
        (
            [SCOPE_GMAIL_READONLY, SCOPE_GMAIL_MODIFY],
            [SCOPE_GMAIL_MODIFY],
            SCOPE_GMAIL_MODIFY,
        ),
        # The #4052 residual case: connection has modify, ledger has readonly.
        ([SCOPE_GMAIL_MODIFY], [SCOPE_GMAIL_READONLY], None),
        # Connected for calendar only.
        (["https://www.googleapis.com/auth/calendar.readonly"], [], None),
        ([], [], None),
    ],
)
def test_request_scope_resolves_from_the_connection(connection, granted, expected):
    assert (
        resolve_request_scope(
            GOOGLE_CONNECTOR_ID, connection_scopes=connection, granted_scopes=granted
        )
        == expected
    )


@pytest.mark.parametrize(
    "connection,granted,expected",
    [
        ([SCOPE_MAIL_READ], [SCOPE_MAIL_READ], SCOPE_MAIL_READ),
        ([SCOPE_MAIL_READWRITE], [SCOPE_MAIL_READWRITE], SCOPE_MAIL_READWRITE),
        (
            [SCOPE_MAIL_READ, SCOPE_MAIL_READWRITE],
            [SCOPE_MAIL_READ, SCOPE_MAIL_READWRITE],
            SCOPE_MAIL_READ,
        ),
        (["https://graph.microsoft.com/Calendars.Read"], [], None),
    ],
)
def test_the_resolver_is_provider_agnostic(connection, granted, expected):
    assert (
        resolve_request_scope(
            MICROSOFT_CONNECTOR_ID,
            connection_scopes=connection,
            granted_scopes=granted,
        )
        == expected
    )


def test_an_unknown_provider_resolves_to_nothing():
    assert (
        resolve_request_scope(
            "dropbox", connection_scopes=["x"], granted_scopes=["x"]
        )
        is None
    )


def test_declared_scopes_are_read_only():
    """A first-time user must never be asked to consent to more than reading."""
    assert DECLARED_SCOPES[GOOGLE_CONNECTOR_ID] == (SCOPE_GMAIL_READONLY,)
    for scope in DECLARED_SCOPES[GOOGLE_CONNECTOR_ID]:
        assert "modify" not in scope and "send" not in scope
    # Microsoft keeps Mail.ReadWrite: narrowing it would re-consent every
    # existing Outlook user for no benefit here.
    assert DECLARED_SCOPES[MICROSOFT_CONNECTOR_ID] == (SCOPE_MAIL_READWRITE,)


def test_read_capable_scopes_are_ordered_narrowest_first():
    assert READ_CAPABLE_SCOPES[GOOGLE_CONNECTOR_ID] == (
        SCOPE_GMAIL_READONLY,
        SCOPE_GMAIL_MODIFY,
    )
    assert READ_CAPABLE_SCOPES[MICROSOFT_CONNECTOR_ID] == (
        SCOPE_MAIL_READ,
        SCOPE_MAIL_READWRITE,
    )


def test_every_declared_and_read_capable_scope_is_in_the_connector_catalog():
    """A scope outside `available_scopes` raises at the consent screen."""
    import gaia.connectors.catalog  # noqa: F401
    from gaia.connectors.registry import REGISTRY

    for connector_id in (GOOGLE_CONNECTOR_ID, MICROSOFT_CONNECTOR_ID):
        available = set(REGISTRY.get(connector_id).available_scopes)
        declared = set(DECLARED_SCOPES[connector_id])
        capable = set(READ_CAPABLE_SCOPES[connector_id])
        assert declared <= available
        assert capable <= available


def test_full_mailbox_scope_is_never_declared_or_available():
    """`https://mail.google.com/` is total mailbox control, including delete."""
    import gaia.connectors.catalog  # noqa: F401
    from gaia.connectors.registry import REGISTRY

    for table in (DECLARED_SCOPES, READ_CAPABLE_SCOPES):
        for scopes in table.values():
            assert SCOPE_GMAIL_FULL_MAILBOX not in scopes
    assert SCOPE_GMAIL_FULL_MAILBOX not in set(
        REGISTRY.get(GOOGLE_CONNECTOR_ID).available_scopes
    )
