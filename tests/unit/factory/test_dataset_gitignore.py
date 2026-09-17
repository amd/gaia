# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Assert the eval-dataset output can never be committed to this public repo.

``amd/gaia`` is public. The step-level dataset is built from private Claude Code
transcripts and carries absolute paths, branch names, repository content and
anything ever pasted into a prompt. Every record is scrubbed, but a regex cannot
remove arbitrary proper nouns, so the data is private-tier regardless.

Three independent defences exist. This file tests the last one:

1. The dataset is written to ``~/.gaia/cache/factory/dataset/``, outside any repo.
2. ``build.py`` refuses outright to write into a git working tree.
3. ``.gitignore`` catches anything that lands here anyway.

The paired negative tests matter as much as the positive ones: a careless
``dataset/`` pattern would also hide ``src/gaia/factory/dataset/``, silently
dropping the builder from the repo.
"""

import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]

#: Where a stray build, redirect or copy would plausibly land.
MUST_BE_IGNORED = [
    "dataset/oracle/records.jsonl",
    "dataset/pool/records.jsonl",
    "dataset/blobs/deadbeef.txt",
    "dataset/DATASHEET.md",
    "dataset/MANIFEST.json",
    "dataset/partition_audit.json",
    "oracle/records.jsonl",
    "pool/records.jsonl",
    "blobs/deadbeef.txt",
    "factory-cache/snap5/traces.jsonl",
    "DATASHEET.md",
    "MANIFEST.json",
    "partition_audit.json",
    "contamination.json",
    "scrub_report.json",
    "tool_catalog.json",
    "commit_recovery.json",
    # Distinctive data filenames, wherever they land.
    "some/nested/dir/records.jsonl",
    "src/gaia/factory/dataset/records.jsonl",
    "anywhere/traces.jsonl",
    "anywhere/intents.jsonl",
    # Harvest output, already covered — asserted so it stays covered.
    "labels.txt",
    "tables.md",
    "context.md",
    "savings.md",
]

#: Builder code and its tests. These must stay tracked.
MUST_NOT_BE_IGNORED = [
    "src/gaia/factory/dataset/__init__.py",
    "src/gaia/factory/dataset/build.py",
    "src/gaia/factory/dataset/scrub.py",
    "src/gaia/factory/dataset/audit.py",
    "src/gaia/factory/dataset/verifiers.py",
    "src/gaia/factory/dataset/extract.py",
    "src/gaia/factory/dataset/partition.py",
    "src/gaia/factory/dataset/axes.py",
    "src/gaia/factory/dataset/commits.py",
    "src/gaia/factory/dataset/verify.py",
    "src/gaia/factory/harvest/reader.py",
    "tests/unit/factory/test_dataset_scrub.py",
    "tests/unit/factory/test_dataset_audit.py",
]


def _is_ignored(path: str) -> bool:
    """Ask git itself, rather than re-implementing gitignore semantics."""
    result = subprocess.run(
        ["git", "check-ignore", "-q", path],
        cwd=REPO,
        capture_output=True,
        check=False,
    )
    if result.returncode not in (0, 1):
        pytest.skip(f"git check-ignore unavailable: {result.stderr.decode()[:200]}")
    return result.returncode == 0


@pytest.mark.parametrize("path", MUST_BE_IGNORED)
def test_dataset_output_is_ignored(path):
    assert _is_ignored(path), (
        f"{path} is NOT gitignored. Dataset output is derived from private "
        "transcripts and this repository is public. Restore the pattern in "
        ".gitignore under 'Step-level eval dataset output'."
    )


@pytest.mark.parametrize("path", MUST_NOT_BE_IGNORED)
def test_builder_code_is_not_ignored(path):
    assert not _is_ignored(path), (
        f"{path} IS gitignored, but it is source code. A pattern like `dataset/` "
        "without a leading slash hides src/gaia/factory/dataset/ too, silently "
        "dropping the builder from the repo."
    )


def test_no_dataset_artifact_is_currently_tracked():
    """Nothing derived from a transcript has ever been committed."""
    tracked = subprocess.run(
        ["git", "ls-files"], cwd=REPO, capture_output=True, text=True, check=False
    )
    if tracked.returncode != 0:
        pytest.skip("git ls-files unavailable")
    forbidden = (
        "records.jsonl",
        "traces.jsonl",
        "intents.jsonl",
        "partition_audit.json",
    )
    hits = [
        line
        for line in tracked.stdout.splitlines()
        if any(line.endswith(name) for name in forbidden)
    ]
    assert not hits, f"private dataset artifacts are tracked in git: {hits}"
