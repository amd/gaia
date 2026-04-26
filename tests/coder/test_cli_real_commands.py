# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""End-to-end tests for the seven previously-stubbed CLI verbs.

Covers ``doctor`` / ``status`` / ``audit`` / ``spend`` / ``introspect``
/ ``egress`` / ``skill`` — every verb gets at least one happy-path and
one failure-path assertion.

Each test runs the CLI as a *subprocess* so the entry-point, argparse
wiring, and env-var handling are exercised end-to-end — the same path a
real user hits.

``GAIA_CODER_HOME`` is overridden to a pytest ``tmp_path`` so no test
touches the user's real state. ``PYTHONPATH`` prepends the worktree's
``src/`` so the worktree's CLI wins over any editable install of the
``gaia`` package that resolves to a different repo.
"""

from __future__ import annotations

import json
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
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    """Invoke ``python -m gaia.coder.cli <args>`` with isolated state."""
    env = {
        **os.environ,
        "GAIA_CODER_HOME": str(home),
        # Ensure the worktree's source wins over any editable install
        # of ``gaia`` that resolves to a different path.
        "PYTHONPATH": str(REPO_ROOT / "src")
        + os.pathsep
        + os.environ.get("PYTHONPATH", ""),
    }
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [sys.executable, "-m", "gaia.coder.cli", *args],
        env=env,
        capture_output=True,
        text=True,
        check=check,
    )


# ---------------------------------------------------------------------------
# doctor
# ---------------------------------------------------------------------------


def test_doctor_without_repo_binding_fails_actionably(tmp_path: Path) -> None:
    """No ``repo_binding.toml`` → exit 1 with a §15.6 pointer."""
    r = _run_cli("doctor", home=tmp_path)
    assert r.returncode == 1
    assert "no repo binding" in r.stderr
    assert "§15.6" in r.stderr


def test_doctor_with_invalid_binding_surfaces_validation_error(tmp_path: Path) -> None:
    """A malformed binding TOML must not silently default — exit 1."""
    (tmp_path / "repo_binding.toml").write_text(
        # Missing required fields.
        'repo = "amd/gaia"\n',
        encoding="utf-8",
    )
    r = _run_cli("doctor", home=tmp_path)
    assert r.returncode == 1
    assert "doctor:" in r.stderr


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------


def test_status_on_fresh_install_renders_snapshot(tmp_path: Path) -> None:
    """No EM, no repo binding, no stores — all the labels still appear."""
    r = _run_cli("status", home=tmp_path)
    assert r.returncode == 0, r.stderr
    out = r.stdout
    for label in (
        "EM:",
        "Tier:",
        "Dev mode:",
        "Repo binding:",
        "Pending feedback:",
        "Pending inbox:",
        "Audit (last 5):",
    ):
        assert label in out
    assert "(none — run `gaia-coder trust --bootstrap`" in out


def test_status_after_bootstrap_shows_em_and_tier(tmp_path: Path) -> None:
    """Once an EM is bound, ``status`` renders the handle and tier label."""
    boot = _run_cli(
        "trust",
        "--bootstrap",
        "--em-handle",
        "kovtcharov-amd",
        "--em-channel",
        "github-issue-comment",
        home=tmp_path,
    )
    assert boot.returncode == 0, boot.stderr
    r = _run_cli("status", home=tmp_path)
    assert r.returncode == 0, r.stderr
    assert "@kovtcharov-amd" in r.stdout
    assert "Tier 0" in r.stdout


def test_status_failure_path_corrupt_em_toml_raises(tmp_path: Path) -> None:
    """A corrupt ``em.toml`` must surface as a non-zero exit, not a stub."""
    (tmp_path / "em.toml").write_text("not = valid = toml\n", encoding="utf-8")
    r = _run_cli("status", home=tmp_path)
    assert r.returncode != 0
    # The ``trust`` module's TrustError carries an actionable hint.
    assert "TrustError" in r.stderr or "not valid TOML" in r.stderr


# ---------------------------------------------------------------------------
# audit
# ---------------------------------------------------------------------------


def test_audit_on_empty_log_prints_friendly_message(tmp_path: Path) -> None:
    """No ``audit.log.db`` → friendly message + exit 0."""
    r = _run_cli("audit", home=tmp_path)
    assert r.returncode == 0, r.stderr
    assert "no audit log yet" in r.stdout


def test_audit_renders_inserted_rows(tmp_path: Path) -> None:
    """A row inserted into ``audit.log.db`` is rendered on tail."""
    db_path = tmp_path / "audit.log.db"
    seed = (
        "import sys; "
        f"sys.path.insert(0, {str(REPO_ROOT / 'src')!r}); "
        "from gaia.coder.stores import audit as a; "
        f"conn = a.open_store({str(db_path)!r}); "
        "conn.execute('INSERT INTO audit (occurred_at, tool_name, args_json, "
        "loop_version, stage) VALUES (?, ?, ?, ?, ?)', "
        "('2026-04-25T12:00:00+00:00', 'read_file', '{\"path\":\"x\"}', 1, 'Build')); "
        "conn.commit(); conn.close()"
    )
    subprocess.run([sys.executable, "-c", seed], check=True)
    r = _run_cli("audit", home=tmp_path)
    assert r.returncode == 0, r.stderr
    assert "read_file" in r.stdout
    assert "2026-04-25" in r.stdout


def test_audit_since_filter_excludes_older_rows(tmp_path: Path) -> None:
    """``--since`` lexically filters by ISO occurred_at prefix."""
    db_path = tmp_path / "audit.log.db"
    seed = (
        "import sys; "
        f"sys.path.insert(0, {str(REPO_ROOT / 'src')!r}); "
        "from gaia.coder.stores import audit as a; "
        f"conn = a.open_store({str(db_path)!r}); "
        "conn.execute('INSERT INTO audit (occurred_at, tool_name, args_json, "
        "loop_version) VALUES (?, ?, ?, ?)', "
        "('2026-01-01T00:00:00+00:00', 'old_tool', '{}', 1)); "
        "conn.execute('INSERT INTO audit (occurred_at, tool_name, args_json, "
        "loop_version) VALUES (?, ?, ?, ?)', "
        "('2026-04-25T00:00:00+00:00', 'new_tool', '{}', 1)); "
        "conn.commit(); conn.close()"
    )
    subprocess.run([sys.executable, "-c", seed], check=True)
    r = _run_cli("audit", "--since", "2026-04-01", home=tmp_path)
    assert r.returncode == 0, r.stderr
    assert "new_tool" in r.stdout
    assert "old_tool" not in r.stdout


# ---------------------------------------------------------------------------
# spend
# ---------------------------------------------------------------------------


def test_spend_on_empty_db_returns_friendly_zero(tmp_path: Path) -> None:
    """No ``spend.db`` → friendly informational line, exit 0."""
    r = _run_cli("spend", home=tmp_path)
    assert r.returncode == 0, r.stderr
    assert "no spend recorded" in r.stdout


def test_spend_total_aggregates_across_models(tmp_path: Path) -> None:
    """Two rows on different models render a TOTAL row summing both."""
    db_path = tmp_path / "spend.db"
    seed = (
        "import sys; "
        f"sys.path.insert(0, {str(REPO_ROOT / 'src')!r}); "
        "from gaia.coder.stores import spend as s; "
        f"conn = s.open_store({str(db_path)!r}); "
        "conn.execute('INSERT INTO spend "
        "(id, occurred_at, call_site, model, input_tokens, output_tokens, usd) "
        "VALUES (?,?,?,?,?,?,?)', "
        "('a', '2020-01-01T00:00:00+00:00', 'plan', 'opus', 100, 50, 1.5)); "
        "conn.execute('INSERT INTO spend "
        "(id, occurred_at, call_site, model, input_tokens, output_tokens, usd) "
        "VALUES (?,?,?,?,?,?,?)', "
        "('b', '2020-01-02T00:00:00+00:00', 'plan', 'sonnet', 200, 80, 0.5)); "
        "conn.commit(); conn.close()"
    )
    subprocess.run([sys.executable, "-c", seed], check=True)
    r = _run_cli("spend", "--total", home=tmp_path)
    assert r.returncode == 0, r.stderr
    assert "opus" in r.stdout
    assert "sonnet" in r.stdout
    assert "TOTAL" in r.stdout
    # Sum: $2.0000 with two calls.
    assert "$2.0000" in r.stdout


def test_spend_day_scope_excludes_yesterday(tmp_path: Path) -> None:
    """``--day`` (default) only sums rows occurring after midnight today UTC."""
    db_path = tmp_path / "spend.db"
    seed = (
        "import sys; "
        f"sys.path.insert(0, {str(REPO_ROOT / 'src')!r}); "
        "from gaia.coder.stores import spend as s; "
        f"conn = s.open_store({str(db_path)!r}); "
        "conn.execute('INSERT INTO spend "
        "(id, occurred_at, call_site, model, input_tokens, output_tokens, usd) "
        "VALUES (?,?,?,?,?,?,?)', "
        "('y', '2020-01-01T00:00:00+00:00', 'plan', 'opus', 100, 50, 9.99)); "
        "conn.commit(); conn.close()"
    )
    subprocess.run([sys.executable, "-c", seed], check=True)
    r = _run_cli("spend", "--day", home=tmp_path)
    assert r.returncode == 0, r.stderr
    # The 2020 row falls outside today's scope → empty result.
    assert "no spend recorded" in r.stdout


# ---------------------------------------------------------------------------
# introspect
# ---------------------------------------------------------------------------


def test_introspect_state_machine_emits_mermaid(tmp_path: Path) -> None:
    """``introspect state-machine`` prints the §15.3 Mermaid render."""
    r = _run_cli("introspect", "state-machine", home=tmp_path)
    assert r.returncode == 0, r.stderr
    assert "stateDiagram" in r.stdout


def test_introspect_tools_lists_all_registered_tools(tmp_path: Path) -> None:
    """File / search / cli mixins register at least 12 tools combined."""
    r = _run_cli("introspect", "tools", home=tmp_path)
    assert r.returncode == 0, r.stderr
    # A sample of tools we know are registered by the mixins.
    for name in ("read_file", "write_file", "edit_file", "grep", "list_files"):
        assert name in r.stdout


def test_introspect_em_without_config_prints_pointer(tmp_path: Path) -> None:
    """No ``em.toml`` → pointer to bootstrap, not a crash."""
    r = _run_cli("introspect", "em", home=tmp_path)
    assert r.returncode == 0, r.stderr
    assert "no EM config" in r.stdout
    assert "trust --bootstrap" in r.stdout


def test_introspect_repo_without_binding_prints_pointer(tmp_path: Path) -> None:
    """No ``repo_binding.toml`` → "(no repo binding manifest)"."""
    r = _run_cli("introspect", "repo", home=tmp_path)
    assert r.returncode == 0, r.stderr
    assert "no repo binding manifest" in r.stdout


def test_introspect_em_prints_toml_when_present(tmp_path: Path) -> None:
    """With an em.toml on disk, ``introspect em`` prints the file verbatim."""
    boot = _run_cli(
        "trust",
        "--bootstrap",
        "--em-handle",
        "kovtcharov-amd",
        "--em-channel",
        "github-issue-comment",
        home=tmp_path,
    )
    assert boot.returncode == 0, boot.stderr
    r = _run_cli("introspect", "em", home=tmp_path)
    assert r.returncode == 0, r.stderr
    assert 'em_handle = "kovtcharov-amd"' in r.stdout


def test_introspect_unknown_thing_exits_with_argparse_error(tmp_path: Path) -> None:
    """Argparse `choices=` forbids invalid values → exit 2."""
    r = _run_cli("introspect", "definitely-not-a-thing", home=tmp_path)
    assert r.returncode == 2
    assert "invalid choice" in r.stderr


# ---------------------------------------------------------------------------
# egress
# ---------------------------------------------------------------------------


def test_egress_without_policy_prints_pointer(tmp_path: Path) -> None:
    """Missing ``egress.toml`` → discoverable pointer, exit 0."""
    r = _run_cli("egress", home=tmp_path)
    assert r.returncode == 0, r.stderr
    assert "no egress policy" in r.stdout
    assert "§6.7" in r.stdout


def test_egress_prints_policy_when_present(tmp_path: Path) -> None:
    """With a file present, ``egress`` prints it verbatim under a header."""
    body = '[outbound]\nallow = ["github.com"]\n'
    (tmp_path / "egress.toml").write_text(body, encoding="utf-8")
    r = _run_cli("egress", home=tmp_path)
    assert r.returncode == 0, r.stderr
    assert "Egress policy at" in r.stdout
    assert 'allow = ["github.com"]' in r.stdout


# ---------------------------------------------------------------------------
# skill
# ---------------------------------------------------------------------------


def _seed_user_catalog(home: Path, body: str) -> Path:
    catalog = home / "skills" / "catalog.toml"
    catalog.parent.mkdir(parents=True, exist_ok=True)
    catalog.write_text(body, encoding="utf-8")
    return catalog


def test_skill_list_against_user_catalog(tmp_path: Path) -> None:
    """``skill list`` enumerates entries from the user-override catalog."""
    _seed_user_catalog(
        tmp_path,
        '[[skill]]\nname = "release-scripts"\npriority = "high"\n'
        'description = "Rules for release scripts"\nenabled = true\n',
    )
    r = _run_cli("skill", "list", home=tmp_path)
    assert r.returncode == 0, r.stderr
    assert "release-scripts" in r.stdout
    assert "high" in r.stdout


def test_skill_show_emits_json(tmp_path: Path) -> None:
    """``skill show <name>`` emits a JSON record for that skill."""
    _seed_user_catalog(
        tmp_path,
        '[[skill]]\nname = "release-scripts"\npriority = "high"\nenabled = true\n',
    )
    r = _run_cli("skill", "show", "release-scripts", home=tmp_path)
    assert r.returncode == 0, r.stderr
    payload = json.loads(r.stdout)
    assert payload["name"] == "release-scripts"
    assert payload["enabled"] is True


def test_skill_show_unknown_exits_nonzero(tmp_path: Path) -> None:
    """``skill show`` on an unknown name exits 1 with an actionable line."""
    _seed_user_catalog(
        tmp_path,
        '[[skill]]\nname = "alpha"\nenabled = true\n',
    )
    r = _run_cli("skill", "show", "beta", home=tmp_path)
    assert r.returncode == 1
    assert "no skill named" in r.stderr


def test_skill_disable_then_enable_round_trip(tmp_path: Path) -> None:
    """``disable`` flips the flag; ``enable`` flips it back."""
    _seed_user_catalog(
        tmp_path,
        '[[skill]]\nname = "release-scripts"\npriority = "high"\nenabled = true\n',
    )
    r = _run_cli("skill", "disable", "release-scripts", home=tmp_path)
    assert r.returncode == 0, r.stderr
    catalog = (tmp_path / "skills" / "catalog.toml").read_text(encoding="utf-8")
    assert "enabled = false" in catalog

    r = _run_cli("skill", "enable", "release-scripts", home=tmp_path)
    assert r.returncode == 0, r.stderr
    catalog = (tmp_path / "skills" / "catalog.toml").read_text(encoding="utf-8")
    assert "enabled = true" in catalog


def test_skill_enable_unknown_skill_exits_nonzero(tmp_path: Path) -> None:
    """``skill enable`` on an unknown name exits 1 (not silently)."""
    _seed_user_catalog(
        tmp_path,
        '[[skill]]\nname = "alpha"\nenabled = true\n',
    )
    r = _run_cli("skill", "enable", "nonexistent", home=tmp_path)
    assert r.returncode == 1
    assert "no skill named" in r.stderr
