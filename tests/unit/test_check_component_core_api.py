# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Unit tests for util/check_component_core_api.py.

The bug these guard against: hub/components/terminal-hub/gaia-agent.yaml
declared min_gaia_version 0.22.0 while the terminal hub requires daemon host
API v1.1, and released 0.22.0 ships v1. Publishing that manifest would have
handed every user a binary that cannot talk to the core it names as its own
minimum.

Comparing the manifest against src/gaia/daemon/constants.py would NOT have
caught it — this tree already has 1.1. The break is between the tree and a
published wheel, so several tests below exist specifically to prove the guard
never answers for a released version by reading this tree.
"""

import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]

# Ensure util/ is importable regardless of where pytest is invoked from.
sys.path.insert(0, str(REPO_ROOT / "util"))

import check_component_core_api as guard  # noqa: E402

TERMINAL_HUB_MANIFEST = (
    REPO_ROOT / "hub" / "components" / "terminal-hub" / "gaia-agent.yaml"
)
AGENT_UI_MANIFEST = REPO_ROOT / "hub" / "components" / "agent-ui" / "gaia-agent.yaml"

TUI_FLOOR = guard.HostAPIFloor(major=1, minor=1)

# Stand-ins for "what each release shipped", so these tests do not move when a
# real release is added to the production table.
FAKE_RELEASED = {(0, 22, 0): "1", (0, 23, 0): "1.1"}
FAKE_LATEST = (0, 23, 0)
FAKE_PUBLISHED = {(0, 22, 0), (0, 23, 0)}


def exploding_fetcher(version):
    raise AssertionError(f"unexpected PyPI fetch for {version}")


def check(min_gaia_version, *, tree_api="1.1", floor=TUI_FLOOR, **kwargs):
    """Run the component check against a synthetic manifest."""
    return guard.check_component(
        "terminal-hub",
        {"min_gaia_version": min_gaia_version},
        floor=floor,
        tree_api=tree_api,
        released_api=FAKE_RELEASED,
        latest_release=FAKE_LATEST,
        **kwargs,
    ).problems


# ── The regression this guard exists for ────────────────────────────────────


def test_min_gaia_version_naming_a_release_without_the_agents_api_fails():
    """0.22.0 shipped host API v1; the terminal hub needs v1.1. Must fail."""
    problems = check("0.22.0")
    assert len(problems) == 1
    assert "v1.1+" in problems[0]
    assert "ships v1" in problems[0]


def test_a_released_version_is_never_answered_from_this_tree():
    """The exact hole in the old state: the tree says 1.1, the release shipped 1.

    If resolution ever consults the tree for a released version, this passes and
    the guard is worthless.
    """
    assert check("0.22.0", tree_api="1.1") != []
    assert check("0.22.0", tree_api="9.9") != []


def test_release_that_predates_the_daemon_fails():
    problems = check("0.21.2")
    assert len(problems) == 1
    assert "no daemon at all" in problems[0]


def test_first_release_shipping_the_required_api_passes():
    assert check("0.23.0") == []


# ── Resolution rules ────────────────────────────────────────────────────────


def test_unreleased_version_resolves_from_this_tree():
    """A version above every known release can only be cut from this tree."""
    api, provenance = guard.resolve_daemon_api(
        "0.24.0",
        tree_api="1.1",
        released_api=FAKE_RELEASED,
        latest_release=FAKE_LATEST,
    )
    assert api == "1.1"
    assert "this tree" in provenance


def test_unreleased_version_fails_when_the_tree_does_not_satisfy_the_floor():
    assert check("0.24.0", tree_api="1.0") != []


def test_released_version_missing_from_the_table_is_a_hard_error():
    """No guessing: an unknown release must stop the check, not fall back."""
    with pytest.raises(guard.CheckError, match="no row in RELEASED_DAEMON_API"):
        guard.resolve_daemon_api(
            "0.22.5",
            tree_api="1.1",
            released_api=FAKE_RELEASED,
            latest_release=FAKE_LATEST,
        )


def test_verify_mode_reads_a_published_version_from_the_wheel_not_the_table():
    """Ground truth wins, even over a stale LATEST_CORE_RELEASE."""
    api, provenance = guard.resolve_daemon_api(
        "0.23.0",
        tree_api="9.9",
        verify_released=True,
        released_api=FAKE_RELEASED,
        latest_release=(0, 22, 0),  # stale on purpose
        published=FAKE_PUBLISHED,
        fetcher=lambda _v: "1.1",
    )
    assert api == "1.1"
    assert "wheel" in provenance


def test_verify_mode_does_not_trust_a_stale_latest_release_constant():
    """The regression the first version of this guard shipped with.

    With LATEST_CORE_RELEASE stale, a released version used to take the
    "unreleased -> read this tree" path and never touch PyPI.
    """
    problems = guard.check_component(
        "terminal-hub",
        {"min_gaia_version": "0.23.0"},
        floor=TUI_FLOOR,
        tree_api="1.1",  # tree looks fine...
        verify_released=True,
        released_api=FAKE_RELEASED,
        latest_release=(0, 22, 0),  # ...and the constant says 0.23.0 is unreleased
        published=FAKE_PUBLISHED,
        fetcher=lambda _v: "1",  # ...but the wheel actually shipped v1
    ).problems
    assert problems, "verify mode must read the wheel, not fall back to the tree"


def test_verify_mode_uses_the_tree_only_when_pypi_has_no_such_release():
    api, provenance = guard.resolve_daemon_api(
        "0.99.0",
        tree_api="1.1",
        verify_released=True,
        released_api=FAKE_RELEASED,
        latest_release=FAKE_LATEST,
        published=FAKE_PUBLISHED,
        fetcher=exploding_fetcher,
    )
    assert api == "1.1"
    assert "no such release yet" in provenance


# ── The released-table audit ────────────────────────────────────────────────


def test_audit_detects_a_pinned_row_that_disagrees_with_the_wheel():
    with pytest.raises(guard.CheckError, match="RELEASED_DAEMON_API is wrong"):
        guard.audit_released_table(
            published=FAKE_PUBLISHED,
            released_api=FAKE_RELEASED,
            latest_release=FAKE_LATEST,
            fetcher=lambda _v: "1.4",
        )


def test_audit_detects_a_stale_latest_release_constant():
    with pytest.raises(guard.CheckError, match="LATEST_CORE_RELEASE is stale"):
        guard.audit_released_table(
            published=FAKE_PUBLISHED,
            released_api=FAKE_RELEASED,
            latest_release=(0, 22, 0),
            fetcher=lambda v: FAKE_RELEASED[guard.parse_version(v)],
        )


def test_audit_ignores_the_release_currently_being_cut():
    """At tag time the core wheel may already be on PyPI; that is not staleness."""
    guard.audit_released_table(
        cutting=(0, 23, 0),
        published=FAKE_PUBLISHED,
        released_api={(0, 22, 0): "1"},
        latest_release=(0, 22, 0),
        fetcher=lambda _v: "1",
    )


def test_audit_passes_when_the_table_matches_pypi():
    guard.audit_released_table(
        published=FAKE_PUBLISHED,
        released_api=FAKE_RELEASED,
        latest_release=FAKE_LATEST,
        fetcher=lambda v: FAKE_RELEASED[guard.parse_version(v)],
    )


def test_verify_released_run_actually_audits_the_table():
    """Catches "the flag is wired but does no work" — the defect this replaced.

    The committed manifests pin an unreleased version, so a manifest-driven
    check makes zero PyPI calls. The audit must run regardless.
    """
    fetched = []

    guard.run(
        verify_released=True,
        release_version="0.23.0",
        published=FAKE_PUBLISHED,
        released_api=FAKE_RELEASED,
        latest_release=FAKE_LATEST,
        fetcher=lambda v: (fetched.append(v), FAKE_RELEASED[guard.parse_version(v)])[1],
    )
    assert fetched, "--verify-released must read published wheels, not just the tree"


# ── Publish gate ────────────────────────────────────────────────────────────


def test_publishing_under_a_core_older_than_the_declared_minimum_is_blocked():
    problems = check("0.23.0", release_version="0.22.1")
    assert any("cannot publish under 0.22.1" in p for p in problems)


def test_publishing_under_the_declared_minimum_is_allowed():
    assert check("0.23.0", release_version="0.23.0") == []


def test_publishing_under_a_newer_core_is_allowed():
    assert check("0.23.0", release_version="0.24.0") == []


def test_publish_gate_applies_to_components_without_a_daemon_floor():
    """agent-ui has no host API floor, but its minimum still bounds the publish."""
    problems = guard.check_component(
        "agent-ui",
        {"min_gaia_version": "0.23.0"},
        floor=None,
        tree_api="1.1",
        release_version="0.22.0",
    ).problems
    assert any("cannot publish under 0.22.0" in p for p in problems)


def test_manifest_without_a_minimum_is_a_problem():
    problems = guard.check_component(
        "terminal-hub", {}, floor=TUI_FLOOR, tree_api="1.1"
    ).problems
    assert any("no min_gaia_version" in p for p in problems)


# ── Classification ──────────────────────────────────────────────────────────


def test_unknown_host_api_source_is_a_hard_error():
    """A typo must not silently mean "no floor" and disable the check."""
    with pytest.raises(guard.CheckError, match="unknown host API source"):
        guard.resolve_floor("terminal-hub", "tuiy", TUI_FLOOR)


def test_known_sources_resolve():
    assert guard.resolve_floor("terminal-hub", "tui", TUI_FLOOR) == TUI_FLOOR
    assert guard.resolve_floor("agent-ui", None, TUI_FLOOR) is None


def test_an_unclassified_component_on_disk_fails_the_run(tmp_path):
    """A new component must be classified, not silently skipped by the gate."""
    for component_id in guard.COMPONENT_HOST_API_SOURCE:
        source = (
            REPO_ROOT / "hub" / "components" / component_id / "gaia-agent.yaml"
        ).read_text(encoding="utf-8")
        target = tmp_path / component_id
        target.mkdir()
        (target / "gaia-agent.yaml").write_text(source, encoding="utf-8")
    newcomer = tmp_path / "brand-new-component"
    newcomer.mkdir()
    (newcomer / "gaia-agent.yaml").write_text(
        'min_gaia_version: "0.22.0"\n', encoding="utf-8"
    )

    with pytest.raises(guard.CheckError, match="unclassified hub component"):
        guard.run(components_dir=tmp_path)


# ── Reading the authoritative sources ───────────────────────────────────────


def test_tui_floor_is_read_from_the_go_constants_the_binary_enforces():
    floor = guard.read_tui_host_api_floor()
    assert (floor.major, floor.minor) == (1, 1)


def test_missing_go_constant_fails_loudly(tmp_path):
    """A rename in instance.go must break the guard, never silently pass it."""
    stub = tmp_path / "instance.go"
    stub.write_text(
        "package daemon\n\nconst (\n\tSomethingElse = 1\n)\n", encoding="utf-8"
    )
    with pytest.raises(guard.CheckError, match="RequiredAPIMajor"):
        guard.read_tui_host_api_floor(stub)


def test_absent_go_file_fails_loudly(tmp_path):
    with pytest.raises(guard.CheckError, match="not found"):
        guard.read_tui_host_api_floor(tmp_path / "nope.go")


def test_missing_core_constant_fails_loudly(tmp_path):
    stub = tmp_path / "constants.py"
    stub.write_text("OTHER = 1\n", encoding="utf-8")
    with pytest.raises(guard.CheckError, match="DAEMON_API_VERSION"):
        guard.read_tree_daemon_api(stub)


def test_absent_core_constants_file_fails_loudly(tmp_path):
    with pytest.raises(guard.CheckError, match="not found"):
        guard.read_tree_daemon_api(tmp_path / "nope.py")


@pytest.mark.parametrize(
    "raw,expected",
    [("1", (1, 0)), ("1.1", (1, 1)), ("2.13", (2, 13))],
)
def test_bare_major_parses_as_minor_zero(raw, expected):
    assert guard.parse_api_version(raw) == expected


def test_short_version_is_padded_not_treated_as_older():
    assert guard.parse_version("0.22") == guard.parse_version("0.22.0")


@pytest.mark.parametrize("raw", ["0.23.0rc1", "v0.23.0", "", "latest"])
def test_non_numeric_versions_are_rejected(raw):
    with pytest.raises(guard.CheckError, match="cannot parse version"):
        guard.parse_version(raw)


def test_floor_requires_an_exact_major():
    """Mirror Instance.CheckVersion(): a different MAJOR is never compatible."""
    assert TUI_FLOOR.satisfied_by((1, 1))
    assert TUI_FLOOR.satisfied_by((1, 2))
    assert not TUI_FLOOR.satisfied_by((1, 0))
    assert not TUI_FLOOR.satisfied_by((2, 1))


# ── The real manifests ──────────────────────────────────────────────────────


def test_real_components_declare_a_serviceable_minimum_core():
    """The end-to-end guard over the manifests as they are committed."""
    assert guard.run().problems == []


def test_terminal_hub_minimum_is_not_a_release_that_predates_the_agents_api():
    """The invariant that actually fails if someone lowers the manifest again."""
    manifest = yaml.safe_load(TERMINAL_HUB_MANIFEST.read_text(encoding="utf-8"))
    assert (
        guard.parse_version(manifest["min_gaia_version"]) > guard.LATEST_CORE_RELEASE
    ), (
        "no published core release serves host API v1.1 yet, so the terminal hub's "
        "minimum must name a future release"
    )


def test_agent_ui_declares_no_daemon_host_api_requirement():
    """agent-ui talks to the gaia.ui.server FastAPI backend, not the control plane.

    If that ever changes, give it a floor here rather than leaving it unchecked.
    """
    assert guard.COMPONENT_HOST_API_SOURCE["agent-ui"] is None
    manifest = yaml.safe_load(AGENT_UI_MANIFEST.read_text(encoding="utf-8"))
    assert manifest["min_gaia_version"]


def test_every_hub_component_is_classified():
    on_disk = {p.parent.name for p in guard.COMPONENTS_DIR.glob("*/gaia-agent.yaml")}
    assert on_disk == set(guard.COMPONENT_HOST_API_SOURCE)
