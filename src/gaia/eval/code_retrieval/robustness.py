# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Damage the index and the tree it reads; the index must fail loudly or recover.

**Cache damage.** A small real repository is indexed once; each scenario
copies that cache, damages one file, then (1) searches and (2) re-indexes and
searches. Each phase is classified:

- ``correct``: the results equal the undamaged index's;
- ``raised``: an exception reached the caller (loud);
- ``silent-empty`` / ``silent-wrong``: no error, and no or different results.

A scenario passes when no phase is silent: a user told "no matches" for a
corrupt index, or handed another file's code, has no reason to distrust it.
``recovers`` says whether re-indexing alone restored correct results.

**Hostile trees.** Binary files with code extensions, a NUL past the binary
sniff window, an oversized file, Latin-1 text, links out of the repository, a
directory loop and a dangling link are added to a copy of the tree, which is
then discovered and indexed. A hazard that cannot be built on this machine
(file symlinks need a privilege on Windows) is reported ``not-run``, never
passed.
"""

from __future__ import annotations

import json
import os
import random
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from gaia.eval.code_retrieval.common import (
    cache_path,
    make_sdk,
    posix,
    read_metadata,
    top_keys,
)
from gaia.eval.code_retrieval.repos import RepoPin, checkout_at

Progress = Callable[[str], None]

#: Questions about the robustness repository (requests), compared across damage.
PROBES = (
    "merge session cookies into the request",
    "where is the request timeout enforced",
    "read proxy settings from environment variables",
    "retry a connection when the adapter fails",
    "encode multipart form data for file uploads",
)


@dataclass(frozen=True)
class Damage:
    name: str
    what: str
    apply: Callable[[Path], None]


def _truncate(name: str) -> Callable[[Path], None]:
    def apply(cache: Path) -> None:
        data = (cache / name).read_bytes()
        (cache / name).write_bytes(data[: len(data) // 2])

    return apply


def _empty(name: str) -> Callable[[Path], None]:
    return lambda cache: (cache / name).write_bytes(b"")


def _missing(name: str) -> Callable[[Path], None]:
    return lambda cache: (cache / name).unlink()


def _garbage(name: str) -> Callable[[Path], None]:
    def apply(cache: Path) -> None:
        size = (cache / name).stat().st_size
        (cache / name).write_bytes(random.Random(0).randbytes(size))

    return apply


def _edit_meta(edit: Callable[[Dict[str, Any]], None]) -> Callable[[Path], None]:
    def apply(cache: Path) -> None:
        meta = json.loads((cache / "metadata.json").read_text(encoding="utf-8"))
        edit(meta)
        (cache / "metadata.json").write_text(json.dumps(meta), encoding="utf-8")

    return apply


def _leftover_temps(cache: Path) -> None:
    (cache / "index.tmp.faiss").write_bytes(b"\x00" * 64)
    (cache / "metadata.tmp.json").write_text('{"version": ', encoding="utf-8")


DAMAGE: Tuple[Damage, ...] = (
    Damage(
        "faiss-truncated", "index.faiss cut to half its size", _truncate("index.faiss")
    ),
    Damage("faiss-empty", "index.faiss is zero bytes", _empty("index.faiss")),
    Damage(
        "faiss-garbage",
        "index.faiss overwritten with random bytes",
        _garbage("index.faiss"),
    ),
    Damage(
        "faiss-missing", "index.faiss deleted, metadata kept", _missing("index.faiss")
    ),
    Damage(
        "metadata-truncated",
        "metadata.json cut to half (invalid JSON)",
        _truncate("metadata.json"),
    ),
    Damage("metadata-empty", "metadata.json is zero bytes", _empty("metadata.json")),
    Damage(
        "metadata-missing",
        "metadata.json deleted, index kept",
        _missing("metadata.json"),
    ),
    Damage(
        "metadata-chunk-dropped",
        "one chunk entry removed (vector count no longer matches)",
        _edit_meta(lambda m: m["chunks"].pop()),
    ),
    Damage(
        "metadata-old-version",
        "cache written by an older schema version",
        _edit_meta(lambda m: m.update(version=m["version"] - 1)),
    ),
    Damage(
        "metadata-other-model",
        "index built with a different embedding model",
        _edit_meta(lambda m: m.update(embedding_model="user.some-other-embedder")),
    ),
    Damage(
        "leftover-temp-files",
        "a crashed save left its temp files behind",
        _leftover_temps,
    ),
)


def _attempt(run: Callable[[], List[List[List[Any]]]], baseline) -> Dict[str, Any]:
    try:
        got = run()
    except Exception as exc:  # classified, not handled: any escape is "loud"
        return {"outcome": "raised", "error": f"{type(exc).__name__}: {str(exc)[:300]}"}
    if got == baseline:
        return {"outcome": "correct"}
    if all(not r for r in got):
        return {"outcome": "silent-empty"}
    overlap = sum(
        len({tuple(x) for x in a} & {tuple(x) for x in b}) / max(len(b), 1)
        for a, b in zip(got, baseline)
    ) / len(baseline)
    return {"outcome": "silent-wrong", "overlap@10": round(overlap, 3)}


def _verdict(phases: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "passed": all(not p["outcome"].startswith("silent") for p in phases.values()),
        "recovers": phases["reindex"]["outcome"] == "correct",
    }


def _search_all(repo: Path, root: Path) -> List[List[List[Any]]]:
    sdk = make_sdk(repo, root)
    return [top_keys(sdk.search(q, top_k=10)) for q in PROBES]


def _reindex_and_search(repo: Path, root: Path) -> List[List[List[Any]]]:
    make_sdk(repo, root).index_repository()
    return _search_all(repo, root)


def _interrupted_save(repo: Path, base: Path) -> Dict[str, Any]:
    """A crash between ``_save_atomic``'s two renames: new vectors, old metadata.

    A copy of the tree is indexed, one function is edited (the chunk count
    stays the same), and the tree is re-indexed; then the metadata.json from
    before the edit is put back next to the new index.faiss.
    """
    tree, root = base / "interrupted-tree", base / "interrupted-save"
    shutil.copytree(repo, tree)
    make_sdk(tree, root).index_repository()
    cache = cache_path(make_sdk(tree, root))
    old_meta = (cache / "metadata.json").read_bytes()
    # A one-line top-level signature, so the inserted comment keeps it parseable.
    spot = next(
        (
            (p, i)
            for p in sorted(tree.rglob("*.py"))
            for i, ln in enumerate(p.read_text(encoding="utf-8").splitlines())
            if ln.startswith("def ") and ln.rstrip().endswith(":")
        ),
        None,
    )
    if spot is None:
        raise RuntimeError(f"{repo} has no one-line top-level def to edit")
    target, at = spot
    lines = target.read_text(encoding="utf-8").splitlines(keepends=True)
    lines.insert(at + 1, "    # edited by the robustness check\n")
    target.write_text("".join(lines), encoding="utf-8")
    make_sdk(tree, root).index_repository()
    baseline = _search_all(tree, root)
    (cache / "metadata.json").write_bytes(old_meta)
    phases = {
        "search": _attempt(lambda: _search_all(tree, root), baseline),
        "reindex": _attempt(lambda: _reindex_and_search(tree, root), baseline),
    }
    return {
        "name": "interrupted-save",
        "damage": "index.faiss from a newer save next to the older metadata.json",
        **phases,
        **_verdict(phases),
    }


# ---------------------------------------------------------------------------
# Hostile trees
# ---------------------------------------------------------------------------


def _link(link: Path, target: Path, is_dir: bool) -> Optional[str]:
    """Create *link* -> *target*; None, or why this machine cannot.

    Without the symlink privilege, Windows still allows directory junctions,
    which ``os.walk`` treats differently from symlinks, so they are tested too.
    """
    try:
        os.symlink(target, link, target_is_directory=is_dir)
        return None
    except OSError as exc:
        if sys.platform != "win32" or getattr(exc, "winerror", None) != 1314:
            raise
    if not is_dir:
        return "file symlinks need Developer Mode or admin rights on Windows"
    import _winapi  # pylint: disable=import-outside-toplevel,import-error

    try:
        _winapi.CreateJunction(str(target), str(link))
    except OSError as exc:
        return f"could not create a directory junction here: {exc}"
    return None


@dataclass(frozen=True)
class FileHazard:
    name: str
    what: str
    filename: str
    data: bytes
    #: Whether a sane index holds the file.
    expect_indexed: bool


FILE_HAZARDS: Tuple[FileHazard, ...] = (
    FileHazard(
        "binary-with-code-extension",
        "a .py file holding NUL bytes",
        "hazard_binary.py",
        b"def f():\n    pass\n" + b"\x00" * 64,
        False,
    ),
    FileHazard(
        "nul-after-sniff-window",
        "a .py file whose first NUL is past the 8 KB binary sniff",
        "hazard_late_nul.py",
        b"# padding\n" * 1000 + b"\x00\n",
        False,
    ),
    FileHazard(
        "oversized-file",
        "a 1.2 MB .py file (limit 1 MB)",
        "hazard_huge.py",
        b"x = 1\n" * 200_000,
        False,
    ),
    FileHazard(
        "latin-1-file",
        "a .py file that is not valid UTF-8",
        "hazard_latin1.py",
        b"# caf\xe9\ndef hazard_cafe():\n    return 1\n",
        True,
    ),
)


@dataclass(frozen=True)
class LinkHazard:
    name: str
    what: str
    link: str
    #: ``"outside"`` (a file or directory outside the tree), ``"root"`` (a
    #: loop back to the tree), or ``"missing"`` (dangling).
    target: str
    is_dir: bool


LINK_HAZARDS: Tuple[LinkHazard, ...] = (
    LinkHazard(
        "file-link-out-of-repo",
        "a .py link to a file outside the repository",
        "hazard_outside.py",
        "outside",
        False,
    ),
    LinkHazard(
        "dir-link-out-of-repo",
        "a directory link to a directory outside the repository",
        "hazard_outside_dir",
        "outside",
        True,
    ),
    LinkHazard(
        "directory-loop",
        "a directory link back to the repository root",
        "hazard_loop",
        "root",
        True,
    ),
    LinkHazard(
        "dangling-link",
        "a directory link to a path that does not exist",
        "hazard_dangling",
        "missing",
        True,
    ),
)


def _file_hazards(repo: Path, base: Path) -> List[Dict[str, Any]]:
    """One copy of the tree with every file hazard added, indexed for real."""
    tree = base / "file-hazards"
    shutil.copytree(repo, tree)
    for h in FILE_HAZARDS:
        (tree / h.filename).write_bytes(h.data)
    sdk = make_sdk(tree, base / "file-hazards-cache")
    try:
        sdk.index_repository()
        # Searchable means it has chunks: a file is hashed even when its text
        # is rejected, so file_hashes alone would call a skipped file indexed.
        indexed = {posix(c["file_path"]) for c in read_metadata(sdk)["chunks"]}
        error = None
    except Exception as exc:  # recorded per hazard below, never passed
        indexed, error = set(), f"{type(exc).__name__}: {str(exc)[:300]}"
    out = []
    for h in FILE_HAZARDS:
        held = h.filename in indexed
        ok = error is None and held == h.expect_indexed
        detail = error or ("indexed" if held else "not indexed")
        out.append({"name": h.name, "hazard": h.what, "ok": ok, "detail": detail})
    return out


def _link_hazard(h: LinkHazard, repo: Path, base: Path) -> Dict[str, Any]:
    """A one-file tree plus *h*'s link: what discovery walks, and what it would read.

    Judged without embedding: a path the walk returns and the index's reader
    accepts is a path the index would hold.
    """
    tree, outside = base / f"link-{h.name}", base / f"link-{h.name}-outside"
    tree.mkdir(parents=True)
    outside.mkdir()
    (outside / "secret.py").write_text("def hazard_secret():\n    return 1\n", "utf-8")
    sample = next(iter(sorted(repo.glob("*.py"))))
    shutil.copy2(sample, tree / sample.name)
    target = {
        "outside": outside if h.is_dir else outside / "secret.py",
        "root": tree,
        "missing": tree / "does-not-exist",
    }[h.target]
    reason = _link(tree / h.link, target, h.is_dir)
    if reason is not None:
        return {"name": h.name, "hazard": h.what, "ok": None, "detail": reason}
    sdk = make_sdk(tree, base / f"link-{h.name}-cache")
    # pylint: disable=protected-access
    try:
        files, _ = sdk._discover_files()
    except RuntimeError as exc:
        return {
            "name": h.name,
            "hazard": h.what,
            "ok": False,
            "detail": f"indexing aborts: {str(exc)[:200]}",
        }
    through = [f for f in files if posix(os.path.relpath(f, tree)).startswith(h.link)]
    readable = [f for f in through if sdk._read_file_safe(f) is not None]
    ok = not readable and (h.target != "root" or not through)
    detail = (
        f"would index {len(readable)} file(s) through the link"
        if readable
        else (
            f"walked {len(through)} path(s) through the link; the path check "
            "rejected them"
            if through
            else "not followed"
        )
    )
    return {"name": h.name, "hazard": h.what, "ok": ok, "detail": detail}


def _hostile_tree(repo: Path, base: Path, progress: Progress) -> List[Dict[str, Any]]:
    rows = _file_hazards(repo, base) + [
        _link_hazard(h, repo, base) for h in LINK_HAZARDS
    ]
    out = []
    for r in rows:
        status = "not-run" if r["ok"] is None else ("pass" if r["ok"] else "fail")
        out.append(
            {
                "name": r["name"],
                "hazard": r["hazard"],
                "status": status,
                "detail": r["detail"],
            }
        )
        progress(f"  tree {r['name']}: {status} ({r['detail']})")
    return out


def run(
    pin: RepoPin, subdir: str, work_root: Path, index_root: Path, progress: Progress
) -> Dict[str, Any]:
    wd = work_root / "retrieval" / "checkouts" / f"{pin.name}-robustness"
    checkout_at(pin, wd, work_root)
    repo = wd / subdir
    base = index_root / "robustness"
    if base.exists():
        shutil.rmtree(base)
    pristine = base / "pristine"
    progress(f"[robustness] indexing {pin.name}/{subdir} at {pin.commit[:10]}")
    make_sdk(repo, pristine).index_repository()
    baseline = _search_all(repo, pristine)
    scenarios = []
    for d in DAMAGE:
        root = base / d.name
        shutil.copytree(pristine, root)
        d.apply(cache_path(make_sdk(repo, root)))
        phases = {
            "search": _attempt(lambda r=root: _search_all(repo, r), baseline),
            "reindex": _attempt(lambda r=root: _reindex_and_search(repo, r), baseline),
        }
        scenarios.append(
            {"name": d.name, "damage": d.what, **phases, **_verdict(phases)}
        )
        progress(
            f"  {d.name}: search {phases['search']['outcome']}, "
            f"reindex {phases['reindex']['outcome']}"
        )
    scenarios.append(_interrupted_save(repo, base))
    progress(
        f"  interrupted-save: search {scenarios[-1]['search']['outcome']}, "
        f"reindex {scenarios[-1]['reindex']['outcome']}"
    )
    tree = _hostile_tree(repo, base, progress)
    return {
        "repo": pin.name,
        "commit": pin.commit,
        "subdir": subdir,
        "probes": list(PROBES),
        "scenarios": scenarios,
        "tree": tree,
        "passed": sum(s["passed"] for s in scenarios)
        + sum(t["status"] == "pass" for t in tree),
        "failed": sum(not s["passed"] for s in scenarios)
        + sum(t["status"] == "fail" for t in tree),
        "not_run": sum(t["status"] == "not-run" for t in tree),
    }
