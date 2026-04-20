# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""CLI-level tests for the Phase-5 trust/inbox verbs (§4.2, §4.5).

Every test runs the CLI as a *subprocess* so the entry-point, argparse
wiring, and env-var handling are exercised end-to-end — the same path a
real user hits.

``GAIA_CODER_HOME`` is overridden to a pytest ``tmp_path`` so no test
touches the user's real state.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def _run_cli(
    *args: str,
    home: Path,
    check: bool = False,
) -> subprocess.CompletedProcess:
    """Invoke ``python -m gaia.coder.cli <args>`` with isolated state."""
    env = {
        **os.environ,
        "GAIA_CODER_HOME": str(home),
        # Ensure the worktree's source wins over any editable install of
        # ``gaia`` that doesn't ship ``gaia.coder`` yet.
        "PYTHONPATH": str(REPO_ROOT / "src")
        + os.pathsep
        + os.environ.get("PYTHONPATH", ""),
    }
    return subprocess.run(
        [sys.executable, "-m", "gaia.coder.cli", *args],
        env=env,
        capture_output=True,
        text=True,
        check=check,
    )


def test_cli_trust_on_fresh_install_prints_bootstrap(tmp_path: Path) -> None:
    """With no em.toml, ``trust`` halts with the §4.1 bootstrap question."""
    result = _run_cli("trust", home=tmp_path)
    assert result.returncode == 0
    assert "no Engineering Manager bound" in result.stdout
    assert "Who is my engineering manager" in result.stdout
    assert "§4.1" in result.stdout


def test_cli_trust_prints_tier_summary(tmp_path: Path) -> None:
    """After bootstrap, ``trust`` renders the §4.2 template with the expected labels."""
    boot = _run_cli(
        "trust",
        "--bootstrap",
        "--em-handle",
        "kovtcharov-amd",
        "--em-channel",
        "github-issue-comment",
        "--persona-name",
        "Coda",
        home=tmp_path,
    )
    assert boot.returncode == 0, boot.stderr

    result = _run_cli("trust", home=tmp_path)
    assert result.returncode == 0, result.stderr
    out = result.stdout
    # Load-bearing labels the EM relies on scanning for. A typo in any of
    # these breaks the "first-glance view of the trust contract" (§4.2).
    assert "Tier:" in out
    assert "EM:" in out
    assert "At this tier you may:" in out
    assert "At this tier you may NOT yet:" in out
    assert "@kovtcharov-amd" in out


def test_cli_promote_and_history(tmp_path: Path) -> None:
    """End-to-end: bootstrap → promote → ``trust --history`` shows the event."""
    _run_cli(
        "trust",
        "--bootstrap",
        "--em-handle",
        "kovtcharov-amd",
        "--em-channel",
        "cli",
        home=tmp_path,
        check=True,
    )
    r = _run_cli(
        "promote",
        "--to-tier",
        "2",
        "--reason",
        "ready to branch",
        "--em-signature",
        "kovtcharov-amd",
        home=tmp_path,
    )
    assert r.returncode == 0, r.stderr
    assert "tier 2" in r.stdout.lower()

    hist = _run_cli("trust", "--history", home=tmp_path)
    assert hist.returncode == 0
    assert "promote" in hist.stdout
    assert "Tier 0 → 2" in hist.stdout
    assert "ready to branch" in hist.stdout


def test_cli_promote_without_matching_signature_rejects(tmp_path: Path) -> None:
    _run_cli(
        "trust",
        "--bootstrap",
        "--em-handle",
        "kovtcharov-amd",
        "--em-channel",
        "cli",
        home=tmp_path,
        check=True,
    )
    r = _run_cli(
        "promote",
        "--to-tier",
        "2",
        "--reason",
        "nope",
        "--em-signature",
        "some-other-user",
        home=tmp_path,
    )
    assert r.returncode == 1
    assert "Promotion rejected" in r.stderr
    assert "signature does not match" in r.stderr


def test_cli_demote_immediate(tmp_path: Path) -> None:
    _run_cli(
        "trust",
        "--bootstrap",
        "--em-handle",
        "kovtcharov-amd",
        "--em-channel",
        "cli",
        home=tmp_path,
        check=True,
    )
    _run_cli(
        "promote",
        "--to-tier",
        "3",
        "--reason",
        "earned",
        "--em-signature",
        "kovtcharov-amd",
        home=tmp_path,
        check=True,
    )
    r = _run_cli("demote", "--reason", "over-extended", home=tmp_path)
    assert r.returncode == 0
    assert "tier 2" in r.stdout.lower()


def test_cli_ask_enqueues_inbox(tmp_path: Path) -> None:
    """``gaia-coder ask`` writes a pending row and prints the auto-ack template."""
    _run_cli(
        "trust",
        "--bootstrap",
        "--em-handle",
        "kovtcharov-amd",
        "--em-channel",
        "cli",
        home=tmp_path,
        check=True,
    )
    r = _run_cli("ask", "enable", "self-edit", home=tmp_path)
    assert r.returncode == 0, r.stderr
    # Templated auto-ack per §4.5.
    assert "next breakpoint" in r.stdout
    assert "[queued as " in r.stdout

    # Inbox now has a pending row. We hit the inbox store directly rather
    # than parse the `inbox` CLI output — the assertion is about durable
    # state, not display.
    from gaia.coder.stores import em_inbox

    db = tmp_path / "em_inbox.db"
    assert db.exists()
    conn = em_inbox.open_store(db)
    try:
        rows = em_inbox.list_rows(conn)
    finally:
        conn.close()
    assert len(rows) == 1
    assert rows[0].severity == "question"
    assert rows[0].state == "pending"
    assert rows[0].body == "enable self-edit"


def test_cli_note_and_critical_route_to_correct_severities(tmp_path: Path) -> None:
    _run_cli(
        "trust",
        "--bootstrap",
        "--em-handle",
        "kovtcharov-amd",
        "--em-channel",
        "cli",
        home=tmp_path,
        check=True,
    )
    _run_cli("note", "heads", "up", home=tmp_path, check=True)
    _run_cli("critical", "production", "regression", home=tmp_path, check=True)

    from gaia.coder.stores import em_inbox

    conn = em_inbox.open_store(tmp_path / "em_inbox.db")
    try:
        sevs = sorted(r.severity for r in em_inbox.list_rows(conn))
    finally:
        conn.close()
    assert sevs == ["critical", "info"]


def test_cli_inbox_lists_pending(tmp_path: Path) -> None:
    _run_cli(
        "trust",
        "--bootstrap",
        "--em-handle",
        "kovtcharov-amd",
        "--em-channel",
        "cli",
        home=tmp_path,
        check=True,
    )
    _run_cli("ask", "tier?", home=tmp_path, check=True)
    r = _run_cli("inbox", home=tmp_path)
    assert r.returncode == 0, r.stderr
    assert "Pending (1)" in r.stdout
    assert "question" in r.stdout


@pytest.mark.parametrize(
    "stub",
    ["status", "audit", "spend", "egress", "introspect", "skill", "doctor"],
)
def test_cli_unimplemented_subcommands_still_stub(tmp_path: Path, stub: str) -> None:
    """Subcommands whose owning phase has not landed remain generic stubs.

    Phase 11 wired real handlers for ``daemon`` (eval harness), ``feedback``
    (self-correction), ``self-fix``, ``dev-mode``, ``debug``, and ``rag``;
    this list covers the ones still deliberately deferred.
    """
    r = _run_cli(stub, home=tmp_path)
    assert r.returncode == 0
    assert "not yet implemented" in r.stdout
