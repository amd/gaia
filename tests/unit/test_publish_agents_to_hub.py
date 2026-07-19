# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Unit tests for ``util/publish_agents_to_hub.py`` (batch pack-and-publish).

No real network, no real CLI: the per-agent ``gaia agent pack/publish``
subprocess calls are replaced by an injected runner, and discovery/token
checks are monkeypatched. The live publish HTTP layer is covered by
``test_hub_publisher.py``; the CLI wiring by ``test_cli_agent.py``.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
UTIL_DIR = REPO_ROOT / "util"
if str(UTIL_DIR) not in sys.path:
    sys.path.insert(0, str(UTIL_DIR))

import list_agent_packages as lap  # noqa: E402  (path set above)
import publish_agents_to_hub as pub  # noqa: E402  (path set above)

# Real package dirs (rel_path resolves against REPO_ROOT), fake subprocesses.
PKG_A = lap.AgentPackage(
    dist_name="gaia-agent-hello-world",
    agent_id="hello-world",
    path=lap.AGENTS_DIR / "hello-world" / "python",
)
PKG_B = lap.AgentPackage(
    dist_name="gaia-agent-word-count",
    agent_id="word-count",
    path=lap.AGENTS_DIR / "word-count" / "python",
)


class FakeRunner:
    """Records CLI invocations and replays scripted (rc, output) results."""

    def __init__(self, results):
        # results: {("pack"|"publish", agent_id): (rc, output)}
        self.results = results
        self.calls = []

    def __call__(self, cmd):
        action = cmd[2]  # [gaia, "agent", <action>, <path>, ...]
        # Agent-first layout: path is hub/agents/<id>/python, so the id is the
        # parent dir name (the leaf is always "python").
        agent_dir = Path(cmd[3]).parent.name
        self.calls.append((action, agent_dir, cmd))
        return self.results[(action, agent_dir)]


@pytest.fixture
def two_agents(monkeypatch):
    monkeypatch.setattr(pub, "list_agent_packages", lambda: [PKG_A, PKG_B])
    monkeypatch.setattr(pub, "_gaia_cli", lambda: "gaia")
    monkeypatch.setattr(pub, "_require_hub_token", lambda: None)


def _args(*extra):
    return pub.parse_args(["--hub-url", "http://localhost:8788", *extra])


# ---------------------------------------------------------------------------
# Discovery wiring / agent selection
# ---------------------------------------------------------------------------


def test_discovery_uses_list_agent_packages():
    """The pipeline's discovery is the real setup.py[agents] helper."""
    packages = pub.list_agent_packages()
    ids = {p.agent_id for p in packages}
    assert {"summarize", "fileio", "analyst"} <= ids


def test_select_agents_default_is_all():
    assert pub.select_agents([PKG_A, PKG_B], None) == [PKG_A, PKG_B]


def test_select_agents_subset_preserves_request_order():
    assert pub.select_agents([PKG_A, PKG_B], ["word-count", "hello-world"]) == [
        PKG_B,
        PKG_A,
    ]


def test_select_agents_unknown_id_fails_loudly():
    with pytest.raises(pub.PipelineError, match="unknown agent id.*nope"):
        pub.select_agents([PKG_A, PKG_B], ["nope"])


# ---------------------------------------------------------------------------
# Publish flow + 409 handling
# ---------------------------------------------------------------------------


def test_all_published(two_agents):
    runner = FakeRunner(
        {
            ("pack", "hello-world"): (0, "Built wheel"),
            ("publish", "hello-world"): (0, "Published"),
            ("pack", "word-count"): (0, "Built wheel"),
            ("publish", "word-count"): (0, "Published"),
        }
    )
    outcomes = pub.run_pipeline(_args(), runner)
    assert [o.status for o in outcomes] == [pub.STATUS_PUBLISHED] * 2
    actions = [(a, d) for a, d, _ in runner.calls]
    assert actions == [
        ("pack", "hello-world"),
        ("publish", "hello-world"),
        ("pack", "word-count"),
        ("publish", "word-count"),
    ]


def test_publish_cli_gets_hub_url_and_skips_pypi(two_agents):
    runner = FakeRunner(
        {
            ("pack", "hello-world"): (0, ""),
            ("publish", "hello-world"): (0, ""),
            ("pack", "word-count"): (0, ""),
            ("publish", "word-count"): (0, ""),
        }
    )
    pub.run_pipeline(_args(), runner)
    publish_cmds = [cmd for a, _, cmd in runner.calls if a == "publish"]
    for cmd in publish_cmds:
        assert "--hub-url" in cmd
        assert cmd[cmd.index("--hub-url") + 1] == "http://localhost:8788"
        assert "--skip-pypi" in cmd


def test_409_without_skip_existing_is_failure(two_agents):
    runner = FakeRunner(
        {
            ("pack", "hello-world"): (0, ""),
            ("publish", "hello-world"): (
                1,
                f"Error: this version {pub.VERSION_EXISTS_MARKER}: bump it",
            ),
            ("pack", "word-count"): (0, ""),
            ("publish", "word-count"): (0, ""),
        }
    )
    outcomes = pub.run_pipeline(_args(), runner)
    by_id = {o.agent_id: o for o in outcomes}
    assert by_id["hello-world"].status == pub.STATUS_FAILED
    assert "409" in by_id["hello-world"].detail
    assert by_id["word-count"].status == pub.STATUS_PUBLISHED


def test_409_with_skip_existing_is_skip(two_agents):
    runner = FakeRunner(
        {
            ("pack", "hello-world"): (0, ""),
            ("publish", "hello-world"): (
                1,
                f"Error: this version {pub.VERSION_EXISTS_MARKER}: bump it",
            ),
            ("pack", "word-count"): (0, ""),
            ("publish", "word-count"): (0, ""),
        }
    )
    outcomes = pub.run_pipeline(_args("--skip-existing"), runner)
    by_id = {o.agent_id: o for o in outcomes}
    assert by_id["hello-world"].status == pub.STATUS_SKIPPED
    assert by_id["word-count"].status == pub.STATUS_PUBLISHED


def test_non_409_failure_is_failure_even_with_skip_existing(two_agents):
    runner = FakeRunner(
        {
            ("pack", "hello-world"): (0, ""),
            ("publish", "hello-world"): (1, "Error: Hub rejected the token (401)"),
            ("pack", "word-count"): (0, ""),
            ("publish", "word-count"): (0, ""),
        }
    )
    outcomes = pub.run_pipeline(_args("--skip-existing"), runner)
    assert outcomes[0].status == pub.STATUS_FAILED
    assert "401" in outcomes[0].detail


def test_pack_failure_skips_publish(two_agents):
    runner = FakeRunner(
        {
            ("pack", "hello-world"): (1, "build exploded"),
            ("pack", "word-count"): (0, ""),
            ("publish", "word-count"): (0, ""),
        }
    )
    outcomes = pub.run_pipeline(_args(), runner)
    assert outcomes[0].status == pub.STATUS_FAILED
    assert "pack failed" in outcomes[0].detail
    actions = [(a, d) for a, d, _ in runner.calls]
    assert ("publish", "hello-world") not in actions


# ---------------------------------------------------------------------------
# Dry-run
# ---------------------------------------------------------------------------


def test_dry_run_packs_only_and_needs_no_token(monkeypatch):
    monkeypatch.setattr(pub, "list_agent_packages", lambda: [PKG_A])
    monkeypatch.setattr(pub, "_gaia_cli", lambda: "gaia")

    def _boom():
        raise AssertionError("token must not be required for --dry-run")

    monkeypatch.setattr(pub, "_require_hub_token", _boom)

    runner = FakeRunner({("pack", "hello-world"): (0, "Built wheel")})
    outcomes = pub.run_pipeline(_args("--dry-run"), runner)
    assert [o.status for o in outcomes] == [pub.STATUS_PACKED]
    assert [(a, d) for a, d, _ in runner.calls] == [("pack", "hello-world")]


# ---------------------------------------------------------------------------
# Token / args / exit codes
# ---------------------------------------------------------------------------


def test_missing_token_fails_before_any_pack(monkeypatch):
    monkeypatch.setattr(pub, "list_agent_packages", lambda: [PKG_A])
    monkeypatch.setattr(pub, "_gaia_cli", lambda: "gaia")
    monkeypatch.delenv("GAIA_HUB_TOKEN", raising=False)
    monkeypatch.setattr("gaia.hub.publisher.get_hub_token", lambda: None, raising=True)
    runner = FakeRunner({})
    with pytest.raises(pub.PipelineError, match="no Hub publish token"):
        pub.run_pipeline(_args(), runner)
    assert runner.calls == []


def test_hub_url_required_without_env(monkeypatch):
    monkeypatch.delenv(pub.HUB_URL_ENV, raising=False)
    with pytest.raises(SystemExit):
        pub.parse_args([])


def test_hub_url_falls_back_to_env(monkeypatch):
    monkeypatch.setenv(pub.HUB_URL_ENV, "http://hub.env:9999")
    args = pub.parse_args([])
    assert args.hub_url == "http://hub.env:9999"


def test_main_exit_codes(two_agents, monkeypatch):
    results = {
        ("pack", "hello-world"): (0, ""),
        ("publish", "hello-world"): (0, ""),
        ("pack", "word-count"): (0, ""),
        ("publish", "word-count"): (0, ""),
    }
    monkeypatch.setattr(pub, "_default_runner", FakeRunner(results))
    assert pub.main(["--hub-url", "http://localhost:8788"]) == 0

    results[("publish", "word-count")] = (1, "Error: kaboom")
    monkeypatch.setattr(pub, "_default_runner", FakeRunner(results))
    assert pub.main(["--hub-url", "http://localhost:8788"]) == 1
