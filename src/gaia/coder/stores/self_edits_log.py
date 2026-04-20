# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""``self-edits.log`` — append-only JSONL of every self-edit PR.

Backs §6.4 (self-fix transparency) of ``docs/plans/coder-agent.mdx``. One JSON
object per line, newline-terminated. Each record captures the PR URL, fix
class, touched files, before/after SHAs, review-pass verdicts, confidence,
EM review outcome, auto-merge flag, and the originating ``feedback_id``.

The log is append-only on the write side. Reads iterate the file line by
line and parse each record.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterator, Optional

from pydantic import BaseModel


class ReviewPasses(BaseModel):
    """Verdicts from each review pass (§8)."""

    static: str
    functional: str
    arch: str
    security: str
    prose: str
    adversarial: str
    feedback_binding: str


class SelfEditRecord(BaseModel):
    """One line of ``self-edits.log``; schema per §15.1."""

    ts: str
    pr: str
    fix_class: str
    files: list[str]
    before_sha: str
    after_sha: str
    review_passes: ReviewPasses
    confidence: int
    em_review: str
    auto_merged: bool
    feedback_id: Optional[str] = None


def append(log_path: str | Path, record: SelfEditRecord) -> None:
    """Append ``record`` to ``log_path`` as a single JSON line.

    The parent directory is created if missing. The file is opened in
    line-buffered append mode so concurrent appends on POSIX are safe for
    records shorter than ``PIPE_BUF`` (4096 bytes on Linux/macOS).
    """
    path = Path(log_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    line = record.model_dump_json()
    # ``model_dump_json`` does not include a trailing newline.
    with path.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def iter_records(log_path: str | Path) -> Iterator[SelfEditRecord]:
    """Yield every record in ``log_path`` as a :class:`SelfEditRecord`.

    Skips empty lines. Raises :class:`ValueError` via Pydantic if a line is
    corrupt — the caller should decide whether to fail loudly or log.
    """
    path = Path(log_path)
    if not path.exists():
        return
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            stripped = line.strip()
            if not stripped:
                continue
            yield SelfEditRecord.model_validate_json(stripped)


def read_all(log_path: str | Path) -> list[SelfEditRecord]:
    """Read every record from ``log_path`` into a list."""
    return list(iter_records(log_path))


def append_dict(log_path: str | Path, payload: dict[str, Any]) -> None:
    """Convenience: append a raw ``dict`` after validating it against the schema."""
    append(log_path, SelfEditRecord.model_validate(payload))
