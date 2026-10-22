# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Undo of a turn's file changes (#5225).

Covers the snapshot store end to end: edited, created and deleted files come
back byte for byte, a restore survives a process restart, a file changed after
the turn is never silently overwritten, and a restore that fails part-way puts
everything back. The tool tests drive the real write/edit tools, not a mock.
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from gaia.agents.base import file_snapshots as fs
from gaia.agents.base.tools import _TOOL_REGISTRY
from gaia.security import PathValidator

SID = "session-1"


@pytest.fixture
def root(tmp_path):
    return tmp_path / "snapshots"


@pytest.fixture
def work(tmp_path):
    folder = tmp_path / "work"
    folder.mkdir()
    return folder


def _record(root, session=SID):
    return fs.recording_turn(session, root=root)


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


class TestRestore:
    def test_edited_file_is_restored_exactly(self, root, work):
        target = work / "notes.txt"
        original = b"line one\r\nline two\n\x00binary tail"
        target.write_bytes(original)
        with _record(root) as rec:
            fs.before_change(target, "edit_file")
            target.write_bytes(b"rewritten")
        result = fs.restore_turn(SID, rec.turn_id, root=root)
        assert target.read_bytes() == original
        assert result["restored"] == [str(target)]

    def test_created_file_and_its_new_folders_are_removed(self, root, work):
        target = work / "new" / "deeper" / "made.md"
        with _record(root) as rec:
            fs.before_change(target, "write_file")
            target.parent.mkdir(parents=True)
            target.write_text("hello", encoding="utf-8")
        result = fs.restore_turn(SID, rec.turn_id, root=root)
        assert not target.exists()
        assert not (work / "new").exists()
        assert result["removed"] == [str(target)]

    def test_folder_holding_other_files_is_kept_and_reported(self, root, work):
        target = work / "new" / "made.md"
        with _record(root) as rec:
            fs.before_change(target)
            target.parent.mkdir()
            target.write_text("hello", encoding="utf-8")
        (work / "new" / "user.txt").write_text("mine", encoding="utf-8")
        result = fs.restore_turn(SID, rec.turn_id, root=root)
        assert (work / "new" / "user.txt").read_text(encoding="utf-8") == "mine"
        assert result["kept_folders"] == [str(work / "new")]

    def test_deleted_file_is_recreated(self, root, work):
        target = work / "gone.bin"
        original = os.urandom(4096)
        target.write_bytes(original)
        with _record(root) as rec:
            fs.before_change(target, "delete")
            target.unlink()
        fs.restore_turn(SID, rec.turn_id, root=root)
        assert target.read_bytes() == original

    def test_first_state_in_the_turn_wins(self, root, work):
        target = work / "a.txt"
        target.write_text("v0", encoding="utf-8")
        with _record(root) as rec:
            for version in ("v1", "v2", "v3"):
                fs.before_change(target)
                target.write_text(version, encoding="utf-8")
        fs.restore_turn(SID, rec.turn_id, root=root)
        assert target.read_text(encoding="utf-8") == "v0"

    def test_restore_after_restart_in_a_new_process(self, root, work, tmp_path):
        target = work / "kept.txt"
        target.write_bytes(b"before restart")
        with _record(root) as rec:
            fs.before_change(target)
            target.write_bytes(b"after")
        script = (
            "import json, sys\n"
            "from pathlib import Path\n"
            "from gaia.agents.base import file_snapshots as fs\n"
            "print(json.dumps(fs.restore_turn(sys.argv[1], sys.argv[2], "
            "root=Path(sys.argv[3]))))\n"
        )
        src = str(Path(fs.__file__).resolve().parents[3])
        env = {**os.environ, "PYTHONPATH": src}
        out = subprocess.run(
            [sys.executable, "-c", script, SID, rec.turn_id, str(root)],
            capture_output=True,
            text=True,
            env=env,
            check=True,
        )
        assert json.loads(out.stdout.strip().splitlines()[-1])["restored"] == [
            str(target)
        ]
        assert target.read_bytes() == b"before restart"


class TestRefusals:
    def test_file_changed_after_the_turn_is_a_conflict(self, root, work):
        target = work / "a.txt"
        other = work / "b.txt"
        target.write_text("v0", encoding="utf-8")
        other.write_text("o0", encoding="utf-8")
        with _record(root) as rec:
            for path in (target, other):
                fs.before_change(path)
                path.write_text("turn", encoding="utf-8")
        other.write_text("user edit", encoding="utf-8")
        with pytest.raises(fs.RestoreConflict) as err:
            fs.restore_turn(SID, rec.turn_id, root=root)
        assert err.value.conflicts == [
            {"path": str(other), "reason": "changed after this turn"}
        ]
        # All or nothing: the file without a conflict was not touched either.
        assert target.read_text(encoding="utf-8") == "turn"
        assert other.read_text(encoding="utf-8") == "user edit"

    def test_force_overwrites_a_later_change(self, root, work):
        target = work / "a.txt"
        target.write_text("v0", encoding="utf-8")
        with _record(root) as rec:
            fs.before_change(target)
            target.write_text("turn", encoding="utf-8")
        target.write_text("user edit", encoding="utf-8")
        fs.restore_turn(SID, rec.turn_id, force=True, root=root)
        assert target.read_text(encoding="utf-8") == "v0"

    def test_second_undo_is_refused(self, root, work):
        target = work / "a.txt"
        target.write_text("v0", encoding="utf-8")
        with _record(root) as rec:
            fs.before_change(target)
            target.write_text("turn", encoding="utf-8")
        fs.restore_turn(SID, rec.turn_id, root=root)
        with pytest.raises(fs.SnapshotError, match="already undone"):
            fs.restore_turn(SID, rec.turn_id, root=root)

    def test_damaged_copy_restores_nothing(self, root, work):
        target = work / "a.txt"
        target.write_text("v0", encoding="utf-8")
        with _record(root) as rec:
            fs.before_change(target)
            target.write_text("turn", encoding="utf-8")
        (rec.dir / "blobs" / "0000.before").write_bytes(b"corrupt")
        with pytest.raises(fs.SnapshotError, match="damaged"):
            fs.restore_turn(SID, rec.turn_id, root=root)
        assert target.read_text(encoding="utf-8") == "turn"

    def test_unfinished_turn_is_refused(self, root, work):
        target = work / "a.txt"
        target.write_text("v0", encoding="utf-8")
        rec = fs.TurnRecorder(SID, "crashed", root)
        rec.capture(target)
        target.write_text("half", encoding="utf-8")
        with pytest.raises(fs.SnapshotError, match="did not finish"):
            fs.restore_turn(SID, "crashed", root=root)

    def test_unknown_turn_is_not_found(self, root):
        with pytest.raises(fs.SnapshotNotFound):
            fs.restore_turn(SID, "nope", root=root)

    @pytest.mark.parametrize("bad", ["..", "../x", "a/b", "a\\b", ""])
    def test_ids_cannot_escape_the_store(self, root, bad):
        with pytest.raises(fs.SnapshotError):
            fs.restore_turn(SID, bad, root=root)

    def test_failure_part_way_puts_every_file_back(self, root, work, monkeypatch):
        first, second = work / "1.txt", work / "2.txt"
        for path in (first, second):
            path.write_text("v0", encoding="utf-8")
        with _record(root) as rec:
            for path in (first, second):
                fs.before_change(path)
                path.write_text("turn", encoding="utf-8")

        real = fs._restore_file
        calls = []

        def flaky(target, source, mode):
            calls.append(target)
            if len(calls) == 2:
                raise PermissionError("locked by another program")
            real(target, source, mode)

        monkeypatch.setattr(fs, "_restore_file", flaky)
        with pytest.raises(fs.SnapshotError, match="nothing changed"):
            fs.restore_turn(SID, rec.turn_id, root=root)
        assert first.read_text(encoding="utf-8") == "turn"
        assert second.read_text(encoding="utf-8") == "turn"
        manifest = json.loads((rec.dir / fs.MANIFEST).read_text(encoding="utf-8"))
        assert manifest["restored_at"] is None

    def test_mismatch_after_writing_rolls_back(self, root, work, monkeypatch):
        first, second = work / "1.txt", work / "2.txt"
        for path in (first, second):
            path.write_text("v0", encoding="utf-8")
        with _record(root) as rec:
            for path in (first, second):
                fs.before_change(path)
                path.write_text("turn", encoding="utf-8")

        real = fs._restore_file

        def corrupting(target, source, mode):
            real(target, source, mode)
            if target == second and source.parent.name == "blobs":
                target.write_text("garbled", encoding="utf-8")

        monkeypatch.setattr(fs, "_restore_file", corrupting)
        with pytest.raises(fs.SnapshotError, match="nothing changed"):
            fs.restore_turn(SID, rec.turn_id, root=root)
        assert first.read_text(encoding="utf-8") == "turn"
        assert second.read_text(encoding="utf-8") == "turn"
        assert not (rec.dir / "rollback").exists()

    def test_copy_failure_blocks_the_change(self, root, work, monkeypatch):
        target = work / "a.txt"
        target.write_text("v0", encoding="utf-8")

        def no_space(*_a, **_k):
            raise OSError(28, "No space left on device")

        monkeypatch.setattr(fs.shutil, "copyfile", no_space)
        with _record(root):
            with pytest.raises(fs.SnapshotError, match="left unchanged"):
                fs.before_change(target)


class TestListingAndReview:
    def test_turn_without_changes_leaves_nothing(self, root):
        with _record(root):
            pass
        assert fs.list_turns(SID, root=root) == []

    def test_listing_names_each_change(self, root, work):
        edited, created, deleted = work / "e.txt", work / "c.txt", work / "d.txt"
        edited.write_text("e0", encoding="utf-8")
        deleted.write_text("d0", encoding="utf-8")
        with _record(root) as rec:
            fs.before_change(edited)
            edited.write_text("e1", encoding="utf-8")
            fs.before_change(created)
            created.write_text("c1", encoding="utf-8")
            fs.before_change(deleted)
            deleted.unlink()
        rec.link_message(42)
        (turn,) = fs.list_turns(SID, root=root)
        assert turn["message_id"] == 42
        assert {f["path"]: f["change"] for f in turn["files"]} == {
            str(edited): "edited",
            str(created): "created",
            str(deleted): "deleted",
        }
        assert turn["incomplete"] is False

    def test_diff_shows_the_turns_change(self, root, work):
        target = work / "a.txt"
        target.write_bytes(b"one\ntwo\n")
        with _record(root) as rec:
            fs.before_change(target)
            target.write_bytes(b"one\nTWO\n")
        target.write_bytes(b"later edit\n")
        (item,) = fs.diff_turn(SID, rec.turn_id, root=root)
        assert item["change"] == "edited"
        assert "-two\n" in item["unified"] and "+TWO\n" in item["unified"]
        assert "later edit" not in item["unified"]

    def test_binary_diff_is_flagged_not_rendered(self, root, work):
        target = work / "a.bin"
        target.write_bytes(b"\x00\x01")
        with _record(root) as rec:
            fs.before_change(target)
            target.write_bytes(b"\x00\x02\x03")
        (item,) = fs.diff_turn(SID, rec.turn_id, root=root)
        assert item["binary"] is True
        assert (item["before_size"], item["after_size"]) == (2, 3)

    def test_untracked_work_marks_the_turn_incomplete(self, root):
        with _record(root):
            fs.note_untracked("shell", "npm run build")
        (turn,) = fs.list_turns(SID, root=root)
        assert turn["incomplete"] is True
        assert turn["untracked"][0]["detail"] == "npm run build"

    def test_read_only_work_alone_records_no_turn(self, root):
        with _record(root):
            fs.note_untracked("run_python", "count errors", may_write=False)
        assert fs.list_turns(SID, root=root) == []
        assert fs.session_changes(SID, root=root)["incomplete"] is False

    def test_only_work_that_may_write_marks_the_turn(self, root, work):
        target = work / "a.txt"
        target.write_text("old")
        with _record(root):
            fs.before_change(target)
            target.write_text("new")
            fs.note_untracked("run_python", "count errors", may_write=False)
        (turn,) = fs.list_turns(SID, root=root)
        assert turn["incomplete"] is False
        assert turn["untracked"] == []
        # Kept in the record, just not reported as a gap in undo.
        manifest = json.loads(
            (root / SID / turn["turn_id"] / fs.MANIFEST).read_text(encoding="utf-8")
        )
        assert manifest["untracked"][0]["may_write"] is False

        with _record(root) as rec:
            fs.note_untracked("run_python", "count", may_write=False)
            fs.note_untracked("run_python", "save totals", ["C:\t.csv"])
        (turn,) = [
            t for t in fs.list_turns(SID, root=root) if t["turn_id"] == rec.turn_id
        ]
        assert turn["incomplete"] is True
        assert [u["detail"] for u in turn["untracked"]] == ["save totals"]
        changes = fs.session_changes(SID, root=root)
        assert changes["incomplete"] is True
        assert [u["detail"] for u in changes["untracked"]] == ["save totals"]

    def test_entries_saved_before_may_write_existed_count_as_writes(self, root):
        with _record(root) as rec:
            fs.note_untracked("shell", "npm run build")
        path = root / SID / rec.turn_id / fs.MANIFEST
        manifest = json.loads(path.read_text(encoding="utf-8"))
        del manifest["untracked"][0]["may_write"]
        path.write_text(json.dumps(manifest), encoding="utf-8")
        (turn,) = fs.list_turns(SID, root=root)
        assert turn["incomplete"] is True

    def test_read_only_work_after_the_turn_ended_is_not_noted(self, root, work):
        target = work / "a.txt"
        target.write_text("old")
        with _record(root) as rec:
            fs.before_change(target)
            target.write_text("new")
        rec.note_untracked("run_python", "late read", may_write=False)
        (turn,) = fs.list_turns(SID, root=root)
        assert turn["incomplete"] is False

    def test_outside_a_turn_nothing_is_recorded(self, root, work):
        fs.before_change(work / "x.txt")
        fs.note_untracked("shell", "rm x")
        assert not root.exists()


class TestRetention:
    def _turn(self, root, work, name, size=10):
        target = work / name
        target.write_bytes(b"x" * size)
        with _record(root) as rec:
            fs.before_change(target)
            target.write_bytes(b"y" * size)
        return rec

    def test_expired_turns_are_removed(self, root, work):
        old = self._turn(root, work, "old.txt")
        new = self._turn(root, work, "new.txt")
        stale = time.time() - 10 * 86400
        os.utime(old.dir / fs.MANIFEST, (stale, stale))
        fs.prune(retention_days=7, max_bytes=10**9, root=root)
        assert not old.dir.exists()
        assert new.dir.exists()

    def test_size_limit_drops_oldest_but_keeps_newest(self, root, work):
        first = self._turn(root, work, "1.txt", size=4000)
        time.sleep(0.02)
        second = self._turn(root, work, "2.txt", size=4000)
        fs.prune(retention_days=7, max_bytes=1, root=root, keep=second.dir)
        assert not first.dir.exists()
        assert second.dir.exists()

    def test_usage_counts_turns_and_bytes(self, root, work):
        self._turn(root, work, "1.txt", size=1000)
        used = fs.usage(root=root)
        assert used["turns"] == 1
        assert used["bytes"] >= 2000


# ---------------------------------------------------------------------------
# The real tools record before they write
# ---------------------------------------------------------------------------

TOOLS = [
    ("gaia.agents.tools.file_io_tools", "FileIOToolsMixin", "register_file_io_tools"),
    (
        "gaia.agents.tools.file_tools",
        "FileSearchToolsMixin",
        "register_file_search_tools",
    ),
]


@pytest.fixture(params=TOOLS, ids=["file_io_tools", "file_tools"])
def tools(request, tmp_path, mock_home):
    import importlib

    module_name, class_name, registrar = request.param
    mixin = getattr(importlib.import_module(module_name), class_name)()
    mixin.path_validator = PathValidator()
    mixin.path_validator.allowed_paths.add(tmp_path.resolve())
    saved = dict(_TOOL_REGISTRY)
    try:
        getattr(mixin, registrar)()
        yield {
            name: _TOOL_REGISTRY[name]["function"]
            for name in ("read_file", "write_file", "edit_file")
        }
    finally:
        _TOOL_REGISTRY.clear()
        _TOOL_REGISTRY.update(saved)


class TestToolsRecord:
    def test_edit_file_then_undo(self, tools, root, work):
        target = work / "app.py"
        original = "x = 1\ny = 2\n"
        target.write_text(original, encoding="utf-8")
        assert tools["read_file"](str(target))["status"] == "success"
        with _record(root) as rec:
            result = tools["edit_file"](str(target), "y = 2", "y = 3")
        assert result["status"] == "success", result
        assert "y = 3" in target.read_text(encoding="utf-8")
        fs.restore_turn(SID, rec.turn_id, root=root)
        assert target.read_text(encoding="utf-8") == original

    def test_write_file_creating_a_file_then_undo(self, tools, root, work):
        target = work / "sub" / "fresh.md"
        with _record(root) as rec:
            result = tools["write_file"](str(target), "# new\n")
        assert result["status"] == "success", result
        (turn,) = fs.list_turns(SID, root=root)
        assert turn["files"] == [
            {"path": str(target), "change": "created", "tool": "write_file"}
        ]
        fs.restore_turn(SID, rec.turn_id, root=root)
        assert not target.exists()
        assert not (work / "sub").exists()

    def test_write_file_overwriting_then_undo(self, tools, root, work):
        target = work / "conf.json"
        target.write_bytes(b'{"a": 1}\n')
        assert tools["read_file"](str(target))["status"] == "success"
        with _record(root) as rec:
            result = tools["write_file"](str(target), '{"a": 2}\n')
        assert result["status"] == "success", result
        fs.restore_turn(SID, rec.turn_id, root=root)
        assert target.read_bytes() == b'{"a": 1}\n'

    def test_a_failed_copy_means_the_tool_writes_nothing(
        self, tools, root, work, monkeypatch
    ):
        target = work / "app.py"
        target.write_text("x = 1\n", encoding="utf-8")
        assert tools["read_file"](str(target))["status"] == "success"

        def no_space(*_a, **_k):
            raise OSError(28, "No space left on device")

        monkeypatch.setattr(fs.shutil, "copyfile", no_space)
        with _record(root):
            result = tools["edit_file"](str(target), "x = 1", "x = 2")
        assert result["status"] == "error"
        assert target.read_text(encoding="utf-8") == "x = 1\n"


class TestReviewFixes:
    def test_one_file_under_two_spellings_is_one_entry(self, root, work):
        real = work / "real"
        real.mkdir()
        link = work / "link"
        try:
            os.symlink(real, link, target_is_directory=True)
        except OSError:
            pytest.skip("creating a symlink needs a privilege this machine lacks")
        target = real / "a.txt"
        target.write_text("ORIGINAL", encoding="utf-8")
        with _record(root) as rec:
            fs.before_change(link / "a.txt")
            (link / "a.txt").write_text("one", encoding="utf-8")
            fs.before_change(target)
            target.write_text("two", encoding="utf-8")
        (turn,) = fs.list_turns(SID, root=root)
        assert len(turn["files"]) == 1
        fs.restore_turn(SID, rec.turn_id, root=root)
        assert target.read_text(encoding="utf-8") == "ORIGINAL"
        assert link.is_symlink()

    @pytest.mark.skipif(os.name != "nt", reason="8.3 short names are Windows-only")
    def test_short_and_long_windows_names_are_one_entry(self, root, work):
        import ctypes

        folder = work / "a long folder name"
        folder.mkdir()
        target = folder / "notes.txt"
        target.write_text("ORIGINAL", encoding="utf-8")
        buf = ctypes.create_unicode_buffer(1024)
        ctypes.windll.kernel32.GetShortPathNameW(str(target), buf, 1024)
        if not buf.value or buf.value == str(target):
            pytest.skip("8.3 names are turned off on this volume")
        with _record(root) as rec:
            fs.before_change(buf.value)
            Path(buf.value).write_text("one", encoding="utf-8")
            fs.before_change(target)
            target.write_text("two", encoding="utf-8")
        (turn,) = fs.list_turns(SID, root=root)
        assert len(turn["files"]) == 1
        fs.restore_turn(SID, rec.turn_id, root=root)
        assert target.read_text(encoding="utf-8") == "ORIGINAL"

    def test_write_after_the_turn_ended_keeps_the_undo(self, root, work):
        target = work / "a.txt"
        target.write_text("v0", encoding="utf-8")
        with _record(root) as rec:
            fs.before_change(target)
            target.write_text("turn", encoding="utf-8")
        fs.restore_turn(SID, rec.turn_id, root=root)
        # A tool abandoned at its time limit still holds the recorder.
        rec.capture(work / "late.txt", "write_file")
        (turn,) = fs.list_turns(SID, root=root)
        assert turn["restored_at"] is not None
        assert turn["incomplete"] is True
        assert "after the turn ended" in turn["untracked"][0]["detail"]
        assert [f["change"] for f in turn["files"]] == ["edited"]

    def test_size_limit_never_drops_a_running_turn(self, root, work):
        running = fs.TurnRecorder("other-chat", "running", root)
        big = work / "big.bin"
        big.write_bytes(b"x" * 5000)
        running.capture(big)
        finished = TestRetention()._turn(root, work, "f.txt", size=10)
        fs.prune(retention_days=7, max_bytes=1, root=root, keep=finished.dir)
        assert running.dir.exists()

    def test_large_file_end_state_is_not_copied_and_not_diffed(
        self, root, work, monkeypatch
    ):
        monkeypatch.setattr(fs, "MAX_AFTER_COPY_BYTES", 100)
        target = work / "big.log"
        target.write_bytes(b"a" * 50)
        with _record(root) as rec:
            fs.before_change(target)
            target.write_bytes(b"b" * 500)
        assert not (rec.dir / "blobs" / "0000.after").exists()
        (item,) = fs.diff_turn(SID, rec.turn_id, root=root)
        assert item["too_large"] is True
        assert (item["before_size"], item["after_size"]) == (50, 500)
        fs.restore_turn(SID, rec.turn_id, root=root)
        assert target.read_bytes() == b"a" * 50


class TestSecondReviewFixes:
    def test_nested_new_folders_from_separate_files_are_all_removed(self, root, work):
        outer = work / "new" / "a.txt"
        inner = work / "new" / "sub" / "deeper" / "b.txt"
        with _record(root) as rec:
            fs.before_change(outer)
            outer.parent.mkdir()
            outer.write_text("a", encoding="utf-8")
            fs.before_change(inner)
            inner.parent.mkdir(parents=True)
            inner.write_text("b", encoding="utf-8")
        result = fs.restore_turn(SID, rec.turn_id, root=root)
        assert not (work / "new").exists()
        assert result["kept_folders"] == []

    def test_nested_new_folders_recorded_inner_first_are_all_removed(self, root, work):
        inner = work / "new" / "sub" / "b.txt"
        sibling = work / "new" / "a.txt"
        with _record(root) as rec:
            fs.before_change(inner)
            inner.parent.mkdir(parents=True)
            inner.write_text("b", encoding="utf-8")
            fs.before_change(sibling)
            sibling.write_text("a", encoding="utf-8")
        result = fs.restore_turn(SID, rec.turn_id, root=root)
        assert not (work / "new").exists()
        assert result["kept_folders"] == []

    def test_outer_folder_holding_a_user_file_is_kept_inner_removed(self, root, work):
        inner = work / "new" / "sub" / "b.txt"
        with _record(root) as rec:
            fs.before_change(inner)
            inner.parent.mkdir(parents=True)
            inner.write_text("b", encoding="utf-8")
        (work / "new" / "user.txt").write_text("mine", encoding="utf-8")
        result = fs.restore_turn(SID, rec.turn_id, root=root)
        assert not (work / "new" / "sub").exists()
        assert (work / "new" / "user.txt").read_text(encoding="utf-8") == "mine"
        assert result["kept_folders"] == [str(work / "new")]

    def test_link_after_a_late_write_keeps_the_incomplete_mark(self, root, work):
        target = work / "a.txt"
        target.write_text("v0", encoding="utf-8")
        with _record(root) as rec:
            fs.before_change(target)
            target.write_text("turn", encoding="utf-8")
        # An abandoned tool writes between the turn ending and the link.
        rec.capture(work / "late.txt", "write_file")
        rec.note_untracked("shell", "rm -rf build")
        rec.link_message(7)
        (turn,) = fs.list_turns(SID, root=root)
        assert turn["message_id"] == 7
        assert turn["incomplete"] is True
        assert len(turn["untracked"]) == 2
        assert all("after the turn ended" in u["detail"] for u in turn["untracked"])

    def test_link_after_undo_does_not_forget_the_undo(self, root, work):
        target = work / "a.txt"
        target.write_text("v0", encoding="utf-8")
        with _record(root) as rec:
            fs.before_change(target)
            target.write_text("turn", encoding="utf-8")
        fs.restore_turn(SID, rec.turn_id, root=root)
        rec.link_message(9)
        (turn,) = fs.list_turns(SID, root=root)
        assert turn["message_id"] == 9
        assert turn["restored_at"] is not None

    def test_link_on_a_turn_without_changes_writes_nothing(self, root):
        with _record(root) as rec:
            pass
        rec.link_message(3)
        assert not rec.dir.exists()

    def test_failed_copy_of_timings_does_not_fail_transcription(
        self, root, work, monkeypatch
    ):
        from types import SimpleNamespace

        from gaia.agents.tools import audio_tools

        def refuse(path, tool=None):
            raise fs.SnapshotError("disk full")

        monkeypatch.setattr(audio_tools, "before_change", refuse)
        transcript = SimpleNamespace(
            duration=1.0,
            segments=[SimpleNamespace(start=0.0, end=1.0, text="hi")],
        )
        destination = work / "t.md"
        audio_tools._write_timings(destination, transcript)
        assert not audio_tools.timings_path_for(destination).exists()

    def test_mcp_client_has_no_module_level_agent_import(self):
        import ast

        import gaia.mcp.client.mcp_client as mcp_client

        tree = ast.parse(Path(mcp_client.__file__).read_text(encoding="utf-8"))
        top = [
            n.module if isinstance(n, ast.ImportFrom) else a.name
            for n in tree.body
            if isinstance(n, (ast.Import, ast.ImportFrom))
            for a in (n.names if isinstance(n, ast.Import) else [n])
        ]
        assert not [m for m in top if m and m.startswith("gaia.agents")]

    @pytest.mark.parametrize(
        "annotations, expected",
        [({}, True), ({"readOnlyHint": True}, False), ({"readOnlyHint": "yes"}, True)],
    )
    def test_mcp_tool_still_marks_the_turn_after_the_lazy_import(
        self, root, monkeypatch, annotations, expected
    ):
        from unittest.mock import MagicMock

        from gaia.mcp.client.mcp_client import MCPClient, MCPTool

        client = MCPClient("srv", transport=MagicMock())
        monkeypatch.setattr(client, "call_tool", lambda name, args: {"ok": True})
        tool = MCPTool("lookup", "", {}, annotations)
        with _record(root) as rec:
            assert client.create_tool_wrapper(tool)() == {"ok": True}
        marked = rec.dir.exists() and any(
            u["kind"] == "mcp" for u in fs.list_turns(SID, root=root)[0]["untracked"]
        )
        assert marked is expected


# ---------------------------------------------------------------------------
# A turn recorded by another process (the agent's sidecar)
# ---------------------------------------------------------------------------


class TestRecordedElsewhere:
    def test_turn_ids_sort_in_the_order_they_ran(self):
        import re

        first = fs.new_turn_id()
        time.sleep(1.1)
        second = fs.new_turn_id()
        assert re.match(fs.TURN_ID_PATTERN, first)
        assert first < second

    def test_the_answer_is_linked_beside_the_manifest(self, root, work):
        target = work / "notes.txt"
        with fs.recording_turn(SID, fs.new_turn_id(), root=root) as rec:
            fs.before_change(target, "write_file")
            target.write_bytes(b"new")
            manifest = (rec.dir / fs.MANIFEST).read_bytes()
            # Linked while the recording process may still be saving.
            assert fs.link_turn_message(SID, rec.turn_id, 7, root=root)
            assert (rec.dir / fs.MANIFEST).read_bytes() == manifest
        (turn,) = fs.list_turns(SID, root=root)
        assert turn["message_id"] == 7
        assert turn["files"] == [
            {"path": str(target), "change": "created", "tool": "write_file"}
        ]

    def test_a_turn_that_changed_nothing_is_not_linked(self, root):
        with fs.recording_turn(SID, fs.new_turn_id(), root=root) as rec:
            pass
        assert fs.link_turn_message(SID, rec.turn_id, 7, root=root) is False
        assert fs.list_turns(SID, root=root) == []

    def test_a_damaged_link_is_reported(self, root, work):
        with _record(root) as rec:
            fs.before_change(work / "a.txt", "write_file")
            (work / "a.txt").write_bytes(b"x")
        (rec.dir / fs.MESSAGE_LINK).write_text("{", encoding="utf-8")
        with pytest.raises(fs.SnapshotError, match="Cannot read"):
            fs.list_turns(SID, root=root)

    def test_finishing_twice_keeps_an_undo_made_in_between(self, root, work):
        target = work / "notes.txt"
        target.write_bytes(b"before")
        with _record(root) as rec:
            fs.before_change(target, "edit_file")
            target.write_bytes(b"after")
            # The sidecar finishes before its last event; the UI then undoes.
            rec.finish()
            fs.restore_turn(SID, rec.turn_id, root=root)
        (turn,) = fs.list_turns(SID, root=root)
        assert turn["restored_at"] is not None
        assert target.read_bytes() == b"before"

    def test_a_finish_that_failed_is_tried_again(self, root, work, monkeypatch):
        target = work / "notes.txt"
        with _record(root) as rec:
            fs.before_change(target, "write_file")
            target.write_bytes(b"new")
            real_copy = fs.shutil.copyfile
            monkeypatch.setattr(
                fs.shutil, "copyfile", lambda *a: (_ for _ in ()).throw(OSError("full"))
            )
            with pytest.raises(OSError, match="full"):
                rec.finish()
            monkeypatch.setattr(fs.shutil, "copyfile", real_copy)
        (turn,) = fs.list_turns(SID, root=root)
        assert turn["finished_at"] is not None
        assert turn["files"][0]["change"] == "created"
