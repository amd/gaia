# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""An offline fixture mailbox for the scenario eval (``gaia_email``).

Set ``GAIA_EVAL_MAILBOX=<path to .mbox>`` on the Agent UI backend and every
mail tool reads that file instead of a connected account. It is served through
the real :class:`GmailReadBackend` over an in-process ``httpx`` transport, so
the eval exercises the same parsing, screening and search code a live Gmail
answer goes through; only the socket is replaced.

While the variable is set the connectors layer is never consulted, so a
developer running the eval on a machine with a real connected mailbox cannot
leak it into a scorecard.

:func:`set_attached` lets one scenario detach the fixture and see exactly the
"no mailbox is connected" answer a new user gets. It is process-wide state,
flipped only by the eval admin endpoint.

Search implements Gmail's query grammar (AND by juxtaposition, ``OR``, ``{}``,
``-``, parentheses) and the operators an agent actually sends. An operator it
cannot answer is an HTTP 400, never a silent empty result.

Eval-only, and it needs a source checkout: the Gmail API message shape comes
from ``tests/fixtures/email/fake_gmail.py``, the fake the email unit tests use.
"""

from __future__ import annotations

import importlib.util
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple
from urllib.parse import parse_qs

import httpx

from gaia.agents.tools._email.errors import MailboxError
from gaia.agents.tools._email.gmail import GMAIL_API_BASE, GmailReadBackend

logger = logging.getLogger(__name__)

EVAL_MAILBOX_ENV = "GAIA_EVAL_MAILBOX"

# The address check_mailbox_access reports for the fixture account.
FIXTURE_ADDRESS = "user@example.com"

_EXCLUDED_FROM_SEARCH = frozenset({"SPAM", "TRASH"})

_LABEL_ALIASES = {
    "inbox": "INBOX",
    "spam": "SPAM",
    "trash": "TRASH",
    "sent": "SENT",
    "drafts": "DRAFT",
    "starred": "STARRED",
    "important": "IMPORTANT",
    "unread": "UNREAD",
    "promotions": "CATEGORY_PROMOTIONS",
    "updates": "CATEGORY_UPDATES",
    "social": "CATEGORY_SOCIAL",
    "forums": "CATEGORY_FORUMS",
    "primary": "CATEGORY_PERSONAL",
    "personal": "CATEGORY_PERSONAL",
}
_LABEL_OPERATORS = frozenset({"in", "label", "is", "category"})
_HEADER_OPERATORS = frozenset({"from", "to", "cc", "subject"})
_DATE_OPERATORS = frozenset(
    {"after", "before", "newer", "older", "newer_than", "older_than"}
)
# Real Gmail operators this fixture cannot answer.
_UNSUPPORTED_OPERATORS = frozenset(
    {
        "bcc",
        "larger",
        "smaller",
        "size",
        "filename",
        "list",
        "deliveredto",
        "rfc822msgid",
        "around",
    }
)

_state_lock = threading.Lock()
_attached = True


class UnsupportedQuery(ValueError):
    """A search the fixture mailbox cannot answer faithfully."""


def fixture_path() -> Optional[str]:
    """The configured fixture mbox, or ``None`` when the eval mode is off."""
    return os.environ.get(EVAL_MAILBOX_ENV) or None


def is_attached() -> bool:
    with _state_lock:
        return _attached


def set_attached(attached: bool) -> None:
    """Attach or detach the fixture for sessions whose mail tools start next."""
    global _attached
    with _state_lock:
        _attached = bool(attached)
    logger.warning("eval fixture mailbox %s", "attached" if attached else "DETACHED")


def _tokenize(query: str) -> List[str]:
    """Split a Gmail query into atoms and ``( ) { } -`` punctuation."""
    tokens: List[str] = []
    i, n = 0, len(query)
    while i < n:
        ch = query[i]
        if ch.isspace():
            i += 1
        elif ch in "(){}":
            tokens.append(ch)
            i += 1
        elif ch == "-" and i + 1 < n and not query[i + 1].isspace():
            tokens.append("-")
            i += 1
        else:
            j, quoted = i, False
            while j < n and (quoted or not (query[j].isspace() or query[j] in "(){}")):
                if query[j] == '"':
                    quoted = not quoted
                j += 1
            tokens.append(query[i:j])
            i = j
    return tokens


class _Parser:
    """Gmail query grammar: juxtaposition is AND, ``OR``/``{}`` is OR, ``-`` NOT.

    ``parse`` returns ``(tree, named)``; ``named`` holds the label ids the query
    names with ``in:``/``label:`` (``"*"`` for ``in:anywhere``), which decides
    whether spam and trash are searched.
    """

    def __init__(self, tokens: List[str]) -> None:
        self._tokens = tokens
        self._pos = 0
        self.named: Set[str] = set()

    def parse(self) -> Tuple[tuple, Set[str]]:
        tree = self._all()
        if self._pos != len(self._tokens):
            raise UnsupportedQuery(f"unbalanced '{self._tokens[self._pos]}'")
        return tree, self.named

    def _peek(self) -> Optional[str]:
        return self._tokens[self._pos] if self._pos < len(self._tokens) else None

    def _all(self) -> tuple:
        children = []
        while self._peek() not in (None, ")", "}"):
            if self._peek() == "AND":
                self._pos += 1
                continue
            children.append(self._any())
        return ("and", children)

    def _any(self) -> tuple:
        children = [self._unary()]
        while self._peek() == "OR":
            self._pos += 1
            children.append(self._unary())
        return children[0] if len(children) == 1 else ("or", children)

    def _unary(self) -> tuple:
        token = self._peek()
        if token is None or token in (")", "}", "OR"):
            raise UnsupportedQuery("a dangling OR, - or bracket")
        self._pos += 1
        if token == "-":
            return ("not", self._unary())
        if token in ("(", "{"):
            close = ")" if token == "(" else "}"
            inner = self._all()
            if self._peek() != close:
                raise UnsupportedQuery(f"unbalanced '{token}'")
            self._pos += 1
            return ("or", inner[1]) if token == "{" else inner
        field, sep, value = token.partition(":")
        if sep and field.lower() in ("in", "label"):
            value = value.strip('"').lower()
            self.named.add(
                "*" if value == "anywhere" else _LABEL_ALIASES.get(value, value.upper())
            )
        return ("atom", token)


def _header(msg: Dict[str, Any], name: str) -> str:
    for header in (msg.get("payload") or {}).get("headers", []):
        if (header.get("name") or "").lower() == name:
            return (header.get("value") or "").lower()
    return ""


def _has_attachment(part: Dict[str, Any]) -> bool:
    if (part.get("body") or {}).get("attachmentId"):
        return True
    return any(_has_attachment(child) for child in part.get("parts") or [])


def _load_fake_gmail():
    """Import ``tests/fixtures/email/fake_gmail.py`` without needing it on sys.path."""
    from gaia.eval.fixture_paths import resolve_repo_fixture

    try:
        path = resolve_repo_fixture("email", "fake_gmail.py")
    except FileNotFoundError as exc:
        raise MailboxError(
            f"{EVAL_MAILBOX_ENV} is set, but the fixture mailbox needs a GAIA "
            "source checkout (tests/fixtures/email/fake_gmail.py). Unset "
            f"{EVAL_MAILBOX_ENV}, or run the backend from the repo root. {exc}"
        ) from exc
    spec = importlib.util.spec_from_file_location("gaia_eval_fake_gmail", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _FixtureGmailApi:
    """Answers the Gmail API v1 GETs ``GmailReadBackend`` makes, from an mbox."""

    def __init__(self, mbox_path: Path) -> None:
        self._fake = _load_fake_gmail()
        self._store = self._fake.FakeGmailBackend(mbox_path, user_email=FIXTURE_ADDRESS)
        self._messages: Dict[str, Dict[str, Any]] = self._store._messages

    def handle(self, request: httpx.Request) -> httpx.Response:
        prefix = httpx.URL(GMAIL_API_BASE).path
        path = request.url.path[len(prefix) :]
        query = parse_qs(request.url.query.decode("ascii"))
        parts = path.strip("/").split("/")
        if parts[:2] != ["users", "me"]:
            return self._error(404, f"unknown path {path}")
        rest = parts[2:]
        if rest == ["profile"]:
            return httpx.Response(200, json={"emailAddress": FIXTURE_ADDRESS})
        if rest == ["labels"]:
            return httpx.Response(200, json={"labels": self._labels()})
        if len(rest) == 2 and rest[0] == "labels":
            return httpx.Response(200, json=self._store.get_label(rest[1]))
        if rest == ["messages"]:
            try:
                return httpx.Response(200, json=self._list(query))
            except UnsupportedQuery as exc:
                # The backend surfaces only Gmail's `reason` token, so name the
                # operator in the log where the eval transcript picks it up.
                logger.warning(
                    "eval fixture mailbox cannot answer query part %r", str(exc)
                )
                return self._error(400, str(exc), reason="fixtureUnsupportedQuery")
        if len(rest) == 2 and rest[0] == "messages":
            if rest[1] not in self._messages:
                return self._error(404, f"no message {rest[1]}")
            fmt = (query.get("format") or ["full"])[0]
            return httpx.Response(
                200, json=self._store.get_message(rest[1], format=fmt)
            )
        return self._error(404, f"unknown path {path}")

    def _labels(self) -> List[Dict[str, Any]]:
        used = {lid for m in self._messages.values() for lid in m.get("labelIds", [])}
        return [{"id": lid, "name": lid, "type": "system"} for lid in sorted(used)]

    def _list(self, query: Dict[str, List[str]]) -> Dict[str, Any]:
        wanted = set(query.get("labelIds") or [])
        limit = int((query.get("maxResults") or ["25"])[0])
        q = (query.get("q") or [""])[0]
        tree, named = _Parser(_tokenize(q)).parse() if q.strip() else (None, set())
        # Real Gmail search skips spam and trash unless the query names them.
        searched = named & (_EXCLUDED_FROM_SEARCH | {"*"})
        skip = set() if searched else _EXCLUDED_FROM_SEARCH
        now = time.time()
        hits = []
        for msg in self._messages.values():
            labels = set(msg.get("labelIds", []))
            if not wanted <= labels:
                continue
            if not wanted and labels & skip:
                continue
            if tree is not None and not self._eval(tree, msg, now):
                continue
            hits.append(msg)
        hits.sort(key=lambda m: int(m.get("internalDate", "0")), reverse=True)
        found = [{"id": m["id"], "threadId": m["threadId"]} for m in hits[:limit]]
        # Gmail omits the key, rather than sending [], when nothing matches.
        return {"messages": found} if found else {"resultSizeEstimate": 0}

    def _eval(self, node: tuple, msg: Dict[str, Any], now: float) -> bool:
        kind = node[0]
        if kind == "and":
            return all(self._eval(child, msg, now) for child in node[1])
        if kind == "or":
            return any(self._eval(child, msg, now) for child in node[1])
        if kind == "not":
            return not self._eval(node[1], msg, now)
        return self._atom(node[1], msg, now)

    def _atom(self, atom: str, msg: Dict[str, Any], now: float) -> bool:
        field, sep, value = atom.partition(":")
        field, value = field.lower(), value.strip('"').lower()
        if sep and field in _LABEL_OPERATORS:
            labels = set(msg.get("labelIds", []))
            if value == "anywhere":
                return True
            if value == "read":
                return "UNREAD" not in labels
            return _LABEL_ALIASES.get(value, value.upper()) in labels
        if sep and field in _HEADER_OPERATORS:
            if field == "to" and value == "me":
                value = FIXTURE_ADDRESS
            return value in _header(msg, field)
        if sep and field == "has":
            if value != "attachment":
                raise UnsupportedQuery(atom)
            return _has_attachment(msg.get("payload") or {})
        if sep and field in _DATE_OPERATORS:
            verdict = self._fake._date_operator_matches(f"{field}:{value}", msg, now)
            if verdict is None:
                raise UnsupportedQuery(atom)
            return verdict
        if sep and field in _UNSUPPORTED_OPERATORS:
            raise UnsupportedQuery(atom)
        # Free text; Gmail also matches it against the sender.
        needle = atom.strip('"').lower()
        return needle in _header(msg, "from") or needle in (
            self._fake._searchable_text(msg).lower()
        )

    @staticmethod
    def _error(code: int, message: str, reason: str = "notFound") -> httpx.Response:
        body = {"code": code, "message": message, "errors": [{"reason": reason}]}
        return httpx.Response(code, json={"error": body})


def build_fixture_backend(mbox_path: str) -> GmailReadBackend:
    """A ``GmailReadBackend`` whose HTTP calls are answered from ``mbox_path``."""
    path = Path(mbox_path).expanduser()
    if not path.is_file():
        raise MailboxError(
            f"{EVAL_MAILBOX_ENV} points at {path}, which does not exist. Build it "
            "with `python tests/fixtures/gaia/email/build_mailbox.py`, or unset "
            f"{EVAL_MAILBOX_ENV} to use a connected mailbox."
        )
    api = _FixtureGmailApi(path)
    client = httpx.Client(transport=httpx.MockTransport(api.handle))
    logger.warning("email tools are reading the eval fixture mailbox %s", path)
    return GmailReadBackend(lambda: "eval-fixture-token", http_client=client)


__all__ = [
    "EVAL_MAILBOX_ENV",
    "FIXTURE_ADDRESS",
    "UnsupportedQuery",
    "build_fixture_backend",
    "fixture_path",
    "is_attached",
    "set_attached",
]
