# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Tests for :mod:`gaia.coder.oss_reuse` (§5.3, §5.4, §15.2).

No network I/O. The two external boundaries
(:func:`gaia.coder.oss_reuse._gh_api` and
:func:`gaia.coder.oss_reuse._fetch_raw`) are monkeypatched per test.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from gaia.agents.base.tools import _TOOL_REGISTRY
from gaia.coder import oss_reuse
from gaia.coder.oss_reuse import (
    AttributionError as OSSAttributionError,
    BLOCKED_LICENSES,
    LicenseIncompatibleError,
    LicenseReport,
    OSSReuseMixin,
    PERMISSIVE_LICENSES,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def registry_snapshot():
    snapshot = dict(_TOOL_REGISTRY)
    yield
    _TOOL_REGISTRY.clear()
    _TOOL_REGISTRY.update(snapshot)


@pytest.fixture
def oss_mixin(registry_snapshot):
    m = OSSReuseMixin()
    m.register_oss_reuse_tools()
    return m


def _get_tool(name: str):
    entry = _TOOL_REGISTRY.get(name)
    assert entry is not None, f"tool {name!r} not registered"
    return entry["function"]


# ---------------------------------------------------------------------------
# Allowlist / denylist constants
# ---------------------------------------------------------------------------


def test_permissive_allowlist_matches_spec():
    """§5.4 rule 1 — the seven permissive SPDX ids from the spec."""
    expected = {
        "MIT",
        "BSD-2-Clause",
        "BSD-3-Clause",
        "Apache-2.0",
        "ISC",
        "Unlicense",
        "0BSD",
    }
    assert PERMISSIVE_LICENSES == expected


def test_blocked_denylist_includes_gpl_family():
    for spdx in ("GPL-3.0", "AGPL-3.0", "LGPL-2.1", "SSPL-1.0"):
        assert spdx in BLOCKED_LICENSES


# ---------------------------------------------------------------------------
# vet_license — per task acceptance criteria
# ---------------------------------------------------------------------------


def test_vet_license_gpl_rejected(oss_mixin, monkeypatch):
    """`vet_license("torvalds/linux")` → compatible=False (GPL-2.0)."""
    monkeypatch.setattr(
        oss_reuse,
        "_gh_api",
        lambda path, **kw: {"license": {"spdx_id": "GPL-2.0"}},
    )
    report = _get_tool("vet_license")("torvalds/linux")
    assert isinstance(report, LicenseReport)
    assert report.compatible is False
    assert report.license == "GPL-2.0"
    assert "copyleft" in report.reason.lower()


def test_vet_license_bsd3_accepted(oss_mixin, monkeypatch):
    """`vet_license("pallets/click")` → compatible=True (BSD-3-Clause)."""
    monkeypatch.setattr(
        oss_reuse,
        "_gh_api",
        lambda path, **kw: {"license": {"spdx_id": "BSD-3-Clause"}},
    )
    report = _get_tool("vet_license")("pallets/click")
    assert report.compatible is True
    assert report.license == "BSD-3-Clause"


def test_vet_license_unknown_flagged(oss_mixin, monkeypatch):
    monkeypatch.setattr(
        oss_reuse,
        "_gh_api",
        lambda path, **kw: {"license": None},
    )
    report = _get_tool("vet_license")("someone/nolicense")
    assert report.compatible is False
    assert report.license is None
    assert "LICENSE" in report.reason or "recognised" in report.reason


def test_vet_license_noassertion_treated_as_missing(oss_mixin, monkeypatch):
    monkeypatch.setattr(
        oss_reuse,
        "_gh_api",
        lambda path, **kw: {"license": {"spdx_id": "NOASSERTION"}},
    )
    report = _get_tool("vet_license")("ambiguous/repo")
    assert report.license is None


def test_vet_license_permissive_but_unusual(oss_mixin, monkeypatch):
    """An SPDX id that is neither blocked nor permissive → human review."""
    monkeypatch.setattr(
        oss_reuse,
        "_gh_api",
        lambda path, **kw: {"license": {"spdx_id": "MPL-2.0"}},
    )
    report = _get_tool("vet_license")("some/repo")
    assert report.compatible is False
    assert "human review" in report.reason


# ---------------------------------------------------------------------------
# gh_search_code / gh_search_repos filtering
# ---------------------------------------------------------------------------


def test_gh_search_code_drops_blocked_licenses(oss_mixin, monkeypatch):
    repo_metadata = {
        "a/permissive": {"license": {"spdx_id": "MIT"}},
        "b/copyleft": {"license": {"spdx_id": "GPL-3.0"}},
    }

    def _api(path, **_):
        if path.startswith("/search/code"):
            return {
                "items": [
                    {
                        "repository": {"full_name": "a/permissive"},
                        "path": "src/foo.py",
                        "html_url": "https://github.com/a/permissive/blob/main/src/foo.py",
                    },
                    {
                        "repository": {"full_name": "b/copyleft"},
                        "path": "src/bar.py",
                        "html_url": "https://github.com/b/copyleft/blob/main/src/bar.py",
                    },
                ]
            }
        # /repos/{repo} lookup
        repo = path.lstrip("/").split("/", 1)[1]
        return repo_metadata[repo]

    monkeypatch.setattr(oss_reuse, "_gh_api", _api)
    hits = _get_tool("gh_search_code")("def foo", language="python")
    assert len(hits) == 1
    assert hits[0]["repository"] == "a/permissive"
    assert hits[0]["license"] == "MIT"


def test_gh_search_code_cannot_widen_filter_to_blocked(oss_mixin):
    with pytest.raises(LicenseIncompatibleError):
        _get_tool("gh_search_code")(
            "q", license_filter=["MIT", "GPL-3.0"]
        )


def test_gh_search_repos_applies_server_and_client_filter(
    oss_mixin, monkeypatch
):
    def _api(path, **_):
        # license qualifiers are URL-encoded as `license%3A<key>`.
        assert "license%3Amit" in path or "license%3Aapache-2.0" in path
        return {
            "items": [
                {
                    "full_name": "good/repo",
                    "description": "x",
                    "stargazers_count": 100,
                    "html_url": "https://github.com/good/repo",
                    "license": {"spdx_id": "MIT"},
                },
                {
                    "full_name": "mystery/repo",
                    "description": "y",
                    "stargazers_count": 5,
                    "html_url": "https://github.com/mystery/repo",
                    "license": None,  # GitHub sometimes returns null
                },
            ]
        }

    monkeypatch.setattr(oss_reuse, "_gh_api", _api)
    hits = _get_tool("gh_search_repos")("some query", min_stars=10)
    assert len(hits) == 1
    assert hits[0]["repository"] == "good/repo"


# ---------------------------------------------------------------------------
# import_with_attribution — the four §5.4 guarantees
# ---------------------------------------------------------------------------


def _install_import_stubs(monkeypatch, license_spdx: str = "MIT"):
    """Patch `_gh_api` and `_fetch_raw` for import_with_attribution tests."""
    monkeypatch.setattr(
        oss_reuse,
        "_gh_api",
        lambda path, **kw: {"license": {"spdx_id": license_spdx}},
    )
    monkeypatch.setattr(
        oss_reuse,
        "_fetch_raw",
        lambda url: 'def greet():\n    return "hi"\n',
    )


def test_import_success_writes_header_and_notices(oss_mixin, monkeypatch, tmp_path):
    _install_import_stubs(monkeypatch, license_spdx="MIT")
    sha = "abc1234def5678"
    result = _get_tool("import_with_attribution")(
        source_url=f"https://github.com/pallets/click/blob/{sha}/src/click/core.py",
        commit_sha=sha,
        dest_path="src/gaia/coder/vendored/click_core.py",
        attribution_note="Used for CLI help layout.",
        repo_root=str(tmp_path),
    )

    dest = tmp_path / "src/gaia/coder/vendored/click_core.py"
    text = dest.read_text()
    assert text.startswith(f"# Adapted from pallets/click @ {sha} — MIT\n")
    assert "Used for CLI help layout." in text
    assert 'def greet():' in text

    notices = (tmp_path / "THIRD_PARTY_NOTICES.md").read_text()
    assert "pallets/click" in notices
    assert sha in notices
    assert "MIT" in notices
    assert result["license"] == "MIT"
    assert result["notices_entry_added"] is True


def test_import_refuses_gpl(oss_mixin, monkeypatch, tmp_path):
    _install_import_stubs(monkeypatch, license_spdx="GPL-3.0")
    sha = "abc1234def5678"
    with pytest.raises(LicenseIncompatibleError) as exc:
        _get_tool("import_with_attribution")(
            source_url=f"https://github.com/torvalds/linux/blob/{sha}/kernel/sched.c",
            commit_sha=sha,
            dest_path="src/gaia/coder/vendored/sched.c",
            repo_root=str(tmp_path),
        )
    assert exc.value.license_id == "GPL-3.0"
    assert exc.value.repository == "torvalds/linux"
    # No files written.
    assert not (tmp_path / "src/gaia/coder/vendored/sched.c").exists()
    assert not (tmp_path / "THIRD_PARTY_NOTICES.md").exists()


def test_import_rejects_branch_pin(oss_mixin, monkeypatch, tmp_path):
    """Rule 3 — pin to SHA, never a branch."""
    _install_import_stubs(monkeypatch)
    with pytest.raises(OSSAttributionError, match="provenance must match"):
        _get_tool("import_with_attribution")(
            source_url="https://github.com/pallets/click/blob/main/src/click/core.py",
            commit_sha="abc1234def5678",
            dest_path="x.py",
            repo_root=str(tmp_path),
        )


def test_import_rejects_non_sha_commit(oss_mixin, monkeypatch, tmp_path):
    _install_import_stubs(monkeypatch)
    with pytest.raises(OSSAttributionError, match="does not look like"):
        _get_tool("import_with_attribution")(
            source_url="https://github.com/pallets/click/blob/release-8.2/src/click/core.py",
            commit_sha="release-8.2",
            dest_path="x.py",
            repo_root=str(tmp_path),
        )


def test_import_appends_without_clobbering_existing_notices(
    oss_mixin, monkeypatch, tmp_path
):
    """Multiple imports should append; never truncate prior entries."""
    _install_import_stubs(monkeypatch, license_spdx="Apache-2.0")
    sha = "a" * 10
    for i in range(2):
        _get_tool("import_with_attribution")(
            source_url=f"https://github.com/some/repo/blob/{sha}/file{i}.py",
            commit_sha=sha,
            dest_path=f"src/gaia/coder/vendored/file{i}.py",
            repo_root=str(tmp_path),
        )
    body = (tmp_path / "THIRD_PARTY_NOTICES.md").read_text()
    # Each import produces one "## " block.
    assert body.count("## `src/gaia/coder/vendored/file") == 2


def test_import_with_raw_url_works(oss_mixin, monkeypatch, tmp_path):
    _install_import_stubs(monkeypatch, license_spdx="BSD-3-Clause")
    sha = "a" * 40
    result = _get_tool("import_with_attribution")(
        source_url=f"https://raw.githubusercontent.com/foo/bar/{sha}/pkg/x.py",
        commit_sha=sha,
        dest_path="src/gaia/coder/vendored/x.py",
        repo_root=str(tmp_path),
    )
    assert result["license"] == "BSD-3-Clause"


def test_import_rejects_unparseable_url(oss_mixin, monkeypatch, tmp_path):
    _install_import_stubs(monkeypatch)
    with pytest.raises(OSSAttributionError, match="cannot parse"):
        _get_tool("import_with_attribution")(
            source_url="https://example.com/not/github",
            commit_sha="abc1234",
            dest_path="x.py",
            repo_root=str(tmp_path),
        )


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def test_all_four_tools_registered(oss_mixin):
    for name in (
        "gh_search_code",
        "gh_search_repos",
        "vet_license",
        "import_with_attribution",
    ):
        assert name in _TOOL_REGISTRY, f"missing tool: {name}"
