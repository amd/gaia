# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""What each suite of ``gaia eval retrieval --component code`` runs, pinned.

Every repository is pinned to a full commit, every sample to a seed, so two
runs of a suite on the same code score the same queries over the same trees.

Indexing runs at about 10 chunks/s on the CPU-pinned embedder, so a suite's
cost is its repositories' size. ``pr`` uses the three smallest SWE-bench
Verified repositories, all of their instances. ``nightly`` samples Verified
and SWE-rebench but skips any repository over :data:`NIGHTLY_MAX_CHUNKS`
(named in the results, never silently). ``scale`` indexes five repositories
from 1.7K to ~97K chunks and takes most of a working day.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Tuple

from gaia.eval.bench.swebench import SAMPLE_SEED
from gaia.eval.code_retrieval.repos import RepoPin

#: Retrieval depth: chunks fetched per query before ranking files and symbols.
SEARCH_DEPTH = 200
FILE_KS = (1, 5, 10)
SYMBOL_KS = (5, 10)
#: A nightly repository larger than this (chunks at its base commit) is skipped.
NIGHTLY_MAX_CHUNKS = 20_000

REQUESTS_MAIN = RepoPin(
    "psf__requests",
    "https://github.com/psf/requests.git",
    "611c6162cbc4ac2020a2f91c7cfa4f3abf9bbb60",
)
FLASK_MAIN = RepoPin(
    "pallets__flask",
    "https://github.com/pallets/flask.git",
    "d73fa1cdcbd8b1465c151db8924ba58b1dd14e35",
)

#: Smallest to largest. Chunk counts are from the pinned trees.
SCALE_REPOS: Tuple[RepoPin, ...] = (
    REQUESTS_MAIN,  # ~1.7K chunks
    RepoPin(
        "therock",
        "https://github.com/ROCm/TheRock",
        "9f4285b3a80d89d4da1b5a8e2f6a43c9a21a7047",
        cache_prefix="therock",
    ),  # ~15.6K chunks, the base of eval task tr-8319
    RepoPin(
        "llama.cpp",
        "https://github.com/ggml-org/llama.cpp",
        "a7b94df2c616bc1f62a73b964b4a71cb0dcc488e",
    ),  # ~46K chunks: C, C++, CUDA, Metal, Python, JS, Swift, Kotlin
    RepoPin(
        "django",
        "https://github.com/django/django.git",
        "a461af8ce48762d7ec602260aaff81014ddccbcb",
    ),  # ~93K chunks
    RepoPin(
        "gaia",
        "https://github.com/amd/gaia.git",
        "d1cc07723ac91dfed4229e1590836aa85b9ef30f",
    ),  # ~97K chunks
)

#: Typical code-search questions, for latency only (no gold answers).
LATENCY_QUERIES = (
    "where is the configuration file parsed",
    "retry failed HTTP requests with backoff",
    "how are command line arguments defined",
    "function that validates user input",
    "database connection setup",
    "parse a date string into a datetime",
    "log an error with a stack trace",
    "class that caches results in memory",
    "read a JSON file from disk",
    "authentication token refresh",
    "convert units between metric and imperial",
    "thread pool for concurrent work",
    "serialize an object to YAML",
    "compute a hash of file contents",
    "handle a keyboard interrupt cleanly",
    "load a plugin by name",
    "unit test fixture that creates a temporary directory",
    "matrix multiplication kernel",
    "escape HTML in a template",
    "where are environment variables read",
)


@dataclass(frozen=True)
class Incremental:
    repo: RepoPin
    #: Commits replayed, ending at ``repo.commit`` (the first is indexed in full).
    commits: int


@dataclass(frozen=True)
class Suite:
    name: str
    verified_ids: Tuple[str, ...] = ()
    #: Seeded Verified sample, drawn in seeded order and skipping oversize repos.
    verified_sample: int = 0
    rebench_splits: Tuple[str, ...] = ()
    rebench_sample: int = 0
    max_repo_chunks: Optional[int] = None
    seed: int = SAMPLE_SEED
    incremental: Optional[Incremental] = None
    #: The repository the robustness checks damage, and the subdirectory indexed.
    robustness: Optional[Tuple[RepoPin, str]] = None
    scale: Tuple[RepoPin, ...] = field(default_factory=tuple)


SUITES = {
    "pr": Suite(
        name="pr",
        verified_ids=(
            "pallets__flask-5014",
            "psf__requests-1142",
            "psf__requests-1724",
            "psf__requests-1766",
            "psf__requests-1921",
            "psf__requests-2317",
            "psf__requests-2931",
            "psf__requests-5414",
            "psf__requests-6028",
            "mwaskom__seaborn-3069",
            "mwaskom__seaborn-3187",
        ),
        incremental=Incremental(REQUESTS_MAIN, commits=11),
        robustness=(REQUESTS_MAIN, "src/requests"),
    ),
    "nightly": Suite(
        name="nightly",
        verified_sample=20,
        rebench_splits=("2026_01", "2026_02", "2026_03"),
        rebench_sample=6,
        max_repo_chunks=NIGHTLY_MAX_CHUNKS,
        incremental=Incremental(FLASK_MAIN, commits=31),
        robustness=(REQUESTS_MAIN, "src/requests"),
    ),
    "scale": Suite(name="scale", scale=SCALE_REPOS),
}
