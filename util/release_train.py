#!/usr/bin/env python3
# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Verify a release-train build and list exactly what it would publish.

The release train (docs/plans/release-train.mdx, #5172) builds every first-party
artifact once per release candidate, plus a final-stamped twin of each artifact
that embeds the version. This is the verify stage. It never publishes anything:
it reads the artifacts a build left on disk and writes a manifest.

Subcommands::

    # resolve an RC tag against the tree it builds; KEY=VALUE for $GITHUB_OUTPUT
    release_train.py plan --tag v0.25.1-rc2 --target target

    # binaries.lock.json for the flagship npm package, from this run's builds
    release_train.py lock --target target --sidecars art --tui art/final-tui-binaries \\
        --tui-version 0.25.1 --out flagship/binaries.lock.json

    # checksums, version stamps, the lock, the twin-diff, and the manifest
    release_train.py verify --art art --target target --tag v0.25.1-rc2 \\
        --sha <commit> --out out

``--target`` is a checkout of the commit being released. The tables that name
the artifacts (installer/tui/artifacts.py, gen_binaries_lock.py,
util/flagship_rc_assets.py) are read from it, never copied here, so this file
cannot drift from the code that builds them.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import importlib.util
import io
import json
import lzma
import re
import sys
import tarfile
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType

_UTIL = str(Path(__file__).resolve().parent)
if _UTIL not in sys.path:
    sys.path.insert(0, _UTIL)

from release_tag import (  # noqa: E402  pylint: disable=wrong-import-position
    ReleaseTag,
    parse_tag,
)

MANIFEST_SCHEMA = 1
HUB_ORIGIN = "https://hub.amd-gaia.ai"
PYPI_PROJECT = "amd-gaia"
AGENT_UI_NPM = "@amd-gaia/agent-ui"
FLAGSHIP_NPM = "@amd-gaia/gaia"
# Decision 4 of the plan: the flagship's train name, with a `gaia-agent` bin.
FLAGSHIP_NPM_TRAIN = "@amd-gaia/agent"

# Same platform keys and order as tui_installers.yml's six targets.
TUI_PLATFORMS = (
    "linux-x64",
    "linux-arm64",
    "darwin-x64",
    "darwin-arm64",
    "win-x64",
    "win-arm64",
)
# tui platform -> the npm key the flagship's lock uses for it.
TUI_NPM_KEY = {
    "linux-x64": "linux-x64",
    "linux-arm64": "linux-arm64",
    "darwin-x64": "darwin-x64",
    "darwin-arm64": "darwin-arm64",
    "win-x64": "win32-x64",
    "win-arm64": "win32-arm64",
}

# A build file the wheel lists with each file's hash. Every file it describes is
# compared directly, so comparing it too would only report the version again.
TWIN_DIFF_SKIP = re.compile(r"(^|/)[^/]+\.dist-info/RECORD$")
# File listings sorted by name: renamed Vite chunks sort differently, so these
# compare as a set of lines. An added or dropped entry still differs.
TWIN_DIFF_UNORDERED = re.compile(r"(^|/)[^/]+\.egg-info/SOURCES\.txt$")
# Vite names a chunk after a hash of its content, so a chunk holding the version
# gets a new name, and so does every chunk that imports it.
_VITE_HASHED = re.compile(
    rb"([A-Za-z0-9_.$]+(?:-[A-Za-z0-9_.$]+)*?)-[A-Za-z0-9_-]{8}"
    rb"\.(js|mjs|css|woff2?|ttf|svg|png|jpg|webp|ico|map)\b"
)


class VerifyError(Exception):
    """A check failed; the message says what and what to do."""


def _fail(msg: str) -> None:
    print(f"::error::{msg}", flush=True)
    raise SystemExit(1)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_module(path: Path, name: str, extra_path: Path | None = None) -> ModuleType:
    """Import ``path`` from the release's own checkout."""
    if not path.is_file():
        raise VerifyError(f"{path} is missing from the checkout being released.")
    if extra_path is not None and str(extra_path) not in sys.path:
        sys.path.insert(0, str(extra_path))
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


# ── Versions ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Flavor:
    """One build of the release: the candidate, or its final-stamped twin."""

    name: str  # "rc" | "final"
    rel: ReleaseTag

    @property
    def tag(self) -> str:
        return self.rel.tag

    @property
    def pep440(self) -> str:
        return self.rel.pep440_version

    @property
    def npm(self) -> str:
        return self.rel.npm_version


def core_version(target: Path) -> str:
    text = (target / "src/gaia/version.py").read_text(encoding="utf-8")
    m = re.search(r'(?m)^__version__\s*=\s*"([^"]+)"', text)
    if not m:
        raise VerifyError(f"no __version__ in {target}/src/gaia/version.py")
    return m.group(1)


def agent_version(target: Path) -> str:
    text = (target / "hub/agents/gaia/python/gaia_agent/__init__.py").read_text(
        encoding="utf-8"
    )
    m = re.search(r'(?m)^__version__\s*=\s*"([^"]+)"', text)
    if not m:
        raise VerifyError(
            "no __version__ in hub/agents/gaia/python/gaia_agent/__init__.py"
        )
    return m.group(1)


def flavors(rc_tag: str, target: Path) -> tuple[Flavor, Flavor]:
    """The candidate and its twin, after checking the tag against the tree."""
    try:
        rc = parse_tag(rc_tag)
    except ValueError as e:
        raise VerifyError(str(e)) from e
    if not rc.is_rc:
        raise VerifyError(
            f"{rc_tag} is a final tag. The dry run builds a release candidate and "
            "its final-stamped twin, so name the candidate (vX.Y.Z-rcN)."
        )
    committed = core_version(target)
    if committed != rc.base_version:
        raise VerifyError(
            f"{rc_tag} releases {rc.base_version}, but src/gaia/version.py at this "
            f"commit says {committed}. Name an RC of {committed}, or build a commit "
            f"whose version.py says {rc.base_version}."
        )
    return Flavor("rc", rc), Flavor("final", parse_tag(f"v{rc.base_version}"))


def cmd_plan(args: argparse.Namespace) -> int:
    rc, final = flavors(args.tag, args.target)
    out = {
        "rc_tag": rc.tag,
        "final_tag": final.tag,
        "base_version": rc.rel.base_version,
        "rc_npm": rc.npm,
        "rc_pep440": rc.pep440,
        "agent_version": agent_version(args.target),
    }
    for k, v in out.items():
        print(f"{k}={v}")
    return 0


# ── The flagship lock ───────────────────────────────────────────────────────


def tui_metas(tui_dir: Path, names: dict[str, str]) -> list[dict]:
    """One lock record per terminal binary, under the npm platform key."""
    metas = []
    for platform in TUI_PLATFORMS:
        key = TUI_NPM_KEY[platform]
        filename = names[key]
        path = tui_dir / filename
        if not path.is_file():
            raise VerifyError(
                f"{path} is missing. The terminal build uploads all six binaries; "
                "the lock cannot pin one that was not built."
            )
        metas.append(
            {
                "component": "tui",
                "platform": key,
                "filename": filename,
                "executable": (
                    "gaia-tui.exe" if filename.endswith(".exe") else "gaia-tui"
                ),
                "sha256": sha256(path),
                "size": path.stat().st_size,
            }
        )
    return metas


def build_lock(
    target: Path, sidecars: Path, tui_dir: Path, tui_version: str, out: Path
) -> dict:
    """binaries.lock.json from this run's own sidecar and terminal builds."""
    packaging = target / "hub/agents/gaia/python/packaging"
    gen = load_module(packaging / "gen_binaries_lock.py", "gen_binaries_lock")
    rc_assets = load_module(target / "util/flagship_rc_assets.py", "flagship_rc_assets")

    # collect() re-hashes every sidecar against the meta its freeze leg wrote.
    found = rc_assets.collect(sidecars)
    missing = [p for p in rc_assets.REQUIRED_PLATFORMS if p not in found]
    if missing:
        raise VerifyError(
            f"no gaia-agent sidecar was built for the required platform(s) "
            f"{', '.join(missing)}; the lock would point those users at nothing."
        )
    version = agent_version(target)
    # Start from the committed lock: gen_binaries_lock keeps the shape and
    # replaces every lane and platform this run has a meta for.
    committed = target / "hub/agents/gaia/npm/binaries.lock.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(committed.read_bytes())
    argv = [
        "--version",
        version,
        "--sidecar-base-url",
        f"{HUB_ORIGIN}/agents/gaia/{version}",
        "--tui-version",
        tui_version,
        "--tui-base-url",
        f"{HUB_ORIGIN}/agents/terminal-hub/{tui_version}",
        "--lock",
        str(out),
    ]
    metas = [meta for meta, _binary in found.values()]
    metas += tui_metas(tui_dir, gen.TUI_ARTIFACT_NAMES)
    with tempfile.TemporaryDirectory() as work:
        for i, meta in enumerate(metas):
            path = Path(work) / f"{i}.meta.json"
            path.write_text(json.dumps([meta], indent=2), encoding="utf-8")
            argv += ["--meta", str(path)]
        gen.main(argv)

    lock = json.loads(out.read_text(encoding="utf-8"))
    # A best-effort platform that did not build keeps the committed placeholder;
    # the shipped lock must carry no entry for it rather than a fake hash.
    sidecar_platforms = lock["components"]["sidecar"]["platforms"]
    for platform in list(sidecar_platforms):
        if platform not in found:
            del sidecar_platforms[platform]
            print(
                f"::warning::no gaia-agent sidecar was built for {platform} "
                "(best-effort), so the lock has no entry for it."
            )
    bad = [
        f"{c}/{p}"
        for c, lane in lock["components"].items()
        for p, rec in lane["platforms"].items()
        if not re.fullmatch(r"[0-9a-f]{64}", str(rec.get("sha256", "")))
        or int(rec.get("size", 0)) <= 0
    ]
    if bad:
        raise VerifyError(
            "binaries.lock.json still has placeholder entries after generation: "
            f"{', '.join(sorted(bad))}. Every entry needs a build in this run."
        )
    out.write_text(json.dumps(lock, indent=2) + "\n", encoding="utf-8")
    return lock


def cmd_lock(args: argparse.Namespace) -> int:
    lock = build_lock(args.target, args.sidecars, args.tui, args.tui_version, args.out)
    print(json.dumps(lock, indent=2))
    return 0


# ── Twin-diff ───────────────────────────────────────────────────────────────


def read_ar(data: bytes) -> dict[str, bytes]:
    """Members of a Unix ar archive (a .deb)."""
    if not data.startswith(b"!<arch>\n"):
        raise VerifyError("not an ar archive")
    members: dict[str, bytes] = {}
    pos = 8
    while pos + 60 <= len(data):
        header = data[pos : pos + 60]
        name = header[0:16].decode("ascii").strip().rstrip("/")
        size = int(header[48:58].decode("ascii").strip())
        members[name] = data[pos + 60 : pos + 60 + size]
        pos += 60 + size + (size % 2)
    return members


def read_asar(data: bytes) -> dict[str, bytes]:
    """Files packed in an Electron app.asar (unpacked ones are listed as such)."""
    header_size = int.from_bytes(data[4:8], "little")
    json_len = int.from_bytes(data[12:16], "little")
    header = json.loads(data[16 : 16 + json_len].decode("utf-8"))
    base = 8 + header_size
    files: dict[str, bytes] = {}

    def walk(node: dict, prefix: str) -> None:
        for name, entry in node.get("files", {}).items():
            path = f"{prefix}{name}"
            if "files" in entry:
                walk(entry, f"{path}/")
            elif "link" in entry:
                files[path] = f"link:{entry['link']}".encode()
            elif entry.get("unpacked"):
                files[path] = b"unpacked"
            else:
                start = base + int(entry["offset"])
                files[path] = data[start : start + int(entry["size"])]

    walk(header, "")
    return files


def _tar_files(data: bytes) -> dict[str, bytes]:
    files = {}
    with tarfile.open(fileobj=io.BytesIO(data)) as tf:
        for m in tf.getmembers():
            if m.isfile():
                f = tf.extractfile(m)
                assert f is not None
                files[m.name.removeprefix("./")] = f.read()
    return files


def archive_files(path: Path) -> dict[str, bytes]:
    """Every file in a wheel, sdist, npm tarball, or a .deb's app.asar."""
    name = path.name
    if name.endswith(".whl"):
        with zipfile.ZipFile(path) as zf:
            return {i.filename: zf.read(i) for i in zf.infolist() if not i.is_dir()}
    if name.endswith((".tar.gz", ".tgz")):
        return _tar_files(gzip.decompress(path.read_bytes()))
    if name.endswith(".deb"):
        members = read_ar(path.read_bytes())
        data_name = next((n for n in members if n.startswith("data.tar")), None)
        if data_name is None:
            raise VerifyError(f"{name} has no data.tar member")
        raw = members[data_name]
        if data_name.endswith(".xz"):
            raw = lzma.decompress(raw)
        elif data_name.endswith(".gz"):
            raw = gzip.decompress(raw)
        elif not data_name.endswith(".tar"):
            raise VerifyError(f"{name}: unsupported {data_name}")
        files = _tar_files(raw)
        asars = [n for n in files if n.endswith("resources/app.asar")]
        if len(asars) != 1:
            raise VerifyError(f"{name} holds {len(asars)} app.asar files, expected 1")
        return read_asar(files[asars[0]])
    raise VerifyError(f"no twin-diff reader for {name}")


def _replacements(rc: Flavor, final: Flavor) -> list[tuple[bytes, bytes]]:
    pairs = [(rc.tag, final.tag), (rc.npm, final.npm), (rc.pep440, final.pep440)]
    # Longest first, so the tag's spelling is not half-replaced by the npm one.
    pairs.sort(key=lambda p: len(p[0]), reverse=True)
    return [(a.encode(), b.encode()) for a, b in pairs]


def normalise(data: bytes, pairs: list[tuple[bytes, bytes]]) -> bytes:
    for old, new in pairs:
        data = data.replace(old, new)
    return _VITE_HASHED.sub(rb"\1-#.\2", data)


def twin_diff(rc_path: Path, final_path: Path, rc: Flavor, final: Flavor) -> list[str]:
    """Differences between an RC archive and its twin, once versions are normalised."""
    pairs = _replacements(rc, final)
    ident: list[tuple[bytes, bytes]] = []

    def view(files: dict[str, bytes], subs) -> dict[str, list[bytes]]:
        out: dict[str, list[bytes]] = {}
        for name, data in files.items():
            if TWIN_DIFF_SKIP.search(name):
                continue
            key = normalise(name.encode(), subs).decode()
            data = normalise(data, subs)
            if TWIN_DIFF_UNORDERED.search(name):
                data = b"\n".join(sorted(data.splitlines()))
            out.setdefault(key, []).append(data)
        return {k: sorted(v) for k, v in out.items()}

    a = view(archive_files(rc_path), pairs)
    b = view(archive_files(final_path), ident)
    problems = []
    for name in sorted(set(a) | set(b)):
        if name not in b:
            problems.append(f"only in the candidate: {name}")
        elif name not in a:
            problems.append(f"only in the twin: {name}")
        elif a[name] != b[name]:
            problems.append(f"differs beyond its version: {name}")
    return problems


# ── The manifest ────────────────────────────────────────────────────────────


@dataclass
class Artifact:
    flavor: str
    name: str
    kind: str
    path: Path
    version: str
    destinations: list[dict]
    required: bool = True
    sha256: str = ""
    size: int = 0

    def record(self) -> dict:
        return {
            "flavor": self.flavor,
            "name": self.name,
            "kind": self.kind,
            "version": self.version,
            "sha256": self.sha256,
            "size": self.size,
            "destinations": self.destinations,
        }


@dataclass
class Report:
    artifacts: list[Artifact] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    checks: list[str] = field(default_factory=list)


def _gh(f: Flavor) -> dict:
    return {
        "channel": "github-release",
        "tag": f.tag,
        "prerelease": f.name == "rc",
    }


def _npm(package: str, f: Flavor, version: str | None = None) -> dict:
    return {
        "channel": "npm",
        "package": package,
        "version": version or f.npm,
        "dist_tag": "next" if f.name == "rc" else "latest",
    }


def _find(art: Path, artifact: str, name: str) -> Path:
    return art / artifact / name


def catalog(
    art: Path, target: Path, rc: Flavor, final: Flavor, flagship_dir: Path
) -> list[Artifact]:
    """Every artifact a real train run would publish, and where each one goes."""
    tui_table = load_module(
        target / "installer/tui/artifacts.py",
        "tui_artifacts",
        extra_path=target / "installer/tui",
    )
    gen = load_module(
        target / "hub/agents/gaia/python/packaging/gen_binaries_lock.py",
        "gen_binaries_lock",
    )
    rc_assets = load_module(target / "util/flagship_rc_assets.py", "flagship_rc_assets")
    agent_ver = agent_version(target)
    items: list[Artifact] = []

    for f in (rc, final):
        p = f.name
        pypi = {"channel": "pypi", "project": PYPI_PROJECT, "version": f.pep440}
        core = f"{p}-core-dist"
        items += [
            Artifact(
                p,
                f"amd_gaia-{f.pep440}-py3-none-any.whl",
                "wheel",
                _find(art, core, f"amd_gaia-{f.pep440}-py3-none-any.whl"),
                f.pep440,
                [pypi, _gh(f)],
            ),
            Artifact(
                p,
                f"amd_gaia-{f.pep440}.tar.gz",
                "sdist",
                _find(art, core, f"amd_gaia-{f.pep440}.tar.gz"),
                f.pep440,
                [pypi, _gh(f)],
            ),
            Artifact(
                p,
                f"amd-gaia-agent-ui-{f.npm}.tgz",
                "npm",
                _find(art, core, f"amd-gaia-agent-ui-{f.npm}.tgz"),
                f.npm,
                [_npm(AGENT_UI_NPM, f)],
            ),
        ]
        ui = f"gaia-agent-ui-{f.npm}"
        for artifact, names in (
            (
                f"{p}-windows-installer",
                [f"{ui}-x64-setup.exe", f"{ui}-x64-setup.exe.blockmap", "latest.yml"],
            ),
            (
                f"{p}-macos-installer",
                [f"{ui}-arm64.dmg", f"{ui}-arm64.dmg.blockmap", "latest-mac.yml"],
            ),
            (
                f"{p}-linux-installer",
                [f"{ui}-amd64.deb", f"{ui}-x86_64.AppImage", "latest-linux.yml"],
            ),
        ):
            for n in names:
                kind = (
                    "electron-update-feed"
                    if n.endswith(".yml")
                    else "agent-ui-installer"
                )
                items.append(
                    Artifact(p, n, kind, _find(art, artifact, n), f.npm, [_gh(f)])
                )

        for platform in TUI_PLATFORMS:
            n = gen.TUI_ARTIFACT_NAMES[TUI_NPM_KEY[platform]]
            dests = [_gh(f)]
            if p == "final":
                # The flagship lock pins these on the hub (#5509 keeps them there).
                dests.append(
                    {"channel": "hub", "path": f"agents/terminal-hub/{f.npm}/{n}"}
                )
            items.append(
                Artifact(
                    p, n, "tui-binary", _find(art, f"{p}-tui-binaries", n), f.npm, dests
                )
            )

        setup = tui_table.artifacts_for("win-x64", f.npm)[0][0]
        items.append(
            Artifact(
                p,
                setup,
                "windows-setup",
                _find(art, f"{p}-windows-setup", setup),
                f.npm,
                [_gh(f)],
            )
        )

    # rpm cannot carry -rc.N, so only the twin has the native packages today.
    for platform, label in (
        ("darwin-arm64", "macos"),
        ("darwin-x64", "macos"),
        ("linux-x64", "linux"),
    ):
        for n, _key in tui_table.artifacts_for(platform, final.npm):
            items.append(
                Artifact(
                    "final",
                    n,
                    "tui-installer",
                    _find(art, f"final-tui-installers-{label}", n),
                    final.npm,
                    [_gh(final)],
                    required=platform in rc_assets.REQUIRED_PLATFORMS,
                )
            )

    # One sidecar build serves both: it embeds the agent's own version, not core's.
    for platform in rc_assets.REQUIRED_PLATFORMS + rc_assets.OPTIONAL_PLATFORMS:
        n = f"gaia-agent-{platform}" + (".exe" if platform.startswith("win32") else "")
        dests = [
            _gh(rc),
            _gh(final),
            {"channel": "hub", "path": f"agents/gaia/{agent_ver}/{n}"},
        ]
        items.append(
            Artifact(
                "both",
                n,
                "flagship-sidecar",
                _find(art, f"gaia-agent-{platform}", n),
                agent_ver,
                dests,
                required=platform in rc_assets.REQUIRED_PLATFORMS,
            )
        )
    for n in ("gaia-agent-release.json", "gaia-agent.yaml"):
        items.append(
            Artifact(
                "rc",
                n,
                "flagship-rc-manifest",
                flagship_dir / "rc-assets" / n,
                agent_ver,
                [_gh(rc)],
            )
        )
    npm_dest = _npm(FLAGSHIP_NPM, final, agent_ver)
    npm_dest["train_package"] = FLAGSHIP_NPM_TRAIN
    items.append(
        Artifact(
            "final",
            f"amd-gaia-gaia-{agent_ver}.tgz",
            "npm",
            flagship_dir / f"amd-gaia-gaia-{agent_ver}.tgz",
            agent_ver,
            [npm_dest],
        )
    )
    return items


def _expect(report: Report, ok: bool, what: str, why: str) -> None:
    if ok:
        report.checks.append(what)
    else:
        report.errors.append(f"{what}: {why}")


def check_versions(
    report: Report,
    by_name: dict[tuple[str, str], Artifact],
    rc: Flavor,
    final: Flavor,
    agent_ver: str,
) -> None:
    """Every artifact embeds the version its flavor promises, and only that one."""
    for f in (rc, final):
        whl = by_name.get((f.name, f"amd_gaia-{f.pep440}-py3-none-any.whl"))
        if whl:
            with zipfile.ZipFile(whl.path) as zf:
                meta = next(
                    n for n in zf.namelist() if n.endswith(".dist-info/METADATA")
                )
                text = zf.read(meta).decode()
            _expect(
                report,
                f"\nVersion: {f.pep440}\n" in text,
                f"{whl.name} METADATA says {f.pep440}",
                "it does not",
            )
        sdist = by_name.get((f.name, f"amd_gaia-{f.pep440}.tar.gz"))
        if sdist:
            files = archive_files(sdist.path)
            pkg_info = files.get(f"amd_gaia-{f.pep440}/PKG-INFO", b"").decode()
            _expect(
                report,
                f"\nVersion: {f.pep440}\n" in pkg_info,
                f"{sdist.name} PKG-INFO says {f.pep440}",
                "it does not",
            )
        tgz = by_name.get((f.name, f"amd-gaia-agent-ui-{f.npm}.tgz"))
        if tgz:
            pkg = json.loads(archive_files(tgz.path)["package/package.json"])
            _expect(
                report,
                pkg.get("version") == f.npm,
                f"{tgz.name} package.json says {f.npm}",
                f"it says {pkg.get('version')}",
            )
        for feed in ("latest.yml", "latest-mac.yml", "latest-linux.yml"):
            a = by_name.get((f.name, feed))
            if a:
                text = a.path.read_text(encoding="utf-8")
                _expect(
                    report,
                    re.search(rf"(?m)^version: {re.escape(f.npm)}$", text) is not None,
                    f"{f.name} {feed} offers {f.npm}",
                    "it offers another version",
                )
        for (flavor, name), a in by_name.items():
            if flavor != f.name or a.kind != "tui-binary":
                continue
            data = a.path.read_bytes()
            _expect(
                report,
                f.npm.encode() in data,
                f"{f.name} {name} reports {f.npm}",
                "the version string is not in the binary",
            )
            if f.name == "final":
                _expect(
                    report,
                    rc.npm.encode() not in data,
                    f"final {name} carries no RC version",
                    f"it embeds {rc.npm}",
                )
    npm = by_name.get(("final", f"amd-gaia-gaia-{agent_ver}.tgz"))
    if npm:
        pkg = json.loads(archive_files(npm.path)["package/package.json"])
        _expect(
            report,
            pkg.get("version") == agent_ver,
            f"{npm.name} package.json says {agent_ver}",
            f"it says {pkg.get('version')}",
        )


def check_lock(
    report: Report,
    lock: dict,
    by_name: dict[tuple[str, str], Artifact],
    final: Flavor,
    agent_ver: str,
    flagship_tgz: Path | None,
) -> None:
    """The generated lock pins exactly the bytes this run built."""
    comps = lock.get("components", {})
    _expect(
        report,
        lock.get("agentVersion") == agent_ver,
        f"lock agentVersion is {agent_ver}",
        f"it is {lock.get('agentVersion')}",
    )
    _expect(
        report,
        comps.get("tui", {}).get("componentVersion") == final.npm,
        f"lock pins terminal {final.npm}",
        f"it pins {comps.get('tui', {}).get('componentVersion')}",
    )
    for lane, flavor in (("sidecar", "both"), ("tui", "final")):
        for platform, rec in comps.get(lane, {}).get("platforms", {}).items():
            a = by_name.get((flavor, rec["filename"]))
            ok = a is not None and a.sha256 == rec["sha256"] and a.size == rec["size"]
            _expect(
                report,
                ok,
                f"lock {lane}/{platform} matches {rec['filename']}",
                (
                    "no such artifact was built"
                    if a is None
                    else f"lock {rec['sha256']} / {rec['size']} vs built {a.sha256} / {a.size}"
                ),
            )
    if flagship_tgz and flagship_tgz.is_file():
        shipped = json.loads(archive_files(flagship_tgz)["package/binaries.lock.json"])
        _expect(
            report,
            shipped == lock,
            f"{flagship_tgz.name} ships the generated lock",
            "its binaries.lock.json differs",
        )


def verify(
    art: Path, target: Path, rc_tag: str, sha: str, out: Path, flagship_dir: Path
) -> Report:
    rc, final = flavors(rc_tag, target)
    agent_ver = agent_version(target)
    report = Report()
    for a in catalog(art, target, rc, final, flagship_dir):
        if not a.path.is_file():
            if a.required:
                report.errors.append(
                    f"{a.flavor} {a.name} was not built (expected {a.path})"
                )
            else:
                report.warnings.append(
                    f"{a.flavor} {a.name} was not built (best-effort)"
                )
            continue
        a.sha256, a.size = sha256(a.path), a.path.stat().st_size
        report.artifacts.append(a)
    by_name = {(a.flavor, a.name): a for a in report.artifacts}

    check_versions(report, by_name, rc, final, agent_ver)

    lock_path = flagship_dir / "binaries.lock.json"
    if lock_path.is_file():
        check_lock(
            report,
            json.loads(lock_path.read_text(encoding="utf-8")),
            by_name,
            final,
            agent_ver,
            flagship_dir / f"amd-gaia-gaia-{agent_ver}.tgz",
        )
    else:
        report.errors.append(f"no generated lock at {lock_path}")

    for kind_name in (
        lambda f: f"amd_gaia-{f.pep440}-py3-none-any.whl",
        lambda f: f"amd_gaia-{f.pep440}.tar.gz",
        lambda f: f"amd-gaia-agent-ui-{f.npm}.tgz",
        lambda f: f"gaia-agent-ui-{f.npm}-amd64.deb",
    ):
        a, b = by_name.get(("rc", kind_name(rc))), by_name.get(
            ("final", kind_name(final))
        )
        if a is None or b is None:
            continue
        problems = twin_diff(a.path, b.path, rc, final)
        _expect(
            report,
            not problems,
            f"twin-diff {a.name} == {b.name}",
            "; ".join(problems[:10])
            + (f" (+{len(problems) - 10} more)" if len(problems) > 10 else ""),
        )

    write_outputs(report, out, rc, final, sha, agent_ver)
    return report


def write_outputs(
    report: Report, out: Path, rc: Flavor, final: Flavor, sha: str, agent_ver: str
) -> None:
    out.mkdir(parents=True, exist_ok=True)
    manifest = {
        "schema": MANIFEST_SCHEMA,
        "dry_run": True,
        "commit": sha,
        "rc_tag": rc.tag,
        "final_tag": final.tag,
        "agent_version": agent_ver,
        "artifacts": [a.record() for a in report.artifacts],
        "not_built_here": [
            "Sigstore bundles for the wheel and sdist (signed at publish, needs id-token)",
            "SHA256SUMS and this manifest are themselves release assets",
        ],
        "errors": report.errors,
        "warnings": report.warnings,
    }
    (out / "release-manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    for f in (rc, final):
        lines = sorted(
            f"{a.sha256}  {a.name}"
            for a in report.artifacts
            if a.flavor in (f.name, "both")
        )
        (out / f"SHA256SUMS-{f.tag}").write_text(
            "\n".join(lines) + "\n", encoding="utf-8"
        )
    (out / "summary.md").write_text(
        render_summary(report, rc, final, sha), encoding="utf-8"
    )


def _dest(d: dict) -> str:
    ch = d["channel"]
    if ch == "github-release":
        return f"GitHub {d['tag']}" + (" (pre-release)" if d["prerelease"] else "")
    if ch == "pypi":
        return f"PyPI {d['project']}=={d['version']}"
    if ch == "npm":
        s = f"npm {d['package']}@{d['version']} ({d['dist_tag']})"
        return s + (
            f", train name {d['train_package']}" if d.get("train_package") else ""
        )
    return f"hub {d['path']}"


def render_summary(report: Report, rc: Flavor, final: Flavor, sha: str) -> str:
    status = "FAILED" if report.errors else "passed"
    lines = [
        f"## Release-train dry run {status}: {rc.tag} + {final.tag} twin",
        "",
        f"Commit `{sha}`. Nothing was published. "
        f"{len(report.artifacts)} artifacts, {len(report.checks)} checks passed, "
        f"{len(report.errors)} failed, {len(report.warnings)} warnings.",
        "",
    ]
    if report.errors:
        lines += ["### Failed", ""] + [f"- {e}" for e in report.errors] + [""]
    if report.warnings:
        lines += ["### Warnings", ""] + [f"- {w}" for w in report.warnings] + [""]
    lines += [
        "### What a train would publish",
        "",
        "| Build | Artifact | Size | SHA-256 | Destinations |",
        "|---|---|---:|---|---|",
    ]
    for a in sorted(report.artifacts, key=lambda a: (a.flavor, a.kind, a.name)):
        lines.append(
            f"| {a.flavor} | `{a.name}` | {a.size / 1048576:.1f} MiB | `{a.sha256[:12]}` | "
            + "; ".join(_dest(d) for d in a.destinations)
            + " |"
        )
    lines += ["", "<details>", "<summary>Checks that passed</summary>", ""]
    lines += [f"- {c}" for c in report.checks]
    lines += ["</details>", ""]
    return "\n".join(lines)


def cmd_verify(args: argparse.Namespace) -> int:
    report = verify(args.art, args.target, args.tag, args.sha, args.out, args.flagship)
    print((args.out / "summary.md").read_text(encoding="utf-8"))
    for e in report.errors:
        print(f"::error::{e}", flush=True)
    for w in report.warnings:
        print(f"::warning::{w}", flush=True)
    return 1 if report.errors else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    plan = sub.add_parser("plan", help="resolve an RC tag against the tree")
    plan.add_argument("--tag", required=True)
    plan.add_argument("--target", type=Path, required=True)
    plan.set_defaults(func=cmd_plan)

    lock = sub.add_parser("lock", help="generate binaries.lock.json from this run")
    lock.add_argument("--target", type=Path, required=True)
    lock.add_argument("--sidecars", type=Path, required=True)
    lock.add_argument("--tui", type=Path, required=True)
    lock.add_argument("--tui-version", required=True)
    lock.add_argument("--out", type=Path, required=True)
    lock.set_defaults(func=cmd_lock)

    ver = sub.add_parser("verify", help="check every artifact and write the manifest")
    ver.add_argument("--art", type=Path, required=True)
    ver.add_argument("--target", type=Path, required=True)
    ver.add_argument("--tag", required=True)
    ver.add_argument("--sha", required=True)
    ver.add_argument(
        "--flagship",
        type=Path,
        required=True,
        help="directory with the generated lock, the flagship npm tarball "
        "and the RC flagship manifest (rc-assets/)",
    )
    ver.add_argument("--out", type=Path, required=True)
    ver.set_defaults(func=cmd_verify)

    args = ap.parse_args(argv)
    try:
        return args.func(args)
    except VerifyError as e:
        _fail(str(e))
        return 1


if __name__ == "__main__":
    sys.exit(main())
