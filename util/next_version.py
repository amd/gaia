#!/usr/bin/env python3
# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Open the PR that moves main past a final release (#5172).

After a final ``vX.Y.Z`` publishes, ``main`` must:

* list ``releases/vX.Y.Z`` in docs.json, so the navbar names that release;
* declare a newer version than X.Y.Z in ``src/gaia/version.py``. If it doesn't
  already, it becomes X.(Y+1).0, and every field that follows it is stamped;
* carry a notes stub for that version, which the release PR fills in.

This makes those edits, commits them to ``next-version/vX.Y.Z`` and opens one
PR against main. Safe to re-run: no PR when main already has everything, and
an open PR has its branch refreshed instead of a second one opened.

A patch final also needs a PR to its ``release/vX.Y`` branch. Those branches
arrive in Phase 6 of the release train, so that half only reports that there is
no branch yet, and fails loudly if one exists before it is built.

Usage::

    python util/next_version.py --tag v0.26.0 --repo amd/gaia [--run-url URL]
    python util/next_version.py --tag v0.26.0 --repo amd/gaia --dry-run
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
_UTIL = str(Path(__file__).resolve().parent)
if _UTIL not in sys.path:
    sys.path.insert(0, _UTIL)

# pylint: disable=wrong-import-position
import release_versions as rv  # noqa: E402
from release_tag import parse_tag  # noqa: E402

# Not release/*: origin keeps a `release` branch, so git cannot create refs under it.
BRANCH_PREFIX = "next-version/v"
BOT_NAME = "github-actions[bot]"
BOT_EMAIL = "41898282+github-actions[bot]@users.noreply.github.com"
LABEL = "release"
REVIEWERS = "kovtcharov-amd,itomek-amd"
STUB_MARKER = "release-notes-stub"
NOTES_DIR = "docs/releases"
FLAGSHIP_CHANGELOG = "hub/agents/gaia/npm/CHANGELOG.md"
_UNRELEASED = re.compile(r"(?m)^## \[Unreleased\][ \t]*\r?\n")

_FINAL = re.compile(r"^\d+\.\d+\.\d+$")
_PAGES = re.compile(
    r'("group":\s*"Release Notes",\s*"pages":\s*\[)(?P<body>[^\]]*)(\])', re.S
)

Runner = Callable[[Sequence[str]], subprocess.CompletedProcess]

PERMISSION_MARKERS = (
    "resource not accessible by integration",
    "not permitted to create or approve pull requests",
    "permission to",
    "403",
    "write access",
)


class NextVersionError(Exception):
    """The next-version PR could not be prepared or opened; the message says why."""


@dataclass
class Prepared:
    final: str
    version_before: str
    version_after: str
    changes: list[str] = field(default_factory=list)

    @property
    def bumped(self) -> bool:
        return self.version_before != self.version_after


def next_minor(version: str) -> str:
    major, minor, _ = (int(p) for p in version.split("."))
    return f"{major}.{minor + 1}.0"


def final_version(tag: str) -> str:
    """X.Y.Z of a final tag; an RC or a four-part tag is refused."""
    try:
        rel = parse_tag(tag)
    except ValueError as e:
        raise NextVersionError(str(e)) from e
    if rel.is_rc:
        raise NextVersionError(
            f"{tag} is a release candidate. The next-version PR follows a final "
            "(vX.Y.Z) only."
        )
    if not _FINAL.match(rel.base_version):
        raise NextVersionError(
            f"{tag} is not vX.Y.Z. The release train has no four-part tags."
        )
    return rel.base_version


def notes_stub(version: str, final: str) -> str:
    return (
        "---\n"
        f'title: "v{version}"\n'
        f'description: "GAIA v{version} release notes (draft)"\n'
        "noindex: true\n"
        "---\n"
        "\n"
        f"{{/* {STUB_MARKER}: opened after v{final} shipped. The v{version} release "
        "PR replaces this file with the real notes and adds it to the Releases tab "
        "in docs/docs.json. */}\n"
        "\n"
        f"# GAIA v{version} Release Notes\n"
        "\n"
        "## What's New\n"
        "\n"
        "## Full Changelog\n"
        "\n"
        f"Full Changelog: [v{final}...v{version}]"
        f"(https://github.com/amd/gaia/compare/v{final}...v{version})\n"
    )


def open_changelog_entry(text: str, version: str) -> str:
    """Open the flagship's ``## [<version>]`` entry under ``## [Unreleased]``.

    Its first line quotes the new minimum core, which test_npm_doc_min_version.py
    holds to min_gaia_version.
    """
    m = _UNRELEASED.search(text)
    if not m:
        raise NextVersionError(
            f"{FLAGSHIP_CHANGELOG} has no '## [Unreleased]' heading to open the "
            f"{version} entry under."
        )
    if re.search(rf"(?m)^## \[{re.escape(version)}\]", text):
        return text
    nl = "\r\n" if m.group(0).endswith("\r\n") else "\n"
    entry = (
        f"{nl}## [{version}]{nl}{nl}"
        f"Ships with GAIA {version} and requires the `gaia` Python CLI {version}+ "
        f"on `PATH`.{nl}"
    )
    return text[: m.end()] + entry + text[m.end() :]


def list_release_page(docs_text: str, version: str) -> str:
    """Insert ``releases/v<version>`` into the Release Notes group, newest first."""
    m = _PAGES.search(docs_text)
    if not m:
        raise NextVersionError(
            f"{rv.DOCS_JSON} has no Releases > 'Release Notes' pages list to add "
            f"releases/v{version} to."
        )
    body = m.group("body")
    page = f"releases/v{version}"
    if f'"{page}"' in body:
        return docs_text
    entries = list(re.finditer(r'(?P<indent>[ \t]*)"(?P<page>[^"]+)"', body))
    if not entries:
        raise NextVersionError(
            f"{rv.DOCS_JSON}: the Release Notes pages list is empty."
        )
    indent = entries[0].group("indent")
    nl = "\r\n" if "\r\n" in body else "\n"
    want = rv.version_key(version)
    for e in entries:
        pm = rv.RELEASE_PAGE.match(e.group("page"))
        if pm and rv.version_key(pm.group(1)) < want:
            at = m.start("body") + e.start()
            return docs_text[:at] + f'{indent}"{page}",{nl}' + docs_text[at:]
    at = m.start("body") + entries[-1].end()
    return docs_text[:at] + f',{nl}{indent}"{page}"' + docs_text[at:]


def _read(path: Path) -> str:
    with open(path, encoding="utf-8", newline="") as f:
        return f.read()


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(text)


def prepare_main(root: Path, tag: str, final_notes: str | None = None) -> Prepared:
    """Edit the tree at *root* so main follows final *tag*. Writes nothing on error.

    *final_notes* is the release's notes as tagged, used only when main lacks them.
    """
    final = final_version(tag)
    core_text = _read(root / rv.CORE_PY)
    found = rv._PY_VERSION.findall(core_text)  # pylint: disable=protected-access
    if len(found) != 1:
        raise NextVersionError(f"{rv.CORE_PY}: expected one __version__.")
    core = found[0][1]
    if not _FINAL.match(core):
        raise NextVersionError(
            f"{rv.CORE_PY} on main says {core}; main only ever declares X.Y.Z. "
            "Restore it before re-running."
        )
    target = core if rv.version_key(core) > rv.version_key(final) else next_minor(final)
    prepared = Prepared(final=final, version_before=core, version_after=target)

    writes: dict[Path, str] = {}
    notes = root / NOTES_DIR / f"v{final}.mdx"
    if not notes.is_file():
        if final_notes is None:
            raise NextVersionError(
                f"{notes.relative_to(root).as_posix()} is missing on main and no "
                f"tagged copy was given; read it with `git show {tag}:"
                f"{NOTES_DIR}/v{final}.mdx`."
            )
        writes[notes] = final_notes
        prepared.changes.append(f"copied the v{final} notes from {tag}")

    docs_path = root / rv.DOCS_JSON
    docs_text = _read(docs_path)
    listed = list_release_page(docs_text, final)
    if listed != docs_text:
        writes[docs_path] = listed
        prepared.changes.append(f"listed releases/v{final} in the Releases tab")

    if prepared.bumped:
        writes[root / rv.CORE_PY] = (
            rv._PY_VERSION.sub(  # pylint: disable=protected-access
                lambda m: f"{m.group(1)}{target}{m.group(3)}", core_text, count=1
            )
        )
        prepared.changes.append(f"set {rv.CORE_PY} to {target}")

    changelog = root / FLAGSHIP_CHANGELOG
    changelog_text = _read(changelog)
    opened = open_changelog_entry(changelog_text, target)
    if opened != changelog_text:
        writes[changelog] = opened
        prepared.changes.append(f"opened the flagship's {target} changelog entry")

    stub = root / NOTES_DIR / f"v{target}.mdx"
    if not stub.is_file():
        writes[stub] = notes_stub(target, final)
        prepared.changes.append(f"opened the v{target} notes stub")

    originals = {p: (_read(p) if p.is_file() else None) for p in writes}
    for path, text in writes.items():
        _write(path, text)
    report = rv.run(root, stamp=True)
    if report.errors:
        for path, text in originals.items():
            if text is None:
                path.unlink()
            else:
                _write(path, text)
        raise NextVersionError(
            "release_versions.py could not stamp the tree:\n  - "
            + "\n  - ".join(report.errors)
        )
    prepared.changes.extend(f"stamped {line.split(':')[0]}" for line in report.stamped)
    return prepared


def branch_name(final: str) -> str:
    return f"{BRANCH_PREFIX}{final}"


def pr_title(p: Prepared) -> str:
    if p.bumped:
        return f"chore(release): start {p.version_after} after v{p.final}"
    return f"chore(release): follow v{p.final} on main"


def pr_body(p: Prepared, run_url: str | None) -> str:
    run = f" ([release run]({run_url}))" if run_url else ""
    if p.bumped:
        lead = (
            f"v{p.final} is published{run}, so main moves on to {p.version_after}: "
            f"the docs navbar names v{p.final}, the version every package builds "
            f"from main becomes {p.version_after}, and a notes stub for it is "
            "waiting for the release PR."
        )
    else:
        lead = (
            f"v{p.final} is published{run}. Main already declares "
            f"{p.version_after}, so this only lists v{p.final} in the docs and "
            f"opens what is missing for {p.version_after}."
        )
    changes = "\n".join(f"- {c}" for c in p.changes)
    return (
        f"{lead} The release workflow opened this PR, so its checks start when the "
        "merge queue picks it up.\n\n"
        f"<details>\n<summary>Changes</summary>\n\n{changes}\n</details>\n\n"
        "## Test plan\n"
        "- [ ] `python util/release_versions.py --check` passes\n"
        "- [ ] `python -m pytest tests/unit/test_docs_json_release.py` passes\n"
    )


def _default_runner(cmd: Sequence[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        list(cmd), cwd=REPO_ROOT, capture_output=True, text=True, check=False
    )


def _fail(what: str, proc: subprocess.CompletedProcess, hint: str) -> NextVersionError:
    detail = (proc.stderr or proc.stdout or "").strip() or f"exit {proc.returncode}"
    return NextVersionError(f"{what}: {detail}\n{hint}")


def _permission_hint(stderr: str, fallback: str) -> str:
    if any(m in stderr.lower() for m in PERMISSION_MARKERS):
        return (
            "The job's token lacks access. Give it `contents: write` and "
            "`pull-requests: write`, and turn on Settings -> Actions -> General -> "
            "'Allow GitHub Actions to create and approve pull requests'."
        )
    return fallback


def _must(run: Runner, cmd: Sequence[str], hint: str = "Re-run this job.") -> str:
    proc = run(cmd)
    if proc.returncode != 0:
        raise _fail(f"`{' '.join(cmd[:3])}` failed", proc, hint)
    return proc.stdout


def patch_branch_status(final: str, run: Runner) -> str:
    """The patch half. Stubbed until Phase 6 creates release/vX.Y branches."""
    major, minor, patch = final.split(".")
    if patch == "0":
        return f"v{final} is not a patch, so no release-branch PR is due."
    branch = f"release/v{major}.{minor}"
    out = _must(run, ["git", "ls-remote", "--heads", "origin", branch])
    if not out.strip():
        return (
            f"No {branch} branch exists yet (release branches arrive in Phase 6 of "
            f"#5172), so no {major}.{minor}.{int(patch) + 1} bump PR was opened for it."
        )
    raise NextVersionError(
        f"{branch} exists, but the PR that bumps it to "
        f"{major}.{minor}.{int(patch) + 1} is not built yet (Phase 6 of #5172). "
        "Open that bump by hand: set src/gaia/version.py on the branch and run "
        "`python util/release_versions.py --stamp`."
    )


def open_main_pr(
    tag: str,
    repo: str,
    *,
    run_url: str | None = None,
    dry_run: bool = False,
    run: Runner = _default_runner,
    workdir: Path = REPO_ROOT,
    base: str = "main",
) -> str:
    """Prepare main for the release after *tag* and open (or refresh) its PR."""
    final = final_version(tag)
    if _must(run, ["git", "status", "--porcelain"]).strip() and not dry_run:
        raise NextVersionError(
            "the checkout has uncommitted changes; run this on a clean checkout."
        )
    _must(run, ["git", "fetch", "--no-tags", "origin", base])
    branch = branch_name(final)
    if not dry_run:
        _must(run, ["git", "checkout", "-B", branch, f"origin/{base}"])

    notes_rel = f"{NOTES_DIR}/v{final}.mdx"
    final_notes = None
    if not (workdir / notes_rel).is_file():
        final_notes = _must(
            run,
            ["git", "show", f"{tag}:{notes_rel}"],
            f"{tag} has no {notes_rel}; fetch tags (`git fetch --tags`) and re-run.",
        )
    prepared = prepare_main(workdir, tag, final_notes)

    changed = _must(run, ["git", "status", "--porcelain"]).strip()
    if not prepared.changes:
        return f"{base} already follows v{final}; no PR needed."
    if dry_run:
        diff = _must(run, ["git", "--no-pager", "diff", "--stat"])
        return (
            f"DRY RUN: would open '{pr_title(prepared)}' from {branch}.\n"
            + "\n".join(f"  - {c}" for c in prepared.changes)
            + f"\n{diff}{changed}"
        )

    _must(run, ["git", "add", "--all"])
    _must(
        run,
        [
            "git",
            "-c",
            f"user.name={BOT_NAME}",
            "-c",
            f"user.email={BOT_EMAIL}",
            "commit",
            "-m",
            pr_title(prepared),
            "-m",
            pr_body(prepared, run_url).split("\n\n", 1)[0],
        ],
    )
    remote = f"refs/remotes/origin/{branch}"
    # A missing branch fails this fetch; that only means there is nothing to compare.
    has_remote = (
        run(["git", "fetch", "--no-tags", "origin", f"+{branch}:{remote}"]).returncode
        == 0
    )
    same = (
        has_remote and run(["git", "diff", "--quiet", "HEAD", remote]).returncode == 0
    )
    if not same:
        pushed = run(["git", "push", "--force", "origin", f"HEAD:refs/heads/{branch}"])
        if pushed.returncode != 0:
            raise _fail(
                f"could not push {branch}",
                pushed,
                _permission_hint(
                    pushed.stderr or "", "Re-run this job; it is idempotent."
                ),
            )

    listed = _must(
        run,
        [
            "gh",
            "pr",
            "list",
            "--repo",
            repo,
            "--head",
            branch,
            "--base",
            base,
            "--state",
            "open",
            "--json",
            "url",
            "--jq",
            ".[0].url // empty",
        ],
        "The job's GH_TOKEN needs read access to pull requests.",
    ).strip()
    if listed:
        verb = "already carries these changes" if same else "was refreshed"
        return f"{listed} is already open; its branch {verb}."

    with tempfile.NamedTemporaryFile(
        "w", suffix=".md", delete=False, encoding="utf-8"
    ) as body:
        body.write(pr_body(prepared, run_url))
    try:
        created = run(
            [
                "gh",
                "pr",
                "create",
                "--repo",
                repo,
                "--base",
                base,
                "--head",
                branch,
                "--title",
                pr_title(prepared),
                "--body-file",
                body.name,
                "--label",
                LABEL,
                "--reviewer",
                REVIEWERS,
            ]
        )
    finally:
        os.unlink(body.name)
    if created.returncode != 0:
        raise _fail(
            f"branch {branch} is pushed but the PR could not be opened",
            created,
            _permission_hint(
                created.stderr or "",
                f"Re-run this job; it is idempotent. Or by hand: gh pr create "
                f"--repo {repo} --base {base} --head {branch}",
            ),
        )
    return f"opened {created.stdout.strip()}"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--tag", required=True, help="the final just published, vX.Y.Z")
    ap.add_argument("--repo", required=True, help="owner/name, e.g. amd/gaia")
    ap.add_argument("--run-url", help="the release run, linked from the PR body")
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="edit the checkout and print what the PR would carry; push nothing",
    )
    args = ap.parse_args(argv)

    lines: list[str] = []
    try:
        lines.append(
            open_main_pr(
                args.tag, args.repo, run_url=args.run_url, dry_run=args.dry_run
            )
        )
        lines.append(patch_branch_status(final_version(args.tag), _default_runner))
    except (NextVersionError, OSError) as e:
        for line in str(e).splitlines():
            print(f"::error::{line}", flush=True)
        return 1
    finally:
        summary = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary and lines:
            with open(summary, "a", encoding="utf-8") as f:
                f.write("".join(f"- {line.splitlines()[0]}\n" for line in lines))
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
