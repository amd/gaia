# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Refuse evidence files that contain a credential, before they go public.

Usage: python scan_evidence.py FILE...

A file that really is an image or video (by extension and magic bytes) can't be
scanned as text, so it gets a manual-review reminder instead. Every other file
must be UTF-8 text, and is checked against the patterns
``gaia.llm.failure_record.redact`` removes; anything else (an archive, gzip,
UTF-16) is refused, since a key inside it can't be seen. Exit 0 when clean,
1 when any file is refused (file and pattern named, never the matched value),
2 when a file can't be read.
"""

import sys
from pathlib import Path

# Scan with this checkout's patterns, not whichever gaia is installed.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from gaia.llm.failure_record import matched_pattern_names  # noqa: E402


def _is_png(head):
    return head.startswith(b"\x89PNG\r\n\x1a\n")


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


def _is_media(path, data):
    check = MEDIA_MAGIC.get(path.suffix.lower())
    return check is not None and check(data[:16])


def main(argv):
    if not argv:
        print("usage: scan_evidence.py FILE...", file=sys.stderr)
        return 2
    refused = 0
    for name in argv:
        path = Path(name)
        try:
            data = path.read_bytes()
        except OSError as e:
            print(f"error: cannot read {path} to scan it: {e}", file=sys.stderr)
            return 2
        if _is_media(path, data):
            print(
                f"review by eye before sharing: {path} (images and video are "
                "not scanned; check for keys, tokens, emails and personal data)",
                file=sys.stderr,
            )
            continue
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            text = None
        if text is None or "\x00" in text:
            refused += 1
            print(
                f"blocked: {path} is not UTF-8 text (an archive, gzip or UTF-16?), "
                "so it can't be scanned; upload it unpacked or re-saved as UTF-8",
                file=sys.stderr,
            )
            continue
        for kind in matched_pattern_names(text):
            refused += 1
            print(f"blocked: {path} contains a {kind}", file=sys.stderr)
    return 1 if refused else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
