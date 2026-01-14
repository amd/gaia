# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
Generic file watching utilities for GAIA agents.

Provides FileChangeHandler and FileWatcher for monitoring directories
and responding to file system events with callbacks.

Also provides file hashing utilities for duplicate detection.

Example:
    from gaia.utils import FileChangeHandler, FileWatcher, compute_file_hash

    def on_new_file(path: str):
        print(f"New file: {path}")
        file_hash = compute_file_hash(path)
        print(f"Hash: {file_hash}")

    watcher = FileWatcher(
        directory="./data",
        on_created=on_new_file,
        extensions=[".pdf", ".txt"],
    )
    watcher.start()
"""

import hashlib
import logging
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Union

try:
    from watchdog.events import FileSystemEvent, FileSystemEventHandler
    from watchdog.observers import Observer

    WATCHDOG_AVAILABLE = True
except ImportError:
    # Create dummy base class when watchdog is not available
    class FileSystemEventHandler:
        """Dummy base class when watchdog is not installed."""

    class FileSystemEvent:
        """Dummy event class when watchdog is not installed."""

        src_path: str = ""
        dest_path: str = ""
        is_directory: bool = False

    Observer = None
    WATCHDOG_AVAILABLE = False

logger = logging.getLogger(__name__)

# Type alias for event callbacks
EventCallback = Callable[[str], None]
MoveCallback = Callable[[str, str], None]  # (src_path, dest_path)
FilterCallback = Callable[[str], bool]

# Default chunk size for file hashing (64KB)
HASH_CHUNK_SIZE = 65536


def compute_file_hash(
    path: Union[str, Path],
    algorithm: str = "sha256",
    chunk_size: int = HASH_CHUNK_SIZE,
) -> Optional[str]:
    """
    Compute a hash of a file's contents.

    Uses chunked reading to handle large files efficiently without
    loading the entire file into memory.

    Args:
        path: Path to the file to hash.
        algorithm: Hash algorithm to use (default: sha256).
                   Supports any algorithm from hashlib.
        chunk_size: Size of chunks to read at a time (default: 64KB).

    Returns:
        Hex-encoded hash string, or None if file cannot be read.

    Example:
        from gaia.utils import compute_file_hash

        # Check if file was already processed
        file_hash = compute_file_hash("intake_form.pdf")
        if file_hash in processed_hashes:
            print("Already processed")
        else:
            process_file("intake_form.pdf")
            processed_hashes.add(file_hash)
    """
    try:
        file_path = Path(path)
        if not file_path.exists() or not file_path.is_file():
            return None

        hasher = hashlib.new(algorithm)
        with open(file_path, "rb") as f:
            while chunk := f.read(chunk_size):
                hasher.update(chunk)
        return hasher.hexdigest()
    except (OSError, IOError, ValueError) as e:
        logger.warning(f"Could not compute hash for {path}: {e}")
        return None


def compute_bytes_hash(
    data: bytes,
    algorithm: str = "sha256",
) -> str:
    """
    Compute a hash of bytes data.

    Useful when the file content is already loaded in memory.

    Args:
        data: Bytes to hash.
        algorithm: Hash algorithm to use (default: sha256).

    Returns:
        Hex-encoded hash string.

    Example:
        from gaia.utils import compute_bytes_hash

        with open("file.pdf", "rb") as f:
            content = f.read()
        file_hash = compute_bytes_hash(content)
    """
    hasher = hashlib.new(algorithm)
    hasher.update(data)
    return hasher.hexdigest()


class FileChangeHandler(FileSystemEventHandler):
    """
    Generic handler for file system events.

    A flexible, callback-based file system event handler that can be used
    with any agent or application. Supports:
    - Callbacks for created, modified, deleted, and moved events
    - File extension filtering
    - Custom filter predicates
    - Debouncing to prevent duplicate events
    - Telemetry tracking

    Example:
        from gaia.utils import FileChangeHandler
        from watchdog.observers import Observer

        def handle_new_file(path: str):
            print(f"Processing: {path}")

        handler = FileChangeHandler(
            on_created=handle_new_file,
            extensions=[".pdf", ".png", ".jpg"],
            debounce_seconds=2.0,
        )

        observer = Observer()
        observer.schedule(handler, "./intake_forms", recursive=False)
        observer.start()
    """

    # Default extensions for document processing
    DEFAULT_EXTENSIONS: List[str] = [
        ".pdf",
        ".txt",
        ".md",
        ".markdown",
        ".csv",
        ".json",
        ".py",
        ".js",
        ".ts",
        ".java",
        ".cpp",
        ".c",
        ".html",
        ".css",
        ".yaml",
        ".yml",
        ".xml",
        ".rst",
        ".log",
    ]

    def __init__(
        self,
        on_created: Optional[EventCallback] = None,
        on_modified: Optional[EventCallback] = None,
        on_deleted: Optional[EventCallback] = None,
        on_moved: Optional[MoveCallback] = None,
        extensions: Optional[List[str]] = None,
        filter_func: Optional[FilterCallback] = None,
        debounce_seconds: float = 2.0,
        ignore_directories: bool = True,
    ):
        """
        Initialize FileChangeHandler.

        Args:
            on_created: Callback for file creation. Receives file path.
            on_modified: Callback for file modification. Receives file path.
            on_deleted: Callback for file deletion. Receives file path.
            on_moved: Callback for file move/rename. Receives (src_path, dest_path).
            extensions: List of file extensions to watch (e.g., [".pdf", ".txt"]).
                       If None, uses DEFAULT_EXTENSIONS.
                       If empty list [], watches all files.
            filter_func: Custom filter function. If provided, called with file path
                        and should return True to process the event.
                        Takes precedence over extensions filter.
            debounce_seconds: Minimum time between processing same file.
            ignore_directories: If True, ignores directory events.

        Example:
            # Watch only PDFs and images
            handler = FileChangeHandler(
                on_created=process_file,
                extensions=[".pdf", ".png", ".jpg"],
            )

            # Watch all files with custom filter
            handler = FileChangeHandler(
                on_created=process_file,
                extensions=[],  # Watch all
                filter_func=lambda p: not p.startswith("."),  # Exclude hidden
            )
        """
        super().__init__()
        self._on_created = on_created
        self._on_modified = on_modified
        self._on_deleted = on_deleted
        self._on_moved = on_moved

        # Set up extensions filter
        if extensions is None:
            self._extensions: Set[str] = set(self.DEFAULT_EXTENSIONS)
        else:
            # Normalize extensions to lowercase with leading dot
            self._extensions = {
                ext.lower() if ext.startswith(".") else f".{ext.lower()}"
                for ext in extensions
            }

        self._filter_func = filter_func
        self._debounce_seconds = debounce_seconds
        self._ignore_directories = ignore_directories

        # Debounce tracking
        self._last_processed: Dict[str, float] = {}
        self._max_cache_size = 1000

        # Telemetry
        self._telemetry: Dict[str, Any] = {
            "files_created": 0,
            "files_modified": 0,
            "files_deleted": 0,
            "files_moved": 0,
            "total_events": 0,
            "last_event_time": None,
        }

    def _should_process(self, file_path: str) -> bool:
        """Check if file should be processed based on filters."""
        # Custom filter takes precedence
        if self._filter_func is not None:
            return self._filter_func(file_path)

        # Empty extensions list means watch all files
        if not self._extensions:
            return True

        # Check extension
        file_lower = file_path.lower()
        return any(file_lower.endswith(ext) for ext in self._extensions)

    def _is_debounced(self, file_path: str) -> bool:
        """Check if file was recently processed (within debounce window)."""
        current_time = time.time()
        last_time = self._last_processed.get(file_path, 0)

        if current_time - last_time <= self._debounce_seconds:
            return True

        # Update last processed time
        self._last_processed[file_path] = current_time

        # LRU cache eviction to prevent memory leaks
        if len(self._last_processed) > self._max_cache_size:
            num_to_remove = self._max_cache_size // 10
            sorted_items = sorted(self._last_processed.items(), key=lambda x: x[1])
            for path, _ in sorted_items[:num_to_remove]:
                del self._last_processed[path]
            logger.debug(f"Cleaned up {num_to_remove} old entries from debounce cache")

        return False

    def _update_telemetry(self, event_type: str) -> None:
        """Update telemetry statistics."""
        self._telemetry[event_type] += 1
        self._telemetry["total_events"] += 1
        self._telemetry["last_event_time"] = time.time()

        # Log telemetry periodically
        if self._telemetry["total_events"] % 10 == 0:
            logger.debug(
                f"File Watch Telemetry: "
                f"Created: {self._telemetry['files_created']}, "
                f"Modified: {self._telemetry['files_modified']}, "
                f"Deleted: {self._telemetry['files_deleted']}, "
                f"Moved: {self._telemetry['files_moved']}, "
                f"Total: {self._telemetry['total_events']}"
            )

    def on_created(self, event: FileSystemEvent) -> None:
        """Handle file creation."""
        if self._ignore_directories and event.is_directory:
            return

        if self._on_created and self._should_process(event.src_path):
            if not self._is_debounced(event.src_path):
                logger.debug(f"File created: {event.src_path}")
                try:
                    self._on_created(event.src_path)
                    self._update_telemetry("files_created")
                except Exception as e:
                    logger.error(
                        f"Error in on_created callback for {event.src_path}: {e}"
                    )

    def on_modified(self, event: FileSystemEvent) -> None:
        """Handle file modification."""
        if self._ignore_directories and event.is_directory:
            return

        if self._on_modified and self._should_process(event.src_path):
            if not self._is_debounced(event.src_path):
                logger.debug(f"File modified: {event.src_path}")
                try:
                    self._on_modified(event.src_path)
                    self._update_telemetry("files_modified")
                except Exception as e:
                    logger.error(
                        f"Error in on_modified callback for {event.src_path}: {e}"
                    )

    def on_deleted(self, event: FileSystemEvent) -> None:
        """Handle file deletion."""
        if self._ignore_directories and event.is_directory:
            return

        if self._on_deleted and self._should_process(event.src_path):
            logger.debug(f"File deleted: {event.src_path}")
            try:
                self._on_deleted(event.src_path)
                self._update_telemetry("files_deleted")
                # Clean up from debounce cache
                self._last_processed.pop(event.src_path, None)
            except Exception as e:
                logger.error(f"Error in on_deleted callback for {event.src_path}: {e}")

    def on_moved(self, event: FileSystemEvent) -> None:
        """Handle file move/rename."""
        if self._ignore_directories and event.is_directory:
            return

        src_path = event.src_path
        dest_path = getattr(event, "dest_path", None)

        if self._on_moved and dest_path:
            # Process if either source or destination matches filter
            if self._should_process(src_path) or self._should_process(dest_path):
                logger.debug(f"File moved: {src_path} -> {dest_path}")
                try:
                    self._on_moved(src_path, dest_path)
                    self._update_telemetry("files_moved")
                    # Update debounce cache
                    self._last_processed.pop(src_path, None)
                except Exception as e:
                    logger.error(f"Error in on_moved callback for {src_path}: {e}")

    @property
    def telemetry(self) -> Dict[str, Any]:
        """Get current telemetry statistics."""
        return self._telemetry.copy()

    def reset_telemetry(self) -> None:
        """Reset telemetry counters."""
        self._telemetry = {
            "files_created": 0,
            "files_modified": 0,
            "files_deleted": 0,
            "files_moved": 0,
            "total_events": 0,
            "last_event_time": None,
        }


class FileWatcher:
    """
    Convenience wrapper for watching a directory with FileChangeHandler.

    Combines Observer and FileChangeHandler for easy directory watching.
    Handles start/stop lifecycle and provides a clean API.

    Example:
        from gaia.utils import FileWatcher

        def process_intake(path: str):
            print(f"Processing intake form: {path}")

        watcher = FileWatcher(
            directory="./intake_forms",
            on_created=process_intake,
            extensions=[".pdf", ".png", ".jpg"],
        )

        watcher.start()
        # ... do work ...
        watcher.stop()

        # Or use as context manager:
        with FileWatcher("./data", on_created=process) as watcher:
            # watcher is running
            pass
        # watcher is stopped
    """

    def __init__(
        self,
        directory: Union[str, Path],
        on_created: Optional[EventCallback] = None,
        on_modified: Optional[EventCallback] = None,
        on_deleted: Optional[EventCallback] = None,
        on_moved: Optional[MoveCallback] = None,
        extensions: Optional[List[str]] = None,
        filter_func: Optional[FilterCallback] = None,
        debounce_seconds: float = 2.0,
        recursive: bool = False,
    ):
        """
        Initialize FileWatcher.

        Args:
            directory: Directory path to watch.
            on_created: Callback for file creation.
            on_modified: Callback for file modification.
            on_deleted: Callback for file deletion.
            on_moved: Callback for file move/rename.
            extensions: File extensions to watch. None uses defaults, [] watches all.
            filter_func: Custom filter predicate.
            debounce_seconds: Debounce time between processing same file.
            recursive: If True, watch subdirectories recursively.

        Raises:
            ImportError: If watchdog package is not installed.
            FileNotFoundError: If directory does not exist.
        """
        if not WATCHDOG_AVAILABLE:
            raise ImportError(
                "FileWatcher requires the 'watchdog' package.\n"
                "Install with: pip install 'watchdog>=2.1.0'\n"
                "Or: uv pip install -e '.[dev]'"
            )

        self._directory = Path(directory)
        if not self._directory.exists():
            raise FileNotFoundError(f"Directory does not exist: {directory}")

        self._recursive = recursive
        self._observer: Optional[Observer] = None

        self._handler = FileChangeHandler(
            on_created=on_created,
            on_modified=on_modified,
            on_deleted=on_deleted,
            on_moved=on_moved,
            extensions=extensions,
            filter_func=filter_func,
            debounce_seconds=debounce_seconds,
        )

    def start(self) -> None:
        """
        Start watching the directory.

        Safe to call multiple times - will not start multiple observers.
        """
        if self._observer is not None:
            logger.warning("FileWatcher already running")
            return

        self._observer = Observer()
        self._observer.schedule(
            self._handler,
            str(self._directory),
            recursive=self._recursive,
        )
        self._observer.start()
        logger.info(
            f"Started watching: {self._directory} " f"(recursive={self._recursive})"
        )

    def stop(self) -> None:
        """
        Stop watching the directory.

        Safe to call multiple times.
        """
        if self._observer is not None:
            self._observer.stop()
            self._observer.join(timeout=5.0)
            self._observer = None
            logger.info(f"Stopped watching: {self._directory}")

    @property
    def is_running(self) -> bool:
        """True if watcher is currently running."""
        return self._observer is not None and self._observer.is_alive()

    @property
    def directory(self) -> Path:
        """Directory being watched."""
        return self._directory

    @property
    def telemetry(self) -> Dict[str, Any]:
        """Get telemetry from the handler."""
        return self._handler.telemetry

    def __enter__(self) -> "FileWatcher":
        """Context manager entry - starts watching."""
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Context manager exit - stops watching."""
        self.stop()


def check_watchdog_available() -> bool:
    """Check if watchdog package is available."""
    return WATCHDOG_AVAILABLE


class FileWatcherMixin:
    """
    Mixin providing file watching capabilities for GAIA agents.

    Manages multiple FileWatcher instances with automatic cleanup.

    Example:
        from gaia import Agent, FileWatcherMixin

        class IntakeAgent(Agent, FileWatcherMixin):
            def __init__(self, **kwargs):
                super().__init__(**kwargs)

                self.watch_directory(
                    "./intake_forms",
                    on_created=self._process_form,
                    extensions=[".pdf", ".png"],
                )

            def _process_form(self, path: str):
                print(f"Processing: {path}")
    """

    _watchers: List[FileWatcher]

    def watch_directory(
        self,
        directory: Union[str, Path],
        on_created: Optional[EventCallback] = None,
        on_modified: Optional[EventCallback] = None,
        on_deleted: Optional[EventCallback] = None,
        on_moved: Optional[MoveCallback] = None,
        extensions: Optional[List[str]] = None,
        filter_func: Optional[FilterCallback] = None,
        debounce_seconds: float = 2.0,
        recursive: bool = False,
        auto_start: bool = True,
    ) -> FileWatcher:
        """
        Watch a directory for file changes.

        Args:
            directory: Directory path to watch.
            on_created: Callback for file creation.
            on_modified: Callback for file modification.
            on_deleted: Callback for file deletion.
            on_moved: Callback for file move/rename.
            extensions: File extensions to watch. None uses defaults, [] watches all.
            filter_func: Custom filter predicate.
            debounce_seconds: Debounce time between processing same file.
            recursive: If True, watch subdirectories recursively.
            auto_start: If True, start watching immediately.

        Returns:
            The FileWatcher instance.

        Example:
            self.watch_directory(
                "./data",
                on_created=self.handle_new_file,
                extensions=[".pdf", ".txt"],
            )
        """
        # Initialize watchers list if needed
        if not hasattr(self, "_watchers"):
            self._watchers = []

        watcher = FileWatcher(
            directory=directory,
            on_created=on_created,
            on_modified=on_modified,
            on_deleted=on_deleted,
            on_moved=on_moved,
            extensions=extensions,
            filter_func=filter_func,
            debounce_seconds=debounce_seconds,
            recursive=recursive,
        )

        self._watchers.append(watcher)

        if auto_start:
            watcher.start()

        return watcher

    def stop_all_watchers(self) -> None:
        """Stop all file watchers."""
        if hasattr(self, "_watchers"):
            for watcher in self._watchers:
                watcher.stop()
            logger.info(f"Stopped {len(self._watchers)} file watcher(s)")

    @property
    def watchers(self) -> List[FileWatcher]:
        """List of active file watchers."""
        if not hasattr(self, "_watchers"):
            self._watchers = []
        return self._watchers

    @property
    def watching_directories(self) -> List[Path]:
        """List of directories being watched."""
        return [w.directory for w in self.watchers if w.is_running]

    @property
    def watcher_telemetry(self) -> Dict[str, Any]:
        """Combined telemetry from all watchers."""
        combined = {
            "files_created": 0,
            "files_modified": 0,
            "files_deleted": 0,
            "files_moved": 0,
            "total_events": 0,
            "watcher_count": len(self.watchers),
            "active_count": sum(1 for w in self.watchers if w.is_running),
        }
        for watcher in self.watchers:
            t = watcher.telemetry
            combined["files_created"] += t.get("files_created", 0)
            combined["files_modified"] += t.get("files_modified", 0)
            combined["files_deleted"] += t.get("files_deleted", 0)
            combined["files_moved"] += t.get("files_moved", 0)
            combined["total_events"] += t.get("total_events", 0)
        return combined
