# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The agent can read back the images and audio it generated, and nothing more.

Regression: "create an image" then "upscale it", "edit it" or "what's in it"
was refused, because the picture sits in GAIA's own folder, which no approval
can open.
"""

from pathlib import Path

import pytest

import gaia.config
from gaia.security import PathValidator

IMAGE = Path("cache") / "sd" / "images" / "fox.png"
AUDIO = Path("cache") / "audio" / "generations" / "speech.wav"


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda _cls: tmp_path))
    monkeypatch.delenv("GAIA_ALLOWED_PATHS", raising=False)
    gaia_dir = tmp_path / ".gaia"
    monkeypatch.setattr(gaia.config, "GAIA_CONFIG_DIR", gaia_dir)
    for made in (IMAGE, AUDIO):
        (gaia_dir / made).parent.mkdir(parents=True)
        (gaia_dir / made).write_bytes(b"generated")
    return tmp_path


def _validator(allowed: Path) -> PathValidator:
    return PathValidator(allowed_paths=[str(allowed)])


@pytest.mark.parametrize("made", [IMAGE, AUDIO], ids=["image", "audio"])
def test_generated_media_is_readable_from_any_session(home, made):
    allowed, reason = _validator(home / "project").validate_read(
        str(home / ".gaia" / made), prompt_user=False
    )

    assert allowed, reason


@pytest.mark.parametrize("made", [IMAGE, AUDIO], ids=["image", "audio"])
def test_generated_media_is_not_writable_by_other_tools(home, made):
    assert not _validator(home / "project").is_path_allowed(
        str(home / ".gaia" / made), prompt_user=False
    )


def test_a_session_confined_by_the_environment_stays_confined(home, monkeypatch):
    confined = home / "project"
    confined.mkdir()
    monkeypatch.setenv("GAIA_ALLOWED_PATHS", str(confined))

    allowed, _ = _validator(confined).validate_read(
        str(home / ".gaia" / IMAGE), prompt_user=False
    )

    assert not allowed


@pytest.mark.parametrize(
    "neighbour",
    [".gaia/config.json", ".gaia/cache/sd/secret.txt", ".gaia/cache/audio/x.wav"],
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
    escape = home / ".gaia" / IMAGE.parent / ".." / ".." / ".." / "secret.txt"

    allowed, _ = _validator(home / "project").validate_read(
        str(escape), prompt_user=False
    )

    assert not allowed


def test_the_readable_folders_are_the_ones_the_app_plays_from(home):
    from gaia.ui.generated_media import media_dir

    assert gaia.config.generated_media_dirs() == (
        media_dir("image"),
        media_dir("audio"),
    )
