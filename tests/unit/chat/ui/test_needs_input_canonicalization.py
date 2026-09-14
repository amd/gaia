# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Unit tests for canonicalizing a raw ``user_input_request`` event into the
``needs_input`` wire shape (#2595) inside the general (non-email-relay) chat
streaming trunk.

``SSEOutputHandler.request_user_input_blocking()`` emits ``user_input_request``
verbatim — any in-process agent (not just the email sidecar) can call it — so
the general streaming loop must canonicalize it the same way the email-relay
path does via ``CanonicalTranslator``, or the frontend's NeedsInputCard (keyed
on ``type: "needs_input"``) never sees it.
"""

from gaia.ui._chat_helpers import _canonicalize_user_input_request


def test_maps_message_to_question_and_type_to_needs_input():
    raw = {
        "type": "user_input_request",
        "request_id": "req-1",
        "message": "Which mailbox should I use?",
        "choices": ["gmail", "outlook"],
        "options": [
            {"value": "gmail", "label": "Gmail", "description": ""},
            {"value": "outlook", "label": "Outlook", "description": ""},
        ],
        "allow_free_text": True,
        "sensitive": False,
        "timeout_seconds": 240,
        "continue_if_no_response": True,
    }

    canonical = _canonicalize_user_input_request(raw)

    assert canonical == {
        "type": "needs_input",
        "request_id": "req-1",
        "question": "Which mailbox should I use?",
        "options": [
            {"value": "gmail", "label": "Gmail", "description": ""},
            {"value": "outlook", "label": "Outlook", "description": ""},
        ],
        "allow_free_text": True,
        "sensitive": False,
        "timeout_seconds": 240,
    }


def test_defaults_missing_optional_fields():
    canonical = _canonicalize_user_input_request(
        {"type": "user_input_request", "request_id": "req-2", "message": "Proceed?"}
    )
    assert canonical["options"] == []
    assert canonical["allow_free_text"] is True
    assert canonical["sensitive"] is False


def test_plain_choices_form_still_produces_pickable_options():
    # (#3804 review) request_user_input's documented ``choices`` form (a
    # flat list of strings, no options=[{value,label,description}]) must
    # still produce pickable options -- not silently degrade to a
    # free-text-only question. Mirrors sse_translation._normalize_options'
    # documented choices fallback exactly, via delegation rather than a
    # second, independently-maintained normalizer.
    raw = {
        "type": "user_input_request",
        "request_id": "req-4",
        "message": "Which theme do you prefer?",
        "choices": ["Light", "Dark"],
        "options": [],
        "allow_free_text": True,
        "sensitive": False,
    }

    canonical = _canonicalize_user_input_request(raw)

    assert canonical["options"] == [
        {"value": "Light", "label": "Light", "description": ""},
        {"value": "Dark", "label": "Dark", "description": ""},
    ]


def test_rich_options_form_takes_priority_over_choices():
    raw = {
        "type": "user_input_request",
        "request_id": "req-5",
        "message": "Which mailbox?",
        "choices": ["gmail", "outlook"],
        "options": [{"value": "gmail", "label": "Gmail", "description": "Use Gmail."}],
    }

    canonical = _canonicalize_user_input_request(raw)

    assert canonical["options"] == [
        {"value": "gmail", "label": "Gmail", "description": "Use Gmail."}
    ]


def test_sensitive_flag_is_preserved_true():
    canonical = _canonicalize_user_input_request(
        {
            "type": "user_input_request",
            "request_id": "req-3",
            "message": "Enter the OAuth client secret",
            "sensitive": True,
            "allow_free_text": True,
        }
    )
    assert canonical["sensitive"] is True
