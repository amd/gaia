# File System Agent — Feature Specification

> **Branch:** `feature/chat-agent-file-navigation`
> **Date:** 2026-03-09
> **Status:** Draft (v2 — post architecture review)
> **Owner:** GAIA Team

---

## 1. Executive Summary

Enhance the GAIA Chat/RAG agent with a **production-grade file system agent** capable of browsing, searching, indexing, and deeply understanding a user's PC file system. The goal is to provide Claude Code-caliber file navigation combined with persistent semantic indexing — giving the agent a "mental map" of the user's machine that improves over time.

This spec draws on analysis of **11 leading AI file system agents** (Claude Code, Cursor, Copilot, Aider, Open Interpreter, Everything, MCP Filesystem, Anthropic Cowork, Windsurf, Cline, Devin) and maps their best capabilities onto GAIA's existing infrastructure.

---

## 2. Problem Statement

The current GAIA chat agent has **solid foundational file tools** (`search_file`, `search_directory`, `read_file`, `search_file_content`) and a **mature RAG pipeline** (FAISS + embeddings). However, it lacks:

| Gap | Impact |
|-----|--------|
| No persistent file system index/map | Agent forgets file locations between sessions |
| No structural understanding of the file system | Can't answer "what projects do I have?" or "where are my tax docs?" |
| No metadata-aware search (size, date, type) | Can't find "large files modified this week" |
| No file system statistics/dashboard | Can't summarize disk usage or folder sizes |
| No bookmark/favorite system | User must re-navigate to the same places repeatedly |
| No file preview for rich formats | Limited to text content, no image/media metadata |
| No tree visualization | Hard to understand deep directory structures |
| No incremental index updates | Must re-index everything on changes |
| Limited content extraction | No DOCX, PPTX, XLSX content extraction |

---

## 3. Competitive Analysis Summary

### 3.1 Approaches Compared

| Agent | Strategy | Strengths | Weaknesses |
|-------|----------|-----------|------------|
| **Claude Code** | Agentic search (Glob->Grep->Read, no index) | Highest precision, zero setup, fresh results | Token-heavy, no persistence |
| **Cursor** | Merkle tree + embeddings + AST | Fast incremental re-index, semantic search | Server-side processing, scales poorly >500K LOC |
| **Aider** | Repo map via tree-sitter AST + graph ranking | Elegant "table of contents" of codebase | Language-limited to tree-sitter support |
| **Everything (voidtools)** | NTFS MFT + change journal | Indexes millions of files in seconds | Name-only (no content search) |
| **OpenAI File Search** | Hosted RAG (auto chunk/embed) | 100M file scale, zero setup | Cloud-only, cost per query |
| **MCP Filesystem** | Structured tools with access control | Standard protocol, security annotations | Basic — no indexing or search intelligence |
| **Windsurf** | Codemaps + dependency graph + real-time flow | Deep cross-file understanding | Complex, code-focused |
| **Open Interpreter** | Code generation (Python/shell) | Full OS capability | No structure, high risk |

### 3.2 Key Insight: Hybrid Agentic + Indexed

The emerging consensus (2026) is that **agentic search and RAG indexing serve different needs**:

- **Agentic search** (like Claude Code): Best for precision, freshness, ad-hoc exploration
- **Persistent indexing** (like Cursor/OpenAI): Best for repeated access, semantic queries, large collections

**Our approach: Combine both.** Build a persistent file system index for structure/metadata, use agentic search for content, and layer semantic RAG for document Q&A.

---

## 4. Architecture

### 4.1 Three-Layer Design

```
+-------------------------------------------------------------+
|                    GAIA File System Agent                     |
+--------------+------------------+----------------------------+
|  Layer 1     |  Layer 2         |  Layer 3                   |
|  NAVIGATOR   |  SEARCH ENGINE   |  KNOWLEDGE BASE            |
|              |                  |                            |
|  * Tree view |  * Name search   |  * Semantic index (RAG)    |
|  * Browse    |  * Content grep  |  * File system map         |
|  * Bookmarks |  * Metadata      |  * Usage patterns          |
|              |    queries       |  * Persistent memory       |
|              |  * Glob patterns |  * Category tagging        |
+--------------+------------------+----------------------------+
|             File System Index (SQLite + WAL mode)            |
|  * File metadata cache    * Metadata-based change detection  |
|  * Directory structure    * Last-seen timestamps             |
|  * User bookmarks         * Category tags                    |
+--------------------------------------------------------------+
|          Existing GAIA Infrastructure                        |
|  * FileSearchToolsMixin   * RAGSDK (FAISS + embeddings)      |
|  * ShellToolsMixin        * FileWatcher (watchdog)           |
|  * PathValidator          * compute_file_hash()              |
|  * DatabaseMixin          * FileChangeHandler                |
+--------------------------------------------------------------+
```

### 4.2 Component Diagram

```
ChatAgent (enhanced)
  |
  +-- FileSystemToolsMixin (NEW - Layer 1 & 2, shared location)
  |     +-- browse_directory()         # NEW tool
  |     +-- tree()                     # NEW tool
  |     +-- file_info()                # NEW tool
  |     +-- find_files()               # REPLACES search_file + search_directory
  |     +-- bookmark()                 # NEW tool
  |     +-- read_file()                # ENHANCED existing tool (more formats)
  |
  +-- FileSystemIndexService (NEW - Layer 3 backend)
  |     Inherits: DatabaseMixin
  |     +-- scan_directory()
  |     +-- build_map()
  |     +-- update_incremental()
  |     +-- query_index()
  |     +-- get_statistics()
  |
  +-- RAGToolsMixin (EXISTING - enhanced)
  |     +-- index_document()           # add DOCX/PPTX/XLSX support
  |     +-- query_documents()          # integrate with file system map
  |     +-- index_directory()          # incremental with metadata check
  |
  +-- ShellToolsMixin (EXISTING - no changes)
  |
  +-- FileSearchToolsMixin (DEPRECATED - replaced by FileSystemToolsMixin)
        search_file()                  # -> merged into find_files()
        search_directory()             # -> merged into find_files()
        read_file()                    # -> moved to FileSystemToolsMixin (enhanced)
        search_file_content()          # -> enhanced and moved
```

### 4.3 Existing Tool Disposition

> **Critical decision:** The existing `FileSearchToolsMixin` tools are **replaced, not duplicated**.

| Existing Tool | Disposition | Rationale |
|---------------|-------------|-----------|
| `search_file()` | **Replaced** by `find_files()` | `find_files()` subsumes all search_file functionality plus adds index lookup, metadata filters, and smart scoping |
| `search_directory()` | **Replaced** by `find_files(search_type="name")` | Directory search is a subset of unified find |
| `read_file()` | **Enhanced** and moved to `FileSystemToolsMixin` | Add format support for DOCX, XLSX, images; keep same tool name for LLM familiarity |
| `search_file_content()` | **Enhanced** and moved to `FileSystemToolsMixin` | Add context lines, exclusion patterns, result grouping |

The `FileSearchToolsMixin` import is removed from `ChatAgent` and replaced with `FileSystemToolsMixin`. The old mixin remains available for other agents that don't need the full file system feature set.

---

## 5. Feature Specification

### 5.1 Layer 1: File System Navigator

These tools give the agent the ability to **browse and understand** the file system interactively.

> **IMPORTANT — Tool Decorator Pattern:** GAIA's `@tool` decorator (`src/gaia/agents/base/tools.py`) extracts descriptions from **docstrings**, not from a `description=` parameter. All tool code examples below use the correct pattern.

> **IMPORTANT — Path Validation:** Every tool that accepts a `path` parameter MUST validate it through `PathValidator.is_path_allowed()` before any filesystem access. This is enforced at the mixin level via a `_validate_path()` helper.

#### 5.1.1 `browse_directory(path, show_hidden, sort_by, filter_type)`

Browse a directory with rich metadata display.

```python
@tool(atomic=True)
def browse_directory(
    path: str = "~",           # Directory to browse (default: home)
    show_hidden: bool = False,  # Include hidden files/dirs
    sort_by: str = "name",      # name | size | modified | type
    filter_type: str = None,    # Filter by extension (e.g., "pdf", "py")
    max_items: int = 50,        # Limit results
) -> str:
    """Browse a directory and list its contents with metadata.

    Returns files and subdirectories with size, modification date, and type info.
    Use this to explore what's inside a folder.
    """
```

**Output format:**
```
C:\Users\John\Documents (23 items, 4.2 GB total)

  Type  Name                     Size      Modified
  ----  ----                     ----      --------
  [DIR] Projects/                1.2 GB    2026-03-08 14:30
  [DIR] Tax Returns/             340 MB    2026-02-15 09:12
  [DIR] Photos/                  2.1 GB    2026-03-07 18:45
  [FIL] resume.pdf               2.1 MB    2026-01-20 11:00
  [FIL] budget-2026.xlsx         145 KB    2026-03-01 16:22
  [FIL] notes.md                 12 KB     2026-03-09 08:15
  ...
```

#### 5.1.2 `tree(path, max_depth, show_sizes, include_pattern, exclude_pattern)`

Generate a tree visualization of directory structure.

```python
@tool(atomic=True)
def tree(
    path: str = ".",
    max_depth: int = 3,
    show_sizes: bool = False,
    include_pattern: str = None,   # Only show matching files
    exclude_pattern: str = None,   # Hide matching files/dirs
    dirs_only: bool = False,       # Only show directories
) -> str:
    """Show a tree visualization of a directory structure.

    Useful for understanding project layouts and folder hierarchies.
    Shows nested directories and files with optional size info.
    """
```

**Output format:**
```
C:\Users\John\Projects\my-app
+-- src/
|   +-- components/
|   |   +-- Header.tsx (4.2 KB)
|   |   +-- Footer.tsx (2.1 KB)
|   |   +-- Sidebar.tsx (3.8 KB)
|   +-- pages/
|   |   +-- index.tsx (1.5 KB)
|   |   +-- about.tsx (980 B)
|   +-- utils/
|       +-- helpers.ts (2.3 KB)
+-- package.json (1.2 KB)
+-- tsconfig.json (450 B)
+-- README.md (3.4 KB)

3 directories, 8 files, 20.0 KB total
```

#### 5.1.3 `file_info(path)`

Get detailed information about a file or directory.

```python
@tool(atomic=True)
def file_info(path: str) -> str:
    """Get comprehensive information about a file or directory.

    Returns size, dates, type, MIME type, encoding, and format-specific
    metadata (line count for text, dimensions for images, page count for PDFs).
    For directories: item count, total size, file type breakdown.
    """
```

**Returns:**
- Full path (resolved via `pathlib.Path`)
- File type (detected by `mimetypes` stdlib, with optional `python-magic` enhancement)
- Size (human-readable)
- Created / Modified dates
- MIME type
- Encoding detection (for text files, via `charset-normalizer`)
- Line count (for text files)
- Image dimensions (for images, via PIL if available)
- PDF page count (for PDFs)
- For directories: item count, total size, file type breakdown

#### 5.1.4 `read_file(path, lines, encoding)` (ENHANCED existing tool)

Read file contents with smart formatting. **Replaces** the existing `read_file()` from `FileSearchToolsMixin`.

```python
@tool(atomic=True)
def read_file(
    file_path: str,
    lines: int = 100,          # Number of lines to show (0 = all)
    encoding: str = "auto",    # Auto-detect encoding
    mode: str = "full",        # full | preview | metadata
) -> str:
    """Read and display a file's contents with intelligent type-based analysis.

    For text/code: shows content with line numbers.
    For CSV/TSV: shows tabular format with column headers.
    For JSON/YAML: pretty-printed with truncation for large objects.
    For images: dimensions, format, EXIF metadata.
    For PDF: page count, title, text preview.
    For DOCX/XLSX: structure overview and text content.
    For binary: hex dump header and file type detection.
    Use mode='preview' for a quick summary, mode='metadata' for info only.
    """
```

#### 5.1.5 `bookmark(action, path, label)`

Manage file/directory bookmarks for quick access.

```python
@tool(atomic=True)
def bookmark(
    action: str = "list",      # add | remove | list
    path: str = None,
    label: str = None,         # Human-friendly name
) -> str:
    """Save, list, or remove bookmarks for frequently accessed files and directories.

    Bookmarks persist across sessions in the file system index.
    Use 'add' with a path and optional label to save a bookmark.
    Use 'remove' with a path to delete a bookmark.
    Use 'list' to see all saved bookmarks.
    """
```

#### 5.1.6 `find_files(query, ...)` (REPLACES search_file + search_directory)

Unified intelligent file search — the **primary search entry point**.

```python
@tool(atomic=True)
def find_files(
    query: str,                     # Search query (name, content, or natural language)
    search_type: str = "auto",      # auto | name | content | metadata
    scope: str = "smart",           # smart | home | cwd | everywhere | <specific path>
    file_types: str = None,         # Comma-separated extensions: "pdf,docx,txt"
    size_range: str = None,         # e.g., ">10MB", "<1KB", "1MB-100MB"
    date_range: str = None,         # e.g., "today", "this-week", "2026-01", ">2026-01-01"
    max_results: int = 25,
    sort_by: str = "relevance",     # relevance | name | size | modified
) -> str:
    """Search for files by name, content, or metadata.

    This is the primary file search tool. Replaces search_file and search_directory.
    When index is available, searches the index first (<100ms).
    Falls back to filesystem glob when index is unavailable (<10sec).

    Search types:
    - auto: intelligently picks the best strategy based on query
    - name: search by file/directory name pattern (glob)
    - content: search inside file contents (grep-like)
    - metadata: filter by size, date, type

    Scope 'smart' searches: CWD first, then home common locations,
    then indexed directories. Use 'everywhere' for full drive search (slow).
    """
```

**Search strategy (when `search_type="auto"`):**
1. Check persistent index first (instant, if available)
2. If query looks like a glob pattern -> use glob matching
3. If query looks like a file name -> use name search
4. If query contains content-like terms -> use content search
5. Apply metadata filters (size, date, type) on results

**"Smart" scope logic:**
1. Current working directory (deepest)
2. Home directory common locations
3. All indexed directories
4. Full drive search (only if `scope="everywhere"` explicitly)

### 5.2 Deferred Tools (Phase 4+)

The following tools are **deferred** to reduce initial tool count and LLM confusion. They will be added after core tools are stable:

| Tool | Phase | Rationale |
|------|-------|-----------|
| `disk_usage(path, depth, top_n)` | Phase 3 | Requires index to be performant |
| `compare_files(path1, path2)` | Phase 4 | Niche use case, diff library needed |
| `find_duplicates(directory, method)` | Phase 4 | Requires content hashing (opt-in) |
| `recent_files(days, file_type, directory)` | Phase 3 | Can be done via `find_files(date_range="this-week")` |
| `find_by_metadata(criteria)` | Merged | Absorbed into `find_files()` metadata parameters |

---

### 5.3 Layer 3: Persistent Knowledge Base (File System Index)

A **SQLite-backed persistent index** that gives the agent a lasting understanding of the user's file system.

#### 5.3.1 Index Schema

```sql
-- Schema version tracking for migrations
CREATE TABLE schema_version (
    version INTEGER PRIMARY KEY,
    applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    description TEXT
);
INSERT INTO schema_version (version, description) VALUES (1, 'Initial schema');

-- Enable WAL mode for concurrent read/write access
PRAGMA journal_mode=WAL;

-- Core file metadata index
CREATE TABLE files (
    id INTEGER PRIMARY KEY,
    path TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    extension TEXT,
    mime_type TEXT,
    size INTEGER,
    created_at TIMESTAMP,
    modified_at TIMESTAMP,
    -- Change detection: size + mtime is the PRIMARY method (fast, no I/O)
    -- Content hash is OPTIONAL and computed only on user request (Phase 4)
    content_hash TEXT DEFAULT NULL,
    parent_dir TEXT NOT NULL,
    depth INTEGER,                -- Depth from scan root
    is_directory BOOLEAN DEFAULT FALSE,
    indexed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    metadata_json TEXT            -- Extra metadata (dimensions, page count, etc.)
);

-- Full-text search on file names and paths
CREATE VIRTUAL TABLE files_fts USING fts5(
    name, path, extension,
    content='files',
    content_rowid='id'
);

-- Directory statistics cache
CREATE TABLE directory_stats (
    path TEXT PRIMARY KEY,
    total_size INTEGER,
    file_count INTEGER,
    dir_count INTEGER,
    deepest_depth INTEGER,
    common_extensions TEXT,       -- JSON array of top extensions
    last_scanned TIMESTAMP
);

-- User bookmarks (persist across sessions)
CREATE TABLE bookmarks (
    id INTEGER PRIMARY KEY,
    path TEXT NOT NULL UNIQUE,
    label TEXT,
    category TEXT,               -- "project", "documents", "media", etc.
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Scan history for incremental updates
CREATE TABLE scan_log (
    id INTEGER PRIMARY KEY,
    directory TEXT NOT NULL,
    started_at TIMESTAMP,
    completed_at TIMESTAMP,
    files_scanned INTEGER,
    files_added INTEGER,
    files_updated INTEGER,
    files_removed INTEGER,
    duration_ms INTEGER
);

-- File categories (auto-tagged by extension)
CREATE TABLE file_categories (
    file_id INTEGER,
    category TEXT,               -- "code", "document", "image", "video", "data", etc.
    subcategory TEXT,            -- "python", "pdf", "jpeg", "csv", etc.
    FOREIGN KEY (file_id) REFERENCES files(id) ON DELETE CASCADE
);

-- Indexes for fast queries
CREATE INDEX idx_files_parent ON files(parent_dir);
CREATE INDEX idx_files_ext ON files(extension);
CREATE INDEX idx_files_modified ON files(modified_at);
CREATE INDEX idx_files_size ON files(size);
CREATE INDEX idx_files_hash ON files(content_hash) WHERE content_hash IS NOT NULL;
CREATE INDEX idx_categories ON file_categories(category, subcategory);
CREATE INDEX idx_bookmarks_path ON bookmarks(path);
```

**Schema changes from v1 review:**
- Added `schema_version` table for migrations
- Added `PRAGMA journal_mode=WAL` for concurrent read/write
- Removed `accessed_at` column (privacy-invasive, often inaccurate)
- Made `content_hash` DEFAULT NULL (opt-in, not computed during quick scan)
- Removed `last_accessed` from bookmarks (unnecessary)
- Added `ON DELETE CASCADE` to foreign keys
- Added conditional index on `content_hash` (only indexes non-null values)

#### 5.3.2 Schema Migration Strategy

```python
MIGRATIONS = {
    1: "Initial schema (see above)",
    # Future migrations:
    # 2: "ALTER TABLE files ADD COLUMN ...",
}

def migrate(self):
    """Apply pending schema migrations.

    On startup, checks schema_version and applies any missing migrations.
    If database is corrupted or schema is unrecognizable, drops and rebuilds.
    """
    current = self._get_schema_version()
    for version in sorted(MIGRATIONS.keys()):
        if version > current:
            self._apply_migration(version)

def _check_integrity(self) -> bool:
    """Run PRAGMA integrity_check on startup.

    If corrupted, log warning, delete database, and rebuild from scratch.
    The index is fully reconstructable from the filesystem.
    """
```

#### 5.3.3 `FileSystemIndexService` Class

```python
from gaia.database.mixin import DatabaseMixin

class FileSystemIndexService(DatabaseMixin):
    """Persistent file system index backed by SQLite.

    Inherits from DatabaseMixin for all database operations (init_db, query,
    insert, update, delete, transaction, table_exists, execute).

    Inspired by Everything's speed philosophy but with content awareness.
    Uses SQLite FTS5 for fast name/path search and incremental scanning
    with metadata-based change detection (size + mtime).

    Content hashing is OPT-IN and only computed during Phase 2 background
    analysis or on explicit user request.
    """

    DB_PATH = "~/.gaia/file_index.db"

    def __init__(self):
        self.init_db(str(Path(self.DB_PATH).expanduser()))
        self._ensure_schema()
        self._check_integrity()

    def _ensure_schema(self):
        """Create tables if they don't exist, run migrations if needed."""
        if not self.table_exists("schema_version"):
            self.execute(SCHEMA_SQL)
        else:
            self.migrate()

    def scan_directory(
        self,
        path: str,
        max_depth: int = 10,
        exclude_patterns: list = None,
        incremental: bool = True,
    ) -> ScanResult:
        """Scan a directory tree and populate the index.

        Phase 1 (quick): Metadata only — names, sizes, mtime.
        Uses size + mtime comparison for incremental change detection.
        Does NOT read file contents or compute hashes.

        Args:
            path: Directory to scan
            max_depth: Maximum recursion depth (default: 10)
            exclude_patterns: Directory names to skip (merged with defaults)
            incremental: If True, skip files where size+mtime unchanged
        """

    def query_files(
        self,
        name: str = None,        # FTS5 search on name/path
        extension: str = None,
        min_size: int = None,
        max_size: int = None,
        modified_after: str = None,
        modified_before: str = None,
        parent_dir: str = None,
        category: str = None,
        limit: int = 25,
    ) -> list[dict]:
        """Query the file index. Uses DatabaseMixin.query() internally."""

    def get_directory_stats(self, path: str) -> dict:
        """Get cached directory statistics."""

    def get_file_system_map(
        self,
        root: str = "~",
        depth: int = 2,
    ) -> "FileSystemMap":
        """Returns a structured summary of the file system for LLM context."""

    def auto_categorize(self, file_path: str) -> tuple:
        """Returns (category, subcategory) based on extension.

        Categories: code, document, image, video, audio, data, archive, config, other
        """

    def get_statistics(self) -> dict:
        """Total files indexed, breakdown by type, storage used, etc."""

    def cleanup_stale(self, max_age_days: int = 30) -> int:
        """Remove entries for files that no longer exist on disk."""

    # Bookmark operations (use DatabaseMixin.insert/query/delete)
    def add_bookmark(self, path: str, label: str = None, category: str = None) -> int
    def remove_bookmark(self, path: str) -> bool
    def list_bookmarks(self) -> list[dict]
```

#### 5.3.4 File System Map (LLM Context)

A condensed representation of the file system designed to fit in LLM context. Inspired by Aider's repo map concept.

```python
@dataclass
class FileSystemMap:
    """A compact 'mental model' of the user's file system.

    Injected into the LLM system prompt ON DEMAND (not always-on)
    when the user's query involves file operations.

    Decision: On-demand injection, not always-on.
    Rationale: Saves ~500-1000 tokens per non-file query. The agent
    can request it via a tool call when needed. Small local LLMs
    (Qwen3-0.6B) have limited context and cannot afford the overhead.
    """
    home_dir: str
    total_indexed: int
    last_scan: datetime

    # Top-level directory summary
    key_directories: list   # Documents, Projects, Downloads, etc.

    # Bookmarked locations
    bookmarks: list

    # Recent activity
    recently_modified: list  # Last 10 files modified

    # File type distribution
    type_breakdown: dict     # {"pdf": 234, "py": 1502, ...}

    def to_context_string(self, max_tokens: int = 800) -> str:
        """Render as a compact string for LLM system prompt injection.

        Token budget reduced from 2000 to 800 to accommodate smaller
        local LLMs. Prioritizes bookmarks and recent files.
        """
```

**Example context string:**
```
## Your File System (indexed 2026-03-09)
Home: C:\Users\John (45.2 GB, 23,456 files)

Key Directories:
  Documents/ (12.3 GB) - PDFs, DOCX, spreadsheets
  Projects/ (8.1 GB) - Code repos: gaia, my-app, data-pipeline
  Downloads/ (6.2 GB) - Recent: installer.exe, report.pdf
  Desktop/ (1.1 GB) - Shortcuts, quick notes

Bookmarks:
  "GAIA Project" -> C:\Users\John\Work\gaia5
  "Tax Docs" -> C:\Users\John\Documents\Tax Returns\2025

Recently Modified:
  notes.md (8 min ago), budget.xlsx (2 hrs ago), app.py (yesterday)

File Types: 1,502 Python | 234 PDF | 189 Markdown | 156 JSON | ...
```

#### 5.3.5 Incremental Updates via Existing FileWatcher

> **Decision:** Reuse the existing `FileWatcher` and `FileChangeHandler` from
> `src/gaia/utils/file_watcher.py` instead of creating a parallel watcher.

```python
# In FileSystemToolsMixin initialization:
from gaia.utils.file_watcher import FileWatcher

def _start_watching(self, directories: list[str]):
    """Watch bookmarked/indexed directories for changes.

    IMPORTANT: Only watches explicitly bookmarked or user-scanned
    directories. Does NOT watch the entire home directory.
    Rationale: Watching too many directories exhausts OS watch handles
    (especially on Windows with ReadDirectoryChangesW buffer limits).
    """
    for directory in directories:
        watcher = FileWatcher(
            directory=directory,
            on_created=self._on_file_created,
            on_modified=self._on_file_modified,
            on_deleted=self._on_file_deleted,
            extensions=None,  # Watch all file types
        )
        watcher.start()
        self._active_watchers.append(watcher)

def _on_file_created(self, path: str):
    """Add new file to index (metadata only, no content read)."""

def _on_file_modified(self, path: str):
    """Update index entry with new size/mtime."""

def _on_file_deleted(self, path: str):
    """Remove file from index."""
```

#### 5.3.6 Initial Scan Strategy

The initial full scan needs to handle large file systems efficiently:

```
Phase 1: Quick Structure Scan (~5 seconds for typical home dir)
  - Walk directory tree using pathlib (names, sizes, mtime only)
  - NO file content reading, NO hashing
  - Build directory_stats entries
  - Populate files table with metadata
  - Build FTS5 index for name/path search
  - Change detection: compare size + mtime against existing index entries

Phase 2: Content Analysis (background, progressive, OPT-IN)
  - Only runs if user explicitly requests deeper indexing
  - Hash files for duplicate detection (user-facing dirs first)
  - Extract metadata from rich files (PDFs, images, DOCX)
  - Auto-categorize files
  - Update index progressively

Phase 3: Ongoing Maintenance
  - FileWatcher on bookmarked/scanned directories only
  - Periodic re-scan (configurable, default: weekly) to catch missed changes
  - Stale entry cleanup (files that no longer exist)
```

---

### 5.4 Enhanced Document Indexing (RAG Upgrades)

#### 5.4.1 New File Type Support

Extend `RAGSDK.index_document()` to support:

| Format | Library | Extraction |
|--------|---------|------------|
| **DOCX** | `python-docx` | Paragraphs, tables, headers, metadata |
| **PPTX** | `python-pptx` | Slide text, notes, speaker notes |
| **XLSX** | `openpyxl` | Sheet data, formulas (evaluated), headers |
| **HTML** | `beautifulsoup4` | Visible text, headings, links |
| **EPUB** | `ebooklib` | Chapters, metadata |
| **RTF** | `striprtf` | Plain text extraction |

#### 5.4.2 Smarter Chunking

Current chunking is line/character-based. Upgrade to **content-aware chunking**:

```python
class SmartChunker:
    """Content-aware document chunking.

    Uses Python stdlib for chunking — NO tree-sitter dependency.
    AST-based code chunking uses Python's built-in ast module for .py files,
    and regex-based function/class detection for other languages.

    Tree-sitter integration is DEFERRED to a future phase.
    """

    def chunk_markdown(self, content: str) -> list:
        """Split by headers, preserving section boundaries."""

    def chunk_prose(self, content: str) -> list:
        """Split by paragraphs with semantic boundary detection."""

    def chunk_tabular(self, content: str) -> list:
        """Split tables preserving header context with each chunk."""

    def chunk_python(self, content: str) -> list:
        """Split Python code by functions/classes using stdlib ast module."""
```

**Chunking parameters (following OpenAI defaults + our tuning):**
- Max chunk size: 800 tokens
- Overlap: 200 tokens (25%)
- Preserve semantic boundaries (paragraph, function, section)
- Include parent context (file name, section header) in each chunk

#### 5.4.3 Incremental Indexing with Metadata Change Detection

```python
def index_directory_incremental(self, directory: str) -> dict:
    """Index a directory, skipping files that haven't changed.

    Uses size + mtime from FileSystemIndexService for change detection.
    Only re-chunks and re-embeds files where size or mtime differs.
    Content hashing is NOT used for change detection (too slow).
    """
```

---

### 5.5 Layer 4: Data Scratchpad (SQLite Working Memory)

The **critical missing piece** for multi-document analysis. Gives the agent a structured
working memory where it can accumulate, transform, and query extracted data using SQL.

> **Key insight:** LLMs are bad at math but great at extracting structured data from
> unstructured text. SQLite is perfect at math but can't read PDFs. Combining them
> creates an agent that can process 12 months of credit card statements, extract every
> transaction, and produce perfect aggregations — something neither can do alone.

#### 5.5.1 Why a Scratchpad?

| Without Scratchpad | With Scratchpad |
|---|---|
| Must fit all data in LLM context window | Process documents one at a time, accumulate in DB |
| LLM does math (inaccurate) | SQL does math (perfect) |
| Can't handle 1000+ transactions | Handles millions of rows |
| Results lost between sessions | Persistent — pick up where you left off |
| No cross-document analysis | JOIN across tables from different documents |

#### 5.5.2 Architecture

```
Document Pipeline:
                                                    +------------------+
  PDF/DOCX/CSV  -->  RAG Extractor  -->  LLM  -->  | SQLite Scratchpad |
  (raw file)        (text/tables)      (parse     | +-- transactions  |
                                        to struct) | +-- categories    |
                                                    | +-- summaries    |
                                                    +--------+---------+
                                                             |
                                          SQL Query  <-------+
                                             |
                                          Results  -->  LLM  -->  Natural Language
                                                       (interpret      Summary
                                                        & present)
```

The scratchpad lives in the same `~/.gaia/file_index.db` database (separate tables
from the file system index) or optionally in a per-session temp database.

#### 5.5.3 Scratchpad Tools

```python
@tool(atomic=True)
def create_table(
    table_name: str,
    columns: str,
) -> str:
    """Create a table in the scratchpad database for storing extracted data.

    Use this to set up structured storage before processing documents.
    Column definitions follow SQLite syntax.

    Example: create_table("transactions",
        "date TEXT, description TEXT, amount REAL, category TEXT, source_file TEXT")
    """

@tool(atomic=True)
def insert_data(
    table_name: str,
    data: str,
) -> str:
    """Insert rows into a scratchpad table.

    Data is a JSON array of objects matching the table columns.
    Use this after extracting structured data from a document.

    Example: insert_data("transactions", '[
        {"date": "2026-01-05", "description": "NETFLIX", "amount": 15.99,
         "category": "subscription", "source_file": "jan-statement.pdf"},
        {"date": "2026-01-07", "description": "WHOLE FOODS", "amount": 87.32,
         "category": "groceries", "source_file": "jan-statement.pdf"}
    ]')
    """

@tool(atomic=True)
def query_data(
    sql: str,
) -> str:
    """Run a SQL query against the scratchpad database.

    Use SELECT queries to analyze accumulated data. Supports all SQLite
    functions: SUM, AVG, COUNT, GROUP BY, ORDER BY, JOINs, subqueries, etc.

    Examples:
        "SELECT category, SUM(amount) as total FROM transactions GROUP BY category ORDER BY total DESC"
        "SELECT description, COUNT(*) as freq, SUM(amount) as total FROM transactions GROUP BY description HAVING freq > 1 ORDER BY freq DESC"
        "SELECT strftime('%Y-%m', date) as month, SUM(amount) FROM transactions GROUP BY month"
    """

@tool(atomic=True)
def list_tables() -> str:
    """List all tables in the scratchpad database with their schemas and row counts.

    Use this to see what data has been accumulated so far.
    """

@tool(atomic=True)
def drop_table(table_name: str) -> str:
    """Remove a scratchpad table when analysis is complete.

    Use this to clean up after a task is done.
    """
```

#### 5.5.4 Scratchpad Service

```python
from gaia.database.mixin import DatabaseMixin

class ScratchpadService(DatabaseMixin):
    """SQLite-backed working memory for multi-document data analysis.

    Inherits from DatabaseMixin for all database operations.
    Uses the same database file as FileSystemIndexService but with
    a 'scratch_' prefix on all table names to avoid collisions.

    Tables are user-created via tools and can persist across sessions
    or be cleaned up after analysis.
    """

    TABLE_PREFIX = "scratch_"

    def __init__(self, db_path: str = "~/.gaia/file_index.db"):
        self.init_db(str(Path(db_path).expanduser()))

    def create_table(self, name: str, columns: str) -> str:
        """Create a prefixed table. Returns confirmation."""
        safe_name = self._sanitize_name(name)
        self.execute(f"CREATE TABLE IF NOT EXISTS {self.TABLE_PREFIX}{safe_name} ({columns})")
        return f"Table '{safe_name}' created."

    def insert_rows(self, table: str, data: list[dict]) -> int:
        """Bulk insert rows. Returns count inserted."""
        safe_name = f"{self.TABLE_PREFIX}{self._sanitize_name(table)}"
        count = 0
        with self.transaction():
            for row in data:
                self.insert(safe_name, row)
                count += 1
        return count

    def query_data(self, sql: str) -> list[dict]:
        """Execute a SELECT query. Only allows SELECT statements.

        Security: Rejects INSERT/UPDATE/DELETE/DROP/ALTER in this method.
        Those operations have their own dedicated methods.
        """
        normalized = sql.strip().upper()
        if not normalized.startswith("SELECT"):
            raise ValueError("Only SELECT queries allowed via query_data(). "
                           "Use insert_data() or drop_table() for mutations.")
        return self.query(sql)

    def list_tables(self) -> list[dict]:
        """List all scratchpad tables with schema and row count."""
        tables = self.query(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE :prefix",
            {"prefix": f"{self.TABLE_PREFIX}%"}
        )
        result = []
        for t in tables:
            display_name = t["name"].replace(self.TABLE_PREFIX, "", 1)
            schema = self.query(f"PRAGMA table_info({t['name']})")
            count = self.query(f"SELECT COUNT(*) as count FROM {t['name']}", one=True)
            result.append({
                "name": display_name,
                "columns": [{"name": c["name"], "type": c["type"]} for c in schema],
                "rows": count["count"],
            })
        return result

    def drop_table(self, name: str) -> str:
        """Drop a scratchpad table."""
        safe_name = f"{self.TABLE_PREFIX}{self._sanitize_name(name)}"
        self.execute(f"DROP TABLE IF EXISTS {safe_name}")
        return f"Table '{name}' dropped."

    def _sanitize_name(self, name: str) -> str:
        """Sanitize table/column names to prevent SQL injection."""
        import re
        clean = re.sub(r'[^a-zA-Z0-9_]', '_', name)
        if not clean or clean[0].isdigit():
            clean = f"t_{clean}"
        return clean
```

#### 5.5.5 Multi-Document Processing Pipeline

The scratchpad enables a **document processing pipeline** pattern:

```
Step 1: DISCOVER    find_files("credit card statement", file_types="pdf")
                    -> Found 12 PDF files in Documents/Statements/

Step 2: CREATE      create_table("transactions",
                      "date TEXT, description TEXT, amount REAL,
                       category TEXT, source_file TEXT")

Step 3: EXTRACT     For each PDF:
          (loop)      read_file(statement.pdf)
                      -> LLM extracts transactions from text
                      insert_data("transactions", [...extracted rows...])

Step 4: ANALYZE     query_data("SELECT category, SUM(amount), COUNT(*)
                      FROM transactions GROUP BY category
                      ORDER BY SUM(amount) DESC")

Step 5: INSIGHT     query_data("SELECT description, COUNT(*) as months,
                      SUM(amount) as total FROM transactions
                      GROUP BY description HAVING months >= 3
                      ORDER BY total DESC")
                    -> LLM interprets: "Hidden subscriptions detected..."

Step 6: REPORT      LLM synthesizes all query results into a natural
                    language report with actionable recommendations
```

**Max Steps Consideration:** The current ChatAgent `max_steps=10` may be insufficient
for processing 12 documents. The config should be increased for data analysis tasks,
or the pipeline should batch multiple document extractions per step.

**Recommended approach:**
- Batch extraction: process 3-4 documents per LLM call (reduce step count)
- Or add a `max_steps` override for analysis mode: `max_steps=30`
- Or implement a `process_batch()` tool that handles the loop internally

#### 5.5.6 Security Constraints

| Constraint | Implementation |
|---|---|
| **SQL injection prevention** | Table names sanitized; parameterized queries via DatabaseMixin |
| **Query restrictions** | `query_data()` only allows SELECT statements |
| **Table namespace** | All scratchpad tables prefixed with `scratch_` to isolate from system tables |
| **Size limits** | Max 100 tables, max 1M rows per table, max 100MB total scratchpad size |
| **No external data** | Scratchpad only stores data extracted from user's own files |
| **Cleanup** | `gaia fs scratchpad clear` CLI command to wipe all scratchpad tables |

---

## 6. Demo Scenarios

### 6.1 Demo: Personal Finance Analyzer

> **"Find my credit card statements, analyze a year of spending, and tell me
> where my money is going."**

**Pipeline:**

```
User: analyze my credit card spending for the past year

Agent Step 1 (find_files):
  Searching for credit card statements...
  Found 12 PDFs in C:\Users\John\Documents\Statements\Chase\
    jan-2025.pdf through dec-2025.pdf (total: 4.2 MB)

Agent Step 2 (create_table):
  Creating analysis workspace...
  Table 'transactions' ready (date, description, amount, category, source_file)

Agent Steps 3-6 (read_file + insert_data, batched 3 per step):
  Processing jan-2025.pdf... extracted 47 transactions
  Processing feb-2025.pdf... extracted 52 transactions
  Processing mar-2025.pdf... extracted 61 transactions
  ... (12 files, ~600 transactions total)

Agent Step 7 (query_data - category breakdown):
  SELECT category, SUM(amount) as total, COUNT(*) as count
  FROM transactions GROUP BY category ORDER BY total DESC

  Category        Total      Txns
  --------------- ---------- ----
  Housing         $24,000    12
  Groceries       $8,432     156
  Dining Out      $4,891     89
  Transportation  $3,200     48
  Subscriptions   $2,147     36
  Shopping        $1,893     23
  Entertainment   $1,456     18
  ...

Agent Step 8 (query_data - subscription detection):
  SELECT description, COUNT(*) as months, ROUND(AVG(amount),2) as avg
  FROM transactions GROUP BY description
  HAVING months >= 3 AND avg > 5 ORDER BY avg DESC

  Description          Months  Avg/Mo
  -------------------- ------  ------
  SPOTIFY PREMIUM      12      $15.99
  NETFLIX              12      $22.99
  NYT DIGITAL          12      $17.00
  ADOBE CREATIVE       12      $54.99
  CHATGPT PLUS         10      $20.00
  DROPBOX PLUS          8      $11.99
  HULU                  6      $17.99
  CRUNCHYROLL           4       $7.99
  ──────────────────────────────────────
  Total subscriptions:           $168.95/month ($2,027/year)

Agent Step 9 (query_data - month-over-month trend):
  SELECT strftime('%Y-%m', date) as month,
         ROUND(SUM(amount),2) as total
  FROM transactions GROUP BY month ORDER BY month

  Month      Total
  -----      -----
  2025-01    $4,120
  2025-02    $3,890
  2025-03    $5,210  << spike
  2025-04    $4,450
  ...
  2025-12    $6,780  << holiday spending

Agent Step 10 (final synthesis):

  # Your 2025 Spending Analysis

  **Total Spent:** $52,341 across 612 transactions

  ## Key Findings

  1. **Subscriptions are bleeding you dry**
     You're paying $168.95/month ($2,027/year) across 8 subscriptions.
     - Hulu + Netflix + Crunchyroll = 3 streaming services ($48.97/mo)
     - You haven't used Dropbox since August (consider canceling: saves $144/yr)

  2. **Dining out is your biggest discretionary expense**
     $4,891/year on restaurants and delivery. That's $407/month.
     March was the worst month ($890 on dining alone).

  3. **Holiday spending spike**
     December was 64% above your monthly average ($6,780 vs $4,362 avg).

  4. **Grocery spending is steady and reasonable**
     $702/month average with low variance. Well managed.

  ## Recommendations
  - Cancel Dropbox ($144/yr saved) and consolidate streaming ($215/yr saved)
  - Set a dining budget alert at $300/month
  - Start a holiday fund: $200/month to avoid December spikes
```

**Why this is a wow demo:**
- Processes 12 real PDFs from the user's actual PC
- Extracts ~600 transactions without hitting context limits
- SQL gives perfect math (no LLM hallucinated numbers)
- Finds hidden subscriptions automatically
- Actionable recommendations personalized to the user

**What's needed beyond the spec:**
- PDF table extraction (pdfplumber `extract_tables()`) — add to extractors
- `max_steps` increase to 15-20 for analysis mode
- Optionally: chart rendering in Electron UI (Recharts)

---

### 6.2 Demo: Tax Preparation Assistant

> **"Find all my tax-relevant documents and help me prepare for filing."**

```
User: help me prepare my 2025 taxes

Agent:
  Searching for tax-relevant documents...

  Found 23 documents across your PC:
  Income:
    W-2_2025_AMD.pdf — Documents/Tax/
    1099-INT_Chase.pdf — Documents/Tax/
    1099-DIV_Fidelity.pdf — Downloads/ (not filed yet!)

  Deductions:
    mortgage-interest-2025.pdf — Documents/Tax/
    property-tax-receipt.pdf — Documents/Tax/
    charitable-donations/ (4 receipts) — Documents/Tax/Donations/

  Medical:
    medical-expenses-2025.xlsx — Documents/Tax/

  Missing (expected but not found):
    - No 1099-NEC found (did you do any freelance work?)
    - No HSA contribution statement
    - No student loan interest form (1098-E)

  I've extracted and organized all the numbers:

  INCOME
    W-2 Wages:           $125,000
    Interest (1099-INT):  $1,234
    Dividends (1099-DIV): $3,456
    ─────────────────────────────
    Total Income:        $129,690

  DEDUCTIONS (Itemized)
    Mortgage Interest:    $14,200
    Property Tax:          $6,800
    Charitable:            $2,350
    Medical (above 7.5%):    $890
    ─────────────────────────────
    Total Deductions:     $24,240

  ESTIMATE
    Taxable Income:      $105,450
    Estimated Federal Tax: ~$18,200
    Already Withheld (W-2): $19,500
    ─────────────────────────────────
    Estimated Refund:      ~$1,300

  Action Items:
  1. Move 1099-DIV from Downloads/ to Documents/Tax/ (want me to suggest this?)
  2. Check if you have an HSA — you might be missing a deduction
  3. Review charitable donations — did you make any cash donations not receipted?
```

---

### 6.3 Demo: Research Paper Literature Review

> **"I have a bunch of research papers on transformer architectures. Summarize them
> and find connections."**

```
User: analyze all the ML papers in my research folder

Agent:
  Found 34 PDFs in Documents/Research/ML/
  Processing and indexing all papers...

  Created analysis tables:
  - papers (title, authors, year, venue, abstract, key_findings)
  - citations (paper_id, cited_paper_id, context)
  - topics (paper_id, topic, relevance_score)

  Extracted data from 34 papers (287 pages total).

  # Research Collection Summary

  ## By Topic
  | Topic                  | Papers | Key Authors        |
  |------------------------|--------|--------------------|
  | Attention Mechanisms   | 12     | Vaswani, Dao       |
  | Model Compression      | 8      | Hinton, Zhu        |
  | Multi-Modal Learning   | 7      | Radford, Li        |
  | Efficient Inference    | 5      | Leviathan, Pope    |
  | RLHF / Alignment       | 2      | Ouyang, Bai        |

  ## Timeline
  2023: 8 papers (mostly attention + compression)
  2024: 14 papers (shift toward efficiency + multi-modal)
  2025: 12 papers (inference optimization dominates)

  ## Gap Analysis
  Your collection is strong on attention and compression but MISSING:
  - Mixture of Experts (MoE) — no papers despite being a 2024-25 trend
  - Speculative decoding — only 1 brief mention across all papers
  - On-device inference (relevant to AMD NPU work!)

  ## Cross-Paper Insights
  Papers #4, #12, and #28 all cite the same Flash Attention result
  but reach different conclusions about optimal head dimensions.
  This might be worth investigating further.

  Want me to generate a literature review draft from these?
```

---

### 6.4 Demo: Contract & Deadline Tracker

> **"Find all my contracts and leases, extract key dates and obligations."**

```
User: find all my contracts and tell me about upcoming deadlines

Agent:
  Searching for contracts, leases, and agreements...

  Found 8 documents:
  - apartment-lease-2025.pdf — Documents/Housing/
  - car-lease-agreement.pdf — Documents/Auto/
  - employment-agreement-amd.pdf — Documents/Work/
  - freelance-contract-acme.pdf — Documents/Work/
  - gym-membership.pdf — Documents/
  - insurance-policy-home.pdf — Documents/Insurance/
  - insurance-policy-auto.pdf — Documents/Insurance/
  - phone-plan-tmobile.pdf — Downloads/

  Extracting key terms and dates...

  # Active Contracts & Deadlines

  | Contract           | Expires     | Monthly Cost | Auto-Renew | Action Needed |
  |--------------------|-------------|-------------|------------|---------------|
  | Apartment Lease    | 2026-08-31  | $2,100      | No         | 60-day notice required (by Jul 1) |
  | Car Lease          | 2026-11-15  | $389        | No         | Return or buyout ($18K residual) |
  | Gym Membership     | 2026-04-01  | $49         | YES        | Cancel by Mar 15 to avoid renewal |
  | T-Mobile Plan      | Month-to-month | $85      | YES        | No action needed |
  | Home Insurance     | 2026-06-01  | $142        | YES        | Review coverage before renewal |
  | Auto Insurance     | 2026-09-15  | $118        | YES        | Shop rates before renewal |

  ## Urgent (Next 30 Days)
  - GYM MEMBERSHIP auto-renews April 1 — cancel by March 15 if you don't want it
  - Home insurance renewal June 1 — good time to compare rates

  ## Coming Up (Next 90 Days)
  - Apartment lease notice deadline: July 1 (decide if staying or moving)
  - Home insurance renewal: June 1

  Want me to set up reminders for these dates?
```

---

### 6.5 Demo: "Clean Up My PC"

> **"My PC is getting slow. Find what's eating space and help me clean up."**

```
User: my disk is almost full, help me clean up

Agent:
  Scanning your drives...

  Drive C: 237 GB used of 256 GB (92% full!)

  # Space Analysis

  ## Biggest Offenders
  | Location                    | Size    | What's There |
  |-----------------------------|---------|-------------|
  | Users/John/AppData/         | 45.2 GB | App caches, browser data |
  | Users/John/Downloads/       | 23.1 GB | 847 files, oldest from 2023 |
  | Users/John/Videos/          | 18.4 GB | 23 video files |
  | Users/John/.conda/          | 12.8 GB | 4 conda environments |
  | Users/John/node_modules/    |  8.3 GB | 12 projects with node_modules |

  ## Safe to Delete (potential savings: 38.4 GB)

  1. **Downloads older than 6 months** — 14.2 GB
     327 files you probably don't need (installers, ZIPs, temp files)

  2. **Duplicate files** — 3.8 GB
     Found 89 duplicate files across your Documents and Downloads

  3. **Browser caches** — 6.1 GB
     Chrome: 3.2 GB, Firefox: 1.8 GB, Edge: 1.1 GB

  4. **Old conda environments** — 8.9 GB
     'env_old', 'test-env', 'backup-env' haven't been activated in 6+ months

  5. **Orphaned node_modules** — 5.4 GB
     6 projects with node_modules/ but no recent git activity

  That's 38.4 GB — enough to get you down to 77% disk usage.
  Want me to show the specific files for any of these categories?
```

---

### 6.6 Demo: "Smart Project Onboarding"

> **"I just cloned a new project. Help me understand the codebase."**

```
User: I just cloned github.com/some/project into Projects/new-project.
      Help me understand it.

Agent:
  Analyzing C:\Users\John\Projects\new-project...

  # Project Overview

  **Type:** Python web application (FastAPI + React)
  **Size:** 12,847 files, 234 MB (4,521 source files, rest is node_modules)
  **Languages:** Python 62%, TypeScript 31%, CSS 5%, Other 2%

  ## Architecture
  +-- backend/          FastAPI app (47 endpoints)
  |   +-- api/          REST routes
  |   +-- models/       SQLAlchemy ORM (12 tables)
  |   +-- services/     Business logic
  |   +-- tasks/        Celery background workers
  +-- frontend/         React 18 + Vite
  |   +-- components/   42 React components
  |   +-- pages/        8 page views
  |   +-- hooks/        Custom hooks (auth, data fetching)
  +-- docker/           Docker Compose (3 services: app, db, redis)
  +-- tests/            pytest (68% coverage)

  ## Key Files to Start With
  1. backend/api/routes.py — All API endpoints defined here
  2. frontend/src/App.tsx — Main routing and layout
  3. docker/docker-compose.yml — How to run everything
  4. README.md — Setup instructions (looks well-maintained)

  ## Dependencies of Note
  - PostgreSQL 15 (main database)
  - Redis (caching + Celery broker)
  - Stripe SDK (payment processing — handle with care)
  - SendGrid (email — check for API keys in .env)

  ## Potential Issues Found
  - .env.example exists but no .env — you'll need to create one
  - requirements.txt has 3 packages with known CVEs (run safety check)
  - No pre-commit hooks configured

  Want me to index the full codebase for RAG so you can ask questions about it?
```

---

### 6.7 What's Needed for These Demos

| Capability | Status | Needed For |
|---|---|---|
| File system search (`find_files`) | Spec'd (Phase 1) | All demos |
| Directory browsing (`browse_directory`, `tree`) | Spec'd (Phase 1) | All demos |
| PDF text extraction | Existing (RAG) | Finance, Tax, Contracts |
| PDF **table** extraction (pdfplumber) | **GAP — needs pdfplumber `extract_tables()`** | Finance (critical) |
| DOCX/XLSX reading | Spec'd (Phase 4) | Tax, Research |
| SQLite scratchpad (`create_table`, `insert_data`, `query_data`) | **Spec'd above (Phase 2)** | Finance, Tax, Research, Contracts |
| Multi-document batch processing | **Needs `max_steps` increase or batch tool** | Finance, Tax, Research |
| RAG indexing | Existing | Research, Onboarding |
| Disk usage analysis | Spec'd (Phase 3) | Cleanup demo |
| Duplicate detection | Spec'd (Phase 4) | Cleanup demo |
| Chart rendering (Electron UI) | **GAP — needs Recharts in frontend** | Finance (nice-to-have) |
| Calendar/reminder integration | **GAP — not in scope** | Contracts (nice-to-have) |

### 6.8 Priority Demo Implementation Order

| # | Demo | Impact | Effort | Phase Ready |
|---|------|--------|--------|-------------|
| 1 | **Personal Finance Analyzer** | Highest wow factor | Medium | Phase 2 + table extraction |
| 2 | **Clean Up My PC** | Most universal appeal | Low | Phase 3 |
| 3 | **Contract Deadline Tracker** | High practical value | Medium | Phase 2 + table extraction |
| 4 | **Tax Preparation Assistant** | High seasonal value | Medium | Phase 2 + DOCX/XLSX |
| 5 | **Smart Project Onboarding** | Developer audience | Low | Phase 1 + existing RAG |
| 6 | **Research Literature Review** | Academic audience | High | Phase 4 |

### 6.9 Agent Dashboard UI

The Electron/Web UI must provide **full visibility** into the agent's state, the
file system index, and the scratchpad database. This transforms the chat from a
black box into a transparent, inspectable system.

#### 6.9.1 Dashboard Layout

```
+------------------------------------------------------------------+
|  GAIA Chat Agent                                    [Settings] [?] |
+------------------+-----------------------------------------------+
|                  |                                                 |
|  SIDEBAR         |  CHAT AREA                                      |
|                  |                                                 |
|  [Chat]          |  User: analyze my credit card spending          |
|  [Dashboard]  <- |                                                 |
|  [Scratchpad] <- |  Agent: Searching for statements...             |
|  [File Index] <- |  [Step 1/10] find_files: Found 12 PDFs          |
|  [Documents]     |  [Step 2/10] create_table: "transactions"       |
|                  |  [Step 3/10] read_file: jan-2025.pdf             |
|  BOOKMARKS       |    -> Extracted 47 transactions                  |
|  * GAIA Project  |  ...                                            |
|  * Tax Docs      |                                                 |
|  * Statements    |  [SCRATCHPAD PREVIEW]                            |
|                  |  +------------------------------------------+   |
|  RECENT FILES    |  | transactions (612 rows)                  |   |
|  * notes.md      |  | date  | description  | amount | category|   |
|  * budget.xlsx   |  | 01-05 | NETFLIX      | 15.99  | sub     |   |
|  * app.py        |  | 01-07 | WHOLE FOODS  | 87.32  | grocery |   |
|                  |  | ...   | ...          | ...    | ...     |   |
|  INDEX STATUS    |  +------------------------------------------+   |
|  23,456 files    |                                                 |
|  Last: 2 min ago |  Final Answer: Your 2025 Spending Analysis...   |
|                  |                                                 |
+------------------+-----------------------------------------------+
```

#### 6.9.2 Dashboard Tab (Agent State Overview)

A dedicated **Dashboard** tab showing the overall agent configuration and state:

```
+------------------------------------------------------------------+
|  Agent Dashboard                                                   |
+------------------------------------------------------------------+
|                                                                    |
|  AGENT STATUS                          SYSTEM INFO                 |
|  +----------------------------+        +------------------------+  |
|  | State: Idle                |        | Model: Qwen3-Coder-30B |  |
|  | Session: 12 messages       |        | Backend: Lemonade      |  |
|  | Steps used: 0/20          |        | Max Steps: 20          |  |
|  | Tools registered: 16      |        | RAG: Active (5 docs)   |  |
|  +----------------------------+        +------------------------+  |
|                                                                    |
|  FILE SYSTEM INDEX                                                 |
|  +--------------------------------------------------------------+ |
|  | Status: Active | Files: 23,456 | Size: 12 MB | Last: 2m ago  | |
|  |                                                                | |
|  | Top Directories:                                               | |
|  | Documents/ ........... 12.3 GB  [======####] 27%               | |
|  | AppData/ ............. 10.1 GB  [=====###] 22%                 | |
|  | Downloads/ ............ 8.7 GB  [====###] 19%                  | |
|  |                                                                | |
|  | File Types: 1,502 .py | 234 .pdf | 189 .md | 156 .json       | |
|  |                                                                | |
|  | [Scan Now]  [Clear Index]  [View Full Index]                   | |
|  +--------------------------------------------------------------+ |
|                                                                    |
|  SCRATCHPAD                                                        |
|  +--------------------------------------------------------------+ |
|  | Tables: 2 | Total Rows: 724 | Size: 1.2 MB                   | |
|  |                                                                | |
|  | transactions .... 612 rows  (date, desc, amount, category)     | |
|  | tax_documents ... 112 rows  (type, source, amount, status)     | |
|  |                                                                | |
|  | [View Tables]  [Clear Scratchpad]  [Export CSV]                 | |
|  +--------------------------------------------------------------+ |
|                                                                    |
|  BOOKMARKS                                                         |
|  +--------------------------------------------------------------+ |
|  | GAIA Project -> C:\Users\John\Work\gaia5          [Remove]     | |
|  | Tax Docs     -> C:\Users\John\Documents\Tax       [Remove]     | |
|  | Statements   -> C:\Users\John\Documents\Statements [Remove]    | |
|  | [+ Add Bookmark]                                               | |
|  +--------------------------------------------------------------+ |
|                                                                    |
|  ACTIVE WATCHERS                                                   |
|  +--------------------------------------------------------------+ |
|  | Watching 3 directories for changes:                            | |
|  | C:\Users\John\Work\gaia5\             (142 events today)       | |
|  | C:\Users\John\Documents\Tax\          (0 events today)         | |
|  | C:\Users\John\Documents\Statements\   (2 events today)         | |
|  +--------------------------------------------------------------+ |
+------------------------------------------------------------------+
```

#### 6.9.3 Scratchpad Tab (Data Explorer)

A dedicated **Scratchpad** tab with a full data explorer for inspecting tables:

```
+------------------------------------------------------------------+
|  Scratchpad Explorer                                               |
+------------------+-----------------------------------------------+
|  TABLES          |  TABLE: transactions (612 rows)                 |
|                  |                                                 |
|  > transactions  |  [SQL Query Bar]                                |
|    612 rows      |  SELECT * FROM transactions LIMIT 100           |
|                  |  [Run Query]                                    |
|  > tax_documents |                                                 |
|    112 rows      |  +---+--------+-------------+--------+--------+|
|                  |  | # | date   | description | amount | categ  ||
|  > summaries     |  +---+--------+-------------+--------+--------+|
|    5 rows        |  | 1 | 01-05  | NETFLIX     | 15.99  | sub    ||
|                  |  | 2 | 01-07  | WHOLE FOODS | 87.32  | groc   ||
|                  |  | 3 | 01-09  | SHELL GAS   | 45.00  | trans  ||
|                  |  | 4 | 01-12  | AMAZON      | 129.99 | shop   ||
|                  |  | ...                                         ||
|  [+ New Table]   |  +---+--------+-------------+--------+--------+|
|  [Clear All]     |                                                 |
|                  |  QUICK STATS                                     |
|                  |  Total: $52,341 | Avg/mo: $4,362 | Rows: 612   |
|                  |                                                 |
|                  |  [Export CSV]  [Export JSON]  [Drop Table]       |
+------------------+-----------------------------------------------+
```

**Key features:**
- **Table list** — shows all scratchpad tables with row counts
- **Data grid** — paginated table view with sortable columns
- **SQL query bar** — run ad-hoc SELECT queries against scratchpad
- **Quick stats** — auto-computed SUM/AVG/COUNT for numeric columns
- **Export** — download table data as CSV or JSON
- **Schema view** — show column names, types, and sample data

#### 6.9.4 File Index Tab

A dedicated **File Index** tab for browsing the indexed file system:

```
+------------------------------------------------------------------+
|  File System Index                                                 |
+------------------------------------------------------------------+
|  [Search: ________________________] [Type: All v] [Sort: Name v]  |
|                                                                    |
|  PATH BROWSER                                                      |
|  C:\Users\John\                                                    |
|  +-- Documents/ (12.3 GB, 4,521 files)                             |
|  |   +-- Tax/ (890 MB, 23 files)                                   |
|  |   +-- Statements/ (340 MB, 48 files)                            |
|  |   +-- Projects/ (8.1 GB, 12,340 files)                          |
|  +-- Downloads/ (8.7 GB, 847 files)                                |
|  +-- Desktop/ (1.1 GB, 34 files)                                   |
|                                                                    |
|  SCAN HISTORY                                                      |
|  2026-03-09 14:30  Home directory  23,456 files  4.2s              |
|  2026-03-08 09:15  Documents/Tax   23 files      0.3s              |
|                                                                    |
|  [Scan Directory]  [Refresh]  [Clear Index]                        |
+------------------------------------------------------------------+
```

#### 6.9.5 Inline Scratchpad Preview in Chat

When the agent uses scratchpad tools during a conversation, the chat area shows
**inline previews** of the data — not just text descriptions:

```python
# In MessageBubble.tsx, detect scratchpad data markers in agent response:

# Agent response contains embedded data:
# <!--SCRATCHPAD_TABLE:transactions:SELECT * FROM transactions LIMIT 5-->

# Frontend renders this as an interactive table widget instead of markdown text.
# The widget supports:
# - Sortable column headers
# - Row count indicator
# - "Show more" / "View in Scratchpad" link
# - Expandable to full scratchpad tab
```

**Implementation approach:**
1. Agent tool results include a structured marker (e.g., `[TABLE:transactions:5 rows]`)
2. The SSE handler passes structured data alongside the text response
3. `MessageBubble.tsx` detects the marker and renders an interactive `DataTable` component
4. The `DataTable` component uses the same rendering as the Scratchpad tab

#### 6.9.6 Frontend Dependencies for Dashboard

| Package | Purpose | Size |
|---------|---------|------|
| `recharts` | Charts for spending breakdown, trends, disk usage | ~200 KB |
| `@tanstack/react-table` | Sortable/paginated data tables for scratchpad | ~50 KB |
| `react-icons` | File type icons for file index browser | ~20 KB |

These are added to the Electron app's `package.json`, not the Python backend.

#### 6.9.7 API Endpoints for Dashboard

The dashboard needs dedicated API endpoints (added to `src/gaia/api/`):

```
GET  /v1/dashboard/status           Agent state, model info, step count
GET  /v1/dashboard/index/stats      File index statistics
GET  /v1/dashboard/index/tree       Directory tree from index
GET  /v1/dashboard/scratchpad       List scratchpad tables
GET  /v1/dashboard/scratchpad/:table  Query a scratchpad table (paginated)
POST /v1/dashboard/scratchpad/query   Run a SELECT query
GET  /v1/dashboard/bookmarks        List bookmarks
POST /v1/dashboard/scan             Trigger a directory scan
DELETE /v1/dashboard/scratchpad     Clear all scratchpad tables
DELETE /v1/dashboard/index          Reset file index
```

---

## 7. Tool Registration Plan

### 7.1 New Mixin: `FileSystemToolsMixin`

**Location:** `src/gaia/agents/tools/filesystem_tools.py` (shared tools directory)

This mixin provides all Layer 1 and Layer 2 tools. Any agent can include it.

```python
from gaia.agents.base.tools import tool
from gaia.security import PathValidator

class FileSystemToolsMixin:
    """File system navigation, search, and management tools.

    Provides browse, tree, search, file info, bookmarks, and read capabilities.
    All path parameters are validated through PathValidator before access.

    Available to: ChatAgent, CodeAgent, or any agent needing file system access.

    Tool registration follows GAIA pattern: register_filesystem_tools() method
    with @tool decorator using docstrings for descriptions.
    """

    _fs_index: "FileSystemIndexService" = None
    _path_validator: PathValidator = None
    _active_watchers: list = []

    def _validate_path(self, path: str) -> Path:
        """Validate and resolve a path. Raises ValueError if blocked.

        All tools call this before any filesystem access.
        """
        resolved = Path(path).expanduser().resolve()
        if self._path_validator and not self._path_validator.is_path_allowed(str(resolved)):
            raise ValueError(f"Access denied: {resolved}")
        return resolved

    def register_filesystem_tools(self):
        """Register all file system tools. Called during agent init."""

        # Phase 1 Core Tools (6 tools):
        @tool(atomic=True)
        def browse_directory(...): ...

        @tool(atomic=True)
        def tree(...): ...

        @tool(atomic=True)
        def file_info(...): ...

        @tool(atomic=True)
        def find_files(...): ...

        @tool(atomic=True)
        def read_file(...): ...

        @tool(atomic=True)
        def bookmark(...): ...

        # Phase 3 Tools (added later):
        # disk_usage, recent_files

        # Phase 4 Tools (added later):
        # compare_files, find_duplicates
```

### 7.2 New Mixin: `ScratchpadToolsMixin`

**Location:** `src/gaia/agents/tools/scratchpad_tools.py` (shared tools directory)

```python
class ScratchpadToolsMixin:
    """SQLite scratchpad tools for structured data analysis.

    Gives the agent working memory to accumulate, transform, and query
    data extracted from documents. Enables multi-document analysis
    workflows like financial analysis, tax preparation, research reviews.

    Tool registration follows GAIA pattern: register_scratchpad_tools() method.
    """

    _scratchpad: "ScratchpadService" = None

    def register_scratchpad_tools(self):
        """Register scratchpad tools. Called during agent init."""

        @tool(atomic=True)
        def create_table(...): ...

        @tool(atomic=True)
        def insert_data(...): ...

        @tool(atomic=True)
        def query_data(...): ...

        @tool(atomic=True)
        def list_tables(...): ...

        @tool(atomic=True)
        def drop_table(...): ...
```

### 7.3 ChatAgent Integration

```python
# src/gaia/agents/chat/agent.py

class ChatAgent(
    Agent,
    RAGToolsMixin,
    FileToolsMixin,        # Chat-specific file tools (add_watch_directory)
    ShellToolsMixin,
    FileSystemToolsMixin,  # NEW: replaces FileSearchToolsMixin
    ScratchpadToolsMixin,  # NEW: structured data analysis
):
    """Chat Agent with RAG, file system navigation, data analysis,
    and shell capabilities."""
```

**MRO Note:** Neither `FileSystemToolsMixin` nor `ScratchpadToolsMixin` define
`__init__`. They are initialized via `register_*_tools()` called from the agent's
`_register_tools()` method, following the same pattern as `register_file_search_tools()`.

### 7.4 New Backend Services

**Location:** `src/gaia/filesystem/` and `src/gaia/scratchpad/`

```
src/gaia/filesystem/
+-- __init__.py
+-- index.py              # FileSystemIndexService (inherits DatabaseMixin)
+-- map.py                # FileSystemMap dataclass + context rendering
+-- categorizer.py        # Auto-categorization by extension
+-- extractors/
|   +-- __init__.py
|   +-- text.py           # Plain text, code files
|   +-- office.py         # DOCX, PPTX, XLSX (optional deps)
|   +-- pdf.py            # PDF text extraction (wraps existing rag/pdf_utils)
|   +-- pdf_tables.py     # PDF table extraction (pdfplumber extract_tables)
|   +-- image.py          # Image metadata (PIL if available)
+-- chunkers/
    +-- __init__.py
    +-- markdown_chunker.py   # Header/section-aware chunking
    +-- prose_chunker.py      # Paragraph-boundary chunking
    +-- python_chunker.py     # ast module-based Python chunking
    +-- table_chunker.py      # Header-preserving table chunking

src/gaia/scratchpad/
+-- __init__.py
+-- service.py            # ScratchpadService (inherits DatabaseMixin)
```

**Removed from original spec:**
- `watcher.py` — reuse existing `FileWatcher` from `gaia.utils.file_watcher`
- `extractors/media.py` — deferred (audio/video metadata is niche)
- `extractors/archive.py` — deferred (ZIP listing is niche)
- `chunkers/code_chunker.py` — replaced with `python_chunker.py` (no tree-sitter)

---

## 8. Configuration

### 8.1 ChatAgentConfig Additions

```python
@dataclass
class ChatAgentConfig:
    """Configuration for ChatAgent."""

    # ... existing fields ...

    # File System settings (NEW)
    enable_filesystem_index: bool = True        # Enable persistent file index
    filesystem_index_path: str = "~/.gaia/file_index.db"
    filesystem_auto_scan: bool = True           # Quick-scan home on first use
    filesystem_scan_depth: int = 3              # Default scan depth (conservative)
    filesystem_exclude_patterns: List[str] = field(default_factory=list)  # Extra exclusions
    filesystem_content_hashing: bool = False    # Opt-in content hashing for duplicates
    filesystem_watch_bookmarks: bool = True     # Watch bookmarked dirs for changes
    filesystem_map_max_tokens: int = 800        # Token budget for FS map in prompt
```

### 8.2 Feature Flags

The file system features can be fully disabled:
- `--no-filesystem-index` CLI flag disables the index entirely
- Without the index, tools still work but use direct filesystem access (slower)
- This is useful for privacy-sensitive environments

---

## 9. CLI Commands

### 9.1 `gaia fs` Subcommand

```
gaia fs scan [PATH]         Scan a directory and add to index
  --depth N                 Maximum depth (default: 3)
  --full                    Full scan with content hashing

gaia fs status              Show index statistics
  --verbose                 Show per-directory breakdown

gaia fs search QUERY        Search the file index
  --type EXT                Filter by extension
  --size RANGE              Filter by size (e.g., ">10MB")
  --date RANGE              Filter by date (e.g., "this-week")

gaia fs bookmarks           List saved bookmarks
  --add PATH [--label NAME] Add a bookmark
  --remove PATH             Remove a bookmark

gaia fs tree [PATH]         Show directory tree
  --depth N                 Maximum depth (default: 3)

gaia fs cleanup             Remove stale entries from index
  --days N                  Remove entries older than N days (default: 30)

gaia fs reset               Delete and rebuild the index from scratch
```

### 9.2 CLI Implementation

Add to `src/gaia/cli.py` following existing patterns (argparse subcommands):

```python
def add_fs_parser(subparsers):
    """Add 'gaia fs' CLI subcommand."""
    fs_parser = subparsers.add_parser("fs", help="File system index management")
    fs_sub = fs_parser.add_subparsers(dest="fs_command")

    # gaia fs scan
    scan = fs_sub.add_parser("scan", help="Scan a directory")
    scan.add_argument("path", nargs="?", default="~")
    scan.add_argument("--depth", type=int, default=3)
    scan.add_argument("--full", action="store_true")

    # gaia fs status
    fs_sub.add_parser("status", help="Show index statistics")

    # ... etc
```

---

## 10. Security & Privacy

### 10.1 Access Control

| Control | Implementation |
|---------|----------------|
| **Path validation** | Every tool calls `_validate_path()` which uses `PathValidator.is_path_allowed()` |
| **Symlink handling** | `Path.resolve()` follows symlinks to real path; on Windows, check for junction points via `os.path.islink()` |
| **Sensitive file detection** | Three-tier response: BLOCK, SKIP, or WARN (see below) |
| **Configurable exclusions** | Platform-conditional defaults merged with user config |
| **No content in index** | SQLite stores metadata only — no file contents |
| **Local-only** | All indexing happens locally, nothing sent to cloud |
| **Index file permissions** | Set 0600 on `file_index.db` (user-only read/write) |

### 10.2 Sensitive File Handling

| Action | Patterns | Behavior |
|--------|----------|----------|
| **BLOCK** (never index or read) | `*.pem`, `*.key`, `*.p12`, `*.pfx`, `id_rsa`, `id_ed25519`, `*.keystore`, `.aws/credentials`, `.ssh/*` | Skip entirely during scanning. If user explicitly requests via `read_file`, return "This file type is blocked for security." |
| **SKIP** (don't index, allow explicit read) | `.env`, `.env.*`, `.npmrc`, `.pypirc`, `credentials*`, `secrets*` | Skip during directory scanning. Allow `read_file` with a warning: "This file may contain sensitive data." |
| **WARN** (index metadata, warn on read) | `*password*`, `*token*`, `*secret*` | Index file metadata (name, size, date). Warn when content is read. |

### 10.3 Default Exclusions (Platform-Conditional)

```python
import platform

# Cross-platform exclusions
EXCLUDE_ALWAYS = [
    ".git", "node_modules", "__pycache__", ".venv", "venv",
    ".cache", ".tmp", "tmp",
]

# Windows-only exclusions
EXCLUDE_WINDOWS = [
    "AppData/Local/Temp",
    "AppData/Local/Microsoft",
    "$Recycle.Bin",
    "System Volume Information",
    "Windows",
    "Program Files",
    "Program Files (x86)",
    "ProgramData",
]

# macOS-only exclusions
EXCLUDE_MACOS = [
    ".Trash",
    "Library/Caches",
    "Library/Application Support",
]

# Linux-only exclusions
EXCLUDE_LINUX = [
    "/proc", "/sys", "/dev", "/tmp",
    ".local/share/Trash",
]

def get_default_exclusions() -> list:
    """Return platform-appropriate exclusion patterns."""
    exclusions = list(EXCLUDE_ALWAYS)
    system = platform.system()
    if system == "Windows":
        exclusions.extend(EXCLUDE_WINDOWS)
    elif system == "Darwin":
        exclusions.extend(EXCLUDE_MACOS)
    elif system == "Linux":
        exclusions.extend(EXCLUDE_LINUX)
    return exclusions
```

### 10.4 Index Security

The SQLite database at `~/.gaia/file_index.db` stores file paths, sizes, and modification dates. While no file content is stored, this metadata reveals the user's file system structure.

**Mitigations:**
- Set restrictive file permissions (0600) on database file
- Document the risk in user-facing documentation
- Provide `gaia fs reset` command to delete the index
- **Future consideration:** SQLCipher encryption (deferred, adds native dependency)

---

## 11. Performance Targets

| Operation | Target | Strategy |
|-----------|--------|----------|
| Home directory structure scan | < 5 sec | Metadata-only walk, skip excluded dirs |
| File name search (indexed) | < 100 ms | SQLite FTS5 query |
| File name search (not indexed) | < 10 sec | Fallback to `pathlib.rglob()` |
| Content search (single dir) | < 5 sec | Python `open()` + regex per file |
| Directory tree (depth=3) | < 2 sec | Direct filesystem walk |
| File info | < 500 ms | `os.stat()` call |
| Incremental index update | < 1 sec | Size + mtime comparison only |
| Full re-scan (50K files) | < 60 sec | Background, non-blocking |
| SQLite concurrent read/write | No errors | WAL mode + retry logic |

**Memory targets:**
| Scenario | Max Memory |
|----------|------------|
| Index with 50K files | < 50 MB (SQLite on disk) |
| Directory scan in progress | < 100 MB |
| File system map in memory | < 5 MB |

---

## 12. Implementation Phases

### Phase 1: Core Navigator (Week 1-2)
**Goal:** 6 core tools operational, no index dependency.

- [ ] Create `src/gaia/filesystem/` package structure
- [ ] Implement `FileSystemToolsMixin` with `register_filesystem_tools()`:
  - `browse_directory()` — directory listing with metadata
  - `tree()` — tree visualization
  - `file_info()` — detailed file/directory info
  - `find_files()` — unified search (glob-based, no index yet)
  - `read_file()` — enhanced file reading (text, code, CSV, JSON)
  - `bookmark()` — in-memory bookmarks (persisted in Phase 2)
- [ ] Add `_validate_path()` with `PathValidator` integration
- [ ] Remove `FileSearchToolsMixin` from `ChatAgent`, replace with `FileSystemToolsMixin`
- [ ] Keep `FileSearchToolsMixin` available for other agents
- [ ] Add `ChatAgentConfig` filesystem fields
- [ ] Add unit tests for all 6 tools (mock filesystem)
- [ ] Add integration tests with real filesystem
- [ ] Manual testing of navigation flow

### Phase 2: Persistent Index + Data Scratchpad (Week 2-3)
**Goal:** SQLite-backed file system memory AND structured data analysis.

**File System Index:**
- [ ] Implement `FileSystemIndexService` inheriting from `DatabaseMixin`
- [ ] Implement SQLite schema with WAL mode and FTS5
- [ ] Implement schema migration system (`schema_version` table)
- [ ] Implement `scan_directory()` — Phase 1 quick scan (metadata only)
- [ ] Implement FTS5 name/path search via `query_files()`
- [ ] Connect `find_files()` to index for fast lookup (< 100ms)
- [ ] Implement `bookmark()` persistence via index service
- [ ] Implement `auto_categorize()` by extension
- [ ] Add integrity check on startup with auto-rebuild
- [ ] Add `gaia fs` CLI commands: `scan`, `status`, `search`, `bookmarks`, `reset`
- [ ] Unit + integration tests for index service
- [ ] Test concurrent read/write (WAL mode)

**Data Scratchpad:**
- [ ] Create `src/gaia/scratchpad/` package
- [ ] Implement `ScratchpadService` inheriting from `DatabaseMixin`
- [ ] Implement `ScratchpadToolsMixin` with `register_scratchpad_tools()`:
  - `create_table()` — create analysis workspace tables
  - `insert_data()` — bulk insert extracted data (JSON array input)
  - `query_data()` — run SELECT queries for analysis
  - `list_tables()` — show scratchpad contents
  - `drop_table()` — cleanup after analysis
- [ ] Add table name sanitization and SQL injection prevention
- [ ] Add size limits (100 tables, 1M rows/table, 100MB total)
- [ ] Register `ScratchpadToolsMixin` in ChatAgent
- [ ] Add `gaia fs scratchpad clear` CLI command
- [ ] Unit tests for all 5 scratchpad tools
- [ ] Integration test: multi-document extraction pipeline
- [ ] Increase `max_steps` default to 20 for analysis workflows

**Demo validation:**
- [ ] End-to-end test: Personal Finance Analyzer demo with sample PDFs
- [ ] End-to-end test: Tax Preparation demo with sample documents

### Phase 3: Knowledge Base (Week 3-4)
**Goal:** Smart context, background maintenance, and additional tools.

- [ ] Implement `FileSystemMap` dataclass with `to_context_string()`
- [ ] Implement on-demand map injection (via tool, not always-on)
- [ ] Integrate `FileWatcher` from `gaia.utils.file_watcher` for real-time updates
- [ ] Limit watching to bookmarked/scanned directories only
- [ ] Implement `disk_usage()` tool (uses index data when available)
- [ ] Add first-run experience flow (quick scan on first tool use)
- [ ] Implement `cleanup_stale()` for removing deleted file entries
- [ ] Implement periodic re-scan (configurable interval, default: weekly)
- [ ] Performance benchmarking against targets
- [ ] Add `gaia fs cleanup` and `gaia fs tree` CLI commands

### Phase 4: Enhanced Extraction (Week 4-5)
**Goal:** Rich document support, smart chunking, and remaining tools.

- [ ] Implement content extractors:
  - Office formats (DOCX, PPTX, XLSX) — optional dependencies
  - Enhanced PDF (wrapping existing `rag/pdf_utils`)
  - Image metadata (PIL/Pillow if available)
  - HTML content extraction (beautifulsoup4)
- [ ] Implement smart chunkers:
  - Markdown chunker (header/section boundaries)
  - Prose chunker (paragraph boundaries)
  - Python chunker (stdlib `ast` module)
  - Table chunker (header-preserving)
- [ ] Integrate extractors with RAG pipeline
- [ ] Implement incremental indexing with metadata change detection
- [ ] Add `compare_files()` and `find_duplicates()` tools
- [ ] Opt-in content hashing for duplicate detection
- [ ] End-to-end testing with diverse file types

### Phase 5: Polish & Testing (Week 5-6)
**Goal:** Production-ready quality.

- [ ] Performance benchmarking against all targets (time + memory)
- [ ] Large file system stress testing (100K+ files)
- [ ] Windows/Linux/macOS compatibility testing
- [ ] Security audit (path traversal, symlink attacks, sensitive file handling)
- [ ] Documentation: user guide (`docs/guides/filesystem.mdx`)
- [ ] Documentation: SDK reference (`docs/sdk/sdks/filesystem.mdx`)
- [ ] Update `docs/docs.json` navigation
- [ ] Update `docs/reference/cli.mdx` with `gaia fs` commands
- [ ] Error handling and recovery for corrupted index
- [ ] MCP exposure consideration (expose tools via MCP for external agents)

---

## 13. Dependencies

### New Dependencies

| Package | Purpose | Size | Required? | Install Group |
|---------|---------|------|-----------|---------------|
| `pdfplumber` | PDF table extraction | ~2 MB | Recommended | `gaia[filesystem]` |
| `charset-normalizer` | Encoding detection | ~1 MB | Optional | `gaia[filesystem]` |
| `python-docx` | DOCX extraction | ~1 MB | Optional | `gaia[filesystem]` |
| `python-pptx` | PPTX extraction | ~1 MB | Optional | `gaia[filesystem]` |
| `openpyxl` | XLSX extraction | ~3 MB | Optional | `gaia[filesystem]` |
| `beautifulsoup4` | HTML extraction | ~500 KB | Optional | `gaia[filesystem]` |

**Removed from original spec:**
- `python-magic` — Replaced by `mimetypes` (stdlib). `python-magic` requires `libmagic` DLL on Windows which is unreliable. Extension-based detection via `mimetypes` is the DEFAULT.
- `chardet` — Replaced by `charset-normalizer` (MIT license, faster, used by `requests`)

### Existing Dependencies (already in GAIA)

| Package | Usage |
|---------|-------|
| `sqlite3` | Index database (stdlib) |
| `mimetypes` | File type detection (stdlib) |
| `pathlib` | Path manipulation (stdlib) |
| `ast` | Python code chunking (stdlib) |
| `watchdog` | File system monitoring |
| `faiss-cpu` | Vector search (RAG) |
| `sentence-transformers` | Embeddings (RAG) |
| `PyPDF2` / `pdfplumber` | PDF extraction |

### Extras Group

```toml
# In pyproject.toml or setup.cfg:
[project.optional-dependencies]
filesystem = [
    "charset-normalizer>=3.0",
    "python-docx>=1.0",
    "python-pptx>=1.0",
    "openpyxl>=3.1",
    "beautifulsoup4>=4.12",
]
```

---

## 14. Testing Strategy

### 14.1 Test Matrix

| Component | Unit Tests | Integration Tests | Notes |
|-----------|-----------|-------------------|-------|
| `FileSystemToolsMixin` (6 tools) | Yes (mock filesystem via `tmp_path`) | Yes (real filesystem) | Test each tool with expected output format |
| `FileSystemIndexService` | Yes (in-memory SQLite) | Yes (real SQLite file) | Test scan, query, FTS5, incremental, migrations |
| File watcher integration | Yes (mock events) | Yes (real watchdog) | Test create/modify/delete callbacks |
| Content extractors | Yes (fixture files) | No | Test each format with sample files |
| SmartChunker | Yes (fixture content) | No | Test boundary detection accuracy |
| CLI commands (`gaia fs`) | Yes (subprocess) | Yes (real index) | Test each subcommand |
| ChatAgent integration | No | Yes (mock LLM) | End-to-end with mock LLM choosing tools |

### 14.2 Test File Locations

```
tests/
+-- unit/
|   +-- test_filesystem_tools.py      # Tool unit tests
|   +-- test_filesystem_index.py      # Index service unit tests
|   +-- test_filesystem_extractors.py # Extractor unit tests
|   +-- test_filesystem_chunkers.py   # Chunker unit tests
+-- integration/
|   +-- test_filesystem_integration.py  # End-to-end with real FS
|   +-- test_filesystem_cli.py          # CLI command tests
+-- fixtures/
    +-- filesystem/
        +-- sample.pdf
        +-- sample.docx
        +-- sample.xlsx
        +-- sample.csv
        +-- sample.py
        +-- sample.md
```

### 14.3 Performance Benchmarks

```python
# tests/benchmarks/test_filesystem_perf.py

def test_scan_50k_files(tmp_path):
    """Create 50K files and verify scan completes in < 60 seconds."""

def test_fts5_search_latency(populated_index):
    """Verify FTS5 search returns in < 100ms on 50K file index."""

def test_memory_usage_during_scan():
    """Verify memory stays under 100MB during scan of 50K files."""
```

---

## 15. Success Metrics

| Metric | Target |
|--------|--------|
| Can answer "where is file X?" from index | < 1 second |
| Can summarize "what's in directory Y?" | Accurate tree + stats |
| Can find files by content | Correct results with context |
| Can find files by metadata (size, date, type) | Correct filtering |
| Remembers file locations across sessions | 100% (via SQLite) |
| Handles home dir with 50K+ files | No OOM, < 60s scan, < 50MB memory |
| Zero data leakage (all local) | Verified by security audit |
| Works on Windows, Linux, macOS | Tested on all three |
| LLM tool selection accuracy | > 90% correct tool choice (6 tools) |
| No tool name confusion | Zero overlap with remaining agent tools |

---

## 16. Decisions Log

Decisions made during architecture review (2026-03-09):

| # | Decision | Rationale |
|---|----------|-----------|
| D1 | Use docstrings for tool descriptions, not `description=` param | GAIA's `@tool` decorator reads from `__doc__` (line 73 of `tools.py`) |
| D2 | Inherit `FileSystemIndexService` from `DatabaseMixin` | Reuse existing `init_db()`, `query()`, `insert()`, `transaction()` |
| D3 | Reuse `FileWatcher` from `gaia.utils.file_watcher` | Avoid parallel infrastructure; existing watcher is mature |
| D4 | 6 core tools initially (not 11) | Reduce LLM confusion; deferred tools added in Phase 3-4 |
| D5 | Replace `FileSearchToolsMixin` in ChatAgent | Avoid semantic overlap (`find_files` vs `search_file`) |
| D6 | Metadata-based change detection (size + mtime) | Content hashing reads every file = too slow for quick scan |
| D7 | Content hashing is opt-in | Privacy + performance; enabled via `--full` flag or config |
| D8 | Watch only bookmarked/scanned directories | Full home dir watching exhausts OS watch handles |
| D9 | File system map is on-demand, not always-on | Save ~800 tokens per non-file query; critical for small LLMs |
| D10 | `mimetypes` (stdlib) over `python-magic` | `python-magic` requires `libmagic` DLL on Windows |
| D11 | `charset-normalizer` over `chardet` | MIT license, faster, modern replacement |
| D12 | No `accessed_at` in schema | Privacy-invasive, often inaccurate, marginal value |
| D13 | WAL mode for SQLite | Concurrent read/write without SQLITE_BUSY errors |
| D14 | Platform-conditional exclusion patterns | Windows-only paths like `$Recycle.Bin` don't exist on Linux |
| D15 | Three-tier sensitive file handling (BLOCK/SKIP/WARN) | Clear, explicit behavior instead of vague "warn" |
| D16 | Schema migration via `schema_version` table | Graceful upgrades for existing users |
| D17 | Conservative default scan depth (3) | Deeper scanning triggers antivirus alerts, takes too long |
| D18 | No tree-sitter dependency | Use stdlib `ast` for Python; regex for other languages |
| D19 | Defer Everything/Windows Search API integration | Platform-specific complexity; can accelerate later |
| D20 | Defer project/workspace concept | Good future feature but adds schema + UI complexity |
| D21 | SQLite scratchpad as agent working memory | LLMs bad at math, SQL perfect; enables multi-doc analysis without context limits |
| D22 | Scratchpad shares DB file with file index | Single `file_index.db` with `scratch_` table prefix; simpler than separate databases |
| D23 | `max_steps` increase to 20 for analysis mode | Processing 12 documents needs more than 10 steps; batch extraction helps too |
| D24 | `pdfplumber` for table extraction | Critical for finance/tax demos; PyMuPDF does text but not structured tables |
| D25 | Query-only restriction on `query_data()` tool | Security: mutations only through dedicated `insert_data`/`drop_table` tools |

---

## 17. References

- [Claude Code Tool System](https://callsphere.tech/blog/claude-code-tool-system-explained) — Agentic search architecture
- [Why Claude Code Doesn't Index](https://vadim.blog/claude-code-no-indexing) — Agentic vs. RAG tradeoffs
- [How Cursor Indexes Codebases](https://towardsdatascience.com/how-cursor-actually-indexes-your-codebase/) — Merkle tree + embeddings
- [Aider Repository Map](https://aider.chat/docs/repomap.html) — Tree-sitter AST graph ranking
- [Everything (voidtools)](https://www.voidtools.com/support/everything/indexes/) — NTFS MFT indexing
- [MCP Filesystem Server](https://github.com/modelcontextprotocol/servers/tree/main/src/filesystem) — Standard file tools
- [OpenAI File Search](https://developers.openai.com/api/docs/guides/tools-file-search/) — Hosted RAG at scale
- [Anthropic Agent Skills](https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills) — Folder-based context
- [Windsurf Codemaps](https://cognition.ai/blog/codemaps) — AI-annotated code navigation

---

## Appendix A: Deferred Feature Details

### A.1 `disk_usage(path, depth, top_n)` — Phase 3

```python
@tool(atomic=True)
def disk_usage(path: str = "~", depth: int = 2, top_n: int = 15) -> str:
    """Analyze disk usage for a directory.

    Shows which folders and file types are consuming the most space.
    Uses index data when available for fast results.
    """
```

### A.2 `compare_files(path1, path2)` — Phase 4

```python
@tool(atomic=True)
def compare_files(path1: str, path2: str, context_lines: int = 3) -> str:
    """Compare two files or directories.

    For text files, shows a unified diff.
    For directories, shows structural differences (files added/removed/changed).
    """
```

### A.3 `find_duplicates(directory, method)` — Phase 4

```python
@tool(atomic=True)
def find_duplicates(
    directory: str = "~", method: str = "hash", min_size: str = "1KB"
) -> str:
    """Find duplicate files by comparing content hashes, names, or sizes.

    Requires content hashing to be enabled (--full scan or config flag).
    Uses size-based pre-filtering to avoid hashing small files.
    """
```

### A.4 MCP Exposure — Phase 5

Consider exposing file system tools via MCP for external agent access:
- Read-only tools (`browse_directory`, `tree`, `file_info`, `find_files`, `read_file`) can be exposed
- Write tools and bookmark management should require explicit opt-in
- Use MCP tool annotations to mark read-only vs. write operations
