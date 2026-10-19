# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Record the files each Agent UI turn changes, so the turn can be undone."""

from __future__ import annotations

from typing import Optional

from gaia.agents.base import file_snapshots
from gaia.config import GaiaConfig, GaiaConfigError
from gaia.logger import get_logger

logger = get_logger(__name__)


def retention() -> tuple[int, int]:
    """(days, megabytes) the user allows undo history to keep."""
    config = GaiaConfig.load()
    return config.undo_retention_days, config.undo_max_mb


class TurnRecording:
    """One turn's recording, opened and closed on the thread that runs the agent.

    Tools find the recorder through a context variable, so it must be opened
    on the thread that calls ``process_query``.
    """

    def __init__(self, session_id: str):
        self._cm = file_snapshots.recording_turn(session_id)
        self.recorder = self._cm.__enter__()
        self._closed = False

    def close(self) -> Optional[str]:
        """End the turn and apply retention; returns a warning for the user."""
        if self._closed:
            return None
        self._closed = True
        try:
            self._cm.__exit__(None, None, None)
        except file_snapshots.SnapshotError as e:
            logger.error("Undo history for this turn is incomplete: %s", e)
            return f"Undo may not work for this turn: {e}"
        if not self.recorder.has_changes:
            return None
        try:
            days, mb = retention()
            file_snapshots.prune(days, mb * 1024 * 1024, keep=self.recorder.dir)
        except (OSError, GaiaConfigError, file_snapshots.SnapshotError) as e:
            logger.error("Could not apply the undo-history limits: %s", e)
            return (
                f"Could not trim old undo history: {e}. Check Settings → "
                "Privacy → Undo history."
            )
        return None

    def link_message(self, message_id: Optional[int]) -> Optional[str]:
        """Tie the turn to the assistant message the UI shows it under.

        Returns a warning for the user when that fails: without the link the
        answer never offers Undo.
        """
        if message_id is None:
            return None
        try:
            self.recorder.link_message(message_id)
        except OSError as e:
            logger.error("Could not tie undo history to message %s: %s", message_id, e)
            return (
                f"Undo won't be offered for this answer: GAIA could not save "
                f"which turn it belongs to ({e}). The copies are in "
                f"{self.recorder.dir}."
            )
        return None
