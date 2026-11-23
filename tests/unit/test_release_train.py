# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""util/release_train.py: the release-train verify stage.

Builds a complete fake artifact set (real archive formats, dummy native
binaries) for this checkout's version and checks that verify passes it, then
breaks it one way at a time -- an altered twin, a wrong version stamp, a lock
that no longer matches, a missing platform -- and checks that each one fails.
"""

from __future__ import annotations

import hashlib
import io
import json
import lzma
import sys
import tarfile
import time
import zipfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "util"))

import release_train as rt  # noqa: E402  pylint: disable=wrong-import-position

sys.path.insert(0, str(REPO_ROOT / "installer" / "tui"))
from artifacts import artifacts_for  # noqa: E402  pylint: disable=wrong-import-position

CORE = rt.core_version(REPO_ROOT)
AGENT = rt.agent_version(REPO_ROOT)
RC_TAG = f"v{CORE}-rc7"
SHA = "0" * 40


@pytest.fixture(scope="module")
def flavors():
    return rt.flavors(RC_TAG, REPO_ROOT)


# ── Archive builders ────────────────────────────────────────────────────────


def _bundle(version: str, chunk_hash: str) -> dict[str, bytes]:
    """A Vite-like bundle: the chunk holding the version is renamed with it."""
    return {
        f"assets/index-{chunk_hash}.js": (
            f'import "./vendor-react-Ab3dE_g1.js";const v="{version}";'
        ).encode(),
        "assets/vendor-react-Ab3dE_g1.js": b"export const react=1;",
        "index.html": f'<script src="/assets/index-{chunk_hash}.js"></script>'.encode(),
    }


def make_wheel(path: Path, pep: str, npm: str, chunk: str, extra: dict | None = None):
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(
            f"amd_gaia-{pep}.dist-info/METADATA",
            f"Metadata-Version: 2.1\nName: amd-gaia\nVersion: {pep}\n",
        )
        zf.writestr(
            f"amd_gaia-{pep}.dist-info/RECORD", f"gaia/version.py,sha256={pep},1\n"
        )
        zf.writestr("gaia/version.py", f'__version__ = "{pep}"\n')
        for name, data in _bundle(npm, chunk).items():
            zf.writestr(f"gaia/apps/webui/dist/{name}", data)
        for name, data in (extra or {}).items():
            zf.writestr(name, data)


def _tar_gz(path: Path, files: dict[str, bytes]) -> None:
    with tarfile.open(path, "w:gz") as tf:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))


def make_sdist(path: Path, pep: str, chunk: str, extra_listed: tuple = ()):
    # setuptools sorts SOURCES.txt by name, so a renamed Vite chunk can move.
    listed = sorted(
        [
            f"src/gaia/apps/webui/dist/assets/index-{chunk}.js",
            "src/gaia/apps/webui/dist/assets/index-M0n0p0q0.css",
            *extra_listed,
        ]
    )
    _tar_gz(
        path,
        {
            f"amd_gaia-{pep}/PKG-INFO": f"Metadata-Version: 2.1\nName: amd-gaia\nVersion: {pep}\n".encode(),
            f"amd_gaia-{pep}/src/gaia/version.py": f'__version__ = "{pep}"\n'.encode(),
            f"amd_gaia-{pep}/src/amd_gaia.egg-info/SOURCES.txt": "\n".join(
                listed
            ).encode(),
        },
    )


def make_npm(
    path: Path, name: str, version: str, chunk: str, extra: dict | None = None
):
    files = {
        "package/package.json": json.dumps({"name": name, "version": version}).encode()
    }
    for n, data in _bundle(version, chunk).items():
        files[f"package/dist/{n}"] = data
    files.update(extra or {})
    _tar_gz(path, files)


def make_asar(files: dict[str, bytes]) -> bytes:
    tree: dict = {"files": {}}
    payload = b""
    for name, data in files.items():
        node = tree
        parts = name.split("/")
        for part in parts[:-1]:
            node = node["files"].setdefault(part, {"files": {}})
        node["files"][parts[-1]] = {"size": len(data), "offset": str(len(payload))}
        payload += data
    header = json.dumps(tree).encode()
    pickle = len(header).to_bytes(4, "little") + header
    pickle += b"\0" * (-len(pickle) % 4)
    header_pickle = len(pickle).to_bytes(4, "little") + pickle
    return (
        (4).to_bytes(4, "little")
        + len(header_pickle).to_bytes(4, "little")
        + header_pickle
        + payload
    )


def make_deb(path: Path, version: str, chunk: str, extra: dict | None = None):
    app = {"package.json": json.dumps({"version": version}).encode()}
    for n, data in _bundle(version, chunk).items():
        app[f"dist/{n}"] = data
    app.update(extra or {})
    data_tar = io.BytesIO()
    with tarfile.open(fileobj=data_tar, mode="w") as tf:
        blob = make_asar(app)
        info = tarfile.TarInfo("./opt/GAIA Agent UI/resources/app.asar")
        info.size = len(blob)
        tf.addfile(info, io.BytesIO(blob))
    members = [
        ("debian-binary", b"2.0\n"),
        ("data.tar.xz", lzma.compress(data_tar.getvalue())),
    ]
    out = b"!<arch>\n"
    for name, data in members:
        out += f"{name + '/':<16}{0:<12}{0:<6}{0:<6}{'100644':<8}{len(data):<10}`\n".encode()
        out += data + (b"\n" if len(data) % 2 else b"")
    path.write_bytes(out)


# ── A full fake build ───────────────────────────────────────────────────────


def _write(path: Path, data: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def build_art(
    tmp: Path, flavors, *, skip_intel_sidecar: bool = False
) -> tuple[Path, Path]:
    rc, final = flavors
    art = tmp / "art"
    chunks = {"rc": "Rc0aB-1x", "final": "Fn9zY_2w"}
    for f in (rc, final):
        p, chunk = f.name, chunks[f.name]
        core = art / f"{p}-core-dist"
        core.mkdir(parents=True)
        make_wheel(
            core / f"amd_gaia-{f.pep440}-py3-none-any.whl", f.pep440, f.npm, chunk
        )
        make_sdist(core / f"amd_gaia-{f.pep440}.tar.gz", f.pep440, chunk)
        make_npm(
            core / f"amd-gaia-agent-ui-{f.npm}.tgz", "@amd-gaia/agent-ui", f.npm, chunk
        )
        ui = f"gaia-agent-ui-{f.npm}"
        win, mac, lin = (
            art / f"{p}-{x}-installer" for x in ("windows", "macos", "linux")
        )
        for d, names in (
            (win, [f"{ui}-x64-setup.exe", f"{ui}-x64-setup.exe.blockmap"]),
            (
                mac,
                [
                    f"{ui}-arm64.dmg",
                    f"{ui}-arm64.dmg.blockmap",
                    f"{ui}-arm64.zip",
                    f"{ui}-arm64.zip.blockmap",
                ],
            ),
            (lin, [f"{ui}-x86_64.AppImage"]),
        ):
            for n in names:
                _write(d / n, b"MZ installer " + f.npm.encode())
        for d, feed in (
            (win, "latest.yml"),
            (mac, "latest-mac.yml"),
            (lin, "latest-linux.yml"),
        ):
            _write(d / feed, f"version: {f.npm}\nreleaseDate: now\n".encode())
        make_deb(lin / f"{ui}-amd64.deb", f.npm, chunk)
        for platform in rt.TUI_PLATFORMS:
            ext = ".exe" if platform.startswith("win") else ""
            _write(
                art / f"{p}-tui-binaries" / f"gaia-{platform}{ext}",
                b"\x7fELF main.version=" + f.npm.encode() + platform.encode(),
            )
        setup = artifacts_for("win-x64", f.npm)[0][0]
        _write(art / f"{p}-windows-setup" / setup, b"MZ setup")
        for platform, label in (
            ("darwin-arm64", "macos"),
            ("darwin-x64", "macos"),
            ("linux-x64", "linux"),
        ):
            if skip_intel_sidecar and platform == "darwin-x64":
                continue  # no agent, so tui_installers.yml builds no Intel .pkg
            for n, _ in artifacts_for(platform, f.npm):
                _write(art / f"{p}-tui-installers-{label}" / n, b"pkg " + p.encode())

    for platform in ("win32-x64", "darwin-arm64", "linux-x64", "darwin-x64"):
        if skip_intel_sidecar and platform == "darwin-x64":
            continue
        exe = platform.startswith("win32")
        name = f"gaia-agent-{platform}" + (".exe" if exe else "")
        data = b"frozen sidecar " + platform.encode()
        d = art / f"gaia-agent-{platform}"
        _write(d / name, data)
        meta = {
            "component": "sidecar",
            "platform": platform,
            "filename": name,
            "executable": "gaia-agent.exe" if exe else "gaia-agent",
            "sha256": hashlib.sha256(data).hexdigest(),
            "size": len(data),
        }
        _write(d / f"sidecar-{platform}.meta.json", json.dumps([meta]).encode())

    flagship = tmp / "flagship"
    lock = rt.build_lock(
        REPO_ROOT,
        art,
        art / "final-tui-binaries",
        final.npm,
        flagship / "binaries.lock.json",
    )
    make_npm(
        flagship / f"amd-gaia-gaia-{AGENT}.tgz",
        "@amd-gaia/gaia",
        AGENT,
        "Ag3ntJs1",
        extra={"package/binaries.lock.json": json.dumps(lock).encode()},
    )
    # make_npm's version stamp in the bundle is irrelevant here; only the
    # package.json and the lock are checked.
    _write(flagship / "rc-assets" / "gaia-agent-release.json", b"{}")
    _write(flagship / "rc-assets" / "gaia-agent.yaml", b"id: gaia\n")
    return art, flagship


def run_verify(tmp: Path, art: Path, flagship: Path) -> rt.Report:
    return rt.verify(art, REPO_ROOT, RC_TAG, SHA, tmp / "out", flagship)


# ── Tests ───────────────────────────────────────────────────────────────────


def test_a_clean_build_passes_and_lists_every_destination(tmp_path, flavors):
    art, flagship = build_art(tmp_path, flavors)
    report = run_verify(tmp_path, art, flagship)
    assert report.errors == []
    assert report.warnings == []
    manifest = json.loads((tmp_path / "out" / "release-manifest.json").read_text())
    names = {(a["flavor"], a["name"]) for a in manifest["artifacts"]}
    rc, final = flavors
    assert ("rc", f"amd_gaia-{rc.pep440}-py3-none-any.whl") in names
    assert ("final", f"amd_gaia-{final.pep440}-py3-none-any.whl") in names
    assert ("final", f"amd-gaia-gaia-{AGENT}.tgz") in names
    channels = {d["channel"] for a in manifest["artifacts"] for d in a["destinations"]}
    assert channels == {"pypi", "npm", "github-release", "hub"}
    # The hub only carries what the flagship lock pins: sidecars and raw TUI binaries.
    hub_kinds = {
        a["kind"]
        for a in manifest["artifacts"]
        if any(d["channel"] == "hub" for d in a["destinations"])
    }
    assert hub_kinds == {"flagship-sidecar", "tui-binary"}
    sums = (tmp_path / "out" / f"SHA256SUMS-{rc.tag}").read_text()
    assert f"amd_gaia-{rc.pep440}.tar.gz" in sums
    assert f"amd_gaia-{final.pep440}.tar.gz" not in sums
    assert "twin-diff" in "\n".join(report.checks)
    # The install scripts verify the terminal binary against this file.
    for f in flavors:
        assert (f.name, rt.TUI_SUMS) in names
        lines = (tmp_path / "out" / f.name / rt.TUI_SUMS).read_text().splitlines()
        assert len(lines) == len(rt.TUI_PLATFORMS)
        linux = art / f"{f.name}-tui-binaries" / "gaia-linux-x64"
        assert f"{rt.sha256(linux)}  gaia-linux-x64" in lines


def test_the_lock_pins_this_runs_bytes(tmp_path, flavors):
    art, flagship = build_art(tmp_path, flavors)
    lock = json.loads((flagship / "binaries.lock.json").read_text())
    assert lock["agentVersion"] == AGENT
    tui = lock["components"]["tui"]
    assert tui["componentVersion"] == flavors[1].npm
    assert tui["baseUrl"].endswith(f"/agents/terminal-hub/{flavors[1].npm}")
    linux = art / "final-tui-binaries" / "gaia-linux-x64"
    assert tui["platforms"]["linux-x64"]["sha256"] == rt.sha256(linux)
    assert "PENDING" not in json.dumps(lock)


def test_a_missing_best_effort_sidecar_drops_its_lock_entry(tmp_path, flavors):
    art, flagship = build_art(tmp_path, flavors, skip_intel_sidecar=True)
    lock = json.loads((flagship / "binaries.lock.json").read_text())
    assert "darwin-x64" not in lock["components"]["sidecar"]["platforms"]
    report = run_verify(tmp_path, art, flagship)
    assert any("gaia-agent-darwin-x64" in w for w in report.warnings)
    # The final ships without its Intel .pkg; a candidate does not ship at all.
    final_pkg = artifacts_for("darwin-x64", flavors[1].npm)[0][0]
    rc_pkg = artifacts_for("darwin-x64", flavors[0].npm)[0][0]
    assert any(final_pkg in w for w in report.warnings)
    assert len(report.errors) == 1 and rc_pkg in report.errors[0]


def test_a_missing_required_sidecar_refuses_to_lock(tmp_path, flavors):
    art, _ = build_art(tmp_path, flavors)
    for f in (art / "gaia-agent-linux-x64").iterdir():
        f.unlink()
    with pytest.raises(rt.VerifyError, match="linux-x64"):
        rt.build_lock(
            REPO_ROOT,
            art,
            art / "final-tui-binaries",
            flavors[1].npm,
            tmp_path / "again" / "binaries.lock.json",
        )


def test_an_altered_twin_fails_the_twin_diff(tmp_path, flavors):
    art, flagship = build_art(tmp_path, flavors)
    rc, final = flavors
    whl = art / "final-core-dist" / f"amd_gaia-{final.pep440}-py3-none-any.whl"
    make_wheel(
        whl, final.pep440, final.npm, "Fn9zY_2w", extra={"gaia/backdoor.py": b"x"}
    )
    report = run_verify(tmp_path, art, flagship)
    assert any("only in the twin: gaia/backdoor.py" in e for e in report.errors)


def test_a_file_listing_compares_as_a_set(tmp_path, flavors):
    """The RC and twin listings sort differently; one extra entry still fails."""
    art, _ = build_art(tmp_path, flavors)
    rc, final = flavors
    a = art / "rc-core-dist" / f"amd_gaia-{rc.pep440}.tar.gz"
    b = art / "final-core-dist" / f"amd_gaia-{final.pep440}.tar.gz"
    listing = "amd_gaia-{}/src/amd_gaia.egg-info/SOURCES.txt"
    first_a = rt.archive_files(a)[listing.format(rc.pep440)].splitlines()[0]
    first_b = rt.archive_files(b)[listing.format(final.pep440)].splitlines()[0]
    assert first_a != first_b, "the fixture must reorder the listing"
    assert rt.twin_diff(a, b, rc, final) == []
    make_sdist(b, final.pep440, "Fn9zY_2w", extra_listed=("src/gaia/extra.py",))
    assert rt.twin_diff(a, b, rc, final) == [
        f"differs beyond its version: {listing.format(final.pep440)}"
    ]


def test_twin_content_that_differs_beyond_its_version_fails(tmp_path, flavors):
    art, flagship = build_art(tmp_path, flavors)
    rc, final = flavors
    deb = art / "final-linux-installer" / f"gaia-agent-ui-{final.npm}-amd64.deb"
    make_deb(
        deb, final.npm, "Fn9zY_2w", extra={"package.json": b'{"version": "9.9.9"}'}
    )
    report = run_verify(tmp_path, art, flagship)
    assert any("differs beyond its version: package.json" in e for e in report.errors)


def test_a_wrong_version_stamp_fails(tmp_path, flavors):
    art, flagship = build_art(tmp_path, flavors)
    rc, final = flavors
    _write(
        art / "final-windows-installer" / "latest.yml", f"version: {rc.npm}\n".encode()
    )
    report = run_verify(tmp_path, art, flagship)
    assert any("latest.yml offers" in e for e in report.errors)


def test_a_final_terminal_binary_with_the_rc_version_fails(tmp_path, flavors):
    art, flagship = build_art(tmp_path, flavors)
    rc, _ = flavors
    p = art / "final-tui-binaries" / "gaia-win-arm64.exe"
    p.write_bytes(p.read_bytes() + rc.npm.encode())
    report = run_verify(tmp_path, art, flagship)
    assert any("carries no RC version" in e for e in report.errors)
    # The bytes changed after the lock was generated, so the lock check fails too.
    assert any("lock tui/win32-arm64" in e for e in report.errors)


def test_a_malformed_archive_is_reported_not_raised(tmp_path, flavors):
    """A wheel with no METADATA and a truncated tarball each fail one check."""
    art, flagship = build_art(tmp_path, flavors)
    rc, final = flavors
    whl = art / "rc-core-dist" / f"amd_gaia-{rc.pep440}-py3-none-any.whl"
    with zipfile.ZipFile(whl, "w") as zf:
        zf.writestr("gaia/version.py", "")
    tgz = art / "final-core-dist" / f"amd-gaia-agent-ui-{final.npm}.tgz"
    tgz.write_bytes(tgz.read_bytes()[:40])
    report = run_verify(tmp_path, art, flagship)
    assert any(
        e.startswith(whl.name) and "has no" in e and "METADATA" in e
        for e in report.errors
    )
    assert any(e.startswith(tgz.name) for e in report.errors)
    # Everything else was still checked, and the manifest was still written.
    assert any("latest.yml offers" in c for c in report.checks)
    assert (tmp_path / "out" / "release-manifest.json").is_file()


def test_a_missing_required_artifact_fails(tmp_path, flavors):
    art, flagship = build_art(tmp_path, flavors)
    (art / "rc-windows-setup" / artifacts_for("win-x64", flavors[0].npm)[0][0]).unlink()
    report = run_verify(tmp_path, art, flagship)
    assert any("was not built" in e for e in report.errors)


def test_the_flagship_package_must_ship_the_generated_lock(tmp_path, flavors):
    art, flagship = build_art(tmp_path, flavors)
    make_npm(
        flagship / f"amd-gaia-gaia-{AGENT}.tgz",
        "@amd-gaia/gaia",
        AGENT,
        "Ag3ntJs1",
        extra={"package/binaries.lock.json": b"{}"},
    )
    report = run_verify(tmp_path, art, flagship)
    assert any("ships the generated lock" in e for e in report.errors)


@pytest.mark.parametrize(
    "tag,match",
    [
        (f"v{CORE}", "final tag"),
        ("v0.0.1-rc1", "version.py"),
        ("not-a-tag", "not a release tag"),
    ],
)
def test_plan_rejects_a_tag_that_does_not_fit_the_tree(tag, match):
    with pytest.raises(rt.VerifyError, match=match):
        rt.flavors(tag, REPO_ROOT)


def test_plan_prints_github_outputs(capsys):
    assert rt.main(["plan", "--tag", RC_TAG, "--target", str(REPO_ROOT)]) == 0
    out = dict(line.split("=", 1) for line in capsys.readouterr().out.splitlines())
    assert out["final_tag"] == f"v{CORE}"
    assert out["rc_npm"] == f"{CORE}-rc.7"
    assert out["rc_pep440"] == f"{CORE}rc7"
    assert out["agent_version"] == AGENT


@pytest.mark.parametrize(
    "tags,want",
    [
        ([], "v0.30.0-rc1"),
        (["v0.29.9", "v0.29.9-rc4", "v0.31.0-rc8"], "v0.30.0-rc1"),
        (["v0.30.0-rc1", "v0.30.0-rc3", "v0.30.0-rc2"], "v0.30.0-rc4"),
        (["v0.30.0-rc9", "v0.30.0-rc10"], "v0.30.0-rc11"),
        (
            ["v0.30.0.1", "agent-pkg-gaia-v0.30.0", "v0.30.0-rc0", "", "junk"],
            "v0.30.0-rc1",
        ),
        (["a1\trefs/tags/v0.30.0-rc2", "b2\trefs/tags/v0.3.0-rc7"], "v0.30.0-rc3"),
    ],
)
def test_next_rc_tag_is_one_past_the_highest_candidate(tags, want):
    assert rt.next_rc_tag("0.30.0", tags) == want


def test_next_rc_tag_refuses_a_version_already_released():
    with pytest.raises(rt.VerifyError, match="already released.*version bump"):
        rt.next_rc_tag("0.30.0", ["v0.30.0-rc2", "v0.30.0"])


def test_next_tag_reads_ls_remote_and_plan_accepts_it(monkeypatch, capsys):
    lines = f"aaa\trefs/tags/v{CORE}-rc4\nbbb\trefs/tags/v{CORE}-rc5\n"
    monkeypatch.setattr(sys, "stdin", io.StringIO(lines))
    assert rt.main(["next-tag", "--target", str(REPO_ROOT)]) == 0
    tag = capsys.readouterr().out.strip()
    assert tag == f"v{CORE}-rc6"
    rt.flavors(tag, REPO_ROOT)


def test_vite_hash_normalisation_keeps_names_apart():
    a = rt.normalise(b'import "./vendor-react-Ab3dE_g1.js"', [])
    assert a == b'import "./vendor-react-#.js"'
    assert rt.normalise(b"index-Rc0aB-1x.css", []) == b"index-#.css"


# ── verified-run: skip only a commit whose verified build is still downloadable ──


def _whole(extra=()):
    names = [rt.MANIFEST_ARTIFACT, *rt.SHORT_LIVED_ARTIFACTS, *extra]
    return [{"name": n, "expired": False} for n in names]


def _runs(*ids):
    return lambda repo, sha: [
        {"id": i, "url": f"https://x/runs/{i}", "created_at": f"2026-11-0{i}"}
        for i in ids
    ]


def test_verified_run_takes_the_newest_whole_run():
    arts = {1: _whole(), 2: _whole()}
    assert rt.verified_run("o/r", SHA, _runs(1, 2), lambda r, i: arts[i]).endswith("/2")


def test_a_skipped_nightly_does_not_count_as_verified():
    # It passed but built nothing, so it has no artifacts at all.
    arts = {1: _whole(), 2: []}
    assert rt.verified_run("o/r", SHA, _runs(1, 2), lambda r, i: arts[i]).endswith("/1")


def test_a_run_past_retention_must_be_rebuilt():
    # GitHub drops an expired artifact from the list; only long-lived ones remain.
    old = [
        {"name": rt.MANIFEST_ARTIFACT, "expired": False},
        {"name": "gaia-agent-linux-x64", "expired": False},
    ]
    assert rt.verified_run("o/r", SHA, _runs(1), lambda r, i: old) == ""


def test_an_artifact_flagged_expired_does_not_count():
    arts = _whole()
    arts[1]["expired"] = True
    assert rt.verified_run("o/r", SHA, _runs(1), lambda r, i: arts) == ""


def test_no_passing_run_means_build():
    assert rt.verified_run("o/r", SHA, _runs(), lambda r, i: _whole()) == ""


def test_gh_failures_fail_loudly(monkeypatch):
    def run(args, **kw):
        if args[:3] != ["gh", "api", "rate_limit"]:
            assert kw["timeout"] == rt.GH_TIMEOUT_S
        return rt.subprocess.CompletedProcess(args, 1, "", "HTTP 502")

    monkeypatch.setattr(rt.subprocess, "run", run)
    with pytest.raises(rt.VerifyError, match="HTTP 502"):
        rt.verified_run("o/r", SHA)


def _refused_once(monkeypatch, reset_in):
    """gh is refused once on a spent budget, then answers; returns the waits."""
    waits, reset = [], int(time.time()) + reset_in

    def run(args, **kw):
        if args[:3] == ["gh", "api", "rate_limit"]:
            core = {"remaining": 0, "reset": reset}
            return rt.subprocess.CompletedProcess(args, 0, json.dumps(core), "")
        if not waits:
            msg = "gh: API rate limit exceeded for installation ID 1 (HTTP 403)"
            return rt.subprocess.CompletedProcess(args, 1, "", msg)
        return rt.subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(rt.subprocess, "run", run)
    monkeypatch.setattr(time, "sleep", waits.append)
    return waits


def test_a_spent_budget_is_waited_out_not_reported_as_no_run(monkeypatch):
    waits = _refused_once(monkeypatch, reset_in=100)
    assert rt._passing_runs("o/r", SHA) == []
    assert len(waits) == 1 and 95 <= waits[0] <= 110


def test_a_budget_that_outlasts_the_bound_fails_naming_the_rate_limit(monkeypatch):
    waits = _refused_once(monkeypatch, reset_in=2 * rt.GH_RATE_LIMIT_WAIT_S)
    with pytest.raises(rt.VerifyError, match="rate limited"):
        rt._passing_runs("o/r", SHA)
    assert waits == []


def test_runs_are_read_from_every_page(monkeypatch):
    seen = []

    def run(args, **kw):
        seen.append(args)
        body = "\n".join(
            json.dumps({"id": i, "url": f"u{i}", "created_at": f"t{i:03}"})
            for i in range(150)
        )
        return rt.subprocess.CompletedProcess(args, 0, body, "")

    monkeypatch.setattr(rt.subprocess, "run", run)
    assert len(rt._passing_runs("o/r", SHA)) == 150
    assert "--paginate" in seen[0]


@pytest.mark.parametrize("name", rt.SHORT_LIVED_ARTIFACTS)
def test_short_lived_names_are_uploaded_by_the_dry_run(name):
    """Each sentinel must be a real 7-day upload, or every run reads as expired."""
    suffix = name.removeprefix("rc-").removeprefix("final-")
    wf = REPO_ROOT / ".github" / "workflows"
    uploads = []
    for f in ("release_train_dryrun.yml", "tui_installers.yml", "windows_setup.yml"):
        lines = (wf / f).read_text(encoding="utf-8").splitlines()
        for i, line in enumerate(lines):
            if line.strip().startswith("name:") and line.rstrip().endswith(suffix):
                block = "\n".join(lines[i : i + 8])
                uploads.append((f, "retention-days: 7" in block))
    assert uploads, f"no workflow uploads an artifact named *{suffix}"
    assert any(seven for _, seven in uploads), f"*{suffix} is not a 7-day artifact"
