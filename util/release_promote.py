#!/usr/bin/env python3
# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Find the release-train dry run that verified a commit, and check a release against it.

A tag should publish the exact bytes the dry run built and verified for its
commit (#6611, docs/plans/release-promote.md). This is the lookup and the
comparison that promotion stands on. It never uploads anything.

Subcommands::

    # the passing dry run that built <tag> at <sha>; writes its manifest to --out
    release_promote.py locate --repo amd/gaia --sha <sha> --tag v0.25.4-rc5 --out m/

    # each published asset of <tag> against that manifest's SHA-256
    release_promote.py compare --repo amd/gaia --tag v0.25.4-rc5 --manifest m/release-manifest.json

``compare`` reports by default. ``--strict`` fails on any asset that differs from,
or is missing in, the verified build: the gate once publish.yml promotes.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

_UTIL = str(Path(__file__).resolve().parent)
if _UTIL not in sys.path:
    sys.path.insert(0, _UTIL)

from gh_retry import (  # noqa: E402  pylint: disable=wrong-import-position
    RateLimitWaitExceeded,
    run_command,
)
from release_tag import parse_tag  # noqa: E402  pylint: disable=wrong-import-position

DRYRUN_WORKFLOW = "release_train_dryrun.yml"
MANIFEST_ARTIFACT = "release-train-manifest"
MANIFEST_FILE = "release-manifest.json"
MANIFEST_SCHEMA = 1


class PromoteError(Exception):
    """No usable verified build; the message says what to do."""


GH_TIMEOUT_S = 300
# A spent API budget refills within the hour; wait for it at most that long.
GH_RATE_LIMIT_WAIT_S = 3600


def _gh(args: list[str]) -> subprocess.CompletedProcess:
    try:
        return run_command(["gh", *args], GH_RATE_LIMIT_WAIT_S, timeout=GH_TIMEOUT_S)
    except subprocess.TimeoutExpired as e:
        raise PromoteError(
            f"`gh {' '.join(args)}` did not finish in {GH_TIMEOUT_S} s. GitHub's "
            "API is slow or down; re-run this job."
        ) from e
    except RateLimitWaitExceeded as e:
        raise PromoteError(str(e)) from e


def _gh_json(args: list[str]) -> object:
    done = _gh(args)
    if done.returncode != 0:
        raise PromoteError(
            f"`gh {' '.join(args)}` failed: {done.stderr.strip()}. A rate limit "
            "clears within the hour; otherwise check the token has actions: read."
        )
    return json.loads(done.stdout or "null")


def flavor_of(manifest: dict, tag: str) -> str:
    """'rc' or 'final': which of the dry run's two builds ``tag`` publishes."""
    if manifest.get("rc_tag") == tag:
        return "rc"
    if manifest.get("final_tag") == tag:
        return "final"
    raise PromoteError(
        f"the dry run built {manifest.get('rc_tag')} and its twin "
        f"{manifest.get('final_tag')}, not {tag}. Artifacts embed their version, "
        f"so dispatch the dry run as {tag if parse_tag(tag).is_rc else 'an RC of it'}."
    )


def check_manifest(manifest: dict, sha: str, tag: str) -> str:
    """Raise unless this manifest vouches for ``tag`` at ``sha``; return its flavor."""
    if manifest.get("schema") != MANIFEST_SCHEMA:
        raise PromoteError(
            f"manifest schema {manifest.get('schema')!r}, expected {MANIFEST_SCHEMA}. "
            "Re-run the dry run on this commit with the current workflow."
        )
    if manifest.get("commit") != sha:
        raise PromoteError(
            f"the manifest verified {manifest.get('commit')}, not {sha}. Only a dry "
            "run of the tagged commit itself can vouch for its artifacts."
        )
    if manifest.get("errors"):
        raise PromoteError(
            f"the dry run recorded {len(manifest['errors'])} failed check(s), first: "
            f"{manifest['errors'][0]}. Fix it on main and re-run the dry run."
        )
    return flavor_of(manifest, tag)


@dataclass
class Located:
    run_id: int
    run_url: str
    manifest: dict
    flavor: str
    skipped: list[str] = field(default_factory=list)


def _runs(repo: str, sha: str) -> list[dict]:
    """Every passing dry run of ``sha``, paginated.

    A commit that sits unchanged collects one skipped (manifest-less) nightly a
    day, so a fixed page size would eventually hide the run that built it.
    """
    done = _gh(
        [
            "api",
            "--paginate",
            f"repos/{repo}/actions/workflows/{DRYRUN_WORKFLOW}/runs"
            f"?head_sha={sha}&status=success&per_page=100",
            "--jq",
            ".workflow_runs[] | {databaseId: .id, url: .html_url, createdAt: .created_at}",
        ]
    )
    if done.returncode != 0:
        raise PromoteError(
            f"could not list {DRYRUN_WORKFLOW} runs of {sha}: {done.stderr.strip()}. "
            "A rate limit clears within the hour; otherwise check the token has "
            "actions: read."
        )
    try:
        return [json.loads(line) for line in done.stdout.splitlines() if line.strip()]
    except ValueError as e:
        raise PromoteError(f"GitHub returned runs that are not JSON: {e}") from e


def _manifest_of(repo: str, run_id: int) -> dict | None:
    arts = _gh_json(
        [
            "api",
            f"repos/{repo}/actions/runs/{run_id}/artifacts?name={MANIFEST_ARTIFACT}",
        ]
    )
    if not any(not a.get("expired") for a in (arts or {}).get("artifacts", [])):
        return None
    with tempfile.TemporaryDirectory() as tmp:
        done = _gh(
            ["run", "download", str(run_id), "-R", repo, "-n", MANIFEST_ARTIFACT]
            + ["-D", tmp]
        )
        path = Path(tmp) / MANIFEST_FILE
        if done.returncode != 0 or not path.is_file():
            raise PromoteError(
                f"could not download {MANIFEST_ARTIFACT} from run {run_id}: "
                f"{done.stderr.strip() or 'no ' + MANIFEST_FILE + ' in it'}."
            )
        return json.loads(path.read_text(encoding="utf-8"))


def locate(
    repo: str, sha: str, tag: str, runs=_runs, manifest_of=_manifest_of
) -> Located:
    """The newest passing dry run of ``sha`` whose manifest vouches for ``tag``."""
    rel = parse_tag(tag)
    # A final ships the twin of an RC run, so any RC of its version will do.
    rc_hint = tag if rel.is_rc else f"v{rel.base_version}-rcN"
    skipped: list[str] = []
    newest_first = sorted(runs(repo, sha), key=lambda r: r["createdAt"], reverse=True)
    for run in newest_first:
        manifest = manifest_of(repo, run["databaseId"])
        if manifest is None:
            # A nightly that skipped an already-verified commit passes with no manifest.
            skipped.append(f"{run['url']}: no live {MANIFEST_ARTIFACT}")
            continue
        try:
            flavor = check_manifest(manifest, sha, tag)
        except PromoteError as e:
            skipped.append(f"{run['url']}: {e}")
            continue
        return Located(run["databaseId"], run["url"], manifest, flavor, skipped)
    tried = "".join(f"\n  - {s}" for s in skipped) or " none."
    raise PromoteError(
        f"no passing {DRYRUN_WORKFLOW} run of {sha} verified {tag}. Runs tried:{tried}\n"
        "The dry run builds its branch's head, so dispatch it on the branch "
        f"(main or vX.Y.Z-release) whose head is still {sha[:12]}: gh workflow "
        f"run {DRYRUN_WORKFLOW} --ref <branch> -f rc_tag={rc_hint}. Wait for it "
        "to pass, then re-run this job."
    )


@dataclass
class Comparison:
    identical: list[str] = field(default_factory=list)
    differ: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    unverified: list[str] = field(default_factory=list)


def expected_release_assets(manifest: dict, flavor: str, tag: str) -> dict[str, str]:
    """name -> sha256 of every artifact the manifest sends to ``tag``'s GitHub release."""
    out = {}
    for a in manifest.get("artifacts", []):
        if a.get("flavor") not in (flavor, "both"):
            continue
        for d in a.get("destinations", []):
            if d.get("channel") == "github-release" and d.get("tag") == tag:
                out[a["name"]] = a["sha256"]
    return out


def compare(expected: dict[str, str], published: dict[str, str]) -> Comparison:
    c = Comparison()
    for name, digest in sorted(expected.items()):
        got = published.get(name)
        if got is None:
            c.missing.append(name)
        elif got == digest:
            c.identical.append(name)
        else:
            c.differ.append(name)
    c.unverified = sorted(set(published) - set(expected))
    return c


def published_digests(repo: str, tag: str) -> dict[str, str]:
    release = _gh_json(["api", f"repos/{repo}/releases/tags/{tag}"])
    out = {}
    for a in release.get("assets", []):
        digest = a.get("digest") or ""
        if a.get("state") == "uploaded" and digest.startswith("sha256:"):
            out[a["name"]] = digest.removeprefix("sha256:")
    return out


def render(c: Comparison, tag: str, run_url: str) -> str:
    lines = [
        f"## {tag}: published assets vs the verified dry run",
        "",
        f"Verified build: {run_url}",
        "",
        f"- **{len(c.identical)}** byte-identical to the verified build",
        f"- **{len(c.differ)}** differ (rebuilt, not promoted)",
        f"- **{len(c.missing)}** verified but not on the release",
        f"- **{len(c.unverified)}** on the release but not in the manifest",
        "",
    ]
    for title, names in (
        ("Differ", c.differ),
        ("Missing", c.missing),
        ("Not in the manifest", c.unverified),
    ):
        if names:
            lines += [f"<details><summary>{title}</summary>", ""]
            lines += [f"- `{n}`" for n in names]
            lines += ["</details>", ""]
    return "\n".join(lines)


def annotate(level: str, msg: str) -> None:
    """A GitHub annotation on stderr: stdout is for KEY=VALUE outputs and the summary."""
    # A raw newline ends a workflow command; %0A keeps the whole message in it.
    escaped = msg.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    print(f"::{level}::{escaped}", file=sys.stderr, flush=True)


def cmd_locate(args: argparse.Namespace) -> int:
    try:
        found = locate(args.repo, args.sha, args.tag)
    except (PromoteError, ValueError) as e:
        # ValueError: malformed JSON from GitHub, or a malformed tag.
        if not args.missing_ok:
            raise
        # Report mode only: nothing depends on the verified build yet.
        annotate("warning", str(e))
        print("found=false")
        return 0
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / MANIFEST_FILE).write_text(
        json.dumps(found.manifest, indent=2) + "\n", encoding="utf-8"
    )
    for s in found.skipped:
        print(f"skipped {s}", file=sys.stderr)
    print("found=true")
    print(f"run_id={found.run_id}")
    print(f"run_url={found.run_url}")
    print(f"flavor={found.flavor}")
    return 0


def cmd_compare(args: argparse.Namespace) -> int:
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    flavor = flavor_of(manifest, args.tag)
    expected = expected_release_assets(manifest, flavor, args.tag)
    c = compare(expected, published_digests(args.repo, args.tag))
    print(render(c, args.tag, args.run_url or "(not given)"))
    bad = len(c.differ) + len(c.missing)
    if bad and args.strict:
        annotate(
            "error",
            f"{bad} asset(s) of {args.tag} are not the bytes the dry run "
            "verified. Publish only what it built: re-run with the promote path, "
            "never a rebuild.",
        )
        return 1
    if bad:
        annotate(
            "warning",
            f"{len(c.identical)} of {len(expected)} verified assets of "
            f"{args.tag} were published byte-identical; {bad} were rebuilt. "
            "Expected until publish.yml promotes (#6611).",
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n", maxsplit=1)[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    loc = sub.add_parser("locate", help="find the dry run that verified a tag")
    loc.add_argument("--repo", required=True)
    loc.add_argument("--sha", required=True)
    loc.add_argument("--tag", required=True)
    loc.add_argument("--out", type=Path, required=True)
    loc.add_argument(
        "--missing-ok",
        action="store_true",
        help="report mode: warn and print found=false instead of failing",
    )
    loc.set_defaults(func=cmd_locate)

    cmp_ = sub.add_parser("compare", help="published assets vs the verified build")
    cmp_.add_argument("--repo", required=True)
    cmp_.add_argument("--tag", required=True)
    cmp_.add_argument("--manifest", type=Path, required=True)
    cmp_.add_argument("--run-url", default="")
    cmp_.add_argument("--strict", action="store_true")
    cmp_.set_defaults(func=cmd_compare)

    args = ap.parse_args(argv)
    try:
        return args.func(args)
    except (PromoteError, ValueError) as e:
        annotate("error", str(e))
        return 1


if __name__ == "__main__":
    sys.exit(main())
