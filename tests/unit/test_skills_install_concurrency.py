# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Two skill installs at once (CLI and Agent UI, say) must not corrupt each other."""

from __future__ import annotations

import shutil
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from gaia.skills import install as install_mod
from gaia.skills.lock import SkillLock
from tests.unit.skills_helpers import make_marketplace


def _write_source(tmp_path, name, version="1.0.0", extra_files=0):
    directory = tmp_path / "src" / f"{name}-{version}" / name
    directory.mkdir(parents=True)
    (directory / "SKILL.md").write_text(
        "\n".join(
            [
                "---",
                f"name: {name}",
                f"description: The {name} skill, used to test concurrent installs.",
                f"version: {version}",
                "license: MIT",
                "metadata:",
                "  gaia:",
                "    security_tier: community",
                "---",
                "",
                f"# {name}",
                "",
                "1. Do the thing.",
            ]
        ),
        encoding="utf-8",
    )
    refs = directory / "references"
    for i in range(extra_files):
        refs.mkdir(exist_ok=True)
        (refs / f"note-{i}.md").write_text(f"{version} note {i}\n", encoding="utf-8")
    return directory


@pytest.fixture
def marketplace(tmp_path):
    market = make_marketplace(tmp_path)
    market.trust(market.keygen())
    return market


def test_concurrent_installs_of_different_skills_keep_both_lock_entries(
    marketplace, tmp_path, monkeypatch
):
    marketplace.publish(_write_source(tmp_path, "alpha"))
    marketplace.publish(_write_source(tmp_path, "beta"))

    both_downloading = threading.Barrier(2, timeout=30)
    real_download = install_mod.download_artifact

    def download_together(*args, **kwargs):
        # Each install has read the lock; neither has written it back yet.
        both_downloading.wait()
        return real_download(*args, **kwargs)

    monkeypatch.setattr(install_mod, "download_artifact", download_together)

    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(marketplace.install, ["alpha", "beta"]))

    assert {r.name for r in results} == {"alpha", "beta"}
    lock = SkillLock.load(marketplace.skills_root)
    assert set(lock.entries) == {"alpha", "beta"}


def test_force_reinstall_never_exposes_a_half_copied_skill(
    marketplace, tmp_path, monkeypatch
):
    marketplace.publish(_write_source(tmp_path, "gamma", "1.0.0", extra_files=5))
    marketplace.install("gamma")
    target = marketplace.skills_root / "gamma"
    installed = sorted(p.relative_to(target).as_posix() for p in target.rglob("*"))
    marketplace.publish(_write_source(tmp_path, "gamma", "1.1.0", extra_files=5))

    seen = []
    real_copytree = shutil.copytree

    def watching_copytree(src, dst, *args, **kwargs):
        if args or "copy_function" in kwargs:
            return real_copytree(src, dst, *args, **kwargs)

        def copy_and_look(s, d, *a, **kw):
            # What a skill loader in another process would find right now.
            seen.append(
                sorted(p.relative_to(target).as_posix() for p in target.rglob("*"))
                if target.exists()
                else None
            )
            return shutil.copy2(s, d, *a, **kw)

        kwargs["copy_function"] = copy_and_look
        return real_copytree(src, dst, *args, **kwargs)

    monkeypatch.setattr(install_mod.shutil, "copytree", watching_copytree)
    result = marketplace.install("gamma", force=True)

    assert result.version == "1.1.0"
    assert seen, "the install copied no files"
    assert all(s == installed for s in seen), (
        "a reader saw a partial skill directory mid-install: "
        f"{next(s for s in seen if s != installed)}"
    )
    assert "version: 1.1.0" in (target / "SKILL.md").read_text(encoding="utf-8")
    assert not list((marketplace.skills_root / ".staging").glob("*"))


def test_failed_lock_save_puts_the_previous_copy_back(
    marketplace, tmp_path, monkeypatch
):
    """Files and lock must agree: a reinstall whose lock save fails is undone."""
    marketplace.publish(_write_source(tmp_path, "delta", "1.0.0"))
    marketplace.install("delta")
    target = marketplace.skills_root / "delta"
    marketplace.publish(_write_source(tmp_path, "delta", "1.1.0"))

    def disk_full(self, path=None):
        raise OSError("disk full")

    monkeypatch.setattr(SkillLock, "save", disk_full)
    with pytest.raises(OSError, match="disk full"):
        marketplace.install("delta", force=True)
    monkeypatch.undo()

    assert "version: 1.0.0" in (target / "SKILL.md").read_text(encoding="utf-8")
    assert SkillLock.load(marketplace.skills_root).get("delta").version == "1.0.0"
    assert not list((marketplace.skills_root / ".staging").glob("*"))


def test_failed_swap_names_what_to_close(marketplace, tmp_path, monkeypatch):
    marketplace.publish(_write_source(tmp_path, "epsilon", "1.0.0"))
    marketplace.install("epsilon")
    target = marketplace.skills_root / "epsilon"
    marketplace.publish(_write_source(tmp_path, "epsilon", "1.1.0"))
    real_replace = install_mod.os.replace

    def target_in_use(src, dst):
        if str(src) == str(target):
            raise PermissionError(13, "Access is denied", str(src))
        return real_replace(src, dst)

    monkeypatch.setattr(install_mod.os, "replace", target_in_use)
    with pytest.raises(install_mod.SkillInstallError, match="Close anything"):
        marketplace.install("epsilon", force=True)
    monkeypatch.undo()
    assert "version: 1.0.0" in (target / "SKILL.md").read_text(encoding="utf-8")


def test_lock_reads_from_a_skills_root_it_cannot_write(tmp_path, monkeypatch):
    """A read must not need to create the lock file in a read-only root."""
    import errno
    import json
    import os

    from gaia.skills.lock import LOCK_SCHEMA_VERSION, lock_path
    from gaia.utils import file_lock as file_lock_mod

    lock_path(tmp_path).write_text(
        json.dumps(
            {
                "schema_version": LOCK_SCHEMA_VERSION,
                "skills": {"demo": {"name": "demo", "version": "1.0.0"}},
            }
        ),
        encoding="utf-8",
    )
    real_open = os.open

    def read_only_dir(name, flags, *args):
        if str(name).endswith(".lock"):
            raise PermissionError(errno.EACCES, "Permission denied", str(name))
        return real_open(name, flags, *args)

    monkeypatch.setattr(file_lock_mod.os, "open", read_only_dir)
    assert SkillLock.load(tmp_path).get("demo").version == "1.0.0"
