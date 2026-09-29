# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The doc-version gate must catch stale PINS without rewriting version FLOORS.

``util/check_doc_versions.py`` keeps hardcoded Lemonade versions in the docs in
step with ``LEMONADE_VERSION``. Two classes of reference look identical in prose
and must not be treated the same:

* a **pin** — "GAIA pins 2026.39.1" — has to move on every bump, and
* a **floor** — "requires Lemonade 11.8.1 or later" — names the release a feature
  first shipped in and must never move. Bumping one turns a true statement false.

The ``v`` prefix is what separates them, so a bare version is only checked in the
files that opt in. That opt-in exists because applying the bare pattern repo-wide
matches six things it must not touch for every two it should — including the
``127.0.0`` inside an IP address. These tests pin that split down.
"""

import io
import os
import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "util"))

# check_doc_versions rebinds sys.stdout at import time to force UTF-8 on the
# Windows console: ``sys.stdout = io.TextIOWrapper(sys.stdout.buffer, ...)``. Under
# pytest that wraps the capture object's buffer, and when the wrapper is collected
# it CLOSES that buffer — every later write then raises "I/O operation on closed
# file" and the whole session dies. Restoring sys.stdout afterwards is too late, so
# hand the import a throwaway stream to wrap instead.
_stdout = sys.stdout
_devnull = open(os.devnull, "wb")  # noqa: SIM115 - lives for the module's lifetime
sys.stdout = io.TextIOWrapper(_devnull, encoding="utf-8")
try:
    from check_doc_versions import (  # noqa: E402
        BARE_VERSION_PIN_FILES,
        SCAN_PATHS,
        build_bare_pin_pattern,
        build_lemonade_patterns,
    )
finally:
    sys.stdout = _stdout

PIN = "2026.39.1"


def _bare():
    pattern, _desc = build_bare_pin_pattern()
    return re.compile(pattern)


# -- the bare pattern only fires on real pins -------------------------------


@pytest.mark.parametrize(
    "line",
    [
        "✅ Embedded Lemonade 11.8.1 running on http://localhost:62232/api/v1",
        "`lemonade-server` CLI in 10.7; GAIA pins 11.8.1, where Windows runs",
    ],
)
def test_bare_pattern_catches_a_stale_pin(line):
    match = _bare().search(line)
    assert match is not None, f"stale pin not caught: {line!r}"


@pytest.mark.parametrize(
    "line",
    [
        # Floors — never bumped. Each shipped with the feature that needs them.
        "**AMD LLM Gateway** through Lemonade 11.8.1+. Paste a key into",
        "Cloud routing requires Lemonade **11.8.1 or later** and is experimental.",
        "| Local model server | Lemonade **10.2.0** or newer, running |",
        # A YAML example of the per-agent floor, not this repo's pin.
        'min_lemonade_version: "10.2.0"   # optional; SemVer. The backend floor',
        # A recorded observation in an eval scorecard, not a pin to move.
        "lemonade_version: 10.8.0",
        # Not a version at all — the regex must not read an IP as one.
        "✗ The local Lemonade Server is not reachable at http://127.0.0.1:9099/api/v1.",
    ],
)
def test_bare_pattern_leaves_floors_and_non_pins_alone(line):
    assert _bare().search(line) is None, f"wrongly matched: {line!r}"


def test_bare_pattern_defers_v_prefixed_versions_to_the_v_pattern():
    """``v``-prefixed pins are checked everywhere, so the bare one must skip them."""
    line = "Install Lemonade Server v11.8.1:"
    assert _bare().search(line) is None
    v_pattern = next(
        re.compile(pat)
        for pat, desc in build_lemonade_patterns(PIN)
        if "reference in text" in desc
    )
    assert v_pattern.search(line) is not None


# -- configuration stays honest ---------------------------------------------


def test_every_opt_in_file_exists():
    """A renamed file must not silently stop being checked."""
    for rel in sorted(BARE_VERSION_PIN_FILES):
        assert (REPO_ROOT / rel).is_file(), f"{rel} is listed but does not exist"


def test_opt_in_files_are_inside_the_scanned_paths():
    """Opting a file in does nothing if no SCAN_PATHS entry reaches it."""
    scanned = _scanned_files()
    for rel in sorted(BARE_VERSION_PIN_FILES):
        assert (REPO_ROOT / rel).resolve() in scanned, f"{rel} is never scanned"


def _scanned_files():
    return {
        path.resolve()
        for base, glob in SCAN_PATHS
        if base.exists()
        for path in base.glob(glob)
    }


@pytest.mark.parametrize(
    "path",
    [
        # Each of these carried a stale Lemonade pin through the v2026.39.1
        # bump with no gate able to see it.
        ".claude/agents/lemonade-specialist.md",
        "docs/reference/cli.mdx",
        "tui/internal/ui/preflight/lemonade.go",
    ],
)
def test_the_files_that_hid_stale_pins_are_reachable(path):
    """Assert the FILE is scanned, not just that its directory is listed.

    An earlier version of this test checked only that ``tui`` appeared in
    SCAN_PATHS. That passed while the glob was ``**/*.md`` and the stale pin sat
    in a ``.go`` file — green, and measuring nothing.
    """
    assert (REPO_ROOT / path).resolve() in _scanned_files(), f"{path} is not scanned"


def test_source_comments_are_left_out_on_purpose():
    """src/ holds history and floors, not pins — scanning it only finds those."""
    assert not any(
        base == REPO_ROOT / "src" for base, _glob in SCAN_PATHS
    ), "src/ is scanned; see the note in SCAN_PATHS for why that flags only floors"


# -- a "+" suffix is a floor, with or without the v -------------------------


def _v_pattern():
    return re.compile(
        next(
            pat
            for pat, desc in build_lemonade_patterns(PIN)
            if "reference in text" in desc
        )
    )


def test_a_v_prefixed_floor_is_left_alone():
    """A trailing "+" means "that release or newer", so bumping it makes the doc lie.

    The gate used to match this, and marched docs/guides/npu.mdx forward until
    it claimed NPU support needs the newest Lemonade. The npu profile's real
    floor is 10.2.0.
    """
    line = "- **Software:** [Lemonade Server](https://lemonade-server.ai) v10.2.0+"
    assert _v_pattern().search(line) is None


def test_a_v_prefixed_pin_is_still_caught():
    """Only the "+" makes it a floor; a bare v-version is the pin."""
    line = "Install Lemonade Server v11.8.1:"
    match = _v_pattern().search(line)
    assert match is not None and match.group("version") == "11.8.1"
