#!/usr/bin/env python3
# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Build the email-triage eval corpus from the vendor-provided seed.

The corpus is **vendor-derived**, not synthesised by GAIA: the labelled emails
come from the vendor's mailbox dataset (schema-2.0 taxonomy). The committed
**source of truth** is ``vendor_corpus_seed.jsonl`` in this directory — a
deterministic, balanced subset of that dataset (selected by
``select_vendor_subset.py``). This script converts that seed into:

- ``synthetic_inbox.mbox`` (the mbox the eval / ``FakeGmailBackend`` loads)
- ``ground_truth.json`` (per-email labels)

The mbox + ground truth are keyed by the Gmail-derived id
(``sha256(Message-ID)[:16]``) exactly as ``FakeGmailBackend`` derives it, so the
corpus and the labels align 1:1.

Regenerate:  python tests/fixtures/email/generate_mbox.py
Verify:      python tests/fixtures/email/generate_mbox.py --verify

(The filename ``generate_mbox.py`` is kept for continuity with the importers and
docs; the corpus is no longer GAIA-synthesised.)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import mailbox
import sys
import tempfile
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import format_datetime
from pathlib import Path
from typing import Any

# Keep the script runnable standalone even when launched from this directory:
# put the repo root (for ``tests.fixtures``) AND the standalone
# ``gaia_agent_email`` hub package on the path so we can import the *production*
# taxonomy — generated labels can never drift from what the agent emits.
_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
_HUB_EMAIL = _REPO_ROOT / "hub" / "agents" / "python" / "email"
if _HUB_EMAIL.is_dir() and str(_HUB_EMAIL) not in sys.path:
    sys.path.insert(0, str(_HUB_EMAIL))

from gaia_agent_email.tools.triage_heuristics import ALL_CATEGORIES  # noqa: E402

from tests.fixtures.email.fake_gmail import (  # noqa: E402
    mbox_message_to_gmail_payload,
)

SCHEMA_VERSION = 2
# Kept for API compatibility with importers/tests that pass a seed; the corpus
# is now a fixed committed seed file, so there is no RNG to seed.
SEED = 23023

OUT_DIR = Path(__file__).resolve().parent
SEED_JSONL = OUT_DIR / "vendor_corpus_seed.jsonl"
OUT_MBOX = OUT_DIR / "synthetic_inbox.mbox"
OUT_GT = OUT_DIR / "ground_truth.json"

# Sources whose mail is spam (used to set the is_spam ground-truth flag, which
# the vendor encodes via promotional_subtype / the spam source corpora).
_SPAM_SOURCES = {"spamassassin", "ling_spam"}

# Fixed mbox ``From `` separator so the corpus is byte-for-byte deterministic.
_FIXED_FROM = "MAILER-DAEMON Mon Mar  2 08:00:00 2026"


def _load_seed() -> list[dict[str, Any]]:
    if not SEED_JSONL.exists():
        raise FileNotFoundError(
            f"Vendor corpus seed not found: {SEED_JSONL}. It is the committed "
            "source of truth for the corpus; regenerate it from the vendor "
            "dataset with select_vendor_subset.py."
        )
    return [
        json.loads(line)
        for line in SEED_JSONL.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _seed_count() -> int:
    try:
        return len(_load_seed())
    except FileNotFoundError:
        return 0


# Number of messages in the corpus (= committed seed size). Importers read this.
TOTAL_MESSAGES = _seed_count()


def _is_spam(rec: dict) -> bool:
    return (
        rec.get("promotional_subtype") == "spam"
        or rec.get("source_dataset") in _SPAM_SOURCES
    )


def _priority(category: str) -> str:
    return {"URGENT": "high", "PROMOTIONAL": "low"}.get(category, "normal")


def _normalize_body(body: Any) -> str:
    """Coerce a vendor ``body`` into plain text.

    Some source corpora store the body as a JSON-encoded list of lines; flatten
    those to newline-joined text so the email reads naturally.
    """
    if isinstance(body, list):
        return "\n".join(str(x) for x in body)
    if isinstance(body, str):
        stripped = body.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            try:
                parsed = json.loads(stripped)
                if isinstance(parsed, list):
                    return "\n".join(str(x) for x in parsed)
            except (json.JSONDecodeError, TypeError):
                pass
        return body
    return str(body or "")


def _addr_list(value: Any) -> str:
    if not value:
        return ""
    if isinstance(value, list):
        return ", ".join(str(x) for x in value)
    return str(value)


def _parse_date(value: Any) -> datetime:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return datetime(2026, 3, 2, 8, 0, tzinfo=timezone.utc)


def _build_message(rec: dict) -> EmailMessage:
    """Construct a single-part text/plain message from a vendor record.

    Single-part (no multipart boundary) keeps the mbox bytes deterministic — the
    stdlib otherwise generates a random MIME boundary per run.
    """
    msg = EmailMessage()
    msg["From"] = rec.get("sender") or "unknown@example.com"
    to = _addr_list(rec.get("to"))
    if to:
        msg["To"] = to
    cc = _addr_list(rec.get("cc"))
    if cc:
        msg["Cc"] = cc
    subject = (rec.get("subject") or "").strip()
    msg["Subject"] = subject or "(no subject)"
    msg["Date"] = format_datetime(_parse_date(rec.get("date")))
    # Derive the Message-ID from the record's unique ``id`` — the vendor reuses
    # ``message_id`` across some thread/variation rows, which would collide on the
    # Gmail-id (sha256(Message-ID)[:16]); ``id`` is unique per record.
    msg["Message-ID"] = f"<{rec['id']}@mail.amd-gaia.example>"
    msg.set_content(_normalize_body(rec.get("body")))
    return msg


def _meta_for(rec: dict) -> dict[str, Any]:
    # ``category_v1`` is the prior 4-bucket taxonomy label, not an ambiguity
    # signal — the vendor dataset carries no per-email ambiguity flag, so we
    # record ``ambiguous=False`` honestly rather than inventing one.
    category = rec["category"]
    return {
        "category": category,
        "priority": _priority(category),
        "is_spam": _is_spam(rec),
        "is_phishing": bool(rec.get("is_phishing")),
        "is_thread_root": True,
        "has_attachment": False,
        "ambiguous": False,
        "rationale": "",
        "sender_persona": rec.get("mailbox_persona") or "unknown",
        "suggested_action": rec.get("suggestedAction") or "none",
        "source_dataset": rec.get("source_dataset") or "unknown",
    }


def generate(
    out_mbox: Path = OUT_MBOX,
    out_gt: Path = OUT_GT,
    seed: int = SEED,  # noqa: ARG001 — accepted for API compat; corpus is fixed
) -> tuple[str, str]:
    """Build the mbox + ground_truth from the committed vendor seed."""
    records = _load_seed()
    out_mbox.parent.mkdir(parents=True, exist_ok=True)
    if out_mbox.exists():
        out_mbox.unlink()

    box = mailbox.mbox(str(out_mbox), create=True)
    gt: dict[str, Any] = {
        "_meta": {
            "fixture": out_mbox.name,
            "fixture_kind": "vendor-derived",
            "schema_version": SCHEMA_VERSION,
            "taxonomy": list(ALL_CATEGORIES),
            "key": (
                "gmail-id (sha256(Message-ID)[:16]) — aligns with FakeGmailBackend"
            ),
            "comment": (
                "Vendor-provided labelled mailbox dataset, deterministic balanced "
                "subset (vendor_corpus_seed.jsonl). Regenerate with "
                "tests/fixtures/email/generate_mbox.py."
            ),
        }
    }

    for rec in records:
        msg = _build_message(rec)
        mbox_msg = mailbox.mboxMessage(msg)
        mbox_msg.set_from(_FIXED_FROM)
        box.add(mbox_msg)
        payload = mbox_message_to_gmail_payload(msg)
        gid = payload["id"]
        if gid in gt:
            raise ValueError(
                f"Gmail-id collision for {gid} (Message-ID reuse in the seed?)"
            )
        meta = _meta_for(rec)
        meta["thread_id"] = payload["threadId"]
        gt[gid] = meta

    box.flush()
    box.close()
    out_gt.write_text(json.dumps(gt, indent=2, sort_keys=True), encoding="utf-8")

    mbox_size = out_mbox.stat().st_size
    if mbox_size >= 1024 * 1024:
        raise ValueError(f"Generated mbox exceeds 1 MB ({mbox_size} bytes)")

    return _sha256(out_mbox), _sha256(out_gt)


def ensure_corpus(out_mbox: Path = OUT_MBOX, out_gt: Path = OUT_GT) -> None:
    """Build the corpus from the committed seed if it isn't already present.

    ``synthetic_inbox.mbox`` and ``ground_truth.json`` are generated artifacts
    (fully derived from the committed ``vendor_corpus_seed.jsonl``) and are not
    checked in, so a fresh checkout has the seed but not the corpus. Callers that
    need the corpus on disk — the pytest session, the baseline scorer, the eval
    harness — call this first. No-op when both files already exist; raises loudly
    via :func:`_load_seed` if the committed seed is missing.
    """
    if out_mbox.exists() and out_gt.exists():
        return
    generate(out_mbox, out_gt)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify() -> int:
    """Rebuild into a temp dir and compare to the committed fixtures."""
    existing_mbox_hash = _sha256(OUT_MBOX)
    existing_gt_hash = _sha256(OUT_GT)
    with tempfile.TemporaryDirectory() as td:
        temp_mbox = Path(td) / "synthetic_inbox.mbox"
        temp_gt = Path(td) / "ground_truth.json"
        gen_mbox_hash, gen_gt_hash = generate(temp_mbox, temp_gt)
    print(f"existing mbox sha256: {existing_mbox_hash}")
    print(f"generated mbox sha256: {gen_mbox_hash}")
    print(f"existing gt   sha256: {existing_gt_hash}")
    print(f"generated gt   sha256: {gen_gt_hash}")
    if existing_mbox_hash == gen_mbox_hash and existing_gt_hash == gen_gt_hash:
        print("VERIFY OK: checked-in fixtures match deterministic builder output")
        return 0
    print(
        "VERIFY FAILED: committed fixtures differ from a fresh build. "
        "Run 'python tests/fixtures/email/generate_mbox.py' and commit."
    )
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--verify", action="store_true", help="Verify checked-in fixtures"
    )
    parser.add_argument(
        "--seed", type=int, default=SEED, help="Accepted for compatibility; unused"
    )
    args = parser.parse_args()

    if args.verify:
        return verify()

    mbox_hash, gt_hash = generate()
    print(f"Wrote: {OUT_MBOX} ({OUT_MBOX.stat().st_size} bytes)")
    print(f"Wrote: {OUT_GT} ({OUT_GT.stat().st_size} bytes)")
    print(f"messages: {TOTAL_MESSAGES}")
    print(f"mbox sha256: {mbox_hash}")
    print(f"gt   sha256: {gt_hash}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
