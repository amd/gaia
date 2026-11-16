# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Several accounts per OAuth connector: the account index and its operations.

Each account's refresh token lives in its own keyring slot. The keyring cannot
list its entries, so ``~/.gaia/connectors/accounts.json`` (next to
``grants.json``, no secrets) records which slots exist::

    {"version": 1,
     "connectors": {
       "google": {"default_slot": "default",
                  "accounts": [{"slot": "default", "account_id": "1084...",
                                "email": "jane@gmail.com", "state": "ok",
                                "account_type": null}]}}}

- An account is identified by the provider's stable id (Google's ``sub``,
  Microsoft Graph's ``/me`` ``id``). The email is a label.
- The first account a connector ever stores goes in the slot GAIA has always
  used, ``<provider>:default``, so a one-account install and older GAIA builds
  see exactly what they did before. Later accounts get ``<provider>:id:<id>``.
  Blobs never move between slots.
- A ``default`` slot written before this index existed is listed even when the
  index has no entry for it, so a missing or deleted index rebuilds itself.
- A new account's entry is written as ``pending`` before its token, so a crash
  between the two leaves an entry the user can see and remove.
- Each connector has one default account: what ``account=None`` means. The
  first account stored is the default until the user picks another.

Locks: the provider lock (``_refresh_lock.held_sync``) for anything that adds
or removes an account, then the account lock, then the index lock, which is
held only around one read-modify-write of this file.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterator, List, Literal, Optional, Tuple

from gaia.connectors import _refresh_lock
from gaia.connectors.errors import (
    AuthRequiredError,
    ConnectorsError,
    UnknownAccountError,
)
from gaia.connectors.store import (
    DEFAULT_ACCOUNT,
    delete_connection,
    peek_connection,
    save_connection,
)
from gaia.daemon.lock import try_lock, unlock

logger = logging.getLogger(__name__)

#: The keyring slot GAIA has always written. Its account keeps it forever.
LEGACY_SLOT = DEFAULT_ACCOUNT

#: ``account_id`` of a legacy account whose stable id GAIA has not learned yet.
#: Its next token refresh fills it in.
UNIDENTIFIED = DEFAULT_ACCOUNT

AccountStatus = Literal["ok", "reauth_required", "error"]

# What the index stores; ``AccountStatus`` is derived from it plus the keyring.
_State = Literal["pending", "ok", "reauth_required"]

_INDEX_VERSION = 1
_thread_lock = threading.Lock()


@dataclass(frozen=True)
class Account:
    """One stored account of an OAuth connector.

    ``status`` is ``ok`` (signed in), ``reauth_required`` (its sign-in expired
    or was revoked; reconnect it) or ``error`` (a sign-in that never finished
    saving; remove it and connect again). ``slot`` names its keyring entry and
    is internal: callers name an account by ``account_id`` or ``email``.
    """

    connector_id: str
    account_id: str
    email: str
    status: AccountStatus
    is_default: bool
    slot: str
    account_type: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """The JSON shape the CLI and the HTTP routes return."""
        return {
            "account_id": self.account_id,
            "email": self.email,
            "status": self.status,
            "is_default": self.is_default,
        }


def slot_for(account_id: str) -> str:
    """Keyring slot of an account that is not the connector's first."""
    return f"id:{account_id}"


def index_path() -> Path:
    # Beside grants.json and the account locks: per user, like the keyring.
    return _refresh_lock.default_gaia_home() / "connectors" / "accounts.json"


def _lock_path() -> Path:
    return index_path().with_name("accounts.lock")


@contextmanager
def _index_locked() -> Iterator[None]:
    """Hold the index lock: threads here, and other GAIA processes."""
    path = _lock_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with _thread_lock:
        fd = os.open(str(path), os.O_RDWR | os.O_CREAT, 0o600)
        try:
            deadline = time.monotonic() + _refresh_lock.LOCK_TIMEOUT_SECONDS
            while not try_lock(fd):
                if time.monotonic() >= deadline:
                    raise ConnectorsError(
                        f"The account list at {index_path()} has been locked by "
                        "another GAIA process for over "
                        f"{_refresh_lock.LOCK_TIMEOUT_SECONDS:.0f}s (lock file "
                        f"{path}). Retry; if it keeps happening, look for a "
                        "stuck `gaia daemon` or Agent UI process."
                    )
                time.sleep(0.02)
            try:
                yield
            finally:
                unlock(fd)
        finally:
            os.close(fd)


def _load_index() -> Dict[str, Any]:
    path = index_path()
    if not path.exists():
        return {"version": _INDEX_VERSION, "connectors": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise ConnectorsError(
            f"The connected-account list at {path} could not be read ({e}). "
            f"Delete it to rebuild it (`rm {path}`): accounts other than each "
            "connector's first then have to be connected again."
        ) from e
    if not isinstance(data, dict) or not isinstance(data.get("connectors"), dict):
        raise ConnectorsError(
            f"The connected-account list at {path} has the wrong shape. Delete "
            f"it to rebuild it (`rm {path}`)."
        )
    return data


def _save_index(data: Dict[str, Any]) -> None:
    path = index_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".accounts_", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, sort_keys=True, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        if sys.platform != "win32":
            os.chmod(path, 0o600)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _section(data: Dict[str, Any], connector_id: str) -> Dict[str, Any]:
    return data["connectors"].setdefault(
        connector_id, {"default_slot": None, "accounts": []}
    )


def _legacy_entry(connector_id: str) -> Optional[Dict[str, Any]]:
    """An index entry for a ``default`` slot written without one, or None."""
    blob = peek_connection(connector_id, slot=LEGACY_SLOT)
    if blob is None:
        return None
    return {
        "slot": LEGACY_SLOT,
        "account_id": blob.get("account_id") or UNIDENTIFIED,
        "email": blob.get("account_email") or "",
        "state": "ok",
        "account_type": blob.get("account_type"),
    }


def _entries(connector_id: str, data: Dict[str, Any]) -> List[Dict[str, Any]]:
    section = data["connectors"].get(connector_id) or {}
    entries = [dict(e) for e in section.get("accounts", [])]
    if not any(e["slot"] == LEGACY_SLOT for e in entries):
        legacy = _legacy_entry(connector_id)
        if legacy is not None:
            entries.insert(0, legacy)
    return entries


def _default_of(section: Dict[str, Any], slots: List[str]) -> Optional[str]:
    chosen = section.get("default_slot")
    if chosen in slots:
        return chosen
    if LEGACY_SLOT in slots:
        return LEGACY_SLOT
    return slots[0] if slots else None


@contextmanager
def _mutate(connector_id: str) -> Iterator[Dict[str, Any]]:
    """Yield this connector's index section, materialized, and save it after."""
    with _index_locked():
        data = _load_index()
        section = _section(data, connector_id)
        section["accounts"] = _entries(connector_id, data)
        yield section
        slots = [e["slot"] for e in section["accounts"]]
        section["default_slot"] = _default_of(section, slots)
        if not section["accounts"]:
            del data["connectors"][connector_id]
        _save_index(data)


def _status(entry: Dict[str, Any], blob: Optional[dict]) -> AccountStatus:
    if entry.get("state") == "reauth_required":
        return "reauth_required"
    if blob is not None:
        return "ok"
    # Saved once, now gone (a revoked or replaced client cleared it).
    if entry.get("state") == "ok":
        return "reauth_required"
    return "error"


def list_accounts(connector_id: str) -> List[Account]:
    """Every stored account of ``connector_id``, oldest first. Reads the keyring
    once per account; never writes."""
    data = _load_index()
    entries = _entries(connector_id, data)
    section = data["connectors"].get(connector_id) or {}
    default = _default_of(section, [e["slot"] for e in entries])
    out: List[Account] = []
    for entry in entries:
        blob = peek_connection(connector_id, slot=entry["slot"])
        out.append(
            Account(
                connector_id=connector_id,
                account_id=(blob or {}).get("account_id")
                or entry.get("account_id")
                or UNIDENTIFIED,
                email=(blob or {}).get("account_email") or entry.get("email") or "",
                status=_status(entry, blob),
                is_default=entry["slot"] == default,
                slot=entry["slot"],
                account_type=(blob or {}).get("account_type")
                or entry.get("account_type"),
            )
        )
    return out


def default_slot(connector_id: str) -> str:
    """The default account's slot, from the index alone (no keyring read when
    the index lists accounts). ``default`` when nothing is stored."""
    data = _load_index()
    section = data["connectors"].get(connector_id) or {}
    slots = [e["slot"] for e in section.get("accounts", [])]
    return _default_of(section, slots) or LEGACY_SLOT


def _matches(entry: Dict[str, Any], wanted: str) -> bool:
    return (
        entry.get("account_id") == wanted
        or entry["slot"] == wanted
        or (entry.get("email") or "").casefold() == wanted.casefold()
    )


def resolve_slot(connector_id: str, account: Optional[str]) -> str:
    """The keyring slot for ``account`` (stable id, email, or None for the
    default account). Raises ``UnknownAccountError`` when it names no stored
    account, or more than one."""
    if account is None:
        return default_slot(connector_id)
    data = _load_index()
    section = data["connectors"].get(connector_id) or {}
    hits = [e for e in section.get("accounts", []) if _matches(e, account)]
    if not hits and not any(
        e["slot"] == LEGACY_SLOT for e in section.get("accounts", [])
    ):
        legacy = _legacy_entry(connector_id)
        if legacy is not None and _matches(legacy, account):
            hits = [legacy]
    if len(hits) == 1:
        return hits[0]["slot"]
    choices = [a.email or a.account_id for a in list_accounts(connector_id)]
    raise UnknownAccountError(connector_id, account, choices, ambiguous=bool(hits))


def resolve_account(connector_id: str, account: Optional[str]) -> Account:
    """The stored account ``account`` names (None: the default account).

    Raises ``AuthRequiredError(NOT_CONNECTED)`` when nothing is stored and
    ``UnknownAccountError`` when ``account`` names no stored account.
    """
    slot = resolve_slot(connector_id, account)
    for acct in list_accounts(connector_id):
        if acct.slot == slot:
            return acct
    raise AuthRequiredError(
        AuthRequiredError.Reason.NOT_CONNECTED, provider=connector_id
    )


def save_signed_in_account(
    connector_id: str,
    *,
    account_id: str,
    email: str,
    refresh_token: str,
    scopes: List[str],
    client_id_hash: str,
    tenant: Optional[str] = None,
    account_type: Optional[str] = None,
    add_account: bool = False,
) -> Tuple[Account, bool]:
    """Store a finished sign-in; returns ``(account, added)``.

    The same stable id as a stored account updates it in place, keeping its
    default flag. A legacy account with no stable id yet matches by email when
    the account kinds don't conflict; a lone legacy account stored without an
    address matches any sign-in that is not ``add_account``. Anything else is
    added as a new account; another account is never overwritten. Blocking;
    the caller holds the provider lock.
    """
    if not account_id or not email:
        raise ConnectorsError(
            f"save_signed_in_account({connector_id!r}): the account id and email "
            "are both required to tell accounts apart."
        )
    with _mutate(connector_id) as section:
        entries = section["accounts"]
        match = _signed_in_match(entries, account_id, email, account_type, add_account)
        added = match is None
        if added:
            slot = LEGACY_SLOT if not entries else slot_for(account_id)
            # Written before the token so a crash leaves a findable entry.
            match = {"slot": slot, "account_id": account_id, "email": email}
            match["state"] = "pending"
            match["account_type"] = account_type
            entries.append(match)
    slot = match["slot"]
    with _refresh_lock.held_account_sync(connector_id, slot):
        save_connection(
            provider=connector_id,
            slot=slot,
            account_email=email,
            account_id=account_id,
            refresh_token=refresh_token,
            scopes=scopes,
            client_id_hash=client_id_hash,
            tenant=tenant,
            account_type=account_type,
        )
    record_account(
        connector_id,
        slot,
        account_id=account_id,
        email=email,
        state="ok",
        account_type=account_type,
    )
    _evict_token(connector_id, slot)
    account = next((a for a in list_accounts(connector_id) if a.slot == slot), None)
    if account is None:
        raise ConnectorsError(
            f"The {connector_id} sign-in was saved, but its entry is missing from "
            f"the account list at {index_path()}: another GAIA process changed "
            "the list at the same moment. Connect the account again."
        )
    logger.info(
        "accounts: %s %s account in slot %s",
        "added" if added else "updated",
        connector_id,
        "default" if slot == LEGACY_SLOT else "id",
    )
    return account, added


def _unidentified(entry: Dict[str, Any]) -> bool:
    return entry.get("account_id") in (None, "", UNIDENTIFIED)


def _signed_in_match(
    entries: List[Dict[str, Any]],
    account_id: str,
    email: str,
    account_type: Optional[str],
    add_account: bool,
) -> Optional[Dict[str, Any]]:
    """The stored entry a sign-in updates in place, or None to add one."""
    for entry in entries:
        if entry.get("account_id") == account_id:
            return entry
    for entry in entries:
        kind = entry.get("account_type")
        if (
            _unidentified(entry)
            and (entry.get("email") or "").casefold() == email.casefold()
            and (kind is None or account_type is None or kind == account_type)
        ):
            return entry
    # Old builds stored "default" when the address lookup failed.
    if (
        not add_account
        and len(entries) == 1
        and _unidentified(entries[0])
        and entries[0].get("email") in (None, "", DEFAULT_ACCOUNT)
    ):
        return entries[0]
    return None


def remove_account(connector_id: str, slot: str) -> int:
    """Delete one account's token and index entry; returns how many accounts
    remain. Blocking; the caller holds the provider lock."""
    _delete_slot(connector_id, slot)
    with _mutate(connector_id) as section:
        section["accounts"] = [e for e in section["accounts"] if e["slot"] != slot]
        remaining = len(section["accounts"])
    return remaining


def remove_all_accounts(connector_id: str) -> int:
    """Delete every stored account of ``connector_id`` (uninstall, or a full
    disconnect). Returns how many were removed.

    The ``default`` slot goes first, so an unreadable account list (which
    raises) still never leaves the first account's token behind.
    """
    had_legacy = peek_connection(connector_id, slot=LEGACY_SLOT) is not None
    _delete_slot(connector_id, LEGACY_SLOT)
    section = _load_index()["connectors"].get(connector_id) or {}
    slots = {e["slot"] for e in section.get("accounts", [])}
    if had_legacy:
        slots.add(LEGACY_SLOT)
    for slot in slots - {LEGACY_SLOT}:
        _delete_slot(connector_id, slot)
    with _index_locked():
        data = _load_index()
        data["connectors"].pop(connector_id, None)
        _save_index(data)
    return len(slots)


def _delete_slot(connector_id: str, slot: str) -> None:
    with _refresh_lock.held_account_sync(connector_id, slot):
        delete_connection(connector_id, slot=slot)
    _evict_token(connector_id, slot)


def set_default_account(connector_id: str, account: str) -> Account:
    """Make ``account`` (stable id or email) the connector's default."""
    slot = resolve_slot(connector_id, account)
    with _mutate(connector_id) as section:
        section["default_slot"] = slot
    return next(a for a in list_accounts(connector_id) if a.slot == slot)


def mark_state(
    connector_id: str,
    slot: str,
    state: _State,
    *,
    stored: Optional[dict] = None,
) -> None:
    """Record ``state`` for one account. ``stored`` is its last-read blob, used
    to list a legacy account the index did not know about yet."""
    with _mutate(connector_id) as section:
        for entry in section["accounts"]:
            if entry["slot"] == slot:
                if entry.get("state") == state:
                    return
                entry["state"] = state
                break
        else:
            if stored is None:
                return
            section["accounts"].append(
                {
                    "slot": slot,
                    "account_id": stored.get("account_id") or UNIDENTIFIED,
                    "email": stored.get("account_email") or "",
                    "state": state,
                    "account_type": stored.get("account_type"),
                }
            )


def entry_state(connector_id: str, slot: str) -> Optional[str]:
    """The index state of one account, or None when the index has no entry."""
    section = _load_index()["connectors"].get(connector_id) or {}
    for entry in section.get("accounts", []):
        if entry["slot"] == slot:
            return entry.get("state")
    return None


def record_account(
    connector_id: str,
    slot: str,
    *,
    account_id: str,
    email: str,
    state: _State,
    account_type: Optional[str] = None,
) -> None:
    """Add or update one account's index entry."""
    with _mutate(connector_id) as section:
        for entry in section["accounts"]:
            if entry["slot"] == slot:
                break
        else:
            entry = {"slot": slot}
            section["accounts"].append(entry)
        entry.update(
            account_id=account_id, email=email, state=state, account_type=account_type
        )


def record_identity(connector_id: str, slot: str, account_id: str, email: str) -> bool:
    """Fill in the stable id and address of an account stored without them.

    Returns False, writing nothing, when another slot already holds the id or
    ``slot`` is gone: one id is never stored in two slots.
    """
    with _mutate(connector_id) as section:
        entries = section["accounts"]
        if any(
            e["slot"] != slot and e.get("account_id") == account_id for e in entries
        ):
            return False
        for entry in entries:
            if entry["slot"] == slot:
                entry["account_id"] = account_id
                entry["email"] = email
                return True
    return False


def slot_holding(connector_id: str, account_id: str, *, besides: str) -> Optional[str]:
    """Another slot already stored under ``account_id``, or None."""
    for entry in _entries(connector_id, _load_index()):
        if entry["slot"] != besides and entry.get("account_id") == account_id:
            return entry["slot"]
    return None


def drop_duplicate(connector_id: str, *, keep: str, drop: str) -> None:
    """Forget ``drop``, a second slot of the account in ``keep``, after its
    token was deleted. ``keep`` inherits the default flag. The caller holds the
    provider lock and ``drop``'s account lock."""
    with _mutate(connector_id) as section:
        slots = [e["slot"] for e in section["accounts"]]
        if _default_of(section, slots) == drop:
            section["default_slot"] = keep
        section["accounts"] = [e for e in section["accounts"] if e["slot"] != drop]
    _evict_token(connector_id, drop)


def _evict_token(connector_id: str, slot: str) -> None:
    from gaia.connectors.tokens import _cache

    _cache.pop((connector_id, slot), None)


__all__ = [
    "Account",
    "AccountStatus",
    "LEGACY_SLOT",
    "UNIDENTIFIED",
    "default_slot",
    "list_accounts",
    "mark_state",
    "record_account",
    "remove_account",
    "remove_all_accounts",
    "resolve_account",
    "resolve_slot",
    "save_signed_in_account",
    "set_default_account",
]
