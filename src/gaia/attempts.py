# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""``gaia -p --attempts N``: independent attempts at one task, then one that combines them.

Each attempt runs as its own ``gaia -p`` in a private workspace, so no attempt
sees another's changes and the real folder is untouched until the final turn.
In a git repository the workspace is a clone that borrows the repository's
objects and copies its branches, tags and remotes, holding the folder's current
files, uncommitted work included and ignored files left out; its refs, stash
and config are its own. Any other folder is copied. What an
attempt changed, and its answer, become a draft; the final turn works in the
real folder with the drafts to hand, keeps what it can confirm, and checks
where they disagree. Two independent looks catch more than one.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from gaia.logger import get_logger

log = get_logger(__name__)

MAX_ATTEMPTS = 4
#: A folder outside git is copied once per attempt; past this it is refused.
MAX_COPY_FILES = 50_000
MAX_COPY_BYTES = 2 * 1024**3
#: Rebuilt by tools, never a deliverable; left out of plain-folder copies.
_SKIP_DIRS = frozenset(
    {
        ".git",
        "node_modules",
        ".venv",
        "venv",
        "__pycache__",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".tox",
        "dist",
        "build",
        "target",
    }
)
#: Files larger than this are compared by size and time, and never diffed.
_HASH_LIMIT = 8 * 1024 * 1024
#: A draft's CHANGES.patch is cut here; the changed files are in files/.
_PATCH_LIMIT = 2 * 1024 * 1024

ATTEMPT_INSTRUCTIONS = """

## Working as one of several independent attempts

This folder is a private copy made for you; a final pass will compare your
result with other independent attempts and deliver the answer. Do the task
fully here, but take no action outside this folder: do not post, push, send,
or change files elsewhere."""

MERGE_INSTRUCTIONS = """

## Earlier independent attempts

Independent attempts at this same task are in `{drafts}`. Each folder holds
that attempt's final answer (ANSWER.md), a CHANGES.patch of its edits, and a
copy of every file it created or changed (files/). Paths are relative to the
top of this project. This folder itself is unchanged. Treat them as
colleagues' drafts, not as answers: they may be wrong or incomplete, and each
may catch what another missed. Do the task yourself here, using them to look
further: keep every point you can confirm in the code, check any point they
disagree on against the code yourself, drop what the code does not support,
and add what they all missed. Then deliver the single best result exactly as
the task asks.

A merge is not a union. The result must meet every length and format rule
the task and the project's instructions set, so it is usually no longer than
the tighter draft: keep a point only if a reader acting on it would miss it,
and cut what the reader can already get from the diff or the code."""


class AttemptsError(RuntimeError):
    """``--attempts`` cannot run here; the message says what to do instead."""


#: Set by git for hooks it runs; inherited, they would point every command
#: here, and every attempt's, at the caller's real repository.
_GIT_LOCAL_ENV = (
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_COMMON_DIR",
    "GIT_CONFIG",
    "GIT_CONFIG_PARAMETERS",
    "GIT_CONFIG_COUNT",
    "GIT_DIR",
    "GIT_GRAFT_FILE",
    "GIT_IMPLICIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_NAMESPACE",
    "GIT_NO_REPLACE_OBJECTS",
    "GIT_OBJECT_DIRECTORY",
    "GIT_PREFIX",
    "GIT_REPLACE_REF_BASE",
    "GIT_SHALLOW_FILE",
    "GIT_WORK_TREE",
)


def clean_env(**extra: str) -> Dict[str, str]:
    """This process's environment without git's per-repository variables."""
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in _GIT_LOCAL_ENV
        and not k.startswith("GIT_CONFIG_KEY_")
        and not k.startswith("GIT_CONFIG_VALUE_")
    }
    env.update(extra)
    return env


def _git(*args: str, cwd: Path, env: Optional[Dict[str, str]] = None) -> str:
    done = subprocess.run(
        # Long paths: workspaces sit deeper than the real folder.
        ["git", "-c", "core.longpaths=true", *args],
        cwd=cwd,
        env=env or clean_env(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if done.returncode != 0:
        raise AttemptsError(
            f"git {' '.join(args[:3])} failed in {cwd}: "
            f"{(done.stderr or done.stdout).strip()}"
        )
    return done.stdout.strip()


def _git_top(root: Path) -> Optional[Path]:
    """The top of the git repository *root* is in, if it has a commit to start from."""
    if shutil.which("git") is None:
        return None
    try:
        top = Path(_git("rev-parse", "--show-toplevel", cwd=root)).resolve()
        _git("rev-parse", "--verify", "--quiet", "HEAD", cwd=root)
    except AttemptsError:
        return None  # not a repository, or no commit yet: the folder is copied
    return top


def _index_path(top: Path) -> Path:
    return (top / _git("rev-parse", "--git-path", "index", cwd=top)).resolve()


def _tree_of(top: Path) -> str:
    """The tree of *top*'s working files as git sees them, ignored files left out.

    Built in a throwaway index seeded from the repository's own (for its stat
    cache), so the real index, HEAD and branches are never touched.
    """
    fd, index = tempfile.mkstemp(prefix="gaia-index-")
    os.close(fd)
    try:
        real_index = _index_path(top)
        if real_index.is_file():
            shutil.copyfile(real_index, index)
        else:
            os.unlink(index)
        env = clean_env(GIT_INDEX_FILE=index)
        _git("add", "--all", "--", ".", cwd=top, env=env)
        return _git("write-tree", cwd=top, env=env)
    finally:
        if os.path.exists(index):
            os.unlink(index)


def _check_untracked_size(top: Path) -> None:
    """Refuse when untracked, unignored files are too big to snapshot per attempt."""
    listing = _git("ls-files", "--others", "--exclude-standard", "-z", cwd=top)
    total = 0
    for rel in filter(None, listing.split("\0")):
        try:
            total += (top / rel).stat().st_size
        except OSError:
            continue  # vanished since the listing
    if total > MAX_COPY_BYTES:
        raise AttemptsError(
            f"--attempts would snapshot {total / 1024**3:.1f} GB of untracked files "
            f"in {top} for every attempt; the limit is {MAX_COPY_BYTES // 1024**3} GB. "
            "Add them to .gitignore, or run without --attempts."
        )


def _clone(top: Path, dest: Path, tree: str) -> None:
    """A clone at *dest* with *top*'s refs and remotes and *tree* as its files.

    The clone borrows *top*'s objects but has its own refs, stash and config,
    so nothing an attempt does to them reaches the real repository. Files are
    written with read-tree, which runs no hooks.
    """
    _git("clone", "--quiet", "--shared", "--no-checkout", str(top), str(dest), cwd=top)
    _git("remote", "remove", "origin", cwd=dest)
    _git(
        "fetch",
        "--quiet",
        "--no-tags",
        "--update-head-ok",
        str(top),
        "+refs/heads/*:refs/heads/*",
        "+refs/tags/*:refs/tags/*",
        "+refs/remotes/*:refs/remotes/*",
        cwd=dest,
    )
    for line in _local_config(top, r"^(remote|branch)\.").splitlines():
        key, _, value = line.partition(" ")
        _git("config", "--add", key, value, cwd=dest)
    branch = _git("rev-parse", "--abbrev-ref", "HEAD", cwd=top)
    if branch != "HEAD":
        _git("symbolic-ref", "HEAD", f"refs/heads/{branch}", cwd=dest)
    else:
        _git(
            "update-ref",
            "--no-deref",
            "HEAD",
            _git("rev-parse", "HEAD", cwd=top),
            cwd=dest,
        )
    # LFS files stay as pointers: fetching them again per attempt costs a download.
    _git(
        "read-tree",
        "--reset",
        "-u",
        tree,
        cwd=dest,
        env=clean_env(GIT_LFS_SKIP_SMUDGE="1"),
    )
    _git("reset", "--quiet", cwd=dest)  # index = HEAD: uncommitted work shows as such


def _local_config(top: Path, pattern: str) -> str:
    """``key value`` lines of *top*'s own config matching *pattern*; "" if none."""
    done = subprocess.run(
        ["git", "config", "--local", "--get-regexp", pattern],
        cwd=top,
        env=clean_env(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if done.returncode == 1:
        return ""  # no such keys
    if done.returncode != 0:
        raise AttemptsError(f"git config failed in {top}: {done.stderr.strip()}")
    return done.stdout


@dataclass
class Workspace:
    """Where one attempt runs, and how to tell what it changed."""

    top: Path  # the workspace's top folder
    cwd: Path  # where the attempt runs: the same place in it as the real folder
    git_top: Optional[Path] = None  # git mode: the real repository's top
    baseline: Any = None  # git mode: a tree id; copy mode: {path: fingerprint}
    real: Optional[Path] = None  # copy mode: the real folder, for diffs
    skipped: List[str] = field(default_factory=list)


def _fingerprint(path: Path) -> str:
    st = path.stat()
    if st.st_size > _HASH_LIMIT:
        return f"{st.st_size}:{st.st_mtime_ns}"
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _is_link(path: Path) -> bool:
    isjunction = getattr(os.path, "isjunction", None)  # Python 3.12+
    return path.is_symlink() or bool(isjunction and isjunction(path))


def _walk(root: Path, skipped: Optional[List[str]] = None) -> Dict[str, Path]:
    """Every file under *root* outside rebuilt folders; never enters a linked folder."""
    found: Dict[str, Path] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        keep = []
        for d in dirnames:
            full = Path(dirpath) / d
            if d in _SKIP_DIRS:
                continue
            if _is_link(full):
                if skipped is not None:
                    skipped.append(full.relative_to(root).as_posix())
                continue
            keep.append(d)
        dirnames[:] = keep
        for name in filenames:
            full = Path(dirpath) / name
            found[full.relative_to(root).as_posix()] = full
    return found


def _check_copyable(root: Path, files: Dict[str, Path]) -> None:
    """Refuse folders too big, or too broad, to copy once per attempt."""
    temp = Path(tempfile.gettempdir()).resolve()
    if (
        root == Path.home().resolve()
        or root.parent == root
        or temp.is_relative_to(root)
    ):
        raise AttemptsError(
            f"--attempts copies the current folder for every attempt, and {root} "
            "is or holds your home or temp folder. Run it from the project's own folder."
        )
    total = 0
    for path in files.values():
        try:
            total += path.stat().st_size
        except OSError:
            continue  # a dangling link: copying it fails below, loudly
    if len(files) > MAX_COPY_FILES or total > MAX_COPY_BYTES:
        raise AttemptsError(
            f"--attempts would copy {len(files):,} files ({total / 1024**3:.1f} GB) "
            f"from {root} for every attempt; the limit is {MAX_COPY_FILES:,} files "
            f"or {MAX_COPY_BYTES // 1024**3} GB. Run it from a smaller folder, or "
            "make the folder a git repository so attempts share it instead."
        )


def prepare(root: Path, dest: Path) -> Workspace:
    """A private workspace at *dest* holding *root*'s current files."""
    top = _git_top(root)
    if top is not None:
        _check_untracked_size(top)
        tree = _tree_of(top)
        _clone(top, dest, tree)
        cwd = dest / root.relative_to(top)
        cwd.mkdir(parents=True, exist_ok=True)
        return Workspace(top=dest, cwd=cwd, git_top=top, baseline=tree)
    skipped: List[str] = []
    files = _walk(root, skipped)
    _check_copyable(root, files)
    dest.mkdir(parents=True, exist_ok=True)
    for rel, src in files.items():
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copy2(src, target)  # a linked file is copied as its content
        except OSError as e:
            raise AttemptsError(
                f"--attempts could not copy {src}: {e}. Close whatever holds it "
                "or fix the link, or run from a folder without it."
            ) from e
    if skipped:
        log.warning("--attempts: linked folders not copied: %s", ", ".join(skipped))
    baseline = {rel: _fingerprint(p) for rel, p in _walk(dest).items()}
    return Workspace(top=dest, cwd=dest, baseline=baseline, real=root, skipped=skipped)


def collect(ws: Workspace, answer: str, draft: Path) -> List[str]:
    """Save what an attempt changed, and its answer, into *draft*; return the paths."""
    draft.mkdir(parents=True, exist_ok=True)
    (draft / "ANSWER.md").write_text(answer or "(no answer)", encoding="utf-8")
    if ws.git_top is not None:
        changed, removed, patch = _git_changes(ws)
    else:
        changed, removed, patch = _copy_changes(ws)
    for rel in changed:
        source = ws.top / rel
        if source.is_file():
            target = draft / "files" / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
    if len(patch) > _PATCH_LIMIT:
        patch = (
            patch[:_PATCH_LIMIT]
            + "\n(patch cut here; every changed file is in files/)\n"
        )
    (draft / "CHANGES.patch").write_text(patch, encoding="utf-8")
    return changed + removed


def _git_changes(ws: Workspace) -> Tuple[List[str], List[str], str]:
    after = _tree_of(ws.top)
    status = _git(
        "diff", "--name-status", "--no-renames", "-z", ws.baseline, after, cwd=ws.top
    )
    parts = [p for p in status.split("\0") if p]
    changed: List[str] = []
    removed: List[str] = []
    for code, rel in zip(parts[::2], parts[1::2]):
        (removed if code.startswith("D") else changed).append(rel)
    patch = _git("diff", "--no-renames", ws.baseline, after, cwd=ws.top)
    return sorted(changed), sorted(removed), patch + ("\n" if patch else "")


def _copy_changes(ws: Workspace) -> Tuple[List[str], List[str], str]:
    now = _walk(ws.top)
    changed: List[str] = []
    removed: List[str] = []
    patch: List[str] = []
    for rel, path in sorted(now.items()):
        try:
            if ws.baseline.get(rel) == _fingerprint(path):
                continue
        except OSError:
            continue  # vanished mid-walk
        changed.append(rel)
        patch.append(_diff(_original(ws, rel), path, rel))
    for rel in sorted(set(ws.baseline) - set(now)):
        removed.append(rel)
        patch.append(f"--- a/{rel}\n+++ /dev/null\n(deleted)\n")
    return changed, removed, "".join(patch)


def _original(ws: Workspace, rel: str) -> Optional[Path]:
    """The real folder's copy of *rel*, if it still matches what the attempt started from."""
    if ws.real is None or rel not in ws.baseline:
        return None
    try:
        if _fingerprint(ws.real / rel) == ws.baseline[rel]:
            return ws.real / rel
    except OSError:
        pass  # deleted from the real folder since
    return _CHANGED_SINCE


#: Marks an original the user changed during the run, so the diff can't use it.
_CHANGED_SINCE = Path("<changed since>")


def _diff(old: Optional[Path], new: Path, rel: str) -> str:
    if old == _CHANGED_SINCE:
        return (
            f"--- a/{rel}\n+++ b/{rel}\n(the original changed during the run; "
            f"see files/{rel})\n"
        )
    try:
        if new.stat().st_size > _HASH_LIMIT:
            return f"--- a/{rel}\n+++ b/{rel}\n(large file changed; see files/{rel})\n"
        new_lines = new.read_text(encoding="utf-8").splitlines(keepends=True)
        old_lines = (
            old.read_text(encoding="utf-8").splitlines(keepends=True)
            if old is not None and old.is_file()
            else []
        )
    except UnicodeDecodeError:
        return f"--- a/{rel}\n+++ b/{rel}\n(binary file changed; see files/{rel})\n"
    text = "".join(difflib.unified_diff(old_lines, new_lines, f"a/{rel}", f"b/{rel}"))
    return text if text.endswith("\n") or not text else text + "\n"


def dispose(ws: Workspace) -> None:
    """Remove a workspace. A git workspace is a clone: nothing to unregister."""
    remove_tree(ws.top)


def remove_tree(path: Path) -> None:
    """Delete a scratch folder this module made, read-only git objects included."""
    if not path.exists():
        return
    # Python 3.12 renamed the hook; both take (func, path, exc).
    hook = "onexc" if sys.version_info >= (3, 12) else "onerror"
    shutil.rmtree(path, **{hook: _force_remove})


def _force_remove(func, target, _exc) -> None:
    os.chmod(target, 0o700)  # git marks objects read-only on Windows
    func(target)


#: ``python -m`` would put the workspace first on sys.path, so a project's own
#: ``json.py`` or ``argparse.py`` would replace the standard module.
_CHILD_MAIN = "import sys; del sys.path[0]; from gaia.cli import main; sys.exit(main())"


def _child_argv(flags: Sequence[str]) -> List[str]:
    return [sys.executable, "-c", _CHILD_MAIN, "-p", "--output-format", "json", *flags]


def _run_attempt(prompt: str, workdir: Path, flags: Sequence[str]) -> Dict[str, Any]:
    """One ``gaia -p`` in *workdir*: its JSON record, or an error record saying why."""
    try:
        done = subprocess.run(
            _child_argv(flags),
            input=prompt + ATTEMPT_INSTRUCTIONS,
            cwd=workdir,
            env=clean_env(),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except OSError as e:
        return {"is_error": True, "status": "error", "error": f"could not start: {e}"}
    for line in reversed(done.stdout.splitlines()):
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if isinstance(record, dict):
            return record
    tail = (done.stderr or "").strip().splitlines()[-5:]
    return {
        "is_error": True,
        "status": "error",
        "error": f"printed no result (exit {done.returncode}): {' | '.join(tail)}",
    }


def run_attempts(
    prompt: str, count: int, flags: Sequence[str], root: Optional[Path] = None
) -> Tuple[str, List[Dict[str, Any]], Path]:
    """Run *count* attempts in parallel, each in its own workspace.

    Returns the final turn's prompt, every attempt's record, and the scratch
    folder holding the drafts. The caller runs the final turn in *root*, lets
    it read the scratch folder, then removes it with :func:`remove_tree`. A
    failed attempt leaves no draft; if all fail this raises.
    """
    if not 2 <= count <= MAX_ATTEMPTS:
        raise ValueError(f"--attempts takes 2 to {MAX_ATTEMPTS}, got {count}.")
    root = Path(root or os.getcwd()).resolve()
    base = Path(tempfile.mkdtemp(prefix="gaia-attempts-")).resolve()
    if base.is_relative_to(root):
        remove_tree(base)
        raise AttemptsError(
            f"--attempts keeps its workspaces in the temp folder, which is inside "
            f"{root}. Run it from the project's own folder, or point TMP/TMPDIR "
            "outside it."
        )
    drafts = base / "drafts"
    spaces: List[Workspace] = []
    try:
        for k in range(1, count + 1):
            spaces.append(prepare(root, base / f"work-{k}" / root.name))
        with ThreadPoolExecutor(max_workers=count) as pool:
            records = list(
                pool.map(lambda w: _run_attempt(prompt, w.cwd, flags), spaces)
            )
        written = 0
        for ws, record in zip(spaces, records):
            if not record.get("is_error"):
                written += 1
                collect(ws, record.get("result", ""), drafts / f"attempt-{written}")
        if not written:
            raise AttemptsError(
                f"every attempt failed; the last said: {records[-1].get('error')}"
            )
    except BaseException:
        _dispose_all(spaces)
        try_remove(base)
        raise
    _dispose_all(spaces)
    return prompt + MERGE_INSTRUCTIONS.format(drafts=drafts.as_posix()), records, base


def _dispose_all(spaces: List[Workspace]) -> None:
    for ws in spaces:
        try_remove(ws.top)


def try_remove(path: Path) -> None:
    """Remove a scratch folder; if something holds it, say so and leave it."""
    try:
        remove_tree(path)
    except OSError as e:
        # A process an attempt started can still hold a file on Windows.
        log.warning("--attempts: could not remove %s: %s", path, e)
        print(f"⚠️  gaia -p: could not remove {path}: {e}", file=sys.stderr)
