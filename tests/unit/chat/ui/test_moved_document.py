# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""A document moved on disk and added again follows the file to its new place."""

from gaia.ui.database import ChatDatabase

HASH = "a" * 64


def _library(tmp_path):
    old = tmp_path / "old" / "report.pdf"
    old.parent.mkdir()
    old.write_bytes(b"%PDF-1.4\n")
    new = tmp_path / "new" / "report-final.pdf"
    new.parent.mkdir()
    db = ChatDatabase(":memory:")
    doc = db.add_document("report.pdf", str(old), HASH, chunk_count=3)
    return db, doc, old, new


def test_a_moved_document_points_at_its_new_path(tmp_path):
    db, doc, old, new = _library(tmp_path)
    old.rename(new)
    db.update_document_status(doc["id"], "missing")

    again = db.add_document("report-final.pdf", str(new), HASH, chunk_count=3)

    assert again["id"] == doc["id"]
    assert again["filepath"] == str(new)
    assert again["filename"] == "report-final.pdf"
    assert again["indexing_status"] == "complete"
    assert db.get_document(doc["id"])["filepath"] == str(new)


def test_a_copy_does_not_take_over_a_document_still_on_disk(tmp_path):
    db, doc, old, new = _library(tmp_path)
    new.write_bytes(old.read_bytes())

    again = db.add_document("report-final.pdf", str(new), HASH, chunk_count=3)

    assert again["id"] == doc["id"]
    assert again["filepath"] == str(old)


def test_a_new_path_that_does_not_exist_changes_nothing(tmp_path):
    db, doc, old, new = _library(tmp_path)
    old.unlink()

    again = db.add_document("report-final.pdf", str(new), HASH, chunk_count=3)

    assert again["filepath"] == str(old)
