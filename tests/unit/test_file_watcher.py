# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Tests for file watcher utilities including hash functions and parsing."""

import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock, Mock

import pytest

from gaia.utils import (
    compute_bytes_hash,
    compute_file_hash,
    detect_field_changes,
    extract_json_from_text,
    validate_required_fields,
)


class TestFileHashUtilities:
    """Tests for file hashing functions."""

    def test_compute_file_hash_returns_sha256(self):
        """Test that compute_file_hash returns a valid SHA-256 hash."""
        with tempfile.NamedTemporaryFile(delete=False, suffix=".txt") as f:
            f.write(b"test content")
            temp_path = f.name

        try:
            result = compute_file_hash(temp_path)
            assert result is not None
            # SHA-256 produces 64 hex characters
            assert len(result) == 64
            assert all(c in "0123456789abcdef" for c in result)
        finally:
            Path(temp_path).unlink()

    def test_compute_file_hash_consistent(self):
        """Test that same content produces same hash."""
        with tempfile.NamedTemporaryFile(delete=False, suffix=".txt") as f:
            f.write(b"identical content")
            temp_path = f.name

        try:
            hash1 = compute_file_hash(temp_path)
            hash2 = compute_file_hash(temp_path)
            assert hash1 == hash2
        finally:
            Path(temp_path).unlink()

    def test_compute_file_hash_different_content(self):
        """Test that different content produces different hashes."""
        with tempfile.NamedTemporaryFile(delete=False, suffix=".txt") as f1:
            f1.write(b"content A")
            path1 = f1.name

        with tempfile.NamedTemporaryFile(delete=False, suffix=".txt") as f2:
            f2.write(b"content B")
            path2 = f2.name

        try:
            hash1 = compute_file_hash(path1)
            hash2 = compute_file_hash(path2)
            assert hash1 != hash2
        finally:
            Path(path1).unlink()
            Path(path2).unlink()

    def test_compute_file_hash_nonexistent_file(self):
        """Test that nonexistent file returns None."""
        result = compute_file_hash("/nonexistent/path/file.txt")
        assert result is None

    def test_compute_file_hash_with_path_object(self):
        """Test that Path objects work as well as strings."""
        with tempfile.NamedTemporaryFile(delete=False, suffix=".txt") as f:
            f.write(b"path test")
            temp_path = Path(f.name)

        try:
            result = compute_file_hash(temp_path)
            assert result is not None
            assert len(result) == 64
        finally:
            temp_path.unlink()

    def test_compute_bytes_hash_returns_sha256(self):
        """Test that compute_bytes_hash returns a valid SHA-256 hash."""
        result = compute_bytes_hash(b"test bytes")
        assert len(result) == 64
        assert all(c in "0123456789abcdef" for c in result)

    def test_compute_bytes_hash_consistent(self):
        """Test that same bytes produce same hash."""
        data = b"consistent data"
        hash1 = compute_bytes_hash(data)
        hash2 = compute_bytes_hash(data)
        assert hash1 == hash2

    def test_compute_bytes_hash_matches_file_hash(self):
        """Test that bytes hash matches file hash for same content."""
        content = b"matching content test"

        with tempfile.NamedTemporaryFile(delete=False, suffix=".txt") as f:
            f.write(content)
            temp_path = f.name

        try:
            file_hash = compute_file_hash(temp_path)
            bytes_hash = compute_bytes_hash(content)
            assert file_hash == bytes_hash
        finally:
            Path(temp_path).unlink()

    def test_compute_file_hash_large_file(self):
        """Test hashing of a larger file (exercises chunked reading)."""
        # Create a 1MB file
        large_content = b"x" * (1024 * 1024)

        with tempfile.NamedTemporaryFile(delete=False, suffix=".bin") as f:
            f.write(large_content)
            temp_path = f.name

        try:
            result = compute_file_hash(temp_path)
            assert result is not None
            assert len(result) == 64
            # Verify consistency
            assert result == compute_file_hash(temp_path)
        finally:
            Path(temp_path).unlink()

    def test_compute_bytes_hash_empty_bytes(self):
        """Test hashing of empty bytes."""
        result = compute_bytes_hash(b"")
        # SHA-256 of empty string is a known value
        assert (
            result == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        )


class TestJsonExtraction:
    """Tests for JSON extraction from text."""

    def test_extract_plain_json(self):
        """Test extracting pure JSON."""
        text = '{"name": "John", "age": 30}'
        result = extract_json_from_text(text)
        assert result == {"name": "John", "age": 30}

    def test_extract_json_with_surrounding_text(self):
        """Test extracting JSON embedded in text."""
        text = 'Here is the data: {"name": "Jane"} Hope this helps!'
        result = extract_json_from_text(text)
        assert result == {"name": "Jane"}

    def test_extract_nested_json(self):
        """Test extracting nested JSON objects."""
        text = (
            """Response: {"patient": {"address": {"city": "Boston", "zip": "02101"}}}"""
        )
        result = extract_json_from_text(text)
        assert result == {"patient": {"address": {"city": "Boston", "zip": "02101"}}}

    def test_extract_json_empty_text(self):
        """Test with empty text."""
        assert extract_json_from_text("") is None
        assert extract_json_from_text(None) is None

    def test_extract_json_no_json(self):
        """Test with text containing no JSON."""
        text = "This is just plain text with no JSON"
        assert extract_json_from_text(text) is None


class TestFieldChangeDetection:
    """Tests for field change detection."""

    def test_detect_single_change(self):
        """Test detecting a single field change."""
        old = {"phone": "555-1234", "email": "old@test.com"}
        new = {"phone": "555-9999", "email": "old@test.com"}
        changes = detect_field_changes(old, new, ["phone", "email"])
        assert len(changes) == 1
        assert changes[0]["field"] == "phone"
        assert changes[0]["old"] == "555-1234"
        assert changes[0]["new"] == "555-9999"

    def test_detect_no_changes(self):
        """Test when no fields changed."""
        old = {"name": "John", "age": "30"}
        new = {"name": "John", "age": "30"}
        changes = detect_field_changes(old, new, ["name", "age"])
        assert len(changes) == 0

    def test_detect_multiple_changes(self):
        """Test detecting multiple field changes."""
        old = {"a": "1", "b": "2", "c": "3"}
        new = {"a": "1", "b": "X", "c": "Y"}
        changes = detect_field_changes(old, new, ["a", "b", "c"])
        assert len(changes) == 2
        changed_fields = [c["field"] for c in changes]
        assert "b" in changed_fields
        assert "c" in changed_fields

    def test_detect_changes_all_fields(self):
        """Test auto-detecting all fields when none specified."""
        old = {"x": "1", "y": "2"}
        new = {"x": "1", "y": "changed"}
        changes = detect_field_changes(old, new)
        assert len(changes) == 1
        assert changes[0]["field"] == "y"


class TestRequiredFieldValidation:
    """Tests for required field validation."""

    def test_all_fields_present(self):
        """Test when all required fields are present."""
        data = {"name": "John", "email": "john@test.com"}
        is_valid, missing = validate_required_fields(data, ["name", "email"])
        assert is_valid is True
        assert missing == []

    def test_missing_fields(self):
        """Test when some required fields are missing."""
        data = {"name": "John"}
        is_valid, missing = validate_required_fields(data, ["name", "email", "phone"])
        assert is_valid is False
        assert "email" in missing
        assert "phone" in missing

    def test_empty_string_counts_as_missing(self):
        """Test that empty strings are treated as missing."""
        data = {"name": "John", "email": ""}
        is_valid, missing = validate_required_fields(data, ["name", "email"])
        assert is_valid is False
        assert "email" in missing

    def test_whitespace_only_counts_as_missing(self):
        """Test that whitespace-only strings are treated as missing."""
        data = {"name": "John", "email": "   "}
        is_valid, missing = validate_required_fields(data, ["name", "email"])
        assert is_valid is False
        assert "email" in missing


"""Unit tests for FileChangeHandler and FileWatcher."""

from gaia.utils.file_watcher import (
    FileChangeHandler,
    FileWatcher,
    check_watchdog_available,
)


class TestFileChangeHandler:
    """Tests for FileChangeHandler class."""

    def test_init_defaults(self):
        """Handler initializes with default values."""
        handler = FileChangeHandler()
        assert handler._on_created is None
        assert handler._on_modified is None
        assert handler._on_deleted is None
        assert handler._on_moved is None
        assert handler._debounce_seconds == 2.0
        assert handler._ignore_directories is True
        assert len(handler._extensions) > 0  # Has default extensions

    def test_init_with_callbacks(self):
        """Handler initializes with custom callbacks."""
        on_created = Mock()
        on_modified = Mock()
        on_deleted = Mock()
        on_moved = Mock()

        handler = FileChangeHandler(
            on_created=on_created,
            on_modified=on_modified,
            on_deleted=on_deleted,
            on_moved=on_moved,
        )

        assert handler._on_created is on_created
        assert handler._on_modified is on_modified
        assert handler._on_deleted is on_deleted
        assert handler._on_moved is on_moved

    def test_init_with_extensions(self):
        """Handler initializes with custom extensions."""
        handler = FileChangeHandler(extensions=[".pdf", ".png", "jpg"])
        assert ".pdf" in handler._extensions
        assert ".png" in handler._extensions
        assert ".jpg" in handler._extensions  # Normalized with leading dot

    def test_init_with_empty_extensions_watches_all(self):
        """Empty extensions list watches all files."""
        handler = FileChangeHandler(extensions=[])
        assert handler._should_process("anything.xyz")
        assert handler._should_process("file.unknown")

    def test_init_with_filter_func(self):
        """Handler uses custom filter function."""
        handler = FileChangeHandler(
            filter_func=lambda p: p.endswith(".special"),
            extensions=[".pdf"],  # Should be ignored
        )
        assert handler._should_process("file.special")
        assert not handler._should_process("file.pdf")

    def test_should_process_with_extensions(self):
        """File extension filtering works correctly."""
        handler = FileChangeHandler(extensions=[".pdf", ".txt"])
        assert handler._should_process("document.pdf")
        assert handler._should_process("notes.txt")
        assert handler._should_process("UPPER.PDF")  # Case insensitive
        assert not handler._should_process("image.png")
        assert not handler._should_process("script.py")

    def test_debouncing(self):
        """Debouncing prevents duplicate processing."""
        handler = FileChangeHandler(debounce_seconds=1.0)

        # First check should not be debounced
        assert not handler._is_debounced("file.txt")

        # Immediate second check should be debounced
        assert handler._is_debounced("file.txt")

        # Different file should not be debounced
        assert not handler._is_debounced("other.txt")

    def test_debounce_expiry(self):
        """Debounce expires after timeout."""
        handler = FileChangeHandler(debounce_seconds=0.1)

        assert not handler._is_debounced("file.txt")
        assert handler._is_debounced("file.txt")

        # Wait for debounce to expire
        time.sleep(0.15)

        # Should not be debounced anymore
        assert not handler._is_debounced("file.txt")

    def test_on_created_calls_callback(self):
        """on_created calls the callback with file path."""
        callback = Mock()
        handler = FileChangeHandler(
            on_created=callback,
            extensions=[".txt"],
            debounce_seconds=0,
        )

        event = MagicMock()
        event.is_directory = False
        event.src_path = "/path/to/file.txt"

        handler.on_created(event)

        callback.assert_called_once_with("/path/to/file.txt")

    def test_on_created_ignores_directories(self):
        """on_created ignores directory events."""
        callback = Mock()
        handler = FileChangeHandler(on_created=callback)

        event = MagicMock()
        event.is_directory = True
        event.src_path = "/path/to/dir"

        handler.on_created(event)

        callback.assert_not_called()

    def test_on_created_ignores_unmatched_extensions(self):
        """on_created ignores files with non-matching extensions."""
        callback = Mock()
        handler = FileChangeHandler(
            on_created=callback,
            extensions=[".pdf"],
        )

        event = MagicMock()
        event.is_directory = False
        event.src_path = "/path/to/file.txt"

        handler.on_created(event)

        callback.assert_not_called()

    def test_on_modified_calls_callback(self):
        """on_modified calls the callback with file path."""
        callback = Mock()
        handler = FileChangeHandler(
            on_modified=callback,
            extensions=[".txt"],
            debounce_seconds=0,
        )

        event = MagicMock()
        event.is_directory = False
        event.src_path = "/path/to/file.txt"

        handler.on_modified(event)

        callback.assert_called_once_with("/path/to/file.txt")

    def test_on_deleted_calls_callback(self):
        """on_deleted calls the callback with file path."""
        callback = Mock()
        handler = FileChangeHandler(
            on_deleted=callback,
            extensions=[".txt"],
        )

        event = MagicMock()
        event.is_directory = False
        event.src_path = "/path/to/file.txt"

        handler.on_deleted(event)

        callback.assert_called_once_with("/path/to/file.txt")

    def test_on_moved_calls_callback(self):
        """on_moved calls the callback with src and dest paths."""
        callback = Mock()
        handler = FileChangeHandler(
            on_moved=callback,
            extensions=[".txt"],
        )

        event = MagicMock()
        event.is_directory = False
        event.src_path = "/path/old.txt"
        event.dest_path = "/path/new.txt"

        handler.on_moved(event)

        callback.assert_called_once_with("/path/old.txt", "/path/new.txt")

    def test_telemetry_tracking(self):
        """Telemetry tracks event counts."""
        handler = FileChangeHandler(
            on_created=Mock(),
            extensions=[".txt"],
            debounce_seconds=0,
        )

        event = MagicMock()
        event.is_directory = False
        event.src_path = "/path/file.txt"

        assert handler.telemetry["files_created"] == 0
        assert handler.telemetry["total_events"] == 0

        handler.on_created(event)

        assert handler.telemetry["files_created"] == 1
        assert handler.telemetry["total_events"] == 1
        assert handler.telemetry["last_event_time"] is not None

    def test_reset_telemetry(self):
        """reset_telemetry clears all counters."""
        handler = FileChangeHandler(
            on_created=Mock(),
            extensions=[".txt"],
            debounce_seconds=0,
        )

        event = MagicMock()
        event.is_directory = False
        event.src_path = "/path/file.txt"

        handler.on_created(event)
        assert handler.telemetry["total_events"] == 1

        handler.reset_telemetry()

        assert handler.telemetry["total_events"] == 0
        assert handler.telemetry["files_created"] == 0
        assert handler.telemetry["last_event_time"] is None

    def test_callback_error_handling(self):
        """Callback errors are caught and logged."""

        def failing_callback(path):
            raise ValueError("Test error")

        handler = FileChangeHandler(
            on_created=failing_callback,
            extensions=[".txt"],
            debounce_seconds=0,
        )

        event = MagicMock()
        event.is_directory = False
        event.src_path = "/path/file.txt"

        # Should not raise
        handler.on_created(event)

    def test_lru_cache_eviction(self):
        """Debounce cache evicts old entries when full."""
        handler = FileChangeHandler(debounce_seconds=0)
        handler._max_cache_size = 10

        # Fill cache beyond limit
        for i in range(15):
            handler._is_debounced(f"file{i}.txt")

        # Cache should be trimmed
        assert len(handler._last_processed) <= 10


class TestFileWatcher:
    """Tests for FileWatcher class."""

    def test_init_creates_handler(self):
        """FileWatcher creates a FileChangeHandler."""
        with tempfile.TemporaryDirectory() as tmpdir:
            callback = Mock()
            watcher = FileWatcher(
                directory=tmpdir,
                on_created=callback,
                extensions=[".txt"],
            )
            assert watcher._handler is not None
            assert watcher.directory == Path(tmpdir)

    def test_init_raises_for_missing_directory(self):
        """FileWatcher raises if directory doesn't exist."""
        with pytest.raises(FileNotFoundError):
            FileWatcher(directory="/nonexistent/path")

    @pytest.mark.skipif(not check_watchdog_available(), reason="watchdog not installed")
    def test_start_and_stop(self):
        """FileWatcher can start and stop."""
        with tempfile.TemporaryDirectory() as tmpdir:
            watcher = FileWatcher(directory=tmpdir)

            assert not watcher.is_running
            watcher.start()
            assert watcher.is_running

            watcher.stop()
            assert not watcher.is_running

    @pytest.mark.skipif(not check_watchdog_available(), reason="watchdog not installed")
    def test_context_manager(self):
        """FileWatcher works as context manager."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with FileWatcher(directory=tmpdir) as watcher:
                assert watcher.is_running
            assert not watcher.is_running

    @pytest.mark.skipif(not check_watchdog_available(), reason="watchdog not installed")
    def test_double_start_is_safe(self):
        """Starting twice doesn't create multiple observers."""
        with tempfile.TemporaryDirectory() as tmpdir:
            watcher = FileWatcher(directory=tmpdir)

            watcher.start()
            observer1 = watcher._observer

            watcher.start()  # Should log warning but not crash
            observer2 = watcher._observer

            assert observer1 is observer2
            watcher.stop()

    @pytest.mark.skipif(not check_watchdog_available(), reason="watchdog not installed")
    def test_double_stop_is_safe(self):
        """Stopping twice doesn't crash."""
        with tempfile.TemporaryDirectory() as tmpdir:
            watcher = FileWatcher(directory=tmpdir)
            watcher.start()
            watcher.stop()
            watcher.stop()  # Should not crash

    def test_telemetry_access(self):
        """FileWatcher exposes handler telemetry."""
        with tempfile.TemporaryDirectory() as tmpdir:
            watcher = FileWatcher(directory=tmpdir)
            telemetry = watcher.telemetry
            assert "total_events" in telemetry
            assert telemetry["total_events"] == 0


class TestCheckWatchdogAvailable:
    """Tests for check_watchdog_available function."""

    def test_returns_bool(self):
        """Function returns a boolean."""
        result = check_watchdog_available()
        assert isinstance(result, bool)


class TestDefaultExtensions:
    """Tests for default extension list."""

    def test_default_extensions_include_common_types(self):
        """Default extensions include common document types."""
        handler = FileChangeHandler()
        defaults = handler._extensions

        # Check for common document types
        assert ".pdf" in defaults
        assert ".txt" in defaults
        assert ".md" in defaults
        assert ".json" in defaults

        # Check for code files
        assert ".py" in defaults
        assert ".js" in defaults
        assert ".ts" in defaults

        # Check for config files
        assert ".yaml" in defaults
        assert ".yml" in defaults
