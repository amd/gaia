#!/usr/bin/env python3
# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Decide whether a tag-triggered eval workflow runs its self-hosted lanes.

The eval workflows run on release and release-candidate tags. A release
candidate may opt out by carrying ``[skip eval]`` in its ANNOTATED tag message::

    git tag -a v0.25.0-rc16 -m "rc16 [skip eval]"

A final release (``vX.Y.Z``) always runs: the marker is ignored with a notice.
A lightweight tag has no message of its own, so it always runs too (its
``%(contents)`` would be the commit message, which must not be able to skip a
release eval). Every other event - ``workflow_dispatch``, a non-tag push, a
``workflow_call`` from another release - runs.

Writes ``run=true|false`` to ``$GITHUB_OUTPUT`` and, when it skips or ignores
the marker, a ``::notice::`` plus a job-summary line so the decision is visible.

Usage (in a job on a hosted runner, after actions/checkout)::

    python util/eval_tag_gate.py
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from dataclasses import dataclass

from release_tag import RC_TAG_RE

SKIP_MARKER_RE = re.compile(r"\[skip eval\]", re.IGNORECASE)


@dataclass(frozen=True)
class Decision:
    run: bool
    notice: str = ""


def decide(
    event_name: str,
    ref_type: str,
    tag: str,
    tag_object_type: str = "",
    tag_message: str = "",
) -> Decision:
    """Pure decision: should the eval lanes run for this trigger?"""
    if event_name != "push" or ref_type != "tag":
        return Decision(True)
    marker = bool(SKIP_MARKER_RE.search(tag_message)) and tag_object_type == "tag"
    if not marker:
        return Decision(True)
    if not RC_TAG_RE.match(tag):
        return Decision(
            True,
            f"{tag} carries [skip eval], but only a release candidate (vX.Y.Z-rcN) "
            "may skip the eval. Running it.",
        )
    return Decision(
        False,
        f"Eval skipped for {tag} by request: its tag message contains [skip eval]. "
        "To measure this commit, dispatch the workflow on the tag.",
    )


def _git(*args: str) -> str:
    result = subprocess.run(["git", *args], capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(
            f"`git {' '.join(args)}` failed (exit {result.returncode}): "
            f"{result.stderr.strip()}. The eval gate cannot read the tag "
            "annotation; check that the job checked out the repository."
        )
    return result.stdout


def read_tag(tag: str) -> tuple[str, str]:
    """Return (object type, message) for ``refs/tags/<tag>``.

    actions/checkout may store a tag as a lightweight ref, so re-fetch it
    with --force to get the annotated tag object if there is one.
    """
    ref = f"refs/tags/{tag}"
    _git("fetch", "--force", "--no-tags", "--depth=1", "origin", f"{ref}:{ref}")
    object_type = _git("for-each-ref", ref, "--format=%(objecttype)").strip()
    if not object_type:
        raise RuntimeError(f"{ref} not found after fetching it from origin.")
    message = _git("for-each-ref", ref, "--format=%(contents)")
    return object_type, message


def _append(path_var: str, text: str) -> None:
    path = os.environ.get(path_var)
    if not path:
        raise RuntimeError(f"{path_var} is not set; run this inside a GitHub job.")
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(text)


def main() -> int:
    event_name = os.environ.get("GITHUB_EVENT_NAME", "")
    ref_type = os.environ.get("GITHUB_REF_TYPE", "")
    tag = os.environ.get("GITHUB_REF_NAME", "")

    object_type, message = "", ""
    if event_name == "push" and ref_type == "tag":
        object_type, message = read_tag(tag)

    decision = decide(event_name, ref_type, tag, object_type, message)
    _append("GITHUB_OUTPUT", f"run={'true' if decision.run else 'false'}\n")
    if decision.notice:
        print(f"::notice title=Eval gate::{decision.notice}")
        _append("GITHUB_STEP_SUMMARY", f"**Eval gate:** {decision.notice}\n")
    print(f"run={'true' if decision.run else 'false'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
