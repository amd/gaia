# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The one shape of a message on its way out of the mailbox.

Both backends consume an :class:`OutgoingMessage` and the confirmation prompt
renders the same object, so what a person approves cannot differ from what the
provider receives. :class:`SourceMessage` is what a reply or forward is built
from, read once from the mailbox.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from email.utils import getaddresses
from html.parser import HTMLParser
from typing import List, NamedTuple, Optional, Tuple

_ADDRESS_RE = re.compile(r"[^@\s<>\",;]+@[^@\s<>\",;]+")

_BODY_HTML = "html"
_BODY_TEXT = "text"

_HTML_BREAKS = frozenset({"br", "p", "div", "tr", "li", "h1", "h2", "h3", "h4"})
_HTML_HIDDEN = frozenset({"script", "style", "head", "title"})


class Recipient(NamedTuple):
    name: str
    address: str

    def __str__(self) -> str:
        if not self.name:
            return self.address
        return f'"{self.name}" <{self.address}>'


Recipients = Tuple[Recipient, ...]


@dataclass(frozen=True)
class OutgoingMessage:
    to: Recipients = ()
    cc: Recipients = ()
    bcc: Recipients = ()
    subject: str = ""
    body: str = ""
    body_type: str = _BODY_TEXT
    thread_id: Optional[str] = None
    in_reply_to: Optional[str] = None
    references: Optional[str] = None
    # Graph threads a reply through its source message, not through headers.
    reply_to_id: Optional[str] = None
    # Names of files a draft already carries. New messages have none.
    attachments: Tuple[str, ...] = ()


@dataclass(frozen=True)
class SourceMessage:
    id: str
    sender: Optional[Recipient]
    reply_to: Recipients
    to: Recipients
    cc: Recipients
    subject: str
    body: str
    body_type: str
    thread_id: Optional[str]
    internet_message_id: Optional[str]
    references: Optional[str]
    received: str


def parse_recipients(raw: Optional[str], field: str = "to") -> Recipients:
    """Parse a comma-separated address list, or raise naming what is wrong."""
    text = (raw or "").strip()
    if not text:
        return ()
    if "\r" in text or "\n" in text:
        raise ValueError(f"`{field}` contains a line break. Give plain addresses.")
    parsed = []
    for name, address in getaddresses([text]):
        if not _ADDRESS_RE.fullmatch(address):
            raise ValueError(
                f"`{field}` has an address that can't be sent to: {text!r}. Use "
                "name@example.com, separating several with commas."
            )
        parsed.append(Recipient(name.strip(), address))
    if not parsed:
        raise ValueError(f"`{field}` has no usable address: {text!r}.")
    return tuple(parsed)


def require_recipient(message: OutgoingMessage) -> None:
    if not (message.to or message.cc or message.bcc):
        raise ValueError(
            "This message has no recipients, so it can't be sent. Add one first."
        )


def new_message(
    *, to: str, subject: str, body: str, cc: str = "", bcc: str = ""
) -> OutgoingMessage:
    return OutgoingMessage(
        to=parse_recipients(to, "to"),
        cc=parse_recipients(cc, "cc"),
        bcc=parse_recipients(bcc, "bcc"),
        subject=subject,
        body=body,
    )


def _prefixed(prefix: str, subject: str) -> str:
    if subject.lower().startswith(prefix.lower()):
        return subject
    return f"{prefix} {subject}".strip()


def _without(recipients: Recipients, *excluded: str) -> Recipients:
    """*recipients* minus *excluded* addresses and repeats, order kept."""
    seen = {address.lower() for address in excluded}
    kept: List[Recipient] = []
    for recipient in recipients:
        key = recipient.address.lower()
        if key not in seen:
            seen.add(key)
            kept.append(recipient)
    return tuple(kept)


def reply_message(
    source: SourceMessage, *, body: str, own_address: str, reply_all: bool = False
) -> OutgoingMessage:
    """A threaded reply to *source*, sent where the mail standard says to."""
    sent_it_ourselves = bool(
        source.sender and source.sender.address.lower() == own_address.lower()
    )
    if source.reply_to:
        to = source.reply_to
    elif sent_it_ourselves:
        # Replying to your own message continues to whoever you wrote to.
        to = source.to
    else:
        to = (source.sender,) if source.sender else ()
    cc: Recipients = ()
    if reply_all:
        to = tuple(to) + (() if sent_it_ourselves else source.to)
        to = _without(to, own_address)
        cc = _without(source.cc, own_address, *(r.address for r in to))
    if not (to or cc):
        raise ValueError(
            f"Message {source.id!r} names nobody to reply to. Use send_now with "
            "the address you want."
        )
    chain = " ".join(
        part for part in (source.references, source.internet_message_id) if part
    )
    return OutgoingMessage(
        to=tuple(to),
        cc=cc,
        subject=_prefixed("Re:", source.subject),
        body=body,
        thread_id=source.thread_id,
        in_reply_to=source.internet_message_id,
        references=chain or None,
        reply_to_id=source.id,
    )


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: List[str] = []
        self._hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in _HTML_HIDDEN:
            self._hidden += 1
        elif tag in _HTML_BREAKS:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in _HTML_HIDDEN and self._hidden:
            self._hidden -= 1

    def handle_data(self, data):
        if not self._hidden:
            self.parts.append(data)


def _plain_text(source: SourceMessage) -> str:
    if source.body_type != _BODY_HTML:
        return source.body
    extractor = _TextExtractor()
    extractor.feed(source.body)
    extractor.close()
    return re.sub(r"\n{3,}", "\n\n", "".join(extractor.parts)).strip()


def forward_message(
    source: SourceMessage,
    *,
    to: str,
    note: str = "",
    cc: str = "",
    bcc: str = "",
) -> OutgoingMessage:
    """*source*'s text under a forwarding header. Attachments do not come along."""
    sender = str(source.sender) if source.sender else "(unknown sender)"
    quoted = (
        "----- Forwarded message -----\n"
        f"From: {sender}\n"
        f"Date: {source.received}\n"
        f"Subject: {source.subject}\n"
        f"To: {', '.join(str(r) for r in source.to)}\n\n"
        f"{_plain_text(source)}"
    )
    return new_message(
        to=to,
        cc=cc,
        bcc=bcc,
        subject=_prefixed("Fwd:", source.subject),
        body=f"{note}\n\n{quoted}" if note else quoted,
    )


def render_preview(message: OutgoingMessage) -> str:
    """The message as a person approves it: every recipient, then the whole body."""
    lines = [f"To: {', '.join(str(r) for r in message.to)}"]
    if message.cc:
        lines.append(f"Cc: {', '.join(str(r) for r in message.cc)}")
    if message.bcc:
        lines.append(f"Bcc: {', '.join(str(r) for r in message.bcc)}")
    lines.append(f"Subject: {message.subject}")
    if message.attachments:
        lines.append(f"Attachments: {', '.join(message.attachments)}")
    if message.body_type == _BODY_HTML:
        lines.append("Format: HTML")
    return "\n".join(lines) + f"\n\n{message.body}"


def describe(message: OutgoingMessage) -> dict:
    """The fields a tool result reports, so the model repeats real values."""
    return {
        "to": [str(r) for r in message.to],
        "cc": [str(r) for r in message.cc],
        "bcc": [str(r) for r in message.bcc],
        "subject": message.subject,
        "body": message.body,
        "thread_id": message.thread_id,
        "attachments": list(message.attachments),
    }


__all__ = [
    "OutgoingMessage",
    "Recipient",
    "Recipients",
    "SourceMessage",
    "describe",
    "forward_message",
    "new_message",
    "parse_recipients",
    "render_preview",
    "reply_message",
    "require_recipient",
]
