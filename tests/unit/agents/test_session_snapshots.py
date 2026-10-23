# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""The whole-chat view of file changes behind the session panel's Changes (#5278).

Undo all must put every file back byte for byte as it was before the chat's
first turn — edited, created and deleted files alike, across turns that touch
the same file — and must refuse, changing nothing, when it cannot.
"""

import pytest

from gaia.agents.base import file_snapshots as fs

SID = "session-1"


@pytest.fixture
def root(tmp_path):
    return tmp_path / "snapshots"


@pytest.fixture
def work(tmp_path):
    folder = tmp_path / "work"
    folder.mkdir()
    return folder


def _turn(root, n, changes, message_id=None):
    """Run one recorded turn applying *changes*: {path: bytes | None (delete)}."""
    with fs.recording_turn(SID, turn_id=f"t{n:03d}", root=root) as rec:
        for path, data in changes.items():
            fs.before_change(path, "write_file")
            if data is None:
                path.unlink()
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
    if message_id is not None:
        rec.link_message(message_id)
    return rec.turn_id


def _tree(folder):
    return {
        p.relative_to(folder).as_posix(): p.read_bytes()
        for p in sorted(folder.rglob("*"))
        if p.is_file()
    }


class TestUndoAll:
    def test_restores_created_edited_and_deleted_files_exactly(self, root, work):
        (work / "edit.txt").write_bytes(b"one\r\ntwo\n\x00tail")
        (work / "gone.bin").write_bytes(bytes(range(256)))
        (work / "untouched.md").write_bytes(b"leave me")
        before = _tree(work)

        _turn(
            root, 1, {work / "edit.txt": b"rewritten", work / "new" / "a.py": b"x"}, 11
        )
        _turn(root, 2, {work / "edit.txt": b"again", work / "gone.bin": None}, 12)
        _turn(root, 3, {work / "new" / "a.py": b"x = 2\n"}, 13)

        result = fs.restore_session(SID, root=root)

        assert _tree(work) == before
        assert not (work / "new").exists(), "a folder the chat created is removed"
        assert sorted(result["restored"]) == sorted(
            [str(work / "edit.txt"), str(work / "gone.bin")]
        )
        assert result["removed"] == [str(work / "new" / "a.py")]
        assert result["turn_ids"] == ["t001", "t002", "t003"]

    def test_marks_every_turn_undone_so_nothing_is_left(self, root, work):
        (work / "f.txt").write_bytes(b"a")
        _turn(root, 1, {work / "f.txt": b"b"})
        fs.restore_session(SID, root=root)
        assert all(t["restored_at"] for t in fs.list_turns(SID, root=root))
        assert fs.session_changes(SID, root=root)["files"] == []
        with pytest.raises(fs.SnapshotError, match="no file changes left"):
            fs.restore_session(SID, root=root)

    def test_a_later_outside_edit_is_a_conflict_and_nothing_changes(self, root, work):
        (work / "a.txt").write_bytes(b"a0")
        (work / "b.txt").write_bytes(b"b0")
        _turn(root, 1, {work / "a.txt": b"a1", work / "b.txt": b"b1"})
        (work / "b.txt").write_bytes(b"user's own edit")

        with pytest.raises(fs.RestoreConflict) as err:
            fs.restore_session(SID, root=root)

        assert err.value.conflicts == [
            {"path": str(work / "b.txt"), "reason": "changed after this turn"}
        ]
        assert (work / "a.txt").read_bytes() == b"a1", "nothing was restored"
        assert (work / "b.txt").read_bytes() == b"user's own edit"
        assert not any(t["restored_at"] for t in fs.list_turns(SID, root=root))

    def test_force_overwrites_the_outside_edit(self, root, work):
        (work / "a.txt").write_bytes(b"a0")
        _turn(root, 1, {work / "a.txt": b"a1"})
        (work / "a.txt").write_bytes(b"later")
        fs.restore_session(SID, force=True, root=root)
        assert (work / "a.txt").read_bytes() == b"a0"

    def test_a_turn_already_undone_is_skipped(self, root, work):
        (work / "a.txt").write_bytes(b"a0")
        (work / "b.txt").write_bytes(b"b0")
        _turn(root, 1, {work / "a.txt": b"a1"})
        second = _turn(root, 2, {work / "b.txt": b"b1"})
        fs.restore_turn(SID, second, root=root)

        result = fs.restore_session(SID, root=root)

        assert result["turn_ids"] == ["t001"]
        assert _tree(work) == {"a.txt": b"a0", "b.txt": b"b0"}

    def test_unfinished_turn_refuses(self, root, work):
        (work / "a.txt").write_bytes(b"a0")
        _turn(root, 1, {work / "a.txt": b"a1"})
        rec = fs.TurnRecorder(SID, "t002", root)
        rec.capture(work / "a.txt", "write_file")  # never finished
        with pytest.raises(fs.SnapshotError, match="did not finish"):
            fs.restore_session(SID, root=root)
        assert (work / "a.txt").read_bytes() == b"a1"

    def test_damaged_copy_restores_nothing(self, root, work):
        (work / "a.txt").write_bytes(b"a0")
        (work / "b.txt").write_bytes(b"b0")
        _turn(root, 1, {work / "a.txt": b"a1", work / "b.txt": b"b1"})
        blob = next((root / SID / "t001" / "blobs").glob("0001.before"))
        blob.write_bytes(b"corrupt")
        with pytest.raises(fs.SnapshotError, match="damaged"):
            fs.restore_session(SID, root=root)
        assert _tree(work) == {"a.txt": b"a1", "b.txt": b"b1"}

    def test_failure_part_way_puts_every_file_back(self, root, work, monkeypatch):
        (work / "a.txt").write_bytes(b"a0")
        (work / "b.txt").write_bytes(b"b0")
        _turn(root, 1, {work / "a.txt": b"a1", work / "b.txt": b"b1"})
        real = fs._restore_file
        calls = []

        def flaky(target, source, mode):
            calls.append(target)
            if len(calls) == 2:
                raise OSError("disk full")
            real(target, source, mode)

        monkeypatch.setattr(fs, "_restore_file", flaky)
        with pytest.raises(fs.SnapshotError, match="nothing changed"):
            fs.restore_session(SID, root=root)
        assert _tree(work) == {"a.txt": b"a1", "b.txt": b"b1"}


class TestTotals:
    def test_totals_match_the_files_on_disk(self, root, work):
        (work / "notes.md").write_bytes(b"a\nb\nc\n")
        (work / "old.txt").write_bytes(b"x\ny\n")
        _turn(root, 1, {work / "notes.md": b"a\nB\nc\nd\n"}, 21)
        _turn(root, 2, {work / "new.txt": b"1\n2\n3\n", work / "old.txt": None}, 22)
        _turn(root, 3, {work / "notes.md": b"a\nB\nc\nd\ne\n"}, 23)

        out = fs.session_changes(SID, root=root)
        by = {f["path"]: f for f in out["files"]}

        assert out["totals"] == {
            "files": 3,
            "additions": 3 + 3 + 0,
            "deletions": 1 + 0 + 2,
            "uncounted": 0,
        }
        assert by[str(work / "notes.md")]["change"] == "edited"
        assert by[str(work / "notes.md")]["message_ids"] == [21, 23]
        assert by[str(work / "new.txt")]["change"] == "created"
        assert by[str(work / "old.txt")]["change"] == "deleted"
        # The diff spans the chat: the first turn's "before" to the last's "after".
        notes = by[str(work / "notes.md")]["unified"]
        assert "-b\n" in notes and "+B\n" in notes and "+e\n" in notes
        assert all(f["changed_since"] is None for f in out["files"])

    def test_a_file_put_back_by_a_later_turn_is_not_counted(self, root, work):
        (work / "a.txt").write_bytes(b"same\n")
        _turn(root, 1, {work / "a.txt": b"different\n"})
        _turn(root, 2, {work / "a.txt": b"same\n"})
        assert fs.session_changes(SID, root=root)["totals"]["files"] == 0

    def test_created_then_deleted_is_not_counted(self, root, work):
        _turn(root, 1, {work / "tmp.txt": b"scratch"})
        _turn(root, 2, {work / "tmp.txt": None})
        assert fs.session_changes(SID, root=root)["files"] == []

    def test_binary_file_is_counted_but_not_line_counted(self, root, work):
        _turn(root, 1, {work / "img.bin": b"\x00\x01\x02"})
        out = fs.session_changes(SID, root=root)
        assert out["files"][0]["binary"] is True
        assert out["totals"] == {
            "files": 1,
            "additions": 0,
            "deletions": 0,
            "uncounted": 1,
        }

    def test_outside_edit_is_flagged(self, root, work):
        (work / "a.txt").write_bytes(b"a0")
        _turn(root, 1, {work / "a.txt": b"a1"})
        (work / "a.txt").unlink()
        out = fs.session_changes(SID, root=root)
        assert out["files"][0]["changed_since"] == "deleted after this turn"

    def test_running_turn_is_pending_not_counted(self, root, work):
        (work / "a.txt").write_bytes(b"a0")
        rec = fs.TurnRecorder(SID, "t001", root)
        rec.capture(work / "a.txt", "write_file")
        out = fs.session_changes(SID, root=root)
        assert out["pending"] is True
        assert out["files"] == []

    def test_chat_with_no_changes_is_empty(self, root):
        out = fs.session_changes(SID, root=root)
        assert out["files"] == [] and out["totals"]["files"] == 0
        assert out["turns"] == 0


def test_undo_all_removes_folders_of_a_file_made_and_deleted_in_the_chat(root, work):
    (work / "keep.txt").write_bytes(b"k")
    _turn(root, 1, {work / "keep.txt": b"k2", work / "tmp" / "deep" / "x.txt": b"x"})
    _turn(root, 2, {work / "tmp" / "deep" / "x.txt": None})
    result = fs.restore_session(SID, root=root)
    assert not (work / "tmp").exists()
    assert (work / "keep.txt").read_bytes() == b"k"
    assert result["kept_folders"] == []


def test_undo_all_keeps_such_a_folder_when_it_holds_other_files(root, work):
    (work / "keep.txt").write_bytes(b"k")
    _turn(root, 1, {work / "keep.txt": b"k2", work / "tmp" / "x.txt": b"x"})
    (work / "tmp" / "mine.txt").write_bytes(b"user's")
    _turn(root, 2, {work / "tmp" / "x.txt": None})
    result = fs.restore_session(SID, root=root)
    assert (work / "tmp" / "mine.txt").read_bytes() == b"user's"
    assert result["kept_folders"] == [str(work / "tmp")]


def test_undo_all_removes_a_parent_emptied_by_a_made_and_deleted_subfolder(root, work):
    _turn(
        root, 1, {work / "tmp" / "notes.md": b"n", work / "tmp" / "sub" / "x.txt": b"x"}
    )
    _turn(root, 2, {work / "tmp" / "sub" / "x.txt": None})
    result = fs.restore_session(SID, root=root)
    assert not (work / "tmp").exists()
    assert result["kept_folders"] == []


def test_undo_all_removes_a_parent_shared_by_two_made_and_deleted_files(root, work):
    (work / "keep.txt").write_bytes(b"k")
    _turn(
        root,
        1,
        {
            work / "keep.txt": b"k2",
            work / "tmp" / "a" / "x.txt": b"x",
            work / "tmp" / "b" / "y.txt": b"y",
        },
    )
    _turn(
        root,
        2,
        {work / "tmp" / "a" / "x.txt": None, work / "tmp" / "b" / "y.txt": None},
    )
    result = fs.restore_session(SID, root=root)
    assert not (work / "tmp").exists()
    assert result["kept_folders"] == []
