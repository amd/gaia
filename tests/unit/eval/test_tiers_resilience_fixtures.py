# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The resilience builder makes a journal repo git refuses to commit in."""

import importlib.util
import shutil
import subprocess
from pathlib import Path

import pytest

FIXTURE_DIR = (
    Path(__file__).resolve().parents[2] / "fixtures" / "gaia" / "tiers_resilience"
)

pytestmark = pytest.mark.skipif(not shutil.which("git"), reason="git is not on PATH")


def _load_builder():
    spec = importlib.util.spec_from_file_location(
        "tiers_resilience_build_fixtures", FIXTURE_DIR / "build_fixtures.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


builder = _load_builder()


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=repo, check=False, capture_output=True, text=True
    )


def test_the_journal_repo_has_a_blank_identity(tmp_path):
    (tmp_path / "journal").mkdir()
    if _git(tmp_path, "rev-parse", "--show-toplevel").returncode == 0:
        pytest.skip("the temp dir sits inside a git repository on this machine")

    journal = builder.build(tmp_path)

    assert _git(journal, "config", "--local", "user.name").stdout.strip() == ""
    assert _git(journal, "config", "--local", "user.email").stdout.strip() == ""


def test_an_enclosing_repo_is_named_in_the_refusal(tmp_path):
    outer = tmp_path / "home"
    dest = outer / "staged"
    (dest / "journal").mkdir(parents=True)
    assert _git(outer, "init", "--quiet").returncode == 0
    toplevel = _git(outer, "rev-parse", "--show-toplevel").stdout.strip()

    with pytest.raises(SystemExit, match="inside the git repository at") as exc:
        builder.build(dest)

    assert toplevel in str(exc.value)
    assert not (dest / "journal" / ".git").exists()
