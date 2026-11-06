# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Unit tests for ``util/verify_published_skills.py`` (post-publish hub check).

The hub is :class:`FakeHub`, filled by the real publish path, and the install is
the real installer, so a pass means the bytes a publish produced download,
checksum and verify.
"""

from __future__ import annotations

import re
import shutil
import sys
from pathlib import Path

import pytest

from gaia.skills.publish import publish_skill
from gaia.skills.signing import BUILTIN_TRUSTED_KEYS
from tests.unit.skills_helpers import fake_hub, make_key, write_audit_report

REPO_ROOT = Path(__file__).resolve().parents[2]
UTIL_DIR = REPO_ROOT / "util"
if str(UTIL_DIR) not in sys.path:
    sys.path.insert(0, str(UTIL_DIR))

import verify_published_skills as vps  # noqa: E402  (path set above)


@pytest.fixture
def hub(tmp_path):
    return fake_hub(tmp_path)


@pytest.fixture
def skills_dir(tmp_path):
    root = tmp_path / "hub-skills"
    root.mkdir()
    for name in ("summarize", "rss-digest"):
        shutil.copytree(REPO_ROOT / "hub" / "skills" / name, root / name)
    return root


def _publish(tmp_path, hub, source, key_name):
    keys_root = tmp_path / "keys-root"
    if not (keys_root / "keys" / f"{key_name}.key").exists():
        make_key(keys_root, name=key_name)
    publish_skill(
        source,
        token="test-token",
        hub_url=hub.BASE_URL,
        uploader=hub.accept_publish,
        keys_root=keys_root,
        key_name=key_name,
        audit_report=write_audit_report(tmp_path),
    )


@pytest.fixture
def amd_key(tmp_path, monkeypatch):
    """A key standing in for GAIA's built-in AMD skill key."""
    key = make_key(tmp_path / "keys-root", name="amd-skills")
    monkeypatch.setitem(
        BUILTIN_TRUSTED_KEYS, key.key_id, {"key_id": key.key_id, "role": "amd"}
    )
    return key


def _run(skills_dir, hub, tmp_path):
    return vps.run(
        skills_dir,
        hub.BASE_URL,
        fetcher=hub.fetcher,
        cache_path=tmp_path / "catalog-cache.json",
    )


def test_amd_signed_skills_at_the_repo_version_pass(tmp_path, hub, skills_dir, amd_key):
    for d in skills_dir.iterdir():
        _publish(tmp_path, hub, d, "amd-skills")

    checks = _run(skills_dir, hub, tmp_path)

    assert [(c.name, c.ok) for c in checks] == [
        ("rss-digest", True),
        ("summarize", True),
    ]


def test_a_skill_missing_from_the_hub_fails(tmp_path, hub, skills_dir, amd_key):
    _publish(tmp_path, hub, skills_dir / "summarize", "amd-skills")

    checks = {c.name: c for c in _run(skills_dir, hub, tmp_path)}

    assert not checks["rss-digest"].ok
    assert "not in the hub catalogue" in checks["rss-digest"].detail
    assert checks["summarize"].ok


def test_a_stale_hub_version_fails(tmp_path, hub, skills_dir, amd_key):
    for d in skills_dir.iterdir():
        _publish(tmp_path, hub, d, "amd-skills")
    skill_md = skills_dir / "summarize" / "SKILL.md"
    text = skill_md.read_text(encoding="utf-8")
    published = re.search(r"^version: (\S+)$", text, re.M).group(1)
    skill_md.write_text(
        text.replace(f"version: {published}", "version: 99.0.0"), "utf-8"
    )

    checks = {c.name: c for c in _run(skills_dir, hub, tmp_path)}

    assert not checks["summarize"].ok
    assert checks["summarize"].detail == f"hub lists {published}"


def test_a_skill_signed_by_another_key_fails(tmp_path, hub, skills_dir):
    for d in skills_dir.iterdir():
        _publish(tmp_path, hub, d, "someone-else")

    checks = _run(skills_dir, hub, tmp_path)

    assert not any(c.ok for c in checks)
    assert all("untrusted key" in c.detail for c in checks)


def test_an_unreachable_hub_fails_loudly(tmp_path, skills_dir):
    from gaia.skills.hub import SkillHubError

    def unreachable(_url):
        raise ConnectionError("hub is down")

    with pytest.raises(SkillHubError, match="Could not reach"):
        vps.run(
            skills_dir,
            "https://nope.test",
            fetcher=unreachable,
            cache_path=tmp_path / "absent-cache.json",
        )
