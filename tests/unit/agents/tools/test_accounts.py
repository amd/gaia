# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Which account a mail or calendar write acts for, and how ids name it."""

import hashlib

import pytest
from _fake_accounts import install

from gaia.agents.tools import _accounts
from gaia.agents.tools._accounts import (
    AccountChoiceError,
    ToolAccount,
    default_account,
    make_tags,
    pick,
    split_id,
    stored_accounts,
)


def _account(email, provider="google", *, default=True, status="ok", tag=None):
    return ToolAccount(
        provider=provider,
        email=email,
        ref=email,
        tag=tag or make_tags(provider, [email])[0],
        is_default=default,
        status=status,
    )


GMAIL = _account("me@gmail.com")
WORK = _account("work@gmail.com", default=False)
OUTLOOK = _account("me@outlook.com", "microsoft")


class TestTags:
    def test_tags_widen_until_they_differ(self):
        # Two keys whose hashes share their first four hex digits.
        first, second = "acct257", "acct303"
        digests = [hashlib.sha1(k.encode()).hexdigest() for k in (first, second)]
        assert digests[0][:4] == digests[1][:4] and digests[0][:6] != digests[1][:6]
        tags = make_tags("google", [first, second])
        assert len(set(tags)) == 2
        assert all(t.startswith("g") and len(t) == 7 for t in tags)

    def test_a_tag_is_short_when_nothing_collides(self):
        assert [len(t) for t in make_tags("microsoft", ["a", "b"])] == [5, 5]

    def test_a_new_account_after_removing_all_gets_a_new_tag(self, monkeypatch):
        """Ids read for a removed account must never route to its successor,
        though both lived in the first keyring slot."""
        connections = {"google": {"account_email": "old@x.com", "account_id": "111"}}
        install(monkeypatch, connections)
        [old] = stored_accounts()
        connections["google"] = {"account_email": "new@x.com", "account_id": "222"}
        [new] = stored_accounts()
        assert old.tag != new.tag


class TestSplitId:
    def test_a_tagged_id_splits(self):
        assert split_id("g3f1a:abc") == ("g3f1a", "abc")

    def test_an_uppercased_tag_still_names_its_account(self):
        assert split_id(" G3F1A:abc ") == ("g3f1a", "abc")

    def test_an_untagged_id_has_no_tag(self):
        assert split_id("18c2f:") == (None, "18c2f:")
        assert split_id("AAMkAG=") == (None, "AAMkAG=")


class TestPick:
    def test_a_provider_word_names_its_only_account(self):
        assert pick([GMAIL, OUTLOOK], "Outlook") is OUTLOOK
        assert pick([GMAIL, OUTLOOK], "gmail") is GMAIL

    def test_a_provider_word_with_two_accounts_is_refused(self):
        with pytest.raises(AccountChoiceError, match="matches more than one") as err:
            pick([GMAIL, WORK, OUTLOOK], "gmail")
        assert err.value.choices == ["me@gmail.com", "work@gmail.com", "me@outlook.com"]

    def test_an_address_beats_a_provider_word(self):
        assert pick([GMAIL, WORK], "WORK@gmail.com") is WORK


class TestDefaultAccount:
    def test_the_default_of_the_first_provider_sends(self):
        assert default_account([GMAIL, WORK, OUTLOOK], [GMAIL, WORK, OUTLOOK]) is GMAIL

    def test_an_expired_default_gmail_never_hands_new_mail_to_outlook(self):
        expired = _account("me@gmail.com", status="reauth_required")
        with pytest.raises(AccountChoiceError) as err:
            default_account([expired, OUTLOOK], [OUTLOOK])
        assert "me@gmail.com" in str(err.value)
        assert "reconnect" in str(err.value).lower()
        assert err.value.choices == ["me@outlook.com"]

    def test_an_expired_default_is_not_swapped_for_its_sibling(self):
        expired = _account("me@gmail.com", status="reauth_required")
        with pytest.raises(AccountChoiceError) as err:
            default_account([expired, WORK], [WORK])
        assert err.value.choices == ["work@gmail.com"]

    def test_with_no_google_account_outlook_sends(self):
        assert default_account([OUTLOOK], [OUTLOOK]) is OUTLOOK

    def test_nothing_usable_is_refused(self):
        with pytest.raises(AccountChoiceError, match="No usable account"):
            default_account([], [])


def test_a_legacy_account_without_an_id_is_tagged_by_its_slot(monkeypatch):
    connections = {"google": {"account_email": "old@x.com", "account_id": "default"}}
    install(monkeypatch, connections)
    [legacy] = stored_accounts()
    assert (
        legacy.tag == make_tags("google", [_accounts.tag_key("default", "default")])[0]
    )
