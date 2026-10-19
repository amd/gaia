# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Two skill installs at once (CLI and Agent UI, say) must not corrupt each other."""

from __future__ import annotations

import os
import shutil
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from gaia.skills import install as install_mod
from gaia.skills.lock import SkillLock
from gaia.utils import file_lock as file_lock_mod
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
    real_replace = os.replace
    monkeypatch.setattr(file_lock_mod, "REPLACE_RETRY_SECONDS", 0.05)

    def target_in_use(src, dst):
        if str(src) == str(target):
            raise PermissionError(13, "Access is denied", str(src))
        return real_replace(src, dst)

    monkeypatch.setattr(os, "replace", target_in_use)
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
    monkeypatch.setattr(
        file_lock_mod.os,
        "access",
        lambda p, mode: False if Path(p) == tmp_path else os.path.exists(p),
    )
    assert SkillLock.load(tmp_path).get("demo").version == "1.0.0"


def test_an_unopenable_lock_file_in_a_writable_root_is_an_error(tmp_path, monkeypatch):
    """Only a read-only directory may be read unlocked; a bad lock file may not."""
    import errno

    from gaia.skills.lock import SkillLockBusyError, lock_path

    lock_path(tmp_path).write_text('{"schema_version": 1, "skills": {}}', "utf-8")
    real_open = os.open

    def lock_file_denied(name, flags, *args):
        if str(name).endswith(".lock"):
            raise PermissionError(errno.EACCES, "Permission denied", str(name))
        return real_open(name, flags, *args)

    monkeypatch.setattr(file_lock_mod.os, "open", lock_file_denied)
    with pytest.raises(SkillLockBusyError, match="Cannot create the lock file"):
        SkillLock.load(tmp_path)


def test_failed_undo_keeps_the_previous_copy(marketplace, tmp_path, monkeypatch):
    """Lock save fails, then a file held open blocks the undo: nothing is lost."""
    marketplace.publish(_write_source(tmp_path, "zeta", "1.0.0", extra_files=3))
    marketplace.install("zeta")
    target = marketplace.skills_root / "zeta"
    marketplace.publish(_write_source(tmp_path, "zeta", "1.1.0", extra_files=3))

    def disk_full(self, path=None):
        raise OSError("disk full")

    real_replace = os.replace
    real_rmtree = shutil.rmtree

    def new_copy_held_open(src, dst):
        if str(src) == str(target) and str(dst).endswith("zeta.failed"):
            raise PermissionError(13, "in use", str(target / "SKILL.md"))
        return real_replace(src, dst)

    def rmtree_partly(path, *a, **kw):
        # A loader holds SKILL.md: the rest of the tree goes, then it fails.
        if str(path) == str(target):
            for p in (target / "references").glob("*"):
                p.unlink()
            raise PermissionError(13, "in use", str(target / "SKILL.md"))
        return real_rmtree(path, *a, **kw)

    monkeypatch.setattr(file_lock_mod, "REPLACE_RETRY_SECONDS", 0.05)
    monkeypatch.setattr(SkillLock, "save", disk_full)
    monkeypatch.setattr(os, "replace", new_copy_held_open)
    monkeypatch.setattr(install_mod.shutil, "rmtree", rmtree_partly)
    with pytest.raises(OSError, match="disk full"):
        marketplace.install("zeta", force=True)
    monkeypatch.undo()

    kept = list((marketplace.skills_root / ".staging").rglob("zeta.replaced"))
    assert kept, "the previous copy was deleted"
    old = kept[0]
    assert "version: 1.0.0" in (old / "SKILL.md").read_text(encoding="utf-8")
    assert len(list((old / "references").glob("*"))) == 3


def test_capture_cleanup_does_not_wait_on_a_busy_lock_twice(tmp_path, monkeypatch):
    """The lock was busy, so the entry was never written: clean up and fail once."""
    import time

    from gaia.skills.capture import capture_skill
    from gaia.skills.lock import SkillLockBusyError, lock_path
    from tests.unit.skills_helpers import isolated_manager
    from tests.unit.test_skill_capture import _markdown

    monkeypatch.setattr(file_lock_mod, "LOCK_TIMEOUT_SECONDS", 1.0)
    manager = isolated_manager(tmp_path)
    root = tmp_path / "gaia-home" / "skills"
    root.mkdir(parents=True, exist_ok=True)
    taken, release = threading.Event(), threading.Event()

    def other_process():
        with file_lock_mod.file_lock(lock_path(root)):
            taken.set()
            release.wait(60)

    holder = threading.Thread(target=other_process)
    holder.start()
    taken.wait(10)
    started = time.monotonic()
    try:
        with pytest.raises(SkillLockBusyError):
            capture_skill(_markdown(), manager=manager)
    finally:
        release.set()
        holder.join()
    # One timeout, not a second wait in the cleanup path.
    assert time.monotonic() - started < 1.8
    assert not (root / "meeting-notes").exists()


def test_revoking_trust_for_a_vanished_entry_is_quiet(tmp_path):
    from gaia.skills.capture import SkillCaptureError, _set_code_trust

    root = tmp_path / "skills"
    root.mkdir()
    SkillLock.load(root).save()
    _set_code_trust(root, "gone", trusted=False, digest="")
    with pytest.raises(SkillCaptureError, match="removed by another process"):
        _set_code_trust(root, "gone", trusted=True, digest="sha256:x")
