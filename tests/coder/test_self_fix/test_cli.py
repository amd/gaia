# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""CLI tests for ``gaia-coder feedback`` and ``gaia-coder self-fix process``."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gaia.coder import cli as coder_cli
from gaia.coder.stores import feedback as feedback_store


def test_cli_feedback_enqueues(
    capsys,
    feedback_db_path: Path,
) -> None:
    """``gaia-coder feedback "<body>" ...`` writes a pending row to feedback.db."""
    rc = coder_cli.main(
        [
            "feedback",
            "classify_failure misfires on timestamped errors",
            "--severity",
            "high",
            "--on",
            "https://github.com/amd/gaia/pull/123",
            "--from-handle",
            "kov",
            "--db-path",
            str(feedback_db_path),
            "--id",
            "fb-cli-1",
        ]
    )
    assert rc == 0
    captured = capsys.readouterr().out.strip()
    assert captured, "CLI should have printed its JSON result"
    parsed = json.loads(captured)
    assert parsed["id"] == "fb-cli-1"
    assert parsed["state"] == "pending"

    conn = feedback_store.open_store(feedback_db_path)
    try:
        row = feedback_store.get_row(conn, "fb-cli-1")
    finally:
        conn.close()
    assert row is not None
    assert row.severity == "high"
    assert row.context_url == "https://github.com/amd/gaia/pull/123"
    assert row.from_handle == "kov"
    assert row.state == "pending"
    assert "classify_failure" in row.body


def test_cli_feedback_rejects_invalid_severity(capsys) -> None:
    """argparse enforces the severity enum.

    Uses ``pytest.raises`` so that an argparse regression that *silently
    accepts* the invalid value fails this test. The earlier
    ``try/except SystemExit`` pattern passed with zero assertions in
    that scenario. Cf. #825 and #829 auto-reviews.
    """
    with pytest.raises(SystemExit) as excinfo:
        coder_cli.main(
            [
                "feedback",
                "body",
                "--severity",
                "nonsense",
            ]
        )
    assert excinfo.value.code != 0


def test_cli_self_fix_subcommand_without_action_prints_help(capsys) -> None:
    """``gaia-coder self-fix`` with no action prints help and exits 0."""
    rc = coder_cli.main(["self-fix"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "process" in out
