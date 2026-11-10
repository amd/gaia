# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The agent can read back the transcripts it wrote, and nothing more.

Regression: in the Agent UI, with no full access, indexing and reading a
transcript in ~/.gaia/transcripts was refused, so summaries and follow-up
questions failed even though the transcript had just been written.
"""

from pathlib import Path

import pytest

from gaia.rag.sdk import RAGSDK, RAGConfig
from gaia.security import PathValidator


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda _cls: tmp_path))
    monkeypatch.delenv("GAIA_ALLOWED_PATHS", raising=False)
    folder = tmp_path / ".gaia" / "transcripts"
    folder.mkdir(parents=True)
    (folder / "talk.transcript.md").write_text("[00:01] Hello.", encoding="utf-8")
    return tmp_path


def _validator(allowed: Path) -> PathValidator:
    return PathValidator(allowed_paths=[str(allowed)])


def test_a_transcript_is_readable_from_any_session(home):
    validator = _validator(home / "project")

    allowed, _ = validator.validate_read(
        str(home / ".gaia" / "transcripts" / "talk.transcript.md"), prompt_user=False
    )

    assert allowed


def test_a_session_confined_by_the_environment_stays_confined(home, monkeypatch):
    confined = home / "project"
    confined.mkdir()
    monkeypatch.setenv("GAIA_ALLOWED_PATHS", str(confined))

    allowed, _ = _validator(confined).validate_read(
        str(home / ".gaia" / "transcripts" / "talk.transcript.md"), prompt_user=False
    )

    assert not allowed


def test_a_transcript_is_not_writable_by_other_tools(home):
    validator = _validator(home / "project")

    assert not validator.is_path_allowed(
        str(home / ".gaia" / "transcripts" / "talk.transcript.md"), prompt_user=False
    )


@pytest.mark.parametrize(
    "neighbour", [".gaia/config.json", ".gaia/memory.db", ".gaia/transcripts-old/x.txt"]
)
def test_the_rest_of_gaias_state_stays_closed(home, neighbour):
    target = home / neighbour
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("private", encoding="utf-8")

    allowed, _ = _validator(home / "project").validate_read(
        str(target), prompt_user=False
    )

    assert not allowed


def test_a_way_out_of_the_folder_is_refused(home):
    (home / ".gaia" / "secret.txt").write_text("private", encoding="utf-8")
    escape = home / ".gaia" / "transcripts" / ".." / "secret.txt"

    allowed, _ = _validator(home / "project").validate_read(
        str(escape), prompt_user=False
    )

    assert not allowed


def test_rag_can_open_a_transcript_for_reading(home):
    rag = RAGSDK.__new__(RAGSDK)
    rag.path_validator = _validator(home / "project")
    rag.config = RAGConfig()
    path = home / ".gaia" / "transcripts" / "talk.transcript.md"

    with rag._safe_open(str(path), "rb") as handle:
        assert b"Hello" in handle.read()


def test_rag_still_cannot_write_there(home):
    rag = RAGSDK.__new__(RAGSDK)
    rag.path_validator = _validator(home / "project")
    rag.config = RAGConfig()

    with pytest.raises(PermissionError):
        rag._safe_open(str(home / ".gaia" / "transcripts" / "new.txt"), "w")
