# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Refuse evidence files that contain a credential, before they go public.

Usage: python scan_evidence.py [--ref REF] FILE...

Every file is checked against the patterns ``gaia.llm.failure_record.redact``
removes, and so is its name and the ``--ref`` it will be filed under. A file
that really is an image or video (by extension and magic bytes) is scanned as
raw bytes, Latin-1 and UTF-16, so text in its metadata (EXIF, PNG text chunks,
bytes appended after the image) is caught; compressed PNG text chunks are
unpacked first, and a malformed PNG or one with data after its end is refused.
Pixels can't be scanned, so each image also gets a review-by-eye reminder.
Every other file must be UTF-8 text; anything else (an archive, gzip, UTF-16)
is refused, since a key inside it can't be seen. Symlinks are refused.

Exit 0 when clean, 1 when any file is refused (file and pattern named, never
the matched value), 2 when a file can't be read.
"""

import re
import struct
import sys
import zlib
from pathlib import Path

# Scan with this checkout's patterns, not whichever gaia is installed.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from gaia.llm.failure_record import matched_pattern_names  # noqa: E402

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
#: Largest unpacked PNG text chunk; a bigger one is refused, not truncated.
MAX_PNG_TEXT_BYTES = 16 * 1024 * 1024


def _is_png(head):
    return head.startswith(PNG_SIGNATURE)


def _is_riff_webp(head):
    return head[:4] == b"RIFF" and head[8:12] == b"WEBP"


def _is_iso_media(head):
    return head[4:8] == b"ftyp"


MEDIA_MAGIC = {
    ".png": _is_png,
    ".jpg": lambda head: head.startswith(b"\xff\xd8\xff"),
    ".jpeg": lambda head: head.startswith(b"\xff\xd8\xff"),
    ".gif": lambda head: head[:6] in (b"GIF87a", b"GIF89a"),
    ".webp": _is_riff_webp,
    ".bmp": lambda head: head.startswith(b"BM"),
    ".mp4": _is_iso_media,
    ".mov": _is_iso_media,
    ".webm": lambda head: head.startswith(b"\x1a\x45\xdf\xa3"),
}


class Unscannable(Exception):
    """The file's content can't be fully read, so it can't be cleared."""


def _is_media(path, data):
    check = MEDIA_MAGIC.get(path.suffix.lower())
    return check is not None and check(data[:16])


def _inflate(blob):
    inflater = zlib.decompressobj()
    try:
        out = inflater.decompress(blob, MAX_PNG_TEXT_BYTES)
    except zlib.error as e:
        raise Unscannable(f"a compressed PNG text chunk is corrupt ({e})") from e
    if inflater.unconsumed_tail:
        raise Unscannable("a compressed PNG text chunk is too large to scan")
    return out


def _png_texts(data):
    """Unpacked zTXt/iTXt text; refuses a truncated PNG or data after IEND."""
    texts = []
    pos = len(PNG_SIGNATURE)
    while True:
        if pos + 8 > len(data):
            raise Unscannable("the PNG is truncated")
        length, kind = struct.unpack(">I4s", data[pos : pos + 8])
        body = data[pos + 8 : pos + 8 + length]
        if len(body) != length or pos + 12 + length > len(data):
            raise Unscannable("the PNG is truncated")
        pos += 12 + length
        if kind == b"zTXt":
            _, _, rest = body.partition(b"\x00")
            texts.append(_inflate(rest[1:]))
        elif kind == b"iTXt":
            _, _, rest = body.partition(b"\x00")
            compressed, rest = rest[:1], rest[2:]
            _, _, rest = rest.partition(b"\x00")
            _, _, text = rest.partition(b"\x00")
            texts.append(_inflate(text) if compressed == b"\x01" else text)
        elif kind == b"IEND":
            if pos != len(data):
                raise Unscannable("the PNG has data after its end")
            return texts


_NON_ASCII = re.compile(r"[^\t\n\r\x20-\x7e]+")


def _byte_views(data):
    """*data* read every way a text string could sit in a binary file.

    Non-ASCII runs become line breaks, so a key right after a binary byte that
    decodes as a letter (byte 0xFE is ``þ`` in Latin-1) still sits on a word
    boundary.
    """
    for view in (
        data.decode("latin-1"),
        data.decode("utf-16-le", errors="replace"),
        data[1:].decode("utf-16-le", errors="replace"),
    ):
        yield _NON_ASCII.sub("\n", view)


def _media_hits(path, data):
    blobs = [data]
    if path.suffix.lower() == ".png":
        blobs += _png_texts(data)
    hits = []
    for blob in blobs:
        for view in _byte_views(blob):
            hits += [k for k in matched_pattern_names(view) if k not in hits]
    return hits


def _refuse(message):
    print(f"blocked: {message}", file=sys.stderr)


def main(argv):
    ref = None
    if argv[:1] == ["--ref"]:
        if len(argv) < 2:
            print("usage: scan_evidence.py [--ref REF] FILE...", file=sys.stderr)
            return 2
        ref, argv = argv[1], argv[2:]
    if not argv:
        print("usage: scan_evidence.py [--ref REF] FILE...", file=sys.stderr)
        return 2
    refused = 0
    if ref is not None:
        for kind in matched_pattern_names(ref):
            refused += 1
            _refuse(f"the ref contains a {kind}; pick a plain ref like pr-1234")
    for index, name in enumerate(argv, 1):
        path = Path(name)
        # A name that matches is never echoed: it is the secret.
        name_hits = matched_pattern_names(path.name)
        if name_hits:
            refused += len(name_hits)
            for kind in name_hits:
                _refuse(f"file {index} of {len(argv)} has a {kind} in its name")
            continue
        if path.is_symlink():
            refused += 1
            _refuse(f"{path} is a symlink; pass the file it points to")
            continue
        try:
            data = path.read_bytes()
        except OSError as e:
            print(f"error: cannot read {path} to scan it: {e}", file=sys.stderr)
            return 2
        if _is_media(path, data):
            try:
                hits = _media_hits(path, data)
            except Unscannable as e:
                refused += 1
                _refuse(f"{path} can't be scanned: {e}")
                continue
            for kind in hits:
                refused += 1
                _refuse(f"{path} has a {kind} in its metadata or embedded text")
            if not hits:
                print(
                    f"review by eye before sharing: {path} (its metadata was "
                    "scanned, its pixels can't be; check for keys, tokens, emails "
                    "and personal data)",
                    file=sys.stderr,
                )
            continue
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            text = None
        if text is None or "\x00" in text:
            refused += 1
            _refuse(
                f"{path} is not UTF-8 text (an archive, gzip or UTF-16?), "
                "so it can't be scanned; upload it unpacked or re-saved as UTF-8"
            )
            continue
        for kind in matched_pattern_names(text):
            refused += 1
            _refuse(f"{path} contains a {kind}")
    return 1 if refused else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
