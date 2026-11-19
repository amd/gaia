# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The one shape of a message on its way out of the mailbox.

Both backends consume an :class:`OutgoingMessage` and the confirmation prompt
renders the same object, so what a person approves cannot differ from what the
provider receives. :class:`SourceMessage` is what a reply or forward is built
from, read once from the mailbox.
"""

from __future__ import annotations

import dataclasses
import re
import unicodedata
from dataclasses import dataclass, replace
from email.utils import getaddresses
from html.parser import HTMLParser
from typing import List, NamedTuple, Optional, Tuple

import idna

_ADDRESS_RE = re.compile(r"[^@\s<>\",;]+@[^@\s<>\",;]+")

_BODY_HTML = "html"
_BODY_TEXT = "text"

_HTML_BREAKS = frozenset({"br", "p", "div", "tr", "li", "h1", "h2", "h3", "h4"})
_HTML_HIDDEN = frozenset({"script", "style", "head", "title"})

# Controls that can hide or reorder text on the approver's screen. Tab and newline
# are layout, and the marks RTL writing needs (LRM, RLM, ZWJ, ZWNJ) can't reorder.
_HIDDEN_RE = re.compile(
    "[\x00-\x08\x0b-\x1f\x7f-\x9f\u2028\u2029\u202a-\u202e\u2066-\u2069]"
)
_HIDDEN_NOTE = "Hidden characters are shown as [U+XXXX]. They are sent as they are."


class Recipient(NamedTuple):
    name: str
    address: str

    def __str__(self) -> str:
        if not self.name or self.name.lower() == self.address.lower():
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
    # A forward carries text only; these files on the original stay behind.
    attachments_left_out: Tuple[str, ...] = ()
    # The HTML half of a multipart draft, which most mail apps show first.
    html_alternative: Optional[str] = None
    # The saved draft an edit overwrites. Shown on its prompt, never sent.
    replaces: Optional["OutgoingMessage"] = None
    # The account it goes out from, shown on the prompt and checked again
    # before the write. Not part of equality: a fetched draft doesn't carry it.
    sender: Optional[str] = dataclasses.field(default=None, compare=False)


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
    attachments: Tuple[str, ...] = ()


def _refuse_controls(text: str, field: str) -> None:
    for char in text:
        if char != "\t" and unicodedata.category(char) in ("Cc", "Zl", "Zp"):
            raise ValueError(
                f"`{field}` contains a line break or control character "
                f"(U+{ord(char):04X}), which could disguise what is sent. Remove it "
                "and try again."
            )


def _sendable_address(address: str, field: str) -> str:
    """*address* as it goes on the wire: an international domain in its ASCII form."""
    if not _ADDRESS_RE.fullmatch(address):
        raise ValueError(
            f"`{field}` has an address that can't be sent to: {address!r}. Use "
            "name@example.com, separating several with commas."
        )
    local, _, domain = address.rpartition("@")
    if not local.isascii():
        raise ValueError(
            f"`{field}` has non-ASCII characters before the @ in {address!r}, which "
            "GAIA can't send. Check the address with the recipient."
        )
    if domain.isascii():
        return address
    try:
        ascii_domain = idna.encode(domain, uts46=True).decode("ascii")
    except idna.IDNAError as exc:
        raise ValueError(
            f"`{field}` has a domain that can't be sent to: {domain!r} ({exc}). Check "
            "the spelling."
        ) from exc
    return f"{local}@{ascii_domain}"


def _sendable_recipient(recipient: Recipient, field: str) -> Recipient:
    name, address = recipient
    _refuse_controls(name, field)
    _refuse_controls(address, field)
    if name.lower() == address.lower():
        name = ""
    if any(unicodedata.category(char) == "Cf" for char in name):
        raise ValueError(
            f"`{field}` has a display name with invisible or direction-changing "
            f"characters, so it may not read as it looks: {address}. Give the bare "
            "address instead."
        )
    if any(char in "@<>" for char in name):
        raise ValueError(
            f"`{field}` has a display name that looks like an address but isn't "
            f"the one receiving the mail, {address}. Give the bare address instead."
        )
    return Recipient(name, _sendable_address(address, field))


def checked(message: OutgoingMessage) -> OutgoingMessage:
    """*message* made safe to show and send, or raise naming the field at fault.

    Both providers pass through here, because Graph has no MIME library to refuse
    a line break. Idempotent, so a saved draft can be re-read and compared.
    """
    _refuse_controls(message.subject, "subject")
    for field, value in (
        ("in_reply_to", message.in_reply_to),
        ("references", message.references),
    ):
        _refuse_controls(value or "", field)
    return replace(
        message,
        to=tuple(_sendable_recipient(r, "to") for r in message.to),
        cc=tuple(_sendable_recipient(r, "cc") for r in message.cc),
        bcc=tuple(_sendable_recipient(r, "bcc") for r in message.bcc),
    )


def parse_recipients(raw: Optional[str], field: str = "to") -> Recipients:
    """Parse a comma-separated address list, or raise naming what is wrong."""
    text = (raw or "").strip()
    if not text:
        return ()
    _refuse_controls(text, field)
    parsed = []
    for name, address in getaddresses([text]):
        parsed.append(_sendable_recipient(Recipient(name.strip(), address), field))
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


def _bare(recipients: Recipients) -> Recipients:
    """Addresses only: a name on inbound mail is the sender's to choose, so it can lie."""
    return tuple(Recipient("", r.address) for r in recipients)


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
            f"Message {source.id!r} names nobody to reply to. Use "
            "send-email/send_now with the address you want."
        )
    chain = " ".join(
        part for part in (source.references, source.internet_message_id) if part
    )
    return OutgoingMessage(
        to=_bare(tuple(to)),
        cc=_bare(cc),
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
    """*source*'s text under a forwarding header. Its attachments are named, not sent."""
    sender = str(source.sender) if source.sender else "(unknown sender)"
    quoted = (
        "----- Forwarded message -----\n"
        f"From: {sender}\n"
        f"Date: {source.received}\n"
        f"Subject: {source.subject}\n"
        f"To: {', '.join(str(r) for r in source.to)}\n\n"
        f"{_plain_text(source)}"
    )
    return replace(
        new_message(
            to=to,
            cc=cc,
            bcc=bcc,
            subject=_prefixed("Fwd:", source.subject),
            body=f"{note}\n\n{quoted}" if note else quoted,
        ),
        attachments_left_out=source.attachments,
    )


def _visible(text: str) -> str:
    return _HIDDEN_RE.sub(
        lambda match: f"[U+{ord(match.group()):04X}]", text.replace("\r\n", "\n")
    )


def _visible_line(text: str) -> str:
    return _visible(text).replace("\n", "[U+000A]")


def _names(recipients: Recipients) -> str:
    return _visible_line(", ".join(str(r) for r in recipients))


def render_preview(message: OutgoingMessage) -> str:
    """The message as a person approves it: every recipient, then the whole body."""
    lines = [f"From: {_visible_line(message.sender)}"] if message.sender else []
    lines.append(f"To: {_names(message.to)}")
    if message.cc:
        lines.append(f"Cc: {_names(message.cc)}")
    if message.bcc:
        lines.append(f"Bcc: {_names(message.bcc)}")
    lines.append(f"Subject: {_visible_line(message.subject)}")
    if message.attachments:
        lines.append(f"Attachments: {_visible_line(', '.join(message.attachments))}")
    if message.attachments_left_out:
        left_out = _visible_line(", ".join(message.attachments_left_out))
        lines.append(f"Attachments left out, not sent: {left_out}")
    if message.body_type == _BODY_HTML:
        lines.append("Format: HTML")
    html = message.html_alternative
    shown = "\n".join(lines) + f"\n\n{_visible(message.body)}"
    if html:
        shown += (
            "\n\n----- HTML version, which most mail apps show instead of the text "
            f"above -----\n{_visible(html)}"
        )
    written = (message.subject, message.body, html or "")
    if any(_HIDDEN_RE.search(text.replace("\r\n", "\n")) for text in written):
        shown = f"{_HIDDEN_NOTE}\n\n{shown}"
    if message.replaces:
        shown += (
            "\n\n----- This replaces the saved draft -----\n"
            f"{render_preview(message.replaces)}"
        )
    return shown


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
        "attachments_left_out": list(message.attachments_left_out),
    }


__all__ = [
    "OutgoingMessage",
    "Recipient",
    "Recipients",
    "SourceMessage",
    "checked",
    "describe",
    "forward_message",
    "new_message",
    "parse_recipients",
    "render_preview",
    "reply_message",
    "require_recipient",
]
