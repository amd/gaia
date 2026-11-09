# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Refuse evidence files that contain a credential, before they go public.

Usage: python scan_evidence.py FILE...

Images and videos can't be scanned as text, so each gets a manual-review
reminder instead. Every other file is read as text and checked against the
same patterns ``gaia.llm.failure_record.redact`` removes. Exit 0 when clean,
1 when any file matches (file and pattern named, never the matched value),
2 when a file can't be read.
"""

import sys
from pathlib import Path

# Scan with this checkout's patterns, not whichever gaia is installed.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from gaia.llm.failure_record import find_secrets  # noqa: E402

MEDIA_SUFFIXES = frozenset(
    {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".mp4", ".webm", ".mov"}
)


def main(argv):
    if not argv:
        print("usage: scan_evidence.py FILE...", file=sys.stderr)
        return 2
    hits = 0
    for name in argv:
        path = Path(name)
        if path.suffix.lower() in MEDIA_SUFFIXES:
            print(
                f"review by eye before sharing: {path} (images and video are "
                "not scanned; check for keys, tokens, emails and personal data)",
                file=sys.stderr,
            )
            continue
        try:
            text = path.read_bytes().decode("utf-8", errors="replace")
        except OSError as e:
            print(f"error: cannot read {path} to scan it: {e}", file=sys.stderr)
            return 2
        for pattern in find_secrets(text):
            hits += 1
            print(f"blocked: {path} matches secret pattern {pattern}", file=sys.stderr)
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
