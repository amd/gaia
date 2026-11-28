# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A model pull's running byte count, from Lemonade's streamed progress events."""

from __future__ import annotations

from typing import Any, Mapping, Optional


class DownloadTally:
    """Sums a pull's bytes across files.

    Lemonade reports bytes per file, so a model's bytes are the files already
    finished plus the one in flight.
    """

    def __init__(self, planned_bytes: Optional[int] = None):
        self.planned_bytes = planned_bytes or 0
        self.bytes_done = 0
        self.bytes_total = self.planned_bytes
        self._finished = 0
        self._file: Optional[str] = None
        self._file_total = 0

    def add(self, event: Mapping[str, Any]) -> None:
        """Count one ``progress`` event."""
        name = event.get("file") or ""
        done = int(event.get("bytes_downloaded") or 0)
        if self._file is not None and name != self._file:
            self._finished += self._file_total
            self._file_total = 0
        self._file = name
        # Mid-file events can carry bytes_total 0, so keep the largest seen.
        self._file_total = max(
            self._file_total, int(event.get("bytes_total") or 0), done
        )
        self.bytes_done = self._finished + done
        total = int(event.get("total_download_size") or 0)
        self.bytes_total = max(
            total or self.planned_bytes or self._finished + self._file_total,
            self.bytes_done,
        )
