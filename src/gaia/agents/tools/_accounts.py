# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The Google and Microsoft accounts the mail and calendar tools act for.

A person may connect several accounts of each provider. Reads ask every usable
account; a write acts for exactly one. So that a reply or an event change can
never land on the wrong account, every message, thread, draft and event id a
tool hands out starts with a short tag naming its account (``g3f1a:<id>``), and
a tool that acts on an id takes its account from that tag.

A tag is derived from the account's keyring slot, which never changes for the
life of the account, so an id read in an earlier turn still names the same
account after a restart.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Iterable, List, Optional, Sequence, Tuple

GOOGLE = "google"
MICROSOFT = "microsoft"

#: Registry order. With one account of each, the Google one sends by default.
PROVIDERS: Tuple[str, ...] = (GOOGLE, MICROSOFT)

PROVIDER_NAMES = {GOOGLE: "Google", MICROSOFT: "Microsoft"}

# What a person calls each provider's accounts when narrowing to them.
_PROVIDER_WORDS = {
    GOOGLE: frozenset({"google", "gmail", "googlemail"}),
    MICROSOFT: frozenset({"microsoft", "outlook", "hotmail", "live", "msn"}),
}
_TAG_LETTER = {GOOGLE: "g", MICROSOFT: "m"}
_TAG_RE = re.compile(r"[gm][0-9a-f]{4,40}")


class AccountChoiceError(ValueError):
    """An account name that matches none, or several, of the usable accounts.

    ``choices`` lists the addresses the person can name instead.
    """

    def __init__(self, message: str, choices: Sequence[str]):
        super().__init__(message)
        self.choices = list(choices)


@dataclass(frozen=True)
class ToolAccount:
    """One connected account, as the mail and calendar tools see it."""

    provider: str
    email: str
    # What ``gaia.connectors`` takes as ``account=``: the stable account id.
    ref: str
    tag: str
    # The default account of its provider.
    is_default: bool
    status: str = "ok"

    @property
    def name(self) -> str:
        """The account as a person knows it."""
        return self.email or f"your {PROVIDER_NAMES[self.provider]} account"

    @property
    def label(self) -> str:
        """The account as an approval prompt names it."""
        if not self.email:
            return (
                f"your {PROVIDER_NAMES[self.provider]} account (its address was "
                "not recorded)"
            )
        return f"{self.email} ({PROVIDER_NAMES[self.provider]})"

    def tag_id(self, raw: str) -> str:
        """``raw`` (a provider id) as the tools hand it out."""
        return f"{self.tag}:{raw}"


def make_tags(provider: str, slots: Sequence[str]) -> List[str]:
    """A short, distinct tag per slot: the provider's letter and a slot hash."""
    digests = [
        hashlib.sha1(slot.encode("utf-8"), usedforsecurity=False).hexdigest()
        for slot in slots
    ]
    for width in (4, 6, 8, 40):
        tags = [_TAG_LETTER[provider] + digest[:width] for digest in digests]
        if len(set(tags)) == len(tags):
            return tags
    raise ValueError(f"two {provider} accounts share the keyring slot: {slots}")


def stored_accounts() -> List[ToolAccount]:
    """Every stored Google and Microsoft account, in registry order, oldest first.

    Includes accounts whose sign-in expired (``status``), so a tool can say
    which account it could not read.
    """
    from gaia.connectors import api
    from gaia.connectors.store import DEFAULT_ACCOUNT

    out: List[ToolAccount] = []
    for provider in PROVIDERS:
        accounts = api.list_accounts(provider)
        tags = make_tags(provider, [a.slot for a in accounts])
        for account, tag in zip(accounts, tags):
            out.append(
                ToolAccount(
                    provider=provider,
                    # A legacy connection stored this sentinel for "no address".
                    email="" if account.email == DEFAULT_ACCOUNT else account.email,
                    ref=account.account_id,
                    tag=tag,
                    is_default=account.is_default,
                    status=account.status,
                )
            )
    return out


def split_id(value: str) -> Tuple[Optional[str], str]:
    """``(tag, provider id)``; the tag is None when ``value`` carries none."""
    tag, sep, raw = value.strip().partition(":")
    if sep and raw and _TAG_RE.fullmatch(tag):
        return tag, raw
    return None, value.strip()


def _names(accounts: Iterable[ToolAccount]) -> List[str]:
    return [a.name for a in accounts]


def pick(accounts: Sequence[ToolAccount], wanted: str) -> ToolAccount:
    """The one account ``wanted`` names: an address, a tag, or a provider word
    ("gmail", "outlook") when that provider has exactly one usable account."""
    key = wanted.strip().casefold()
    hits = [a for a in accounts if a.email.casefold() == key or a.tag == key]
    if not hits:
        hits = [
            a
            for a in accounts
            if key in _PROVIDER_WORDS[a.provider] or key == a.provider
        ]
    if len(hits) == 1:
        return hits[0]
    choices = _names(accounts)
    what = "matches more than one" if hits else "is not one"
    raise AccountChoiceError(
        f"{wanted!r} {what} of the connected accounts ({', '.join(choices)}). "
        "Ask the user which one they mean, then pass its address.",
        choices,
    )


def account_for_id(
    value: str,
    usable: Sequence[ToolAccount],
    *,
    what: str,
    unusable: Sequence[Tuple[ToolAccount, str]] = (),
) -> Tuple[ToolAccount, str]:
    """The account an id belongs to, and the provider's own id inside it.

    An id with no tag is taken only when exactly one account is usable: with
    several, guessing could act on the wrong account.
    """
    tag, raw = split_id(value)
    if not raw:
        raise ValueError(f"A {what} id is required.")
    if tag is None:
        if len(usable) == 1:
            return usable[0], raw
        raise ValueError(
            f"The {what} id {value!r} does not say which account it belongs to, "
            f"and {len(usable)} accounts are connected. Use the id exactly as a "
            f"listing returned it: each one starts with its account's tag, like "
            f"{usable[0].tag}:… Nothing was read or changed."
        )
    for account in usable:
        if account.tag == tag:
            return account, raw
    for account, why in unusable:
        if account.tag == tag:
            raise ValueError(
                f"That {what} is in {account.name}, which can't be used right "
                f"now: {why}. Nothing was read or changed."
            )
    raise ValueError(
        f"The {what} id {value!r} belongs to an account that is no longer "
        f"connected (connected now: {', '.join(_names(usable)) or 'none'}). List "
        "again to get a current id. Nothing was read or changed."
    )


def default_account(
    stored: Sequence[ToolAccount], usable: Sequence[ToolAccount]
) -> ToolAccount:
    """The account a new message or event uses when the person names none.

    The default account of the first provider, in registry order, that has a
    usable account. When that default can't be used but another account of its
    provider can, nothing is guessed: the person is asked which one.
    """
    usable_tags = {a.tag for a in usable}
    for provider in PROVIDERS:
        mine = [a for a in stored if a.provider == provider]
        live = [a for a in usable if a.provider == provider]
        if not live:
            continue
        default = next((a for a in mine if a.is_default), None)
        if default is not None and default.tag in usable_tags:
            return next(a for a in live if a.tag == default.tag)
        missing = default.name if default is not None else "none is set"
        raise AccountChoiceError(
            f"The default {PROVIDER_NAMES[provider]} account ({missing}) can't be "
            "used right now, so GAIA won't guess which account to use. Ask the "
            f"user which account to use: {', '.join(_names(usable))}.",
            _names(usable),
        )
    raise AccountChoiceError("No usable account is connected.", [])


__all__ = [
    "AccountChoiceError",
    "GOOGLE",
    "MICROSOFT",
    "PROVIDERS",
    "PROVIDER_NAMES",
    "ToolAccount",
    "account_for_id",
    "default_account",
    "make_tags",
    "pick",
    "split_id",
    "stored_accounts",
]
