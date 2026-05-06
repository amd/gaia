# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Receipt service reference implementations.

Two variants are shipped:

* :class:`InMemoryReceiptService` — ephemeral, for tests and in-process
  inspection.
* :class:`JsonlReceiptService` — append-only JSONL audit log on disk.
  Survives process exit and is trivially tailable / grep-able. This is
  the minimum viable shape for a real audit trail and is the default
  in the governed example.

Both implement :class:`gaia.governance.protocols.ReceiptServiceProtocol`.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, fields
from pathlib import Path
from threading import Lock
from typing import Iterator

from .exceptions import GaiaGovernanceError
from .schemas import ReceiptRecord

logger = logging.getLogger(__name__)


class InMemoryReceiptService:
    """Process-local receipt store. Lost on exit."""

    def __init__(self) -> None:
        self._records: dict[str, ReceiptRecord] = {}
        self._lock = Lock()

    def issue_receipt(self, record: ReceiptRecord) -> str:
        with self._lock:
            self._records[record.receipt_id] = record
        return record.receipt_id

    def get_receipt(self, receipt_id: str) -> ReceiptRecord:
        with self._lock:
            try:
                return self._records[receipt_id]
            except KeyError as exc:
                raise GaiaGovernanceError(f"receipt not found: {receipt_id}") from exc

    def __iter__(self) -> Iterator[ReceiptRecord]:
        with self._lock:
            return iter(list(self._records.values()))


class JsonlReceiptService:
    """Append-only JSONL receipt log on disk.

    Each receipt is serialized as one JSON object per line. Opens the
    file in append mode, flushes on every write, and uses a process-local
    lock so concurrent in-process callers don't interleave lines.

    Intentionally not cross-process safe — use a dedicated receipt
    service (e.g. a log-forwarder or database) for multi-process
    deployments.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._cache: dict[str, ReceiptRecord] = {}
        self._lock = Lock()

    def issue_receipt(self, record: ReceiptRecord) -> str:
        line = json.dumps(asdict(record), allow_nan=False, sort_keys=True)
        with self._lock:
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
                fh.flush()
            self._cache[record.receipt_id] = record
        return record.receipt_id

    def get_receipt(self, receipt_id: str) -> ReceiptRecord:
        with self._lock:
            cached = self._cache.get(receipt_id)
        if cached is not None:
            return cached
        # Cold-read path: scan the log. O(n) but acceptable for audit
        # queries and avoids loading the whole log eagerly. The scan
        # itself does not hold the lock — the JSONL file is append-only
        # and `issue_receipt` flushes line-aligned writes — but the
        # cache install must re-enter the lock so it does not race
        # with concurrent issuers.
        for record in self._read_all():
            if record.receipt_id == receipt_id:
                with self._lock:
                    self._cache[receipt_id] = record
                return record
        raise GaiaGovernanceError(f"receipt not found: {receipt_id}")

    def _read_all(self) -> Iterator[ReceiptRecord]:
        if not self.path.exists():
            return
        known = {f.name for f in fields(ReceiptRecord)}
        with self.path.open("r", encoding="utf-8") as fh:
            for line in fh:
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    data = json.loads(stripped)
                    yield ReceiptRecord(**{k: v for k, v in data.items() if k in known})
                except (json.JSONDecodeError, TypeError, KeyError) as exc:
                    # Skip malformed or schema-mismatched lines, but leave
                    # a breadcrumb for an operator chasing a missing receipt.
                    logger.debug(
                        "receipt log: skipping unreadable line (%s)",
                        type(exc).__name__,
                    )
                except Exception:  # pylint: disable=broad-exception-caught
                    logger.warning(
                        "receipt log: unexpected error deserializing line; skipping",
                        exc_info=True,
                    )

    def __iter__(self) -> Iterator[ReceiptRecord]:
        return self._read_all()
