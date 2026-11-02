# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""What the flagship asks a mailbox connection for, and when.

A new consent asks once for reading and writing. An existing connection keeps
reading with whatever read scope it carries, with no browser reconnect, and a
write resolves its own scopes separately.
"""

import pytest

from gaia.agents.tools._email.scopes import (
    DECLARED_SCOPES,
    GOOGLE_CONNECTOR_ID,
    MICROSOFT_CONNECTOR_ID,
    OPERATION_DRAFT,
    OPERATION_SEND,
    READ_CAPABLE_SCOPES,
    SCOPE_GMAIL_FULL_MAILBOX,
    SCOPE_GMAIL_MODIFY,
    SCOPE_GMAIL_READONLY,
    SCOPE_MAIL_READ,
    SCOPE_MAIL_READWRITE,
    SCOPE_MAIL_SEND,
    WRITE_SCOPES,
    missing_write_scopes,
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
        resolve_request_scope("dropbox", connection_scopes=["x"], granted_scopes=["x"])
        is None
    )


def test_one_consent_covers_reading_and_writing():
    """Every write's scopes are declared, and permanent delete stays out of reach."""
    assert DECLARED_SCOPES[GOOGLE_CONNECTOR_ID] == (SCOPE_GMAIL_MODIFY,)
    assert DECLARED_SCOPES[MICROSOFT_CONNECTOR_ID] == (
        SCOPE_MAIL_READWRITE,
        SCOPE_MAIL_SEND,
    )
    for provider, operations in WRITE_SCOPES.items():
        for scopes in operations.values():
            assert set(scopes) <= set(DECLARED_SCOPES[provider])
            assert SCOPE_GMAIL_FULL_MAILBOX not in scopes
    for provider, declared in DECLARED_SCOPES.items():
        assert set(declared) & set(READ_CAPABLE_SCOPES[provider])


@pytest.mark.parametrize(
    "provider,operation,held,missing",
    [
        (GOOGLE_CONNECTOR_ID, OPERATION_SEND, [SCOPE_GMAIL_MODIFY], ()),
        (
            GOOGLE_CONNECTOR_ID,
            OPERATION_DRAFT,
            [SCOPE_GMAIL_READONLY],
            (SCOPE_GMAIL_MODIFY,),
        ),
        (MICROSOFT_CONNECTOR_ID, OPERATION_DRAFT, [SCOPE_MAIL_READWRITE], ()),
        (
            MICROSOFT_CONNECTOR_ID,
            OPERATION_SEND,
            [SCOPE_MAIL_READWRITE],
            (SCOPE_MAIL_SEND,),
        ),
    ],
)
def test_missing_write_scopes(provider, operation, held, missing):
    assert missing_write_scopes(provider, operation, held_scopes=held) == missing


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
