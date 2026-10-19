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

What is "released" comes from PyPI on every run (#3922); these tests stand in
for PyPI and refuse any real network read.
"""

import io
import json
import sys
import urllib.error
import zipfile
from pathlib import Path
from unittest.mock import patch

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

# Stand-ins for PyPI, so these tests do not move when a real release ships.
# None = the wheel has no gaia/daemon package.
FAKE_WHEELS = {"0.21.2": None, "0.22.0": "1", "0.23.0": "1.1"}
FAKE_PUBLISHED = {guard.parse_version(v): v for v in FAKE_WHEELS}

# What real releases shipped, read out of their PyPI wheels. Published wheels
# are immutable, so this never needs a new row; it only feeds the committed
# manifests through the guard without the network.
KNOWN_WHEELS = {
    "0.21.2": None,
    "0.22.0": "1",
    "0.23.0": "1.1",
    "0.24.0": "1.1",
    "0.24.1": "1.1",
}
KNOWN_PUBLISHED = {guard.parse_version(v): v for v in KNOWN_WHEELS}

_real_fetch = guard._fetch


@pytest.fixture(autouse=True)
def no_network():
    """Any unplanned PyPI read fails the test instead of reaching the internet."""

    def refuse(url, timeout):
        raise AssertionError(f"unexpected network read of {url}")

    with patch.object(guard, "_fetch", side_effect=refuse):
        yield


def fake_fetcher(wheels=None):
    wheels = FAKE_WHEELS if wheels is None else wheels
    return lambda raw: wheels[raw]


def exploding_fetcher(version):
    raise AssertionError(f"unexpected wheel fetch for {version}")


def published_from(wheels):
    return {guard.parse_version(v): v for v in wheels}


def check(min_gaia_version, *, tree_api="1.1", floor=TUI_FLOOR, **kwargs):
    """Run the component check against a synthetic manifest."""
    kwargs.setdefault("published", FAKE_PUBLISHED)
    kwargs.setdefault("fetcher", fake_fetcher())
    return guard.check_component(
        "terminal-hub",
        {"min_gaia_version": min_gaia_version},
        floor=floor,
        tree_api=tree_api,
        **kwargs,
    ).problems


def resolve(min_gaia_version, **kwargs):
    kwargs.setdefault("tree_api", "1.1")
    kwargs.setdefault("published", FAKE_PUBLISHED)
    kwargs.setdefault("fetcher", fake_fetcher())
    return guard.resolve_daemon_api(min_gaia_version, **kwargs)


def wheel_bytes(daemon_api):
    """A wheel archive, with or without gaia/daemon/constants.py."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr("gaia/version.py", '__version__ = "x"\n')
        if daemon_api is not None:
            archive.writestr(
                guard.WHEEL_MEMBER, f'DAEMON_API_VERSION = "{daemon_api}"\n'
            )
    return buf.getvalue()


def pypi_release(version, *, yanked=False, wheel=True):
    files = [
        {
            "packagetype": "sdist",
            "filename": f"amd_gaia-{version}.tar.gz",
            "url": f"https://files.example/{version}.tar.gz",
            "yanked": yanked,
        }
    ]
    if wheel:
        files.append(
            {
                "packagetype": "bdist_wheel",
                "filename": f"amd_gaia-{version}-py3-none-any.whl",
                "url": f"https://files.example/{version}.whl",
                "yanked": yanked,
            }
        )
    return files


class FakePyPI:
    """Serves a JSON index and wheels through guard._fetch, recording reads."""

    def __init__(self, wheels):
        self.wheels = wheels
        self.releases = {v: pypi_release(v) for v in wheels}
        self.reads = []

    def __call__(self, url, timeout):
        self.reads.append(url)
        if url == guard.PYPI_JSON_URL:
            return json.dumps({"releases": self.releases}).encode()
        version = url.rsplit("/", 1)[1].removesuffix(".whl")
        return wheel_bytes(self.wheels[version])


# ── The regression this guard exists for ────────────────────────────────────


def test_min_gaia_version_naming_a_release_without_the_agents_api_fails():
    """0.22.0 shipped host API v1; the terminal hub needs v1.1. Must fail."""
    problems = check("0.22.0")
    assert len(problems) == 1
    assert "v1.1+" in problems[0]
    assert "ships v1" in problems[0]


def test_a_released_version_is_never_answered_from_this_tree():
    """The tree says 1.1, the release shipped 1.

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


# ── A new release needs no code edit ───────────────────────────────────────


def test_a_newly_published_release_is_read_from_its_wheel_with_no_table_edit():
    """The drift #3922 tracks: a release shipped and nobody recorded it."""
    wheels = {**FAKE_WHEELS, "0.24.0": "1.2"}
    api, provenance = resolve(
        "0.24.0",
        tree_api="9.9",
        published=published_from(wheels),
        fetcher=fake_fetcher(wheels),
    )
    assert api == "1.2"
    assert "amd-gaia==0.24.0 wheel" in provenance


def test_a_new_release_that_regressed_the_api_is_caught_though_the_tree_is_fine():
    """With a stale table this resolved from the tree and passed."""
    wheels = {**FAKE_WHEELS, "0.24.0": "1"}
    problems = check(
        "0.24.0",
        tree_api="1.1",
        published=published_from(wheels),
        fetcher=fake_fetcher(wheels),
    )
    assert problems and "ships v1 " in problems[0]


def test_short_version_resolves_to_the_padded_pypi_release():
    """min_gaia_version "0.23" must read the 0.23.0 wheel, not miss it."""
    seen = []
    api, _ = resolve("0.23", fetcher=lambda raw: (seen.append(raw), "1.1")[1])
    assert api == "1.1"
    assert seen == ["0.23.0"]


# ── Unreleased versions ─────────────────────────────────────────────────────


def test_unreleased_version_resolves_from_this_tree():
    """A version above every published release can only be cut from this tree."""
    api, provenance = resolve("0.24.0", fetcher=exploding_fetcher)
    assert api == "1.1"
    assert "this tree" in provenance


def test_unreleased_version_fails_when_the_tree_does_not_satisfy_the_floor():
    assert check("0.24.0", tree_api="1.0", fetcher=exploding_fetcher) != []


def test_the_release_being_cut_reads_its_wheel_once_pypi_has_it():
    """publish.yml may finish before the components run; then the wheel wins."""
    wheels = {**FAKE_WHEELS, "0.24.0": "1"}
    problems = check(
        "0.24.0",
        tree_api="1.1",
        published=published_from(wheels),
        fetcher=fake_fetcher(wheels),
        cutting=(0, 24, 0),
    )
    assert problems, "a published wheel must outrank the tree, even when cutting"


def test_a_backport_being_cut_below_the_latest_release_resolves_from_the_tree():
    api, provenance = resolve("0.22.1", fetcher=exploding_fetcher, cutting=(0, 22, 1))
    assert api == "1.1"
    assert "this tree" in provenance


# ── A tag that never became a release ───────────────────────────────────────


def test_a_tag_without_a_published_release_is_a_hard_error():
    """v0.23.1 was tagged but never reached PyPI. Never answer it from the tree."""
    with pytest.raises(guard.CheckError, match="PyPI has no installable 0.22.5"):
        resolve("0.22.5", fetcher=exploding_fetcher)


def test_a_tag_without_a_release_is_not_rescued_by_an_unrelated_cut():
    with pytest.raises(guard.CheckError, match="no installable"):
        resolve("0.22.5", fetcher=exploding_fetcher, cutting=(0, 23, 1))


def test_prereleases_alone_do_not_count_as_a_release():
    index = {
        "releases": {
            "0.23.0": pypi_release("0.23.0"),
            "0.24.0rc3": pypi_release("0.24.0rc3"),
        }
    }
    assert set(guard.released_versions(index)) == {(0, 23, 0)}


def test_a_release_with_no_uploaded_files_does_not_count():
    index = {"releases": {"0.23.0": pypi_release("0.23.0"), "0.23.1": []}}
    assert set(guard.released_versions(index)) == {(0, 23, 0)}


def test_a_fully_yanked_release_does_not_count():
    index = {
        "releases": {
            "0.23.0": pypi_release("0.23.0"),
            "0.23.1": pypi_release("0.23.1", yanked=True),
        }
    }
    assert set(guard.released_versions(index)) == {(0, 23, 0)}


def test_a_release_with_only_an_sdist_fails_loudly_when_its_api_is_needed():
    index = {"releases": {"0.23.0": pypi_release("0.23.0", wheel=False)}}
    with pytest.raises(guard.CheckError, match="no unyanked wheel"):
        guard.fetch_released_daemon_api("0.23.0", index)


# ── PyPI unreachable or wrong ───────────────────────────────────────────────


def test_pypi_unreachable_fails_the_run_instead_of_answering_from_the_tree():
    guard_error = guard.CheckError("could not reach PyPI")
    with patch.object(guard, "_fetch", side_effect=guard_error):
        with pytest.raises(guard.CheckError, match="could not reach PyPI"):
            guard.run()


def test_pypi_http_error_fails_the_cli_with_an_actionable_error(capsys):
    error = urllib.error.HTTPError(guard.PYPI_JSON_URL, 503, "down", {}, None)
    with patch.object(guard, "_fetch", side_effect=_real_fetch):
        with patch("urllib.request.urlopen", side_effect=error):
            assert guard.main([]) == 1
    out = capsys.readouterr().out
    assert "::error::PyPI returned HTTP 503" in out
    assert "OK:" not in out


@pytest.mark.parametrize(
    "body",
    [b"<html>maintenance</html>", b"{}", b'{"releases": {}}', b"[]"],
    ids=["not-json", "no-releases-key", "empty-releases", "not-an-object"],
)
def test_a_malformed_index_is_a_hard_error_not_zero_releases(body):
    """An empty answer would make every version "unreleased" — answered by the tree."""
    with patch.object(guard, "_fetch", return_value=body):
        with pytest.raises(guard.CheckError):
            guard.load_pypi_index()


def test_an_index_with_only_prereleases_is_a_hard_error():
    index = {"releases": {"0.25.0rc1": pypi_release("0.25.0rc1")}}
    with pytest.raises(guard.CheckError, match="no installable final release"):
        guard.released_versions(index)


# ── The live read path, end to end over a fake PyPI ─────────────────────────


def _terminal_hub_minimum():
    manifest = yaml.safe_load(TERMINAL_HUB_MANIFEST.read_text(encoding="utf-8"))
    return manifest["min_gaia_version"]


def test_run_reads_the_index_once_and_only_the_wheel_it_needs():
    fake = FakePyPI(KNOWN_WHEELS)
    with patch.object(guard, "_fetch", side_effect=fake):
        assert guard.run().problems == []
    assert fake.reads.count(guard.PYPI_JSON_URL) == 1
    wheel_reads = [url for url in fake.reads if url != guard.PYPI_JSON_URL]
    wanted = _terminal_hub_minimum()
    expected = [f"https://files.example/{wanted}.whl"] if wanted in KNOWN_WHEELS else []
    assert wheel_reads == expected


def test_run_against_a_fake_pypi_catches_a_release_that_ships_too_old_an_api():
    """Same path as production: index JSON, then the wheel bytes."""
    fake = FakePyPI({**KNOWN_WHEELS, _terminal_hub_minimum(): "1"})
    with patch.object(guard, "_fetch", side_effect=fake):
        problems = guard.run().problems
    assert any("ships v1 " in p for p in problems), problems


def test_a_wheel_without_the_daemon_package_reads_as_no_daemon():
    fake = FakePyPI({"0.21.2": None})
    with patch.object(guard, "_fetch", side_effect=fake):
        index = guard.load_pypi_index()
        assert guard.fetch_released_daemon_api("0.21.2", index) is None


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
        published=FAKE_PUBLISHED,
        fetcher=exploding_fetcher,
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
    result = guard.run(published=KNOWN_PUBLISHED, fetcher=fake_fetcher(KNOWN_WHEELS))
    assert result.problems == []


def test_terminal_hub_minimum_is_not_a_release_that_predates_the_agents_api():
    """The invariant that actually fails if someone lowers the manifest again.

    Stated against what releases shipped rather than "must name a future
    release": once a shipped core serves the floor (0.23.0 serves 1.1) that
    stops being true, while the real rule -- never name a release whose daemon
    cannot serve the hub -- still holds.
    """
    manifest = yaml.safe_load(TERMINAL_HUB_MANIFEST.read_text(encoding="utf-8"))
    declared = guard.parse_version(manifest["min_gaia_version"])
    too_old = sorted(
        version
        for version, api in (
            (guard.parse_version(raw), api) for raw, api in KNOWN_WHEELS.items()
        )
        if api is None or not TUI_FLOOR.satisfied_by(guard.parse_api_version(api))
    )
    assert all(declared > version for version in too_old), (
        f"terminal-hub declares min_gaia_version {manifest['min_gaia_version']}, which "
        f"is not newer than "
        f"{[guard.format_version(v) for v in too_old]} — releases whose daemon cannot "
        f"serve host API {TUI_FLOOR}."
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
