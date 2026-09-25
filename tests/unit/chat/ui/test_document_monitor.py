# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Tests for the DocumentMonitor auto re-indexing system."""

import asyncio
import os
import tempfile
import time

import pytest

from gaia.ui.database import ChatDatabase
from gaia.ui.document_monitor import DocumentMonitor, _compute_file_hash, _get_file_info

# ── Helper fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def db():
    """In-memory database for testing."""
    database = ChatDatabase(":memory:")
    yield database
    database.close()


@pytest.fixture
def temp_file():
    """Create a temporary file for testing."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        f.write("Hello, World!")
        f.flush()
        path = f.name

    yield path

    try:
        os.unlink(path)
    except OSError:
        pass


async def _dummy_index(filepath) -> int:
    """Dummy index function that returns a fixed chunk count."""
    return 5


# ── Unit tests ───────────────────────────────────────────────────────────────


class TestComputeFileHash:
    """Tests for _compute_file_hash."""

    def test_basic_hash(self, temp_file):
        h = _compute_file_hash(temp_file)
        assert isinstance(h, str)
        assert len(h) == 64  # SHA-256 hex digest

    def test_consistent_hash(self, temp_file):
        h1 = _compute_file_hash(temp_file)
        h2 = _compute_file_hash(temp_file)
        assert h1 == h2

    def test_hash_changes_on_content_change(self, temp_file):
        h1 = _compute_file_hash(temp_file)
        with open(temp_file, "a") as f:
            f.write(" Modified!")
        h2 = _compute_file_hash(temp_file)
        assert h1 != h2


class TestGetFileInfo:
    """Tests for _get_file_info."""

    def test_existing_file(self, temp_file):
        info = _get_file_info(temp_file)
        assert info is not None
        mtime, size = info
        assert isinstance(mtime, float)
        assert size > 0

    def test_missing_file(self):
        info = _get_file_info("/nonexistent/path/file.txt")
        assert info is None


class TestDatabaseReindexMethods:
    """Tests for database methods used by DocumentMonitor."""

    def test_add_document_with_mtime(self, db, temp_file):
        doc = db.add_document(
            filename="test.txt",
            filepath=temp_file,
            file_hash="abc123",
            file_size=100,
            chunk_count=3,
            file_mtime=1234567890.0,
        )
        assert doc is not None
        assert doc["chunk_count"] == 3

    def test_reindex_document(self, db, temp_file):
        doc = db.add_document(
            filename="test.txt",
            filepath=temp_file,
            file_hash="original_hash",
            file_size=100,
            chunk_count=3,
            file_mtime=1000.0,
        )
        doc_id = doc["id"]

        result = db.reindex_document(
            doc_id,
            file_hash="new_hash",
            file_mtime=2000.0,
            chunk_count=7,
            file_size=200,
        )
        assert result is True

        updated = db.get_document(doc_id)
        assert updated["file_hash"] == "new_hash"
        assert updated["chunk_count"] == 7
        assert updated["file_size"] == 200
        assert updated["indexing_status"] == "complete"

    def test_update_document_mtime(self, db, temp_file):
        doc = db.add_document(
            filename="test.txt",
            filepath=temp_file,
            file_hash="abc",
            file_mtime=1000.0,
        )
        doc_id = doc["id"]

        result = db.update_document_mtime(doc_id, 2000.0)
        assert result is True

        updated = db.get_document(doc_id)
        assert updated["file_mtime"] == 2000.0


class TestDocumentMonitor:
    """Tests for the DocumentMonitor class."""

    def test_init(self, db):
        monitor = DocumentMonitor(db=db, index_fn=_dummy_index, interval=1.0)
        assert not monitor.is_running
        assert len(monitor.reindexing_docs) == 0

    @pytest.mark.asyncio
    async def test_start_stop(self, db):
        monitor = DocumentMonitor(db=db, index_fn=_dummy_index, interval=1.0)
        await monitor.start()
        assert monitor.is_running
        await monitor.stop()
        assert not monitor.is_running

    @pytest.mark.asyncio
    async def test_detects_file_change(self, db, temp_file):
        """Monitor should detect when a file is modified and re-index it."""
        file_hash = _compute_file_hash(temp_file)
        file_stat = os.stat(temp_file)

        doc = db.add_document(
            filename="test.txt",
            filepath=temp_file,
            file_hash=file_hash,
            file_size=file_stat.st_size,
            chunk_count=3,
            file_mtime=file_stat.st_mtime,
        )
        doc_id = doc["id"]

        # Modify the file
        time.sleep(0.1)  # Ensure mtime changes
        with open(temp_file, "a") as f:
            f.write("\nNew content added!")

        index_called = asyncio.Event()
        original_count = [0]

        async def tracking_index(filepath) -> int:
            original_count[0] += 1
            index_called.set()
            return 10

        monitor = DocumentMonitor(db=db, index_fn=tracking_index, interval=0.5)

        await monitor.start()
        try:
            # Wait for the monitor to detect the change
            await asyncio.wait_for(index_called.wait(), timeout=10.0)
            assert original_count[0] >= 1

            # Verify the document was updated
            updated = db.get_document(doc_id)
            assert updated["chunk_count"] == 10
            assert updated["indexing_status"] == "complete"
        finally:
            await monitor.stop()

    @pytest.mark.asyncio
    async def test_skips_unchanged_files(self, db, temp_file):
        """Monitor should not re-index files that haven't changed."""
        file_hash = _compute_file_hash(temp_file)
        file_stat = os.stat(temp_file)

        db.add_document(
            filename="test.txt",
            filepath=temp_file,
            file_hash=file_hash,
            file_size=file_stat.st_size,
            chunk_count=3,
            file_mtime=file_stat.st_mtime,
        )

        call_count = [0]

        async def tracking_index(filepath) -> int:
            call_count[0] += 1
            return 10

        monitor = DocumentMonitor(db=db, index_fn=tracking_index, interval=0.5)

        await monitor.start()
        try:
            # Wait for at least 2 check cycles
            await asyncio.sleep(3.0)
            # Should not have been called since file didn't change
            assert call_count[0] == 0
        finally:
            await monitor.stop()

    @pytest.mark.asyncio
    async def test_handles_missing_file(self, db):
        """Monitor should handle deleted files gracefully."""
        doc = db.add_document(
            filename="missing.txt",
            filepath="/nonexistent/missing.txt",
            file_hash="abc123",
            file_size=100,
            chunk_count=3,
            file_mtime=1000.0,
        )

        monitor = DocumentMonitor(db=db, index_fn=_dummy_index, interval=0.5)

        await monitor.start()
        try:
            await asyncio.sleep(2.0)
            # Should not crash; doc status should remain unchanged
            # (we log a warning but don't modify the record)
        finally:
            await monitor.stop()

    @pytest.mark.asyncio
    async def test_skips_docs_being_indexed(self, db, temp_file):
        """Monitor should skip docs that are currently being indexed by user."""
        file_hash = _compute_file_hash(temp_file)
        doc = db.add_document(
            filename="test.txt",
            filepath=temp_file,
            file_hash=file_hash,
            file_size=100,
            chunk_count=3,
        )
        doc_id = doc["id"]

        # Simulate active indexing task
        active_tasks = {doc_id: True}

        call_count = [0]

        async def tracking_index(filepath) -> int:
            call_count[0] += 1
            return 10

        monitor = DocumentMonitor(
            db=db,
            index_fn=tracking_index,
            interval=0.5,
            active_tasks=active_tasks,
        )

        await monitor.start()
        try:
            await asyncio.sleep(2.0)
            assert call_count[0] == 0
        finally:
            await monitor.stop()

    @pytest.mark.asyncio
    async def test_reindex_zero_chunks_sets_failed(self, db, temp_file):
        """Re-indexing that returns 0 chunks should set status to 'failed'."""
        file_hash = _compute_file_hash(temp_file)
        file_stat = os.stat(temp_file)

        doc = db.add_document(
            filename="test.txt",
            filepath=temp_file,
            file_hash=file_hash,
            file_size=file_stat.st_size,
            chunk_count=3,
            file_mtime=file_stat.st_mtime,
        )
        doc_id = doc["id"]

        # Modify the file to trigger re-indexing
        time.sleep(0.1)
        with open(temp_file, "a") as f:
            f.write("\nModified content")

        index_called = asyncio.Event()

        async def zero_chunk_index(filepath) -> int:
            index_called.set()
            return 0

        monitor = DocumentMonitor(db=db, index_fn=zero_chunk_index, interval=0.5)

        await monitor.start()
        try:
            await asyncio.wait_for(index_called.wait(), timeout=10.0)
            # Give monitor time to update DB
            await asyncio.sleep(0.2)
            updated = db.get_document(doc_id)
            assert updated["indexing_status"] == "failed"
        finally:
            await monitor.stop()


class TestAReconnectedDriveIsRepaired:
    """A document on a removable or network drive came back "missing" forever.

    Unplugging the drive marks it missing, correctly. Plugging it back in
    changes nothing about the file, so its mtime still matches — and the
    monitor's fast path returned before anything could clear the status. Only
    a manual re-index brought it back (#3552).
    """

    @staticmethod
    def _indexed(db, temp_file):
        file_stat = os.stat(temp_file)
        return db.add_document(
            filename="on-a-usb-stick.txt",
            filepath=temp_file,
            file_hash=_compute_file_hash(temp_file),
            file_size=file_stat.st_size,
            chunk_count=3,
            file_mtime=file_stat.st_mtime,
        )

    @pytest.mark.asyncio
    async def test_a_missing_document_recovers_when_the_file_returns(
        self, db, temp_file
    ):
        doc_id = self._indexed(db, temp_file)["id"]
        # The state one sweep with the drive unplugged leaves behind.
        db.update_document_status(doc_id, "missing")

        monitor = DocumentMonitor(
            db=db, index_fn=_dummy_index, interval=0.2, startup_delay=0.0
        )
        await monitor.start()
        try:
            for _ in range(40):
                if db.get_document(doc_id)["indexing_status"] != "missing":
                    break
                await asyncio.sleep(0.1)
        finally:
            await monitor.stop()

        assert db.get_document(doc_id)["indexing_status"] == "complete"

    @pytest.mark.asyncio
    async def test_recovery_does_not_require_re_indexing(self, db, temp_file):
        """The file is unchanged; re-reading it would be wasted work."""
        doc_id = self._indexed(db, temp_file)["id"]
        db.update_document_status(doc_id, "missing")

        calls = [0]

        async def tracking_index(filepath) -> int:
            calls[0] += 1
            return 10

        monitor = DocumentMonitor(
            db=db, index_fn=tracking_index, interval=0.2, startup_delay=0.0
        )
        await monitor.start()
        try:
            for _ in range(40):
                if db.get_document(doc_id)["indexing_status"] != "missing":
                    break
                await asyncio.sleep(0.1)
            await asyncio.sleep(0.5)
        finally:
            await monitor.stop()

        assert db.get_document(doc_id)["indexing_status"] == "complete"
        assert calls[0] == 0

    @pytest.mark.asyncio
    async def test_a_file_that_is_still_gone_stays_missing(self, db, tmp_path):
        gone = tmp_path / "still-unplugged.txt"
        gone.write_text("content")
        file_stat = os.stat(gone)
        doc = db.add_document(
            filename="still-unplugged.txt",
            filepath=str(gone),
            file_hash=_compute_file_hash(str(gone)),
            file_size=file_stat.st_size,
            chunk_count=3,
            file_mtime=file_stat.st_mtime,
        )
        gone.unlink()

        monitor = DocumentMonitor(
            db=db, index_fn=_dummy_index, interval=0.2, startup_delay=0.0
        )
        await monitor.start()
        try:
            for _ in range(40):
                if db.get_document(doc["id"])["indexing_status"] == "missing":
                    break
                await asyncio.sleep(0.1)
            # And it must STAY missing across further sweeps.
            await asyncio.sleep(0.5)
        finally:
            await monitor.stop()

        assert db.get_document(doc["id"])["indexing_status"] == "missing"

    @pytest.mark.asyncio
    async def test_a_file_changed_while_away_is_still_re_indexed(self, db, temp_file):
        """Repairing the status must not short-circuit a real content change."""
        doc_id = self._indexed(db, temp_file)["id"]
        db.update_document_status(doc_id, "missing")

        time.sleep(0.1)
        with open(temp_file, "a") as f:
            f.write("\nedited while the drive was out")

        index_called = asyncio.Event()

        async def tracking_index(filepath) -> int:
            index_called.set()
            return 10

        monitor = DocumentMonitor(
            db=db, index_fn=tracking_index, interval=0.2, startup_delay=0.0
        )
        await monitor.start()
        try:
            await asyncio.wait_for(index_called.wait(), timeout=10.0)
        finally:
            await monitor.stop()

        assert db.get_document(doc_id)["chunk_count"] == 10

    @pytest.mark.asyncio
    async def test_a_file_swapped_while_away_is_re_read_despite_its_mtime(
        self, db, temp_file
    ):
        """mtime is only trustworthy while the monitor was watching.

        A restore or an rsync that preserves timestamps looks unchanged, and
        the missing window is exactly the period nothing was watching — so the
        size is checked on that one transition.
        """
        doc_id = self._indexed(db, temp_file)["id"]
        db.update_document_status(doc_id, "missing")

        # Same mtime, different content: what `cp -p` leaves behind.
        stat_before = os.stat(temp_file)
        with open(temp_file, "w") as f:
            f.write("completely different content, restored from a backup")
        os.utime(temp_file, (stat_before.st_atime, stat_before.st_mtime))

        index_called = asyncio.Event()

        async def tracking_index(filepath) -> int:
            index_called.set()
            return 7

        monitor = DocumentMonitor(
            db=db, index_fn=tracking_index, interval=0.2, startup_delay=0.0
        )
        await monitor.start()
        try:
            await asyncio.wait_for(index_called.wait(), timeout=10.0)
        finally:
            await monitor.stop()

        assert db.get_document(doc_id)["chunk_count"] == 7
