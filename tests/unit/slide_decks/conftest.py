# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Shared fixtures for the slide-decks skill engine tests.

The engine (``hub/skills/slide-decks/slidedeck``) is plain Python with no GAIA
dependency, so these tests import it directly. All decks are written under
``tmp_path``; every outline here is invented.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

SKILL_DIR = Path(__file__).resolve().parents[3] / "hub" / "skills" / "slide-decks"
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))


@pytest.fixture
def out(tmp_path):
    """A factory for output paths under tmp_path."""

    def make(name: str = "deck.pptx") -> str:
        return str(tmp_path / name)

    return make


@pytest.fixture(scope="session")
def soffice():
    """LibreOffice's path, or skip: rendering is optional in the skill."""
    import shutil

    found = shutil.which("soffice") or shutil.which("libreoffice")
    if not found:
        for cand in (
            Path(r"C:\Program Files\LibreOffice\program\soffice.exe"),
            Path("/usr/bin/soffice"),
            Path("/usr/lib/libreoffice/program/soffice"),
            Path("/Applications/LibreOffice.app/Contents/MacOS/soffice"),
        ):
            if cand.is_file():
                found = str(cand)
                break
    if not found:
        pytest.skip("LibreOffice is not installed; the render check is optional")
    return found


@pytest.fixture
def renderer(soffice, tmp_path):
    """The same conversion the skill's tools.py runs through run_program."""
    import shutil
    import subprocess

    def render(deck: Path) -> Path:
        work = tmp_path / "render"
        work.mkdir(exist_ok=True)
        copy = work / "deck.pptx"
        shutil.copyfile(deck, copy)
        profile = work / "profile"
        profile.mkdir(exist_ok=True)
        subprocess.run(
            [
                soffice,
                f"-env:UserInstallation={profile.resolve().as_uri()}",
                "--headless",
                "--norestore",
                "--convert-to",
                "pdf",
                "--outdir",
                str(work),
                str(copy),
            ],
            capture_output=True,
            timeout=300,
            check=True,
        )
        return work / "deck.pdf"

    return render
