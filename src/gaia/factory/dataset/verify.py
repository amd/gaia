# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Independently sweep a built dataset for anything that should not have shipped.

Deliberately shares **no code** with :mod:`gaia.factory.dataset.scrub`.  A checker
built from the scrubber's own patterns can only confirm the scrubber agrees with
itself; it cannot catch a rule that was wrong.  These patterns are written from
the threat, not from the fix, and they run over the *written bytes* — records and
blobs both — rather than over in-memory objects.

Exit non-zero on any hit.  There is no override flag: a leak is not a warning.
"""

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Pattern, Tuple

#: Written from the threat model, independently of the scrubber's rules.
LEAK_PATTERNS: List[Tuple[str, Pattern[str]]] = [
    ("windows_user_path", re.compile(r"[A-Za-z]:[\\/]{1,2}Users[\\/]{1,2}")),
    ("posix_home_path", re.compile(r"/(?:home|[a-z]/Users|Users)/[A-Za-z0-9._-]")),
    ("slugified_user_path", re.compile(r"[A-Za-z]--Users-")),
    # Local part must start alphanumeric, or every ``-@pytest.mark.parametrize``
    # in a diff is reported as a leaked address.
    (
        "email_address",
        re.compile(
            r"\b[A-Za-z0-9][A-Za-z0-9._%+-]*@[A-Za-z0-9][A-Za-z0-9.-]*\.[A-Za-z]{2,24}\b"
        ),
    ),
    # ``www.amd.com`` is a public website; a machine name is the threat.
    ("amd_hostname", re.compile(r"\b(?!www\.)[A-Za-z0-9-]+\.(?:amd|xilinx)\.com")),
    (
        "github_token",
        re.compile(r"gh[pousr]_[A-Za-z0-9]{16,}|github_pat_[A-Za-z0-9_]{20,}"),
    ),
    ("openai_key", re.compile(r"\bsk-(?:ant-)?[A-Za-z0-9_-]{20,}")),
    ("aws_key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("slack_token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.")),
    ("private_key_header", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    (
        "meeting_speaker_line",
        re.compile(r"(?m)^[A-Z][a-z]+,\s+[A-Z][a-z]+\s+\d{1,2}:\d{2}\s*$"),
    ),
]


def _iter_scannable(dataset: Path) -> Iterator[Tuple[str, str]]:
    """Yield ``(source_name, text)`` for every string a consumer will actually see.

    Records are **decoded** rather than scanned as raw bytes.  Scanning the raw
    JSONL reports escaping artifacts as leaks: a newline inside a string is
    written ``\\n``, so ``…python\\n@pytest.mark.parametrize`` looks like an
    address whose local part is the letter ``n``.  Decoding covers every string
    in the file, keys included, so nothing is lost by doing it correctly.

    Blobs are plain text with no escaping layer and are scanned as-is.
    """
    for name in ("oracle/records.jsonl", "pool/records.jsonl"):
        path = dataset / name
        if not path.is_file():
            continue
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    yield from ((path.name, s) for s in _strings(json.loads(line)))
    blobs = dataset / "blobs"
    if blobs.is_dir():
        for blob in sorted(blobs.glob("*.txt")):
            yield blob.name, blob.read_text(encoding="utf-8", errors="replace")


def _strings(obj: object) -> Iterator[str]:
    """Every string inside a decoded record, mapping keys included."""
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for key, value in obj.items():
            yield str(key)
            yield from _strings(value)
    elif isinstance(obj, list):
        for value in obj:
            yield from _strings(value)


def sweep(
    dataset: Path, username: Optional[str] = None
) -> Tuple[Counter, List[Dict[str, str]]]:
    """Scan every written byte. Returns hit counts and up to 40 redacted samples."""
    patterns = list(LEAK_PATTERNS)
    if username:
        patterns.append(("corpus_username", re.compile(re.escape(username), re.I)))

    hits: Counter = Counter()
    samples: List[Dict[str, str]] = []
    for source, text in _iter_scannable(dataset):
        for name, pattern in patterns:
            for match in pattern.finditer(text):
                hits[name] += 1
                if len(samples) < 40:
                    found = match.group(0)
                    samples.append(
                        {
                            "rule": name,
                            "file": source,
                            # Length and shape only — a leak report that reprints
                            # the leak is a second copy of the leak.
                            "shape": f"{len(found)} chars starting {found[:4]!r}",
                        }
                    )
    return hits, samples


def spot_check(
    dataset: Path, count: int = 5, seed: str = "spotcheck"
) -> List[Dict[str, object]]:
    """Pull records for hand-verification against their raw transcripts.

    Emits the replay pointer for each, so a human can open the source transcript
    and compare field by field.  Deterministic given ``seed``.
    """
    import hashlib

    records: List[Dict[str, object]] = []
    for name in ("oracle/records.jsonl", "pool/records.jsonl"):
        path = dataset / name
        if not path.is_file():
            continue
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                records.append(json.loads(line))
    records.sort(
        key=lambda r: hashlib.sha256((seed + str(r["record_id"])).encode()).hexdigest()
    )
    return [
        {
            "record_id": r["record_id"],
            "partition": r["partition"],
            "use_case": r["use_case"],
            "session_id": r["session_id"],
            "message_id": r["message_id"],
            "scope": r["scope"],
            "step_index": r["step_index"],
            "depth_index": r["depth_index"],
            "width": r["action"]["width"],
            "tools": [c["tool"] for c in r["action"]["calls"]],
            "reference_quality": r["outcome"]["reference_quality"],
            "reasoning_status": r["reasoning"]["status"],
        }
        for r in records[:count]
    ]


def assert_no_leaks(dataset: Path, username: Optional[str] = None) -> None:
    """Raise unless the written bytes are clean. Called at the end of every build.

    Run as a separate chore this is a step someone forgets. A leak is not a
    warning and there is no override: the corpus is private and this repository
    is public.
    """
    hits, samples = sweep(dataset, username)
    if not hits:
        print(
            f"      leak sweep clean — {len(LEAK_PATTERNS) + bool(username)} patterns"
        )
        return
    raise SystemExit(
        "Leak sweep FAILED — the built dataset contains material the scrubber "
        "should have removed:\n"
        + json.dumps({"hits": dict(hits), "samples": samples[:10]}, indent=2)
        + "\n\nFix the rule in gaia/factory/dataset/scrub.py and rebuild."
    )


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--dataset",
        type=Path,
        default=Path.home() / ".gaia" / "cache" / "factory" / "dataset",
    )
    parser.add_argument(
        "--username", default=None, help="Corpus account name to assert absent."
    )
    parser.add_argument(
        "--spot-check", type=int, default=0, help="Emit N records to hand-verify."
    )
    args = parser.parse_args(argv)

    if args.spot_check:
        print(json.dumps(spot_check(args.dataset, args.spot_check), indent=2))
        return 0

    hits, samples = sweep(args.dataset, args.username)
    report = {
        "dataset": str(args.dataset),
        "clean": not hits,
        "hits": dict(hits),
        "samples": samples,
    }
    print(json.dumps(report, indent=2))
    if hits:
        print(
            "\nLEAK: the built dataset contains material the scrubber should have "
            "removed. Fix the rule in gaia/factory/dataset/scrub.py and rebuild — "
            "there is no override.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
