# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A hub-supplied artifact filename must never steer the write it names.

``install_skill`` downloads to ``workdir / artifact.filename`` *before* the
signature, tier, ``--allow-experimental`` and dangerous-grant gates run, and the
SHA-256 in the same manifest attests to the bytes, never to the destination. A
manifest from an attacker-controlled origin — a compromised bucket, or
``GAIA_HUB_URL`` pointed elsewhere — therefore got a pre-consent arbitrary file
write out of a field nobody validated.
"""

import pytest

from gaia.hub.installer import InstallError, _sanitize_artifact_filename
from gaia.skills.errors import SkillValidationError
from gaia.skills.hub import RemoteArtifact, RemoteSkill, download_artifact
from gaia.skills.naming import artifact_path
from gaia.utils.paths import UnsafePathSegment, safe_path_segment

#: The four shapes the review named, plus the Windows drive-relative form that a
#: separator scan alone lets through.
HOSTILE_NAMES = [
    ".",
    "..",
    "../x",
    "/etc/cron.d/gaia",
    "..\\x",
    "sub/dir.zip",
    "C:evil.zip",
    "\\\\server\\share\\evil.zip",
    "NUL",
    "",
]


@pytest.mark.parametrize("hostile", HOSTILE_NAMES)
def test_safe_path_segment_refuses(hostile):
    with pytest.raises(UnsafePathSegment):
        safe_path_segment(hostile, what="artifact filename")


@pytest.mark.parametrize("ordinary", ["web-research-1.2.0.zip", "a.zip", "x.tar.gz"])
def test_safe_path_segment_passes_an_ordinary_bundle_name(ordinary):
    assert safe_path_segment(ordinary) == ordinary


def test_the_error_says_what_to_do():
    with pytest.raises(UnsafePathSegment) as exc:
        safe_path_segment("../x", what="artifact filename", origin="hub manifest")

    message = str(exc.value)
    assert "artifact filename" in message
    assert "hub manifest" in message
    assert "Nothing was written" in message


@pytest.mark.parametrize("hostile", HOSTILE_NAMES)
def test_the_manifest_parser_refuses_a_hostile_filename(hostile):
    """Refused at the parse boundary, so nothing downstream ever sees it."""
    remote = RemoteSkill(
        name="victim",
        latest_version="1.0.0",
        versions={"1.0.0": {"artifact": {"filename": hostile, "sha256": "ab" * 32}}},
    )

    with pytest.raises(SkillValidationError):
        remote.artifact("1.0.0")


def test_the_manifest_parser_accepts_a_published_bundle_name():
    remote = RemoteSkill(
        name="web-research",
        latest_version="1.2.0",
        versions={
            "1.2.0": {
                "artifact": {"filename": "web-research-1.2.0.zip", "sha256": "ab" * 32}
            }
        },
    )

    assert remote.artifact("1.2.0").filename == "web-research-1.2.0.zip"


@pytest.mark.parametrize("hostile", [".", "..", "../x", "/etc/passwd"])
def test_artifact_path_refuses_the_join(tmp_path, hostile):
    with pytest.raises(SkillValidationError):
        artifact_path(tmp_path / "work", hostile)


def test_download_writes_nothing_outside_the_workdir(tmp_path):
    """The probe from the review: '../outside-marker.zip' must not land."""
    workdir = tmp_path / "work"
    workdir.mkdir()
    artifact = RemoteArtifact(
        filename="ok.zip", sha256="ab" * 32, size_bytes=0, path="", content_type=""
    )
    object.__setattr__(artifact, "filename", "../outside-marker.zip")

    with pytest.raises(SkillValidationError):
        download_artifact(
            "victim",
            "1.0.0",
            artifact,
            workdir / ".." / "outside-marker.zip",
            fetcher=lambda url: b"payload",
        )

    assert not (tmp_path / "outside-marker.zip").exists()


@pytest.mark.parametrize("hostile", HOSTILE_NAMES)
def test_the_agent_hub_installer_shares_the_same_rule(hostile):
    """One helper, not two that drift — the agent hub joins the same way."""
    with pytest.raises(InstallError):
        _sanitize_artifact_filename(hostile, "some-agent")


def test_the_agent_hub_installer_accepts_a_wheel_name():
    _sanitize_artifact_filename("gaia_agent-0.1.0-py3-none-any.whl", "some-agent")
