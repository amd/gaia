# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
SystemDiscovery: Local system scanner for day-zero bootstrap.

Scans the user's machine to discover projects, installed apps, browser data,
git repos, and email accounts. Each method returns a list of dicts (discovered
facts) that are NOT stored directly — the caller presents them for user review.

Cross-platform (Windows / macOS / Linux). No third-party dependencies — stdlib
plus ``gaia.logger``. Never crashes — catches exceptions, returns partial
results. A source with no scanner for the running platform is reported by name,
never silently empty; see :func:`unsupported_reason`.
"""

import configparser
import json
import os
import plistlib
import re
import shutil
import sqlite3
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from gaia.logger import get_logger

try:
    import winreg  # Windows only
except ImportError:
    winreg = None  # type: ignore[assignment]  # Linux / macOS

logger = get_logger(__name__)

# ============================================================================
# Constants
# ============================================================================

# Directories to skip during file system walks
_SKIP_DIRS = {
    ".git",
    ".hg",
    ".svn",
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
    ".env",
    ".tox",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "dist",
    "build",
    ".next",
    ".nuxt",
    ".cache",
    ".gradle",
    ".idea",
    ".vscode",
    "target",
    "bin",
    "obj",
}

# Extension -> language mapping
_EXT_LANG = {
    ".py": "Python",
    ".js": "JavaScript",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
    ".jsx": "JavaScript",
    ".rs": "Rust",
    ".go": "Go",
    ".java": "Java",
    ".kt": "Kotlin",
    ".cs": "C#",
    ".cpp": "C++",
    ".c": "C",
    ".h": "C/C++",
    ".rb": "Ruby",
    ".php": "PHP",
    ".swift": "Swift",
    ".dart": "Dart",
    ".lua": "Lua",
    ".r": "R",
    ".jl": "Julia",
    ".scala": "Scala",
    ".zig": "Zig",
    ".ex": "Elixir",
    ".exs": "Elixir",
    ".hs": "Haskell",
    ".ml": "OCaml",
    ".vue": "Vue",
    ".svelte": "Svelte",
    ".html": "HTML",
    ".css": "CSS",
    ".scss": "SCSS",
    ".sql": "SQL",
    ".sh": "Shell",
    ".ps1": "PowerShell",
    ".bat": "Batch",
    ".md": "Markdown",
    ".mdx": "MDX",
    ".ipynb": "Jupyter",
}

# App categories by keyword matching
_APP_CATEGORIES = {
    "IDE": [
        "visual studio",
        "vscode",
        "vs code",
        "intellij",
        "pycharm",
        "webstorm",
        "rider",
        "clion",
        "goland",
        "datagrip",
        "android studio",
        "eclipse",
        "sublime",
        "atom",
        "notepad++",
        "vim",
        "neovim",
        "emacs",
        "cursor",
    ],
    "DevTool": [
        "git",
        "docker",
        "postman",
        "insomnia",
        "wsl",
        "windows terminal",
        "powershell",
        "cmake",
        "mingw",
        "msys",
        "putty",
        "winscp",
        "filezilla",
        "wireshark",
        "fiddler",
        "node.js",
        "nodejs",
        "python",
        "go programming",
        "rust",
        "ruby",
        "java",
        "dotnet",
        ".net",
    ],
    "Browser": [
        "chrome",
        "firefox",
        "edge",
        "brave",
        "opera",
        "vivaldi",
        "arc",
    ],
    "Communication": [
        "slack",
        "discord",
        "teams",
        "zoom",
        "telegram",
        "signal",
        "whatsapp",
        "skype",
        "webex",
        "thunderbird",
        "outlook",
        "mailbird",
    ],
    "Creative": [
        "photoshop",
        "illustrator",
        "figma",
        "blender",
        "gimp",
        "inkscape",
        "obs",
        "davinci",
        "premiere",
        "after effects",
        "audacity",
        "ableton",
        "fl studio",
        "unity",
        "unreal",
        "godot",
    ],
    "Productivity": [
        "notion",
        "obsidian",
        "todoist",
        "trello",
        "jira",
        "confluence",
        "onenote",
        "evernote",
        "1password",
        "bitwarden",
        "lastpass",
        "keepass",
    ],
    "Cloud": [
        "aws",
        "azure",
        "google cloud",
        "gcloud",
        "terraform",
        "kubectl",
        "helm",
        "ansible",
    ],
    "Database": [
        "mysql",
        "postgresql",
        "postgres",
        "mongodb",
        "redis",
        "sqlite",
        "dbeaver",
        "datagrip",
        "sql server",
        "ssms",
        "pgadmin",
    ],
}

# Sensitive bookmark domains (banking, finance, health)
_SENSITIVE_DOMAINS = {
    "chase.com",
    "bankofamerica.com",
    "wellsfargo.com",
    "citi.com",
    "capitalone.com",
    "usbank.com",
    "schwab.com",
    "fidelity.com",
    "vanguard.com",
    "tdameritrade.com",
    "etrade.com",
    "robinhood.com",
    "coinbase.com",
    "binance.com",
    "paypal.com",
    "venmo.com",
    "mint.com",
    "creditkarma.com",
    "irs.gov",
    "turbotax.intuit.com",
    "healthcare.gov",
    "mychart.com",
    "portal.azure.com",
}

# Personal / social media domains
_PERSONAL_DOMAINS = {
    "facebook.com",
    "instagram.com",
    "twitter.com",
    "x.com",
    "tiktok.com",
    "reddit.com",
    "youtube.com",
    "netflix.com",
    "hulu.com",
    "spotify.com",
    "twitch.tv",
    "pinterest.com",
    "snapchat.com",
    "linkedin.com",
    "tumblr.com",
    "discord.com",
}

# Work-related domains
_WORK_DOMAINS = {
    "github.com",
    "gitlab.com",
    "bitbucket.org",
    "jira.atlassian.com",
    "confluence.atlassian.com",
    "slack.com",
    "notion.so",
    "figma.com",
    "vercel.com",
    "netlify.com",
    "aws.amazon.com",
    "console.cloud.google.com",
    "portal.azure.com",
    "stackoverflow.com",
    "npmjs.com",
    "pypi.org",
    "hub.docker.com",
    "circleci.com",
    "travis-ci.com",
    "docs.google.com",
    "drive.google.com",
}

# Email addresses, as they appear in credential dumps and mail-client config
_EMAIL_PATTERN = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")

# Mail-plist keys whose values hold the USER's own address. Everything else in
# a mail plist (previous recipients, signatures) belongs to other people and is
# deliberately not harvested. Compared lowercased.
_PLIST_ACCOUNT_KEYS = frozenset(
    {
        "emailaddresses",
        "emailaddress",
        "accountemailaddresses",
        "address",
        "acct",
        "fullusername",
        "username",
    }
)

# Mail hosts queried one-by-one in the macOS Keychain. `security` cannot
# enumerate items, so the set of hosts to ask about has to be fixed up front.
_MACOS_KEYCHAIN_MAIL_HOSTS = (
    "imap.gmail.com",
    "smtp.gmail.com",
    "outlook.office365.com",
    "smtp.office365.com",
    "imap-mail.outlook.com",
    "imap.mail.yahoo.com",
    "imap.mail.me.com",
    "smtp.mail.me.com",
)

# `security` exit code for "the item could not be found" (errSecItemNotFound)
_SECURITY_ITEM_NOT_FOUND = 44

# System-wide application directories. /System/Applications is deliberately
# absent: the ~40 Apple stock apps on every Mac would swamp the review list.
_MACOS_APP_DIRS: Tuple[Path, ...] = (Path("/Applications"),)

# System-wide freedesktop entry directories, including the Flatpak and Snap
# export dirs — that is how those two surface their GUI apps. Also the spec
# default when XDG_DATA_DIRS is unset; see _linux_desktop_dirs.
_LINUX_DESKTOP_DIRS: Tuple[Path, ...] = (
    Path("/usr/share/applications"),
    Path("/usr/local/share/applications"),
    Path("/var/lib/flatpak/exports/share/applications"),
    Path("/var/lib/snapd/desktop/applications"),
)


# ============================================================================
# Helpers
# ============================================================================


def _make_fact(
    content: str,
    context: str = "unclassified",
    entity: str = "",
    sensitive: bool = False,
    confidence: float = 0.4,
) -> Dict:
    """Create a discovered fact dict matching the spec format."""
    return {
        "content": content,
        "category": "fact",
        "context": context,
        "entity": entity,
        "sensitive": sensitive,
        "confidence": confidence,
        "source": "discovery",
        "approved": None,
    }


def _make_profile_fact(
    content: str,
    context: str = "global",
    entity: str = "",
    sensitive: bool = False,
    confidence: float = 0.7,
    domain: Optional[str] = None,
) -> Dict:
    """Create a discovered profile fact about the user."""
    return {
        "content": content,
        "category": "profile",
        "context": context,
        "entity": entity,
        "sensitive": sensitive,
        "confidence": confidence,
        "source": "discovery",
        "approved": None,
        "domain": domain,
    }


# ============================================================================
# File type categories — shared by recent-file scanners across all platforms
# ============================================================================

_FILE_TYPE_CATEGORIES = {
    # Office/productivity
    ".xlsx": ("office", "spreadsheets (Excel)"),
    ".xls": ("office", "spreadsheets (Excel)"),
    ".csv": ("office", "spreadsheets/data files"),
    ".docx": ("office", "Word documents"),
    ".doc": ("office", "Word documents"),
    ".pptx": ("office", "PowerPoint presentations"),
    ".ppt": ("office", "PowerPoint presentations"),
    ".odt": ("office", "OpenDocument text files"),
    ".ods": ("office", "OpenDocument spreadsheets"),
    ".pdf": ("reading", "PDF documents"),
    ".msg": ("office", "Outlook emails"),
    # Creative/design
    ".psd": ("design", "Photoshop files"),
    ".psb": ("design", "Photoshop files"),
    ".ai": ("design", "Illustrator files"),
    ".indd": ("design", "InDesign files"),
    ".xd": ("design", "Adobe XD files"),
    ".afphoto": ("design", "Affinity Photo files"),
    ".afdesign": ("design", "Affinity Designer files"),
    ".sketch": ("design", "Sketch files"),
    ".fig": ("design", "Figma files"),
    # Media — audio
    ".mp3": ("music", "music files"),
    ".flac": ("music", "lossless audio files"),
    ".aac": ("music", "audio files"),
    ".wav": ("music", "audio files"),
    ".ogg": ("music", "audio files"),
    ".m4a": ("music", "audio files"),
    # Media — video
    ".mp4": ("video", "video files"),
    ".mkv": ("video", "video files"),
    ".avi": ("video", "video files"),
    ".mov": ("video", "video files"),
    ".wmv": ("video", "video files"),
    ".prproj": ("video_edit", "Premiere Pro projects"),
    ".aep": ("video_edit", "After Effects projects"),
    ".drp": ("video_edit", "DaVinci Resolve projects"),
    # Photography
    ".raw": ("photo", "RAW photo files"),
    ".cr2": ("photo", "Canon RAW photos"),
    ".cr3": ("photo", "Canon RAW photos"),
    ".nef": ("photo", "Nikon RAW photos"),
    ".arw": ("photo", "Sony RAW photos"),
    ".dng": ("photo", "DNG raw photos"),
    ".jpg": ("photo", "JPEG images"),
    ".jpeg": ("photo", "JPEG images"),
    # Development
    ".py": ("dev", "Python files"),
    ".js": ("dev", "JavaScript files"),
    ".ts": ("dev", "TypeScript files"),
    ".go": ("dev", "Go files"),
    ".rs": ("dev", "Rust files"),
    ".java": ("dev", "Java files"),
    ".cpp": ("dev", "C++ files"),
    ".cs": ("dev", "C# files"),
    ".ipynb": ("dev", "Jupyter notebooks"),
    # Data/research
    ".json": ("data", "JSON data files"),
    ".xml": ("data", "XML files"),
    ".sql": ("data", "SQL files"),
    # 3D / game
    ".blend": ("3d", "Blender files"),
    ".fbx": ("3d", "3D model files"),
    ".obj": ("3d", "3D model files"),
    ".unitypackage": ("game_dev", "Unity packages"),
}


def _is_hidden(name: str) -> bool:
    """Check if a file/folder name is hidden (starts with dot)."""
    return name.startswith(".")


def _classify_path(path: Path) -> str:
    """Auto-classify a path into a context based on location."""
    parts = [p.lower() for p in path.parts]
    if "work" in parts:
        return "work"
    if "projects" in parts:
        return "work"
    if "personal" in parts:
        return "personal"
    if "documents" in parts:
        return "unclassified"
    return "unclassified"


def _classify_remote(remote_url: str) -> str:
    """Classify a git remote URL into a context."""
    url_lower = remote_url.lower()
    # Corporate / org patterns
    if any(
        org in url_lower
        for org in ["/amd/", "/microsoft/", "/google/", "/amazon/", "/meta/"]
    ):
        return "work"
    # Personal GitHub indicators — parse the hostname to avoid substring spoofing
    try:
        hostname = urlparse(remote_url).hostname or ""
    except Exception:
        hostname = ""
    if hostname == "github.com" or hostname.endswith(".github.com"):
        return "unclassified"
    return "unclassified"


def _classify_domain(domain: str) -> str:
    """Classify a domain into a context."""
    domain_lower = domain.lower()
    if domain_lower in _WORK_DOMAINS:
        return "work"
    if domain_lower in _PERSONAL_DOMAINS:
        return "personal"
    return "unclassified"


def _extract_domain(url: str) -> str:
    """Extract domain from a URL without urllib."""
    url = url.strip()
    # Remove protocol
    for prefix in ("https://", "http://", "ftp://"):
        if url.lower().startswith(prefix):
            url = url[len(prefix) :]
            break
    # Remove www.
    if url.lower().startswith("www."):
        url = url[4:]
    # Take just the domain
    domain = url.split("/")[0].split("?")[0].split("#")[0]
    # Remove port
    domain = domain.split(":")[0]
    return domain.lower()


def _classify_project(path: Path, languages: List[str]) -> str:
    """Return a brief classification of a project based on markers and languages.

    Checks for framework-specific files to provide richer insight.
    Returns an empty string if no specific classification is found.
    """
    markers = {
        "Dockerfile": "containerized app",
        "docker-compose.yml": "containerized app",
        "Makefile": "build-system project",
        "setup.py": "Python package",
        "pyproject.toml": "Python package",
        "package.json": "Node.js project",
        "Cargo.toml": "Rust project",
        "go.mod": "Go module",
        "pom.xml": "Java/Maven project",
        "build.gradle": "Java/Gradle project",
        "Gemfile": "Ruby project",
        "composer.json": "PHP project",
    }
    # Extensions that indicate project type (matched by suffix, not exact name)
    ext_markers = {
        ".sln": "C#/.NET solution",
    }
    found: List[str] = []
    try:
        entries = {e.name for e in os.scandir(str(path)) if e.is_file()}
    except (PermissionError, OSError):
        entries = set()

    for marker, label in markers.items():
        if marker in entries:
            found.append(label)

    for entry_name in entries:
        ext = os.path.splitext(entry_name)[1].lower()
        if ext in ext_markers:
            found.append(ext_markers[ext])

    # Framework-specific markers in subdirectories
    if (path / "src").is_dir():
        found.append("structured src/ layout")

    if found:
        return found[0]  # Return most specific marker

    # Fall back to language-based classification
    if languages:
        primary = languages[0]
        return f"{primary} codebase"

    return ""


def _detect_languages(path: Path, max_depth: int = 2) -> List[str]:
    """Detect programming languages in a directory by file extensions."""
    lang_counts: Dict[str, int] = {}
    try:
        for depth, (_dirpath, dirnames, filenames) in enumerate(os.walk(path)):
            if depth >= max_depth:
                dirnames.clear()
                continue
            # Skip hidden and ignored directories
            dirnames[:] = [
                d for d in dirnames if not _is_hidden(d) and d not in _SKIP_DIRS
            ]
            for fname in filenames:
                ext = os.path.splitext(fname)[1].lower()
                lang = _EXT_LANG.get(ext)
                if lang and lang not in ("Markdown", "MDX", "HTML", "CSS", "SCSS"):
                    lang_counts[lang] = lang_counts.get(lang, 0) + 1
    except (PermissionError, OSError):
        pass
    # Return top languages sorted by count
    sorted_langs = sorted(lang_counts.items(), key=lambda x: x[1], reverse=True)
    return [lang for lang, _ in sorted_langs[:5]]


def _safe_read_json(path: Path) -> Optional[dict]:
    """Read and parse a JSON file, returning None on any error."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None


def _safe_copy_and_query_sqlite(
    db_path: Path, query: str, params: tuple = (), label: str = ""
) -> List[tuple]:
    """Copy a SQLite DB to temp dir and query it (avoids lock issues).

    Browsers hold locks on their databases; copying first is required. The
    ``-wal``/``-shm`` sidecars are copied too — Safari's History.db and
    Firefox's places.sqlite run in WAL mode, so the most recent visits (exactly
    what a 30-day scan wants) live in the -wal until the next checkpoint.

    Args:
        db_path: The database to copy and read.
        query: SQL to execute against the copy.
        params: Bound parameters for `query`.
        label: Human-readable source name used when a permission denial has to
            be reported (for example "Safari history").

    Returns:
        The fetched rows, or an empty list when the database is missing or
        unreadable. A permission denial is reported at WARNING with the remedy;
        every other failure is logged at DEBUG.
    """
    if not db_path.exists():
        return []
    tmp_path = None
    sidecar_suffixes = ("-wal", "-shm")
    try:
        tmp_fd, tmp_path = tempfile.mkstemp(suffix=".db")
        os.close(tmp_fd)
        shutil.copy2(str(db_path), tmp_path)
        for suffix in sidecar_suffixes:
            sidecar = db_path.with_name(db_path.name + suffix)
            if sidecar.exists():
                shutil.copy2(str(sidecar), tmp_path + suffix)
        conn = sqlite3.connect(tmp_path)
        try:
            cursor = conn.execute(query, params)
            return cursor.fetchall()
        finally:
            conn.close()
    except PermissionError as e:
        logger.warning(
            "%s", _permission_denied_message(label or "browser database", db_path, e)
        )
        return []
    except (OSError, sqlite3.Error) as e:
        logger.debug("SQLite query failed for %s: %s", db_path, e)
        return []
    finally:
        if tmp_path:
            for path in (tmp_path, *(tmp_path + s for s in sidecar_suffixes)):
                try:
                    os.unlink(path)
                except FileNotFoundError:
                    pass
                except OSError as e:
                    logger.debug("Could not remove temp database copy %s: %s", path, e)


def _categorize_app(app_name: str) -> str:
    """Categorize an application by its name."""
    name_lower = app_name.lower()
    for category, keywords in _APP_CATEGORIES.items():
        for keyword in keywords:
            if keyword in name_lower:
                return category
    return "Other"


def _collect_plist_emails(node: Any, under_account_key: bool = False) -> List[str]:
    """Collect the user's own addresses from a decoded mail property list.

    Only values reached through an account-identity key are read. A mail plist
    also holds correspondents (previous recipients, signatures), and those are
    other people's addresses — harvesting the whole tree would put them in the
    user's review list. Nesting varies across macOS releases, so the walk is
    recursive, but it only descends into account-bearing keys.

    Args:
        node: A decoded plist value (dict, list, or scalar).
        under_account_key: True when `node` was reached through a key in
            ``_PLIST_ACCOUNT_KEYS``; only then are strings harvested.

    Returns:
        Every address found, in traversal order. Duplicates are not removed —
        the caller deduplicates.
    """
    found: List[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            is_account_key = isinstance(key, str) and key.lower() in _PLIST_ACCOUNT_KEYS
            found.extend(_collect_plist_emails(value, is_account_key))
    elif isinstance(node, (list, tuple)):
        for value in node:
            found.extend(_collect_plist_emails(value, under_account_key))
    elif isinstance(node, str) and under_account_key:
        found.extend(match.group(0) for match in _EMAIL_PATTERN.finditer(node))
    return found


# ============================================================================
# Platform support
# ============================================================================

# Discovery sources that only have a scanner for some platforms. A source that
# is absent from this map is platform-neutral and runs everywhere.
#
# This map is the GATE, not documentation: `scan_all` and the Agent UI skip a
# source without calling it when the running platform is missing here. Adding a
# platform branch to a scanner without adding it here leaves that branch
# unreachable, behind a log line insisting no scanner exists. Update both.
_PLATFORM_SUPPORT: Dict[str, Tuple[str, ...]] = {
    "installed_apps": ("win32", "darwin", "linux"),
    "browser_bookmarks": ("win32", "darwin", "linux"),
    "browser_history": ("win32", "darwin", "linux"),
    "email_accounts": ("win32", "darwin", "linux"),
    "windows_userassist": ("win32",),
    "macos_app_usage": ("darwin",),
    "recent_file_types": ("win32", "darwin", "linux"),
}


def _platform_key() -> str:
    """Return the running platform as a ``_PLATFORM_SUPPORT`` key.

    Normalizes every Linux variant ``sys.platform`` can report ("linux",
    "linux2") to "linux". Other values ("win32", "darwin", "freebsd13") pass
    through unchanged.

    Returns:
        The normalized platform key for the running interpreter.
    """
    import sys

    if sys.platform.startswith("linux"):
        return "linux"
    return sys.platform


def unsupported_reason(source: str) -> Optional[str]:
    """Return why `source` cannot run on this platform, or None if it can.

    Sources absent from ``_PLATFORM_SUPPORT`` are platform-neutral and always
    return None.

    Args:
        source: A discovery source name as used by :meth:`SystemDiscovery.scan_all`
            (for example "browser_history").

    Returns:
        A one-line, end-user-readable reason when the source has no scanner for
        the running platform, otherwise None. This string is shown in the Agent
        UI, so it states what happened, not what a contributor should do about
        it — :func:`_log_unsupported` adds that hint for the log.
    """
    supported = _PLATFORM_SUPPORT.get(source)
    if supported is None:
        return None
    platform = _platform_key()
    if platform in supported:
        return None
    return (
        f"no scanner for '{source}' on {platform} "
        f"(supported: {', '.join(supported)}) — nothing was scanned."
    )


def _log_unsupported(source: str) -> List[Dict]:
    """Log that `source` has no branch for the running platform, return [].

    The single place an unsupported platform is reported, so a source is never
    silently empty. The log line adds the contributor hint that the user-facing
    reason deliberately omits.

    Args:
        source: A discovery source name.

    Returns:
        An empty list, so callers can ``return _log_unsupported(...)``.
    """
    reason = unsupported_reason(source)
    if reason:
        logger.info(
            "%s Add a branch in src/gaia/agents/base/discovery.py and register "
            "the platform in _PLATFORM_SUPPORT, or open an issue at "
            "https://github.com/amd/gaia/issues.",
            reason,
        )
    else:
        # Reached only when a scanner fell through to "no branch" for a platform
        # _PLATFORM_SUPPORT claims it handles. Warn rather than return a quiet
        # [], or the drift this helper exists to surface becomes invisible.
        logger.warning(
            "'%s' found no platform branch on %s, but _PLATFORM_SUPPORT lists "
            "that platform as supported — the map and the scanner branches have "
            "drifted. Fix one of them in src/gaia/agents/base/discovery.py.",
            source,
            _platform_key(),
        )
    return []


def _linux_desktop_dirs(home: Path) -> List[Path]:
    """Resolve the freedesktop application directories for this session.

    Honors ``XDG_DATA_HOME`` and ``XDG_DATA_DIRS``. On Nix, Guix, or any
    custom-prefix install the applications live nowhere else, so hardcoding
    /usr/share returns [] on a machine full of apps.

    Args:
        home: The user's home directory.

    Returns:
        Application directories in search order, deduplicated. Directories that
        do not exist are included; the caller skips them.
    """
    data_home = Path(os.environ.get("XDG_DATA_HOME") or home / ".local" / "share")
    # The XDG spec fixes the separator as ":" regardless of platform.
    data_dirs = [
        Path(p) for p in (os.environ.get("XDG_DATA_DIRS") or "").split(":") if p.strip()
    ]

    candidates: List[Path] = [data_home / "applications"]
    candidates.extend(d / "applications" for d in data_dirs)
    candidates.append(data_home / "flatpak" / "exports" / "share" / "applications")
    candidates.extend(_LINUX_DESKTOP_DIRS)

    seen = set()
    ordered: List[Path] = []
    for path in candidates:
        if str(path) not in seen:
            seen.add(str(path))
            ordered.append(path)
    return ordered


def _permission_denied_message(label: str, path: Path, error: OSError) -> str:
    """Build an actionable message for a permission-denied read.

    Args:
        label: Human-readable name of what failed to load ("Safari history").
        path: The file the scan could not read.
        error: The raised :class:`PermissionError`.

    Returns:
        A message naming what failed, what to do about it, and where.
    """
    remedy = (
        "Grant Full Disk Access to the terminal or app running GAIA in "
        "System Settings → Privacy & Security → Full Disk Access, then re-run "
        "discovery."
        if _platform_key() == "darwin"
        else f"Check the file permissions on {path}, or run discovery as the "
        f"user that owns that profile."
    )
    return f"Cannot read {label} at {path}: {error}. {remedy}"


# ============================================================================
# SystemDiscovery
# ============================================================================


class SystemDiscovery:
    """Local system scanner for bootstrap. No agent dependencies.

    Each method returns a list of discovered fact dicts, NOT stored directly.
    The caller (`gaia memory bootstrap --discover`) presents them for user review.

    All methods catch exceptions internally and return partial results.
    """

    def __init__(self):
        self._home = Path.home()

    # ------------------------------------------------------------------
    # Platform path resolvers — shared by the browser and email scanners
    # ------------------------------------------------------------------

    def _chromium_profile_dirs(self) -> List[Tuple[str, Path]]:
        """Locate Chromium-family browser profiles for the running platform.

        Covers Chrome and Edge on all three platforms, plus the ``chromium``
        distro package and the Snap and Flatpak sandbox locations on Linux.
        Within each user-data root both the default profile and every additional
        ``Profile N`` directory are returned, so a user with more than one
        browser profile is not scanned half-blind.

        Returns:
            List of ``(browser_label, profile_dir)`` tuples for profile
            directories that exist. The label names the browser and profile,
            e.g. ``("Chrome (Profile 1)", Path(...))``.
        """
        platform = _platform_key()
        if platform == "win32":
            local = self._home / "AppData" / "Local"
            roots = [
                ("Chrome", local / "Google" / "Chrome" / "User Data"),
                ("Edge", local / "Microsoft" / "Edge" / "User Data"),
            ]
        elif platform == "darwin":
            app_support = self._home / "Library" / "Application Support"
            roots = [
                ("Chrome", app_support / "Google" / "Chrome"),
                ("Edge", app_support / "Microsoft Edge"),
            ]
        elif platform == "linux":
            config = self._home / ".config"
            snap = self._home / "snap"
            flatpak = self._home / ".var" / "app"
            roots = [
                ("Chrome", config / "google-chrome"),
                ("Edge", config / "microsoft-edge"),
                ("Chromium", config / "chromium"),
                # Ubuntu ships Chromium as a snap by default, and Flatpak
                # installs redirect $HOME — neither writes to ~/.config.
                ("Chromium (Snap)", snap / "chromium" / "common" / "chromium"),
                (
                    "Chromium (Flatpak)",
                    flatpak / "org.chromium.Chromium" / "config" / "chromium",
                ),
                (
                    "Chrome (Flatpak)",
                    flatpak / "com.google.Chrome" / "config" / "google-chrome",
                ),
                (
                    "Edge (Flatpak)",
                    flatpak / "com.microsoft.Edge" / "config" / "microsoft-edge",
                ),
            ]
        else:
            return []

        profiles: List[Tuple[str, Path]] = []
        for browser, root in roots:
            # scandir, not Path.glob: glob swallows PermissionError and yields
            # nothing, which is the silent-empty result this scanner avoids.
            try:
                with os.scandir(str(root)) as entries:
                    names = sorted(
                        e.name
                        for e in entries
                        if e.is_dir()
                        and (e.name == "Default" or e.name.startswith("Profile "))
                    )
            except (FileNotFoundError, NotADirectoryError):
                continue
            except PermissionError as e:
                logger.warning(
                    "%s", _permission_denied_message(f"{browser} profiles", root, e)
                )
                continue
            except OSError as e:
                logger.debug("Cannot list %s profiles in %s: %s", browser, root, e)
                continue
            profiles.extend((f"{browser} ({name})", root / name) for name in names)
        return profiles

    def _firefox_profile_roots(self) -> List[Path]:
        """Locate the directories that hold Firefox profiles on this platform.

        Includes the Snap and Flatpak sandbox locations on Linux, which is where
        the distro-packaged Firefox keeps its profiles.

        Returns:
            List of existing directories whose subdirectories are Firefox
            profiles.
        """
        platform = _platform_key()
        if platform == "win32":
            roots = [
                self._home / "AppData" / "Roaming" / "Mozilla" / "Firefox" / "Profiles"
            ]
        elif platform == "darwin":
            roots = [
                self._home / "Library" / "Application Support" / "Firefox" / "Profiles"
            ]
        elif platform == "linux":
            roots = [
                self._home / ".mozilla" / "firefox",
                self._home / "snap" / "firefox" / "common" / ".mozilla" / "firefox",
                self._home
                / ".var"
                / "app"
                / "org.mozilla.firefox"
                / ".mozilla"
                / "firefox",
            ]
        else:
            return []
        return [root for root in roots if root.is_dir()]

    def _thunderbird_profile_roots(self) -> List[Path]:
        """Locate the directories that hold Thunderbird profiles on this platform.

        Includes the Snap and Flatpak sandbox locations on Linux.

        Returns:
            List of existing directories whose subdirectories are Thunderbird
            profiles.
        """
        platform = _platform_key()
        if platform == "win32":
            roots = [self._home / "AppData" / "Roaming" / "Thunderbird" / "Profiles"]
        elif platform == "darwin":
            roots = [self._home / "Library" / "Thunderbird" / "Profiles"]
        elif platform == "linux":
            roots = [
                self._home / ".thunderbird",
                self._home / "snap" / "thunderbird" / "common" / ".thunderbird",
                self._home
                / ".var"
                / "app"
                / "org.mozilla.Thunderbird"
                / ".thunderbird",
            ]
        else:
            return []
        return [root for root in roots if root.is_dir()]

    # ------------------------------------------------------------------
    # File System Scan
    # ------------------------------------------------------------------

    def scan_file_system(self, paths: Optional[List[Path]] = None) -> List[Dict]:
        """Walk project directories (top 2 levels). Returns project names + languages.

        Scans ~/Work, ~/Documents, ~/Projects by default.
        Only reads folder names and file extensions — never file contents.
        Skips hidden directories, node_modules, .git, etc.

        Produces enriched insights: each project fact includes a ``file_type``
        classification (``"project"``), the detected languages, and a brief
        classification of what the project suggests about the user.

        Args:
            paths: Override directories to scan. Defaults to common project dirs.

        Returns:
            List of discovered fact dicts with project info.
        """
        if paths is None:
            paths = [
                self._home / "Work",
                self._home / "Documents",
                self._home / "Projects",
            ]

        results: List[Dict] = []

        for base_path in paths:
            if not base_path.exists() or not base_path.is_dir():
                continue

            try:
                for entry in os.scandir(str(base_path)):
                    if not entry.is_dir():
                        continue
                    if _is_hidden(entry.name) or entry.name in _SKIP_DIRS:
                        continue

                    project_path = Path(entry.path)
                    project_name = entry.name

                    # Detect languages at top 2 levels
                    languages = _detect_languages(project_path, max_depth=2)
                    lang_str = "/".join(languages) if languages else "unknown"

                    # Count immediate subdirectories for size hint
                    try:
                        subfolder_count = sum(
                            1
                            for e in os.scandir(str(project_path))
                            if e.is_dir()
                            and not _is_hidden(e.name)
                            and e.name not in _SKIP_DIRS
                        )
                    except (PermissionError, OSError):
                        subfolder_count = 0

                    # Classify project type from markers
                    file_type = "project"
                    classification = _classify_project(project_path, languages)

                    context = _classify_path(project_path)
                    content = (
                        f"Project '{project_name}' in {base_path.name}/ "
                        f"— {lang_str}"
                    )
                    if subfolder_count > 0:
                        content += f" ({subfolder_count} subfolders)"
                    if classification:
                        content += f" [{classification}]"

                    fact = _make_fact(
                        content=content,
                        context=context,
                        entity=f"project:{project_name.lower().replace(' ', '_')}",
                    )
                    fact["file_type"] = file_type
                    fact["languages"] = languages
                    fact["path"] = str(project_path)
                    results.append(fact)
            except (PermissionError, OSError) as e:
                logger.debug("scan_file_system error for %s: %s", base_path, e)

        # If we found multiple projects, add a summary insight
        if len(results) >= 3:
            # Aggregate languages across all projects
            all_langs: Dict[str, int] = {}
            for r in results:
                for lang in r.get("languages", []):
                    all_langs[lang] = all_langs.get(lang, 0) + 1
            if all_langs:
                top_langs = sorted(all_langs.items(), key=lambda x: x[1], reverse=True)
                lang_summary = "/".join(lang for lang, _ in top_langs[:4])
                paths_shown = [r.get("path", "") for r in results[:4]]
                summary = _make_profile_fact(
                    f"Active {lang_summary} developer — "
                    f"{len(results)} projects found",
                    context="work",
                    confidence=0.75,
                    domain="work",
                )
                summary["paths"] = paths_shown
                results.append(summary)

        return results

    # ------------------------------------------------------------------
    # Git Repos Scan
    # ------------------------------------------------------------------

    def scan_git_repos(self, paths: Optional[List[Path]] = None) -> List[Dict]:
        """Find .git directories. Returns repo info with remotes, branches, languages.

        Args:
            paths: Directories to search for git repos. Defaults to common dirs.

        Returns:
            List of discovered fact dicts with repo details.
        """
        if paths is None:
            paths = [
                self._home / "Work",
                self._home / "Documents",
                self._home / "Projects",
            ]

        results: List[Dict] = []
        seen_repos: set = set()

        for base_path in paths:
            if not base_path.exists() or not base_path.is_dir():
                continue

            # Walk top 3 levels looking for .git directories
            try:
                for depth, (dirpath, dirnames, _filenames) in enumerate(
                    os.walk(str(base_path))
                ):
                    if depth >= 3:
                        dirnames.clear()
                        continue

                    dirnames[:] = [
                        d for d in dirnames if not _is_hidden(d) and d not in _SKIP_DIRS
                    ]

                    git_dir = Path(dirpath) / ".git"
                    if not git_dir.is_dir():
                        continue

                    repo_path = Path(dirpath)
                    repo_name = repo_path.name

                    # Avoid duplicates
                    canonical = str(repo_path).lower()
                    if canonical in seen_repos:
                        continue
                    seen_repos.add(canonical)

                    # Parse .git/config for remotes
                    remotes = self._parse_git_config(git_dir / "config")

                    # Get current branch from HEAD
                    branch = self._parse_git_head(git_dir / "HEAD")

                    # Detect languages
                    languages = _detect_languages(repo_path, max_depth=2)
                    lang_str = "/".join(languages) if languages else "unknown"

                    # Build content string
                    remote_str = ""
                    context = _classify_path(repo_path)
                    if remotes:
                        origin = remotes.get("origin", next(iter(remotes.values()), ""))
                        if origin:
                            remote_str = f", remote: {origin}"
                            # Refine context from remote
                            remote_context = _classify_remote(origin)
                            if remote_context != "unclassified":
                                context = remote_context

                    content = f"Git repo '{repo_name}' — {lang_str}{remote_str}"
                    if branch:
                        content += f" (branch: {branch})"

                    results.append(
                        _make_fact(
                            content=content,
                            context=context,
                            entity=f"project:{repo_name.lower().replace(' ', '_')}",
                        )
                    )

                    # Don't recurse into this repo's subdirectories
                    dirnames.clear()

            except (PermissionError, OSError) as e:
                logger.debug("scan_git_repos error for %s: %s", base_path, e)

        return results

    def _parse_git_config(self, config_path: Path) -> Dict[str, str]:
        """Parse .git/config and extract remote URLs."""
        remotes: Dict[str, str] = {}
        if not config_path.exists():
            return remotes
        try:
            parser = configparser.ConfigParser()
            parser.read(str(config_path), encoding="utf-8")
            for section in parser.sections():
                if section.startswith('remote "') and section.endswith('"'):
                    remote_name = section[8:-1]
                    url = parser.get(section, "url", fallback="")
                    if url:
                        remotes[remote_name] = url
        except (configparser.Error, OSError) as e:
            logger.debug("Failed to parse git config %s: %s", config_path, e)
        return remotes

    def _parse_git_head(self, head_path: Path) -> str:
        """Parse .git/HEAD to get the current branch name."""
        try:
            content = head_path.read_text(encoding="utf-8").strip()
            if content.startswith("ref: refs/heads/"):
                return content[16:]
        except (OSError, UnicodeDecodeError):
            pass
        return ""

    # ------------------------------------------------------------------
    # Installed Apps Scan (per-platform)
    # ------------------------------------------------------------------

    def scan_installed_apps(self) -> List[Dict]:
        """Inventory the applications installed on this machine.

        - **Windows**: registry Uninstall keys + Start Menu shortcuts.
        - **macOS**: ``.app`` bundles in ``/Applications`` and ``~/Applications``.
        - **Linux**: ``.desktop`` entries, including the Flatpak and Snap export
          directories.

        Returns:
            List of discovered fact dicts with app name and category. Empty on a
            platform with no branch — reported, never silent.
        """
        platform = _platform_key()
        if platform == "win32":
            return self._scan_windows_installed_apps()
        if platform == "darwin":
            return self._scan_macos_installed_apps()
        if platform == "linux":
            return self._scan_linux_installed_apps()
        return _log_unsupported("installed_apps")

    def _scan_windows_installed_apps(self) -> List[Dict]:
        """Read Windows registry Uninstall keys + Start Menu shortcuts.

        Scans:
        - HKLM\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Uninstall
        - HKLM\\SOFTWARE\\WOW6432Node\\Microsoft\\Windows\\CurrentVersion\\Uninstall
        - HKCU\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Uninstall
        - Start Menu .lnk files

        Returns:
            List of discovered fact dicts with app name and category.
        """
        if winreg is None:
            return []  # Not on Windows

        results: List[Dict] = []
        seen_apps: set = set()

        # Registry paths to scan
        reg_paths = [
            (
                winreg.HKEY_LOCAL_MACHINE,
                r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
            ),
            (
                winreg.HKEY_LOCAL_MACHINE,
                r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall",
            ),
            (
                winreg.HKEY_CURRENT_USER,
                r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
            ),
        ]

        for hive, key_path in reg_paths:
            try:
                self._scan_registry_uninstall(hive, key_path, seen_apps, results)
            except OSError as e:
                logger.debug("Registry scan failed for %s: %s", key_path, e)

        # Scan Start Menu shortcuts
        try:
            self._scan_start_menu(seen_apps, results)
        except OSError as e:
            logger.debug("Start Menu scan failed: %s", e)

        return results

    def _scan_registry_uninstall(
        self,
        hive: int,
        key_path: str,
        seen_apps: set,
        results: List[Dict],
    ) -> None:
        """Scan a single registry Uninstall key for installed apps."""
        if winreg is None:
            return  # Not on Windows
        try:
            key = winreg.OpenKey(hive, key_path)
        except OSError:
            return

        try:
            i = 0
            while True:
                try:
                    subkey_name = winreg.EnumKey(key, i)
                    i += 1
                except OSError:
                    break

                try:
                    subkey = winreg.OpenKey(key, subkey_name)
                    try:
                        display_name, _ = winreg.QueryValueEx(subkey, "DisplayName")
                    except OSError:
                        winreg.CloseKey(subkey)
                        continue

                    # Skip system components and updates
                    system_update = False
                    try:
                        sys_component, _ = winreg.QueryValueEx(
                            subkey, "SystemComponent"
                        )
                        if sys_component == 1:
                            system_update = True
                    except OSError:
                        pass

                    if system_update:
                        winreg.CloseKey(subkey)
                        continue

                    # Skip Windows updates (KB numbers)
                    if re.match(r"^(KB\d+|Update for|Security Update)", display_name):
                        winreg.CloseKey(subkey)
                        continue

                    # Get publisher
                    try:
                        publisher, _ = winreg.QueryValueEx(subkey, "Publisher")
                    except OSError:
                        publisher = ""

                    winreg.CloseKey(subkey)

                    # Dedup by normalized name
                    norm_name = display_name.strip().lower()
                    if norm_name in seen_apps or not norm_name:
                        continue
                    seen_apps.add(norm_name)

                    category = _categorize_app(display_name)
                    content = f"Installed app: {display_name}"
                    if publisher:
                        content += f" (by {publisher})"
                    content += f" [{category}]"

                    entity = f"app:{re.sub(r'[^a-z0-9]+', '_', norm_name).strip('_')}"

                    results.append(
                        _make_fact(
                            content=content,
                            context="unclassified",
                            entity=entity,
                        )
                    )

                except OSError:
                    continue
        finally:
            winreg.CloseKey(key)

    def _scan_start_menu(self, seen_apps: set, results: List[Dict]) -> None:
        """Scan Start Menu folders for .lnk shortcuts."""
        start_menu_paths = [
            self._home
            / "AppData"
            / "Roaming"
            / "Microsoft"
            / "Windows"
            / "Start Menu"
            / "Programs",
            Path(os.environ.get("PROGRAMDATA", "C:\\ProgramData"))
            / "Microsoft"
            / "Windows"
            / "Start Menu"
            / "Programs",
        ]

        for menu_path in start_menu_paths:
            if not menu_path.exists():
                continue
            try:
                for entry in os.scandir(str(menu_path)):
                    if entry.is_file() and entry.name.lower().endswith(".lnk"):
                        app_name = entry.name[:-4]  # Remove .lnk
                        norm_name = app_name.strip().lower()
                        if norm_name in seen_apps or not norm_name:
                            continue
                        # Skip generic shortcuts
                        if norm_name in ("uninstall", "readme", "help", "license"):
                            continue
                        seen_apps.add(norm_name)
                        category = _categorize_app(app_name)
                        entity = (
                            f"app:{re.sub(r'[^a-z0-9]+', '_', norm_name).strip('_')}"
                        )
                        results.append(
                            _make_fact(
                                content=f"Installed app: {app_name} [{category}]",
                                context="unclassified",
                                entity=entity,
                            )
                        )
            except (PermissionError, OSError):
                pass

    def _append_app_fact(
        self, app_name: str, seen_apps: set, results: List[Dict]
    ) -> None:
        """Append a deduplicated "installed app" fact for `app_name`.

        Args:
            app_name: Display name of the application.
            seen_apps: Lowercased names already recorded; mutated in place.
            results: Fact list to append to; mutated in place.
        """
        norm_name = app_name.strip().lower()
        if not norm_name or norm_name in seen_apps:
            return
        slug = re.sub(r"[^a-z0-9]+", "_", norm_name).strip("_")
        if not slug:
            return  # A name with no alphanumerics would yield a bare "app:"
        seen_apps.add(norm_name)
        category = _categorize_app(app_name)
        entity = f"app:{slug}"
        results.append(
            _make_fact(
                content=f"Installed app: {app_name} [{category}]",
                context="unclassified",
                entity=entity,
            )
        )

    def _scan_macos_installed_apps(self) -> List[Dict]:
        """Scan macOS ``.app`` bundles in ``/Applications`` and ``~/Applications``.

        ``/System/Applications`` is deliberately excluded: the ~40 Apple stock
        apps present on every Mac would swamp the user's review list without
        saying anything about them.

        Returns:
            List of discovered fact dicts, one per unique application bundle.
        """
        results: List[Dict] = []
        seen_apps: set = set()

        for app_dir in (*_MACOS_APP_DIRS, self._home / "Applications"):
            if not app_dir.is_dir():
                continue
            try:
                with os.scandir(str(app_dir)) as entries:
                    for entry in entries:
                        if not entry.name.endswith(".app") or not entry.is_dir():
                            continue
                        self._append_app_fact(entry.name[:-4], seen_apps, results)
            except PermissionError as e:
                logger.warning(
                    "%s", _permission_denied_message("installed apps", app_dir, e)
                )
            except OSError as e:
                logger.debug("Application scan failed for %s: %s", app_dir, e)

        return results

    def _scan_linux_installed_apps(self) -> List[Dict]:
        """Scan Linux ``.desktop`` entries for installed GUI applications.

        Covers every directory on ``XDG_DATA_DIRS``/``XDG_DATA_HOME`` plus the
        Flatpak and Snap export dirs — both export a ``.desktop`` file per GUI
        app, so no package-manager subprocess is needed. Entries hidden from
        menus (``NoDisplay``, ``Hidden``) and non-application types are skipped.

        Returns:
            List of discovered fact dicts, one per unique application.
        """
        results: List[Dict] = []
        seen_apps: set = set()

        for desktop_dir in _linux_desktop_dirs(self._home):
            try:
                with os.scandir(str(desktop_dir)) as scan:
                    entries = sorted(scan, key=lambda e: e.name)
            except (FileNotFoundError, NotADirectoryError):
                continue
            except PermissionError as e:
                logger.warning(
                    "%s", _permission_denied_message("installed apps", desktop_dir, e)
                )
                continue
            except OSError as e:
                logger.debug("Desktop entry scan failed for %s: %s", desktop_dir, e)
                continue

            for entry in entries:
                if not entry.name.endswith(".desktop"):
                    continue
                app_name = self._parse_desktop_entry_name(Path(entry.path))
                if app_name:
                    self._append_app_fact(app_name, seen_apps, results)

        return results

    def _parse_desktop_entry_name(self, path: Path) -> str:
        """Read the display name out of a freedesktop ``.desktop`` file.

        Args:
            path: The ``.desktop`` file to parse.

        Returns:
            The ``Name`` value, or "" when the entry is hidden, is not an
            application, or cannot be parsed.
        """
        # interpolation=None is mandatory: Exec= lines contain %f / %U, which
        # the default interpolator raises on.
        parser = configparser.ConfigParser(interpolation=None, strict=False)
        try:
            parser.read_string(path.read_text(encoding="utf-8", errors="replace"))
        except PermissionError as e:
            logger.warning(
                "%s", _permission_denied_message("an installed app", path, e)
            )
            return ""
        except (OSError, configparser.Error) as e:
            logger.debug("Failed to parse desktop entry %s: %s", path, e)
            return ""

        if not parser.has_section("Desktop Entry"):
            return ""
        section = parser["Desktop Entry"]
        if section.get("Type", "Application") != "Application":
            return ""
        for hidden_key in ("NoDisplay", "Hidden"):
            if section.get(hidden_key, "false").strip().lower() == "true":
                return ""
        return section.get("Name", "").strip()

    # ------------------------------------------------------------------
    # Browser Bookmarks Scan
    # ------------------------------------------------------------------

    def scan_browser_bookmarks(self) -> List[Dict]:
        """Read Chromium, Firefox, and (on macOS) Safari bookmarks.

        Groups bookmarks by domain. Flags banking/finance as sensitive. Every
        profile of every supported browser is read, not just the default one.

        Returns:
            List of discovered fact dicts with bookmark domains and categories.
            Empty on a platform with no branch — reported, never silent.
        """
        if unsupported_reason("browser_bookmarks"):
            return _log_unsupported("browser_bookmarks")
        platform = _platform_key()

        results: List[Dict] = []
        domain_urls: Dict[str, int] = {}

        # Chrome, Edge, and Chromium all use the same bookmark JSON format
        for browser_name, profile_dir in self._chromium_profile_dirs():
            bookmark_path = profile_dir / "Bookmarks"
            if not bookmark_path.exists():
                continue
            try:
                data = _safe_read_json(bookmark_path)
                if data and "roots" in data:
                    self._extract_chromium_bookmarks(
                        data["roots"], domain_urls, browser_name
                    )
            except Exception as e:
                logger.debug("Failed to read %s bookmarks: %s", browser_name, e)

        # Firefox uses SQLite
        try:
            self._extract_firefox_bookmarks(domain_urls)
        except Exception as e:
            logger.debug("Failed to read Firefox bookmarks: %s", e)

        if platform == "darwin":
            self._extract_safari_bookmarks(domain_urls)

        # Convert domain counts to facts
        for domain, count in sorted(
            domain_urls.items(), key=lambda x: x[1], reverse=True
        ):
            is_sensitive = domain in _SENSITIVE_DOMAINS
            context = _classify_domain(domain)
            if is_sensitive:
                context = "personal"

            content = f"Bookmarked site: {domain} ({count} bookmark"
            if count != 1:
                content += "s"
            content += ")"

            results.append(
                _make_fact(
                    content=content,
                    context=context,
                    sensitive=is_sensitive,
                )
            )

        return results

    def _extract_chromium_bookmarks(
        self,
        roots: dict,
        domain_urls: Dict[str, int],
        browser_name: str,  # pylint: disable=unused-argument
    ) -> None:
        """Recursively extract bookmark URLs from Chromium JSON roots."""
        for _root_name, root_data in roots.items():
            if isinstance(root_data, dict):
                self._walk_chromium_bookmark_node(root_data, domain_urls)

    def _walk_chromium_bookmark_node(
        self, node: dict, domain_urls: Dict[str, int]
    ) -> None:
        """Walk a Chromium bookmark tree node, extracting URLs."""
        if not isinstance(node, dict):
            return
        node_type = node.get("type", "")
        if node_type == "url":
            url = node.get("url", "")
            if url:
                domain = _extract_domain(url)
                if domain:
                    domain_urls[domain] = domain_urls.get(domain, 0) + 1
        elif node_type == "folder":
            children = node.get("children", [])
            for child in children:
                self._walk_chromium_bookmark_node(child, domain_urls)

    def _extract_firefox_bookmarks(self, domain_urls: Dict[str, int]) -> None:
        """Extract bookmarks from every Firefox profile's places.sqlite.

        Args:
            domain_urls: Domain -> bookmark-count map; mutated in place.
        """
        for firefox_root in self._firefox_profile_roots():
            try:
                with os.scandir(str(firefox_root)) as entries:
                    profile_dirs = [Path(e.path) for e in entries if e.is_dir()]
            except PermissionError as e:
                logger.warning(
                    "%s",
                    _permission_denied_message("Firefox profiles", firefox_root, e),
                )
                continue
            except OSError as e:
                logger.debug("Cannot list Firefox profiles in %s: %s", firefox_root, e)
                continue

            for profile_dir in profile_dirs:
                places_db = profile_dir / "places.sqlite"
                if not places_db.exists():
                    continue

                rows = _safe_copy_and_query_sqlite(
                    places_db,
                    """
                    SELECT mb.title, mp.url
                    FROM moz_bookmarks mb
                    JOIN moz_places mp ON mb.fk = mp.id
                    WHERE mp.url LIKE 'http%'
                    """,
                    label="Firefox bookmarks",
                )
                for _title, url in rows:
                    domain = _extract_domain(url)
                    if domain:
                        domain_urls[domain] = domain_urls.get(domain, 0) + 1

    def _extract_safari_bookmarks(self, domain_urls: Dict[str, int]) -> None:
        """Extract Safari bookmarks from ``~/Library/Safari/Bookmarks.plist``.

        ``~/Library/Safari`` is protected by macOS TCC; without Full Disk Access
        the read is denied, which is reported with the remedy rather than
        swallowed.

        Args:
            domain_urls: Domain -> bookmark-count map; mutated in place.
        """
        plist_path = self._home / "Library" / "Safari" / "Bookmarks.plist"
        if not plist_path.exists():
            return
        try:
            with open(plist_path, "rb") as f:
                data = plistlib.load(f)
        except PermissionError as e:
            logger.warning(
                "%s", _permission_denied_message("Safari bookmarks", plist_path, e)
            )
            return
        except (OSError, ValueError, plistlib.InvalidFileException) as e:
            logger.debug("Failed to read Safari bookmarks at %s: %s", plist_path, e)
            return

        self._walk_safari_bookmark_node(data, domain_urls)

    def _walk_safari_bookmark_node(
        self, node: Any, domain_urls: Dict[str, int]
    ) -> None:
        """Walk a Safari bookmark plist node, counting bookmarked domains.

        Args:
            node: A decoded plist node (dict or list) from Bookmarks.plist.
            domain_urls: Domain -> bookmark-count map; mutated in place.
        """
        if isinstance(node, list):
            for child in node:
                self._walk_safari_bookmark_node(child, domain_urls)
            return
        if not isinstance(node, dict):
            return
        url = node.get("URLString", "")
        if isinstance(url, str) and url:
            domain = _extract_domain(url)
            if domain:
                domain_urls[domain] = domain_urls.get(domain, 0) + 1
        self._walk_safari_bookmark_node(node.get("Children", []), domain_urls)

    # ------------------------------------------------------------------
    # Browser History Scan
    # ------------------------------------------------------------------

    def scan_browser_history(self, days: int = 30) -> List[Dict]:
        """Read browser history (Chromium/Firefox, plus Safari on macOS).

        Returns top domains only. Copies each DB to a temp file first to avoid
        browser lock issues. Every profile is read, not just the default one.
        ALL results are flagged sensitive=True.

        Args:
            days: Number of days of history to scan. Default 30.

        Returns:
            List of discovered fact dicts. All marked sensitive. Empty on a
            platform with no branch — reported, never silent.
        """
        if unsupported_reason("browser_history"):
            return _log_unsupported("browser_history")
        platform = _platform_key()

        domain_counts: Dict[str, int] = {}

        # Chrome epoch: Jan 1, 1601 (microseconds)
        # Convert days to Chrome timestamp
        import time

        now_unix = time.time()
        cutoff_unix = now_unix - (days * 86400)
        # Chrome timestamp = (Unix timestamp + 11644473600) * 1000000
        chrome_cutoff = int((cutoff_unix + 11644473600) * 1_000_000)

        # Chrome, Edge, and Chromium all use the same History SQLite format
        for browser_name, profile_dir in self._chromium_profile_dirs():
            try:
                rows = _safe_copy_and_query_sqlite(
                    profile_dir / "History",
                    """
                    SELECT url, visit_count
                    FROM urls
                    WHERE last_visit_time > ?
                    ORDER BY visit_count DESC
                    LIMIT 500
                    """,
                    (chrome_cutoff,),
                    label=f"{browser_name} history",
                )
                for url, visit_count in rows:
                    domain = _extract_domain(url)
                    if domain:
                        domain_counts[domain] = (
                            domain_counts.get(domain, 0) + visit_count
                        )
            except Exception as e:
                logger.debug("Failed to read %s history: %s", browser_name, e)

        # Firefox uses Unix timestamps in microseconds
        firefox_cutoff = int(cutoff_unix * 1_000_000)
        try:
            self._extract_firefox_history(domain_counts, firefox_cutoff)
        except Exception as e:
            logger.debug("Failed to read Firefox history: %s", e)

        if platform == "darwin":
            self._extract_safari_history(domain_counts, cutoff_unix)

        # Convert to facts — top domains only, ALL sensitive
        results: List[Dict] = []
        sorted_domains = sorted(domain_counts.items(), key=lambda x: x[1], reverse=True)
        for domain, count in sorted_domains[:50]:  # Top 50 domains
            context = _classify_domain(domain)
            results.append(
                _make_fact(
                    content=f"Frequently visited: {domain} ({count} visits)",
                    context=context,
                    sensitive=True,
                )
            )

        return results

    def _extract_firefox_history(
        self, domain_counts: Dict[str, int], cutoff_timestamp: int
    ) -> None:
        """Extract history from every Firefox profile's places.sqlite.

        Args:
            domain_counts: Domain -> visit-count map; mutated in place.
            cutoff_timestamp: Oldest visit to include, in Unix microseconds.
        """
        for firefox_root in self._firefox_profile_roots():
            try:
                with os.scandir(str(firefox_root)) as entries:
                    profile_dirs = [Path(e.path) for e in entries if e.is_dir()]
            except PermissionError as e:
                logger.warning(
                    "%s",
                    _permission_denied_message("Firefox profiles", firefox_root, e),
                )
                continue
            except OSError as e:
                logger.debug("Cannot list Firefox profiles in %s: %s", firefox_root, e)
                continue

            for profile_dir in profile_dirs:
                places_db = profile_dir / "places.sqlite"
                if not places_db.exists():
                    continue

                rows = _safe_copy_and_query_sqlite(
                    places_db,
                    """
                    SELECT url, visit_count
                    FROM moz_places
                    WHERE last_visit_date > ?
                      AND url LIKE 'http%'
                    ORDER BY visit_count DESC
                    LIMIT 500
                    """,
                    (cutoff_timestamp,),
                    label="Firefox history",
                )
                for url, visit_count in rows:
                    domain = _extract_domain(url)
                    if domain:
                        domain_counts[domain] = domain_counts.get(domain, 0) + (
                            visit_count or 0
                        )

    def _extract_safari_history(
        self, domain_counts: Dict[str, int], cutoff_unix: float
    ) -> None:
        """Extract Safari history from ``~/Library/Safari/History.db``.

        Safari stores visit times as Mac absolute time (seconds since
        2001-01-01), not the Chrome or Firefox epoch.

        Args:
            domain_counts: Domain -> visit-count map; mutated in place.
            cutoff_unix: Oldest visit to include, as a Unix timestamp.
        """
        history_db = self._home / "Library" / "Safari" / "History.db"
        # Mac absolute time epoch: 2001-01-01 == Unix 978307200
        mac_cutoff = cutoff_unix - 978307200

        rows = _safe_copy_and_query_sqlite(
            history_db,
            """
            SELECT hi.url, hi.visit_count
            FROM history_items hi
            JOIN history_visits hv ON hv.history_item = hi.id
            WHERE hv.visit_time > ?
            GROUP BY hi.id
            ORDER BY hi.visit_count DESC
            LIMIT 500
            """,
            (mac_cutoff,),
            label="Safari history",
        )
        for url, visit_count in rows:
            domain = _extract_domain(url)
            if domain:
                domain_counts[domain] = domain_counts.get(domain, 0) + (
                    visit_count or 0
                )

    # ------------------------------------------------------------------
    # Email Accounts Scan
    # ------------------------------------------------------------------

    def scan_email_accounts(self) -> List[Dict]:
        """Discover configured email accounts from this platform's mail clients.

        Reads addresses only — never email content, and never a stored password.

        - **Windows**: Credential Manager, Thunderbird, Outlook registry.
        - **macOS**: Keychain attributes for known mail hosts, Thunderbird,
          Apple Mail.
        - **Linux**: Thunderbird, Evolution. There is no credential-store branch:
          ``secret-tool`` can only look up exact attribute pairs, it cannot
          enumerate the keyring.

        ALL results are flagged sensitive=True.

        Returns:
            List of discovered fact dicts with email addresses. All sensitive.
            Empty on a platform with no branch — reported, never silent.
        """
        if unsupported_reason("email_accounts"):
            return _log_unsupported("email_accounts")
        platform = _platform_key()

        results: List[Dict] = []
        seen_emails: set = set()

        # 1. Platform credential store
        if platform == "win32":
            try:
                self._scan_credential_manager(seen_emails, results)
            except Exception as e:
                # A broken scan and an empty one both look like "no accounts"
                # to the user, so say which one happened.
                logger.warning(
                    "Credential Manager scan failed, no accounts read from it: %s", e
                )
        elif platform == "darwin":
            try:
                self._scan_macos_keychain(seen_emails, results)
            except Exception as e:
                logger.warning("Keychain scan failed, no accounts read from it: %s", e)

        # 2. Thunderbird profiles (prefs.js) — all platforms
        try:
            self._scan_thunderbird(seen_emails, results)
        except Exception as e:
            logger.warning("Thunderbird scan failed, no accounts read from it: %s", e)

        # 3. Platform identity store
        if platform == "win32":
            try:
                self._scan_outlook_registry(seen_emails, results)
            except Exception as e:
                logger.warning(
                    "Outlook registry scan failed, no accounts read from it: %s", e
                )
        elif platform == "darwin":
            try:
                self._scan_apple_mail(seen_emails, results)
            except Exception as e:
                logger.warning(
                    "Apple Mail scan failed, no accounts read from it: %s", e
                )
        elif platform == "linux":
            try:
                self._scan_evolution(seen_emails, results)
            except Exception as e:
                logger.warning("Evolution scan failed, no accounts read from it: %s", e)

        return results

    def _scan_credential_manager(self, seen_emails: set, results: List[Dict]) -> None:
        """Scan Windows Credential Manager for email-related credentials."""
        import subprocess
        import sys

        if sys.platform != "win32":
            return

        # CREATE_NO_WINDOW is defined only on Windows builds of subprocess.
        kwargs: Dict[str, Any] = {}
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

        try:
            output = subprocess.check_output(
                ["cmdkey", "/list"],
                text=True,
                # A console tool writes in the OEM codepage, not the locale one
                # text=True decodes with, so a credential entry holding a
                # non-ASCII name can produce a byte that codec cannot map.
                # UnicodeDecodeError is not a SubprocessError or an OSError, so
                # without this it escapes the handler below and takes the whole
                # discovery pass down over one accented character.
                errors="replace",
                timeout=10,
                **kwargs,
            )
        except (subprocess.SubprocessError, FileNotFoundError, OSError) as e:
            logger.warning(
                "Credential Manager scan failed, no accounts read from it: %s", e
            )
            return

        # Look for email-related targets and extract user fields
        email_pattern = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")

        for match in email_pattern.finditer(output):
            email = match.group(0).lower()
            if email in seen_emails:
                continue
            seen_emails.add(email)

            # Determine provider from domain
            domain = email.split("@")[1]
            provider = domain.split(".")[0]
            entity = f"service:{provider}"

            results.append(
                _make_fact(
                    content=f"Email account: {email}",
                    context="unclassified",
                    entity=entity,
                    sensitive=True,
                )
            )

    def _append_email_fact(
        self,
        email: str,
        seen_emails: set,
        results: List[Dict],
        source_label: str = "",
    ) -> None:
        """Append a deduplicated, sensitive "email account" fact.

        Args:
            email: The discovered address.
            seen_emails: Addresses already recorded; mutated in place.
            results: Fact list to append to; mutated in place.
            source_label: Mail client the address came from, shown in the fact
                ("Thunderbird", "Apple Mail"). Omitted when empty.
        """
        email = email.strip().lower()
        if not email or email in seen_emails:
            return
        seen_emails.add(email)
        domain = email.split("@")[1] if "@" in email else "unknown"
        provider = domain.split(".")[0]
        content = f"Email account: {email}"
        if source_label:
            content += f" ({source_label})"
        results.append(
            _make_fact(
                content=content,
                context="unclassified",
                entity=f"service:{provider}",
                sensitive=True,
            )
        )

    def _scan_macos_keychain(self, seen_emails: set, results: List[Dict]) -> None:
        """Read macOS Keychain *attributes* for a fixed list of mail hosts.

        Runs ``security find-internet-password -s <host>``, which prints an
        item's attributes only. The ``-g`` flag — the one that returns the
        stored secret and raises an authorization prompt — is never passed, so
        no password is ever read and the user is never interrupted.

        `security` returns only the FIRST match per host, so a user with two
        accounts on the same provider surfaces one address here; the other is
        picked up from their mail client's own config if it is configured there.

        Args:
            seen_emails: Addresses already recorded; mutated in place.
            results: Fact list to append to; mutated in place.
        """
        import subprocess

        for host in _MACOS_KEYCHAIN_MAIL_HOSTS:
            argv = ["security", "find-internet-password", "-s", host]
            try:
                proc = subprocess.run(
                    argv,
                    capture_output=True,
                    text=True,
                    # Keychain entries carry user-supplied text; one byte the
                    # locale codec cannot map would otherwise raise instead of
                    # returning a result.
                    errors="replace",
                    timeout=10,
                    check=False,
                )
            except FileNotFoundError as e:
                logger.warning(
                    "Cannot read mail accounts from the macOS Keychain: the "
                    "'security' command is missing (%s). It ships with macOS at "
                    "/usr/bin/security — check that /usr/bin is on PATH.",
                    e,
                )
                return
            except (subprocess.SubprocessError, OSError) as e:
                # One slow or wedged host must not hide the other providers.
                logger.warning("Keychain lookup for %s failed to run: %s", host, e)
                continue

            if proc.returncode == _SECURITY_ITEM_NOT_FOUND:
                logger.debug("No keychain entry for mail host %s", host)
                continue
            if proc.returncode != 0:
                logger.debug(
                    "Keychain lookup for %s exited %s: %s",
                    host,
                    proc.returncode,
                    proc.stderr.strip(),
                )
                continue
            if not proc.stdout.strip():
                logger.warning(
                    "Keychain lookup for %s succeeded but printed no attributes; "
                    "the 'security find-internet-password' output format may have "
                    "changed. Mail accounts from the Keychain were skipped — run "
                    "`%s` by hand to compare.",
                    host,
                    " ".join(argv),
                )
                continue

            for match in _EMAIL_PATTERN.finditer(proc.stdout):
                self._append_email_fact(match.group(0), seen_emails, results)

    def _scan_thunderbird(self, seen_emails: set, results: List[Dict]) -> None:
        """Scan every Thunderbird profile's prefs.js for account addresses.

        Args:
            seen_emails: Addresses already recorded; mutated in place.
            results: Fact list to append to; mutated in place.
        """
        email_pref_pattern = re.compile(
            r'user_pref\("mail\.identity\.id\d+\.useremail"\s*,\s*"([^"]+)"\)'
        )

        for thunderbird_root in self._thunderbird_profile_roots():
            try:
                with os.scandir(str(thunderbird_root)) as entries:
                    profile_dirs = [Path(e.path) for e in entries if e.is_dir()]
            except PermissionError as e:
                logger.warning(
                    "%s",
                    _permission_denied_message(
                        "Thunderbird profiles", thunderbird_root, e
                    ),
                )
                continue
            except OSError as e:
                logger.debug(
                    "Cannot list Thunderbird profiles in %s: %s", thunderbird_root, e
                )
                continue

            for profile_dir in profile_dirs:
                prefs_path = profile_dir / "prefs.js"
                if not prefs_path.exists():
                    continue
                try:
                    content = prefs_path.read_text(encoding="utf-8", errors="replace")
                except PermissionError as e:
                    logger.warning(
                        "%s",
                        _permission_denied_message(
                            "Thunderbird preferences", prefs_path, e
                        ),
                    )
                    continue
                except (OSError, UnicodeDecodeError) as e:
                    logger.debug("Failed to read %s: %s", prefs_path, e)
                    continue

                for match in email_pref_pattern.finditer(content):
                    self._append_email_fact(
                        match.group(1), seen_emails, results, "Thunderbird"
                    )

    def _scan_apple_mail(self, seen_emails: set, results: List[Dict]) -> None:
        """Scan Apple Mail's account plists for configured addresses.

        Reads ``~/Library/Mail/V*/MailData/Accounts.plist`` plus the Mail
        preferences plist in both its pre-Catalina and containerized locations.
        ``~/Library/Mail`` is protected by macOS TCC, so a denial is reported
        with the remedy.

        Args:
            seen_emails: Addresses already recorded; mutated in place.
            results: Fact list to append to; mutated in place.
        """
        # Enumerate with scandir, not Path.glob: glob SWALLOWS PermissionError
        # and yields nothing, which is exactly the silent-empty result this
        # scanner exists to avoid on a Mac without Full Disk Access.
        plist_paths: List[Path] = []
        mail_dir = self._home / "Library" / "Mail"
        if mail_dir.is_dir():
            try:
                with os.scandir(str(mail_dir)) as entries:
                    version_dirs = [
                        Path(e.path)
                        for e in entries
                        if e.is_dir() and e.name.startswith("V")
                    ]
            except PermissionError as e:
                logger.warning(
                    "%s", _permission_denied_message("Apple Mail accounts", mail_dir, e)
                )
                version_dirs = []
            except OSError as e:
                logger.debug("Cannot list Apple Mail data in %s: %s", mail_dir, e)
                version_dirs = []

            for version_dir in sorted(version_dirs):
                accounts_plist = version_dir / "MailData" / "Accounts.plist"
                if accounts_plist.exists():
                    plist_paths.append(accounts_plist)

        # Mail has been containerized since Catalina; the legacy location is
        # kept for older systems.
        prefs_candidates = (
            self._home / "Library" / "Preferences" / "com.apple.mail.plist",
            self._home
            / "Library"
            / "Containers"
            / "com.apple.mail"
            / "Data"
            / "Library"
            / "Preferences"
            / "com.apple.mail.plist",
        )
        plist_paths.extend(path for path in prefs_candidates if path.exists())

        for plist_path in plist_paths:
            try:
                with open(plist_path, "rb") as f:
                    data = plistlib.load(f)
            except PermissionError as e:
                logger.warning(
                    "%s",
                    _permission_denied_message("Apple Mail accounts", plist_path, e),
                )
                continue
            except (OSError, ValueError, plistlib.InvalidFileException) as e:
                logger.debug("Failed to read Apple Mail plist %s: %s", plist_path, e)
                continue

            for email in _collect_plist_emails(data):
                self._append_email_fact(email, seen_emails, results, "Apple Mail")

    def _scan_evolution(self, seen_emails: set, results: List[Dict]) -> None:
        """Scan Evolution account sources for configured addresses.

        Evolution stores one INI file per account under
        ``~/.config/evolution/sources/``; the address lives in
        ``[Mail Identity] -> Address``.

        Args:
            seen_emails: Addresses already recorded; mutated in place.
            results: Fact list to append to; mutated in place.
        """
        sources_dir = self._home / ".config" / "evolution" / "sources"
        if not sources_dir.is_dir():
            return

        # scandir, not Path.glob — glob swallows PermissionError and would make
        # an unreadable sources dir look like "no accounts configured".
        try:
            with os.scandir(str(sources_dir)) as entries:
                source_paths = sorted(
                    Path(e.path) for e in entries if e.name.endswith(".source")
                )
        except PermissionError as e:
            logger.warning(
                "%s", _permission_denied_message("Evolution accounts", sources_dir, e)
            )
            return
        except OSError as e:
            logger.debug("Cannot list Evolution sources in %s: %s", sources_dir, e)
            return

        for source_path in source_paths:
            parser = configparser.ConfigParser(interpolation=None, strict=False)
            try:
                parser.read_string(
                    source_path.read_text(encoding="utf-8", errors="replace")
                )
            except PermissionError as e:
                logger.warning(
                    "%s",
                    _permission_denied_message("Evolution accounts", source_path, e),
                )
                continue
            except (OSError, configparser.Error) as e:
                logger.debug("Failed to parse Evolution source %s: %s", source_path, e)
                continue

            if not parser.has_section("Mail Identity"):
                continue
            address = parser["Mail Identity"].get("Address", "").strip()
            if address:
                self._append_email_fact(address, seen_emails, results, "Evolution")

    def _scan_outlook_registry(self, seen_emails: set, results: List[Dict]) -> None:
        """Scan Outlook registry keys for email account addresses."""
        if winreg is None:
            return  # Not on Windows
        outlook_paths = [
            r"SOFTWARE\Microsoft\Office\16.0\Outlook\Profiles",
            r"SOFTWARE\Microsoft\Office\15.0\Outlook\Profiles",
            r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Windows Messaging Subsystem\Profiles",
        ]

        email_pattern = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")

        for reg_path in outlook_paths:
            try:
                self._walk_registry_for_emails(
                    winreg.HKEY_CURRENT_USER,
                    reg_path,
                    email_pattern,
                    seen_emails,
                    results,
                    max_depth=5,
                )
            except OSError:
                pass

    def _walk_registry_for_emails(
        self,
        hive: int,
        key_path: str,
        email_pattern: re.Pattern,
        seen_emails: set,
        results: List[Dict],
        max_depth: int = 5,
        _depth: int = 0,
    ) -> None:
        """Recursively walk registry keys looking for email addresses."""
        if winreg is None:
            return  # Not on Windows
        if _depth > max_depth:
            return

        try:
            key = winreg.OpenKey(hive, key_path)
        except OSError:
            return

        try:
            # Check values in this key
            i = 0
            while True:
                try:
                    _name, data, _vtype = winreg.EnumValue(key, i)
                    i += 1
                    # Check string values for email patterns
                    if isinstance(data, str):
                        for match in email_pattern.finditer(data):
                            email = match.group(0).lower()
                            if email in seen_emails:
                                continue
                            seen_emails.add(email)
                            domain = email.split("@")[1]
                            provider = domain.split(".")[0]
                            results.append(
                                _make_fact(
                                    content=f"Email account: {email} (Outlook)",
                                    context="unclassified",
                                    entity=f"service:{provider}",
                                    sensitive=True,
                                )
                            )
                    elif isinstance(data, bytes):
                        try:
                            text = data.decode("utf-8", errors="replace")
                            for match in email_pattern.finditer(text):
                                email = match.group(0).lower()
                                if email in seen_emails:
                                    continue
                                seen_emails.add(email)
                                domain = email.split("@")[1]
                                provider = domain.split(".")[0]
                                results.append(
                                    _make_fact(
                                        content=f"Email account: {email} (Outlook)",
                                        context="unclassified",
                                        entity=f"service:{provider}",
                                        sensitive=True,
                                    )
                                )
                        except (UnicodeDecodeError, ValueError):
                            pass
                except OSError:
                    break

            # Recurse into subkeys
            j = 0
            while True:
                try:
                    subkey_name = winreg.EnumKey(key, j)
                    j += 1
                    self._walk_registry_for_emails(
                        hive,
                        f"{key_path}\\{subkey_name}",
                        email_pattern,
                        seen_emails,
                        results,
                        max_depth=max_depth,
                        _depth=_depth + 1,
                    )
                except OSError:
                    break
        finally:
            winreg.CloseKey(key)

    # ------------------------------------------------------------------
    # User profile scanners — infer facts about the user from local files
    # ------------------------------------------------------------------

    def scan_git_identity(self) -> List[Dict]:
        """Read ~/.gitconfig to learn the user's name, email, and employer."""
        facts: List[Dict] = []
        gitconfig = self._home / ".gitconfig"
        if not gitconfig.exists():
            return facts
        try:
            config = configparser.ConfigParser(strict=False)
            config.read(str(gitconfig), encoding="utf-8")
            name = config.get("user", "name", fallback="").strip()
            email = config.get("user", "email", fallback="").strip()
            editor = config.get("core", "editor", fallback="").strip()

            if name:
                facts.append(
                    _make_profile_fact(f"User's name is {name}", confidence=0.9)
                )
            if email:
                facts.append(
                    _make_profile_fact(
                        f"User's email is {email}",
                        confidence=0.9,
                        sensitive=True,
                    )
                )
                # Infer employer from email domain
                domain = email.split("@")[-1].lower() if "@" in email else ""
                free_providers = {
                    "gmail.com",
                    "outlook.com",
                    "hotmail.com",
                    "yahoo.com",
                    "icloud.com",
                    "protonmail.com",
                }
                if domain and domain not in free_providers:
                    company = domain.split(".")[0].title()
                    facts.append(
                        _make_profile_fact(
                            f"User likely works at {company} "
                            f"(inferred from email domain {domain})",
                            confidence=0.6,
                        )
                    )
            if editor:
                facts.append(
                    _make_profile_fact(
                        f"User's preferred editor is {editor}", confidence=0.8
                    )
                )
        except Exception as e:
            logger.debug("scan_git_identity failed: %s", e)
        return facts

    def scan_shell_config(self) -> List[Dict]:
        """Read shell config files to infer tools, aliases, and habits."""
        facts: List[Dict] = []
        home = self._home
        shell_files = [
            home / ".zshrc",
            home / ".bashrc",
            home / ".bash_profile",
            home / ".profile",
            home / ".zprofile",
        ]

        # Keywords that reveal tool usage
        tool_patterns = {
            "kubectl": "Kubernetes (kubectl)",
            "terraform": "Terraform",
            "aws": "AWS CLI",
            "gcloud": "Google Cloud CLI",
            "az ": "Azure CLI",
            "docker": "Docker",
            "nvm": "Node Version Manager (nvm)",
            "pyenv": "pyenv",
            "conda": "Conda/Anaconda",
            "cargo": "Rust/Cargo",
            "go ": "Go",
            "poetry": "Poetry (Python)",
            "pipenv": "Pipenv",
            "rbenv": "rbenv (Ruby)",
            "volta": "Volta (Node.js)",
        }

        found_tools: set = set()
        alias_count = 0

        for shell_file in shell_files:
            if not shell_file.exists():
                continue
            try:
                content = shell_file.read_text(encoding="utf-8", errors="replace")
                lines = content.splitlines()

                # Count aliases
                alias_count += sum(
                    1 for line in lines if line.strip().startswith("alias ")
                )

                # Detect tools from PATH exports and usage
                content_lower = content.lower()
                for pattern, tool_name in tool_patterns.items():
                    if pattern in content_lower and tool_name not in found_tools:
                        found_tools.add(tool_name)
            except Exception:
                continue

        if found_tools:
            tools_str = ", ".join(sorted(found_tools))
            facts.append(
                _make_profile_fact(
                    f"User uses these tools (detected in shell config): {tools_str}",
                    confidence=0.65,
                )
            )
        if alias_count > 5:
            facts.append(
                _make_profile_fact(
                    f"User has {alias_count} shell aliases, "
                    "suggesting a power-user workflow",
                    confidence=0.5,
                )
            )

        return facts

    def scan_project_manifests(self) -> List[Dict]:
        """Find project manifest files to understand what the user builds."""
        facts: List[Dict] = []
        home = self._home

        # Search candidate root directories
        search_roots: List[Path] = []
        for candidate in [
            "Projects",
            "projects",
            "Work",
            "work",
            "code",
            "Code",
            "dev",
            "Dev",
            "src",
            "repos",
            "github",
        ]:
            p = home / candidate
            if p.is_dir():
                search_roots.append(p)
        # Also check Documents
        docs = home / "Documents"
        if docs.is_dir():
            search_roots.append(docs)

        if not search_roots:
            search_roots = [home]

        manifests_found: List[Path] = []
        languages_seen: set = set()
        project_names: List[str] = []

        manifest_files = {
            "package.json": "Node.js/JavaScript",
            "pyproject.toml": "Python",
            "Cargo.toml": "Rust",
            "go.mod": "Go",
            "pom.xml": "Java (Maven)",
            "build.gradle": "Java/Kotlin (Gradle)",
            "Gemfile": "Ruby",
            "composer.json": "PHP",
            "mix.exs": "Elixir",
        }

        for root in search_roots:
            try:
                for dirpath, dirnames, filenames in os.walk(root):
                    depth = len(Path(dirpath).relative_to(root).parts)
                    if depth > 3:
                        dirnames.clear()
                        continue
                    dirnames[:] = [
                        d
                        for d in dirnames
                        if d not in _SKIP_DIRS and not d.startswith(".")
                    ]

                    for fname in filenames:
                        if fname in manifest_files:
                            lang = manifest_files[fname]
                            languages_seen.add(lang)
                            fpath = Path(dirpath) / fname
                            manifests_found.append(fpath)

                            # Try to read project name/description
                            if fname == "package.json":
                                data = _safe_read_json(fpath)
                                if data and isinstance(data, dict):
                                    pname = data.get("name", "")
                                    if pname and pname not in project_names:
                                        project_names.append(pname)
                            elif fname == "pyproject.toml":
                                try:
                                    content = fpath.read_text(
                                        encoding="utf-8", errors="replace"
                                    )
                                    for line in content.splitlines():
                                        if line.strip().startswith("name"):
                                            pname = (
                                                line.split("=")[-1]
                                                .strip()
                                                .strip('"')
                                                .strip("'")
                                            )
                                            if pname and pname not in project_names:
                                                project_names.append(pname)
                                            break
                                except Exception:
                                    pass
                            elif fname == "Cargo.toml":
                                try:
                                    content = fpath.read_text(
                                        encoding="utf-8", errors="replace"
                                    )
                                    for line in content.splitlines():
                                        if line.strip().startswith("name"):
                                            pname = (
                                                line.split("=")[-1].strip().strip('"')
                                            )
                                            if pname and pname not in project_names:
                                                project_names.append(pname)
                                            break
                                except Exception:
                                    pass
                    if len(manifests_found) >= 30:  # cap to avoid huge scans
                        break
            except (PermissionError, OSError):
                continue

        if languages_seen:
            langs = ", ".join(sorted(languages_seen))
            facts.append(
                _make_profile_fact(
                    f"User actively develops in: {langs}", confidence=0.75
                )
            )
        if project_names:
            shown = project_names[:8]
            facts.append(
                _make_profile_fact(
                    f"User has projects named: {', '.join(shown)}",
                    confidence=0.6,
                    context="work",
                )
            )

        return facts

    def scan_ssh_config(self) -> List[Dict]:
        """Read ~/.ssh/config to infer servers and work context."""
        facts: List[Dict] = []
        ssh_config = self._home / ".ssh" / "config"
        if not ssh_config.exists():
            return facts
        try:
            content = ssh_config.read_text(encoding="utf-8", errors="replace")
            hosts: List[str] = []
            for line in content.splitlines():
                line = line.strip()
                if line.lower().startswith("host ") and not line.startswith("Host *"):
                    host = line[5:].strip()
                    if host and host != "*":
                        hosts.append(host)
            if hosts:
                shown = hosts[:6]
                facts.append(
                    _make_profile_fact(
                        f"User has SSH config for: {', '.join(shown)}"
                        + (" and more" if len(hosts) > 6 else ""),
                        confidence=0.5,
                        context="work",
                        sensitive=True,
                    )
                )
        except Exception as e:
            logger.debug("scan_ssh_config failed: %s", e)
        return facts

    def scan_home_structure(self) -> List[Dict]:
        """Infer context from top-level home directory structure."""
        facts: List[Dict] = []
        home = self._home
        interest_hints = {
            "music": "music production or DJ-ing",
            "photos": "photography",
            "photography": "photography",
            "videos": "video editing/production",
            "games": "game development",
            "gamedev": "game development",
            "art": "digital art",
            "design": "design work",
            "writing": "writing",
            "blog": "blogging",
            "research": "research work",
            "papers": "academic research",
            "finance": "personal finance tracking",
            "investing": "investing",
        }

        try:
            top_dirs = [
                d.name.lower()
                for d in home.iterdir()
                if d.is_dir() and not d.name.startswith(".")
            ]
        except (PermissionError, OSError):
            return facts

        found_interests: List[str] = []
        for dirname in top_dirs:
            for keyword, interest in interest_hints.items():
                if keyword in dirname and interest not in found_interests:
                    found_interests.append(interest)

        if found_interests:
            facts.append(
                _make_profile_fact(
                    "User may have interests in: "
                    f"{', '.join(found_interests)} "
                    "(inferred from home folder names)",
                    confidence=0.4,
                )
            )

        return facts

    # ------------------------------------------------------------------
    # Personal Files Scan
    # ------------------------------------------------------------------

    def scan_personal_files(self) -> List[Dict]:
        """Scan for important personal files: configs, documents, creative work, data.

        Examines metadata only (name, path, size, modified date, extension)
        — never reads file contents.  Sensitive paths (SSH keys, credentials)
        are flagged with ``sensitive: True``.

        Returns:
            List of discovered fact/profile dicts with file insights.
        """
        results: List[Dict] = []

        home = self._home

        # --- 1. Resume / CV files in Documents ---
        try:
            self._scan_resume_files(home, results)
        except (PermissionError, OSError) as e:
            logger.debug("scan_personal_files resume scan error: %s", e)

        # --- 2. Important config / dotfiles ---
        try:
            self._scan_config_files(home, results)
        except (PermissionError, OSError) as e:
            logger.debug("scan_personal_files config scan error: %s", e)

        # --- 3. Creative projects (Blender, Photoshop, music DAWs) ---
        try:
            self._scan_creative_files(home, results)
        except (PermissionError, OSError) as e:
            logger.debug("scan_personal_files creative scan error: %s", e)

        # --- 4. Writing / notes directories ---
        try:
            self._scan_writing_directories(home, results)
        except (PermissionError, OSError) as e:
            logger.debug("scan_personal_files writing scan error: %s", e)

        # --- 5. Data files (CSV, SQLite, Jupyter) ---
        try:
            self._scan_data_files(home, results)
        except (PermissionError, OSError) as e:
            logger.debug("scan_personal_files data scan error: %s", e)

        return results

    # -- Personal files sub-scanners --

    def _scan_resume_files(self, home: Path, results: List[Dict]) -> None:
        """Find resume/CV files in Documents by filename patterns."""
        resume_pattern = re.compile(
            r"(resume|cv|curriculum[_\s-]?vitae|cover[_\s-]?letter)",
            re.IGNORECASE,
        )
        docs_dir = home / "Documents"
        if not docs_dir.exists() or not docs_dir.is_dir():
            return

        found: List[str] = []
        try:
            for entry in os.scandir(str(docs_dir)):
                if not entry.is_file(follow_symlinks=False):
                    continue
                ext = os.path.splitext(entry.name)[1].lower()
                if ext not in (".pdf", ".docx", ".doc", ".odt", ".rtf"):
                    continue
                if resume_pattern.search(os.path.splitext(entry.name)[0]):
                    found.append(entry.name)
        except (PermissionError, OSError):
            return

        if found:
            names = ", ".join(found[:5])
            results.append(
                _make_profile_fact(
                    f"Has resume/CV files in Documents: {names}",
                    context="personal",
                    confidence=0.8,
                    sensitive=True,
                )
            )

    def _scan_config_files(self, home: Path, results: List[Dict]) -> None:
        """Check for important dotfiles and config files."""
        # Map of config paths relative to home -> (description, sensitive)
        config_checks = [
            (".gitconfig", "Git configuration", False),
            (".ssh/config", "SSH configuration", True),
            (".ssh/id_rsa", "SSH RSA key", True),
            (".ssh/id_ed25519", "SSH Ed25519 key", True),
            (".bashrc", "Bash shell config", False),
            (".zshrc", "Zsh shell config", False),
            (".npmrc", "npm configuration", True),
            (".pypirc", "PyPI credentials", True),
            (".docker/config.json", "Docker configuration", True),
            (".aws/credentials", "AWS credentials", True),
            (".kube/config", "Kubernetes config", True),
        ]

        found_configs: List[str] = []
        found_sensitive: List[str] = []

        for rel_path, description, is_sensitive in config_checks:
            full_path = home / rel_path
            if full_path.exists():
                if is_sensitive:
                    found_sensitive.append(description)
                else:
                    found_configs.append(description)

        if found_configs:
            results.append(
                _make_fact(
                    content=f"Config files present: {', '.join(found_configs)}",
                    context="unclassified",
                    entity="config:dotfiles",
                    confidence=0.5,
                )
            )

        if found_sensitive:
            results.append(
                _make_fact(
                    content=f"Sensitive config files present: {', '.join(found_sensitive)}",
                    context="unclassified",
                    entity="config:credentials",
                    sensitive=True,
                    confidence=0.5,
                )
            )

    def _scan_creative_files(self, home: Path, results: List[Dict]) -> None:
        """Find creative project files (Blender, Photoshop, music, etc.)."""
        creative_exts = {
            ".blend": ("3D", "Blender project"),
            ".psd": ("design", "Photoshop file"),
            ".psb": ("design", "Photoshop large document"),
            ".ai": ("design", "Illustrator file"),
            ".indd": ("design", "InDesign file"),
            ".sketch": ("design", "Sketch file"),
            ".xd": ("design", "Adobe XD file"),
            # Music production
            ".als": ("music_production", "Ableton Live project"),
            ".flp": ("music_production", "FL Studio project"),
            ".logic": ("music_production", "Logic Pro project"),
            ".ptx": ("music_production", "Pro Tools project"),
            ".rpp": ("music_production", "REAPER project"),
            # 3D / game
            ".fbx": ("3D", "3D model file"),
            ".unitypackage": ("game_dev", "Unity package"),
            ".uproject": ("game_dev", "Unreal Engine project"),
            ".godot": ("game_dev", "Godot project"),
        }

        scan_dirs = [
            home / "Documents",
            home / "Desktop",
            home / "Downloads",
            home / "Projects",
            home / "Creative",
            home / "Art",
        ]

        category_counts: Dict[str, int] = {}  # category -> count

        for scan_dir in scan_dirs:
            if not scan_dir.exists() or not scan_dir.is_dir():
                continue
            try:
                for depth, (_dirpath, dirnames, filenames) in enumerate(
                    os.walk(str(scan_dir))
                ):
                    if depth >= 3:
                        dirnames.clear()
                        continue
                    dirnames[:] = [
                        d
                        for d in dirnames
                        if d not in _SKIP_DIRS and not d.startswith(".")
                    ]
                    for fname in filenames:
                        ext = os.path.splitext(fname)[1].lower()
                        if ext in creative_exts:
                            category, _desc = creative_exts[ext]
                            category_counts[category] = (
                                category_counts.get(category, 0) + 1
                            )
            except (PermissionError, OSError):
                continue

        _CREATIVE_LABELS = {
            "3D": "3D modeling (Blender/FBX)",
            "design": "graphic design (Photoshop/Illustrator)",
            "music_production": "music production (DAW projects)",
            "game_dev": "game development (Unity/Unreal/Godot)",
        }

        for category, count in sorted(
            category_counts.items(), key=lambda x: x[1], reverse=True
        ):
            if count >= 1:
                label = _CREATIVE_LABELS.get(category, category)
                results.append(
                    _make_profile_fact(
                        f"Has {count} {label} file(s) across personal directories",
                        context="personal",
                        confidence=0.75,
                        domain="personal",
                    )
                )

    def _scan_writing_directories(self, home: Path, results: List[Dict]) -> None:
        """Detect note-taking / writing directories (Obsidian, Notion, markdown)."""
        # Check for known note-taking app vaults/exports
        writing_indicators = [
            (home / "Documents" / "Obsidian", "Obsidian vault"),
            (home / "Obsidian", "Obsidian vault"),
            (home / "Documents" / "Notion", "Notion export"),
            (home / "Notion", "Notion export"),
            (home / "Documents" / "Notes", "notes directory"),
            (home / "Notes", "notes directory"),
            (home / "Documents" / "Journal", "journal directory"),
            (home / "Journal", "journal directory"),
            (home / "Documents" / "Writing", "writing directory"),
            (home / "Writing", "writing directory"),
            (home / "Documents" / "Blog", "blog directory"),
            (home / "Blog", "blog directory"),
        ]

        found_writing: List[str] = []
        for check_path, description in writing_indicators:
            if check_path.exists() and check_path.is_dir():
                # Count markdown files at top 2 levels as a quality signal
                md_count = 0
                try:
                    for depth, (_dirpath, dirnames, filenames) in enumerate(
                        os.walk(str(check_path))
                    ):
                        if depth >= 2:
                            dirnames.clear()
                            continue
                        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
                        md_count += sum(
                            1
                            for f in filenames
                            if f.lower().endswith((".md", ".mdx", ".txt"))
                        )
                except (PermissionError, OSError):
                    pass

                if md_count > 0:
                    found_writing.append(f"{description} (~{md_count} files)")
                else:
                    found_writing.append(description)

        # Also scan for .obsidian directories (indicates vault root)
        for candidate in [home / "Documents", home]:
            if not candidate.exists():
                continue
            try:
                for entry in os.scandir(str(candidate)):
                    if not entry.is_dir():
                        continue
                    obsidian_marker = Path(entry.path) / ".obsidian"
                    if obsidian_marker.is_dir():
                        desc = f"Obsidian vault '{entry.name}'"
                        already_found = any(
                            "Obsidian vault" in item for item in found_writing
                        )
                        if not already_found:
                            found_writing.append(desc)
            except (PermissionError, OSError):
                pass

        if found_writing:
            results.append(
                _make_profile_fact(
                    f"Writing/notes: {', '.join(found_writing[:4])}",
                    context="personal",
                    confidence=0.7,
                    domain="personal",
                )
            )

    def _scan_data_files(self, home: Path, results: List[Dict]) -> None:
        """Find data analysis files (CSVs, SQLite databases, Jupyter notebooks)."""
        data_exts = {
            ".csv": "CSV",
            ".tsv": "TSV",
            ".sqlite": "SQLite",
            ".db": "SQLite",
            ".ipynb": "Jupyter",
            ".parquet": "Parquet",
            ".feather": "Feather",
            ".h5": "HDF5",
            ".hdf5": "HDF5",
        }

        scan_dirs = [
            home / "Documents",
            home / "Desktop",
            home / "Downloads",
            home / "Projects",
            home / "Work",
            home / "Data",
        ]

        type_counts: Dict[str, int] = {}

        for scan_dir in scan_dirs:
            if not scan_dir.exists() or not scan_dir.is_dir():
                continue
            try:
                for depth, (_dirpath, dirnames, filenames) in enumerate(
                    os.walk(str(scan_dir))
                ):
                    if depth >= 3:
                        dirnames.clear()
                        continue
                    dirnames[:] = [
                        d
                        for d in dirnames
                        if d not in _SKIP_DIRS and not d.startswith(".")
                    ]
                    for fname in filenames:
                        ext = os.path.splitext(fname)[1].lower()
                        if ext in data_exts:
                            label = data_exts[ext]
                            type_counts[label] = type_counts.get(label, 0) + 1
            except (PermissionError, OSError):
                continue

        if type_counts:
            total = sum(type_counts.values())
            parts = [
                f"{count} {label}"
                for label, count in sorted(
                    type_counts.items(), key=lambda x: x[1], reverse=True
                )
            ]
            results.append(
                _make_profile_fact(
                    f"Data files found: {', '.join(parts[:5])} ({total} total)",
                    confidence=0.65,
                    domain="technical",
                )
            )

    # ------------------------------------------------------------------
    # Windows UserAssist (actually-launched app frequency)
    # ------------------------------------------------------------------

    def scan_windows_userassist(self) -> List[Dict]:
        """Read Windows UserAssist registry to get actually-launched app frequency.

        The UserAssist key stores ROT13-encoded app paths with binary run-count
        data.  We decode the paths, extract the exe filename, and map to
        friendly app names.

        Returns:
            List of profile fact dicts for frequently launched applications.
            Empty on a platform with no branch — reported, never silent.
        """
        if unsupported_reason("windows_userassist"):
            return _log_unsupported("windows_userassist")
        if winreg is None:
            logger.warning(
                "UserAssist scan skipped: the winreg module is unavailable on this "
                "interpreter, so no app-launch frequency was read."
            )
            return []

        import codecs

        # Known exe -> friendly name mapping
        _USERASSIST_APP_NAMES = {
            "spotify.exe": "Spotify",
            "chrome.exe": "Google Chrome",
            "firefox.exe": "Mozilla Firefox",
            "msedge.exe": "Microsoft Edge",
            "code.exe": "VS Code",
            "outlook.exe": "Microsoft Outlook",
            "winword.exe": "Microsoft Word",
            "excel.exe": "Microsoft Excel",
            "powerpnt.exe": "Microsoft PowerPoint",
            "teams.exe": "Microsoft Teams",
            "slack.exe": "Slack",
            "discord.exe": "Discord",
            "zoom.exe": "Zoom",
            "steam.exe": "Steam",
            "epicgameslauncher.exe": "Epic Games Launcher",
            "obs64.exe": "OBS Studio",
            "obs32.exe": "OBS Studio",
            "vlc.exe": "VLC Media Player",
            "wmplayer.exe": "Windows Media Player",
            "mpc-hc64.exe": "MPC-HC",
            "mpc-hc.exe": "MPC-HC",
            "photoshop.exe": "Adobe Photoshop",
            "illustrator.exe": "Adobe Illustrator",
            "premiere.exe": "Adobe Premiere Pro",
            "afterfx.exe": "Adobe After Effects",
            "lightroom.exe": "Adobe Lightroom",
            "davinci resolve.exe": "DaVinci Resolve",
            "resolve.exe": "DaVinci Resolve",
            "figma.exe": "Figma",
            "notion.exe": "Notion",
            "obsidian.exe": "Obsidian",
            "1password.exe": "1Password",
            "bitwarden.exe": "Bitwarden",
            "pycharm64.exe": "PyCharm",
            "idea64.exe": "IntelliJ IDEA",
            "rider64.exe": "JetBrains Rider",
            "clion64.exe": "CLion",
            "webstorm64.exe": "WebStorm",
            "datagrip64.exe": "DataGrip",
            "powershell.exe": "PowerShell",
            "windowsterminal.exe": "Windows Terminal",
            "wt.exe": "Windows Terminal",
            "notepad++.exe": "Notepad++",
            "gimp-2.10.exe": "GIMP",
            "gimp.exe": "GIMP",
            "inkscape.exe": "Inkscape",
            "blender.exe": "Blender",
            "unity.exe": "Unity",
            "unrealengine.exe": "Unreal Engine",
            "cursor.exe": "Cursor",
        }

        # System executables to skip entirely
        _USERASSIST_SKIP = {
            "explorer.exe",
            "searchapp.exe",
            "searchui.exe",
            "startmenuexperiencehost.exe",
            "lockapp.exe",
            "shellexperiencehost.exe",
            "applicationframehost.exe",
            "systemsettings.exe",
            "settingsapp.exe",
            "winstore.app.exe",
            "runtimebroker.exe",
            "svchost.exe",
            "conhost.exe",
            "cmd.exe",
            "taskmgr.exe",
            "msiexec.exe",
            "rundll32.exe",
            "regsvr32.exe",
        }

        facts: List[Dict] = []
        app_counts: Dict[str, int] = {}

        try:
            ua_key_path = (
                r"SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer\UserAssist"
            )
            ua_key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, ua_key_path)
        except (OSError, PermissionError):
            return facts

        try:
            guid_idx = 0
            while True:
                try:
                    guid_name = winreg.EnumKey(ua_key, guid_idx)
                except OSError:
                    break
                guid_idx += 1

                try:
                    count_key = winreg.OpenKey(ua_key, rf"{guid_name}\Count")
                except (OSError, PermissionError):
                    continue

                try:
                    val_idx = 0
                    while True:
                        try:
                            name, data, _ = winreg.EnumValue(count_key, val_idx)
                        except OSError:
                            break
                        val_idx += 1

                        # Decode ROT13-encoded path
                        try:
                            decoded = codecs.decode(name, "rot_13")
                        except Exception:
                            continue

                        # Extract exe filename from the decoded path
                        basename = decoded.rsplit("\\", 1)[-1].rsplit("/", 1)[-1]
                        exe_lower = basename.lower().strip()

                        if not exe_lower.endswith(".exe"):
                            continue

                        # Parse run count from binary data (DWORD at offset 4)
                        if isinstance(data, bytes) and len(data) >= 8:
                            try:
                                run_count = int.from_bytes(
                                    data[4:8], byteorder="little"
                                )
                            except Exception:
                                continue
                        else:
                            continue

                        if run_count <= 2:
                            continue

                        # Skip system executables
                        if exe_lower in _USERASSIST_SKIP:
                            continue

                        # Use friendly name if known, otherwise derive from exe name
                        friendly = _USERASSIST_APP_NAMES.get(exe_lower)
                        if friendly:
                            key = friendly
                        else:
                            # Unknown app — only include if launched frequently enough
                            if run_count <= 5:
                                continue
                            key = exe_lower.replace(".exe", "").title()

                        # Keep highest count per app
                        if key not in app_counts or run_count > app_counts[key]:
                            app_counts[key] = run_count
                finally:
                    winreg.CloseKey(count_key)
        except Exception as e:
            logger.debug("scan_windows_userassist failed: %s", e)
        finally:
            winreg.CloseKey(ua_key)

        # Sort by count descending, take top 20
        sorted_apps = sorted(app_counts.items(), key=lambda x: x[1], reverse=True)[:20]

        for app_name, count in sorted_apps:
            facts.append(
                _make_profile_fact(
                    f"Frequently uses {app_name} ({count} launches)",
                    confidence=0.8,
                )
            )

        return facts

    # ------------------------------------------------------------------
    # Recent File Types
    # ------------------------------------------------------------------

    def scan_recent_file_types(self) -> List[Dict]:
        """Detect recently opened file type patterns across platforms.

        - **Windows**: reads the Recent folder (.lnk shortcuts).
        - **macOS**: scans ~/Downloads, ~/Documents, ~/Desktop for files
          modified in the last 30 days.
        - **Linux**: parses ``~/.local/share/recently-used.xbel``.

        Returns:
            List of profile fact dicts for file-type usage patterns. Empty on a
            platform with no branch — reported, never silent.
        """
        platform = _platform_key()
        if platform == "win32":
            return self._scan_windows_recent_files()
        if platform == "darwin":
            return self._scan_macos_recent_files()
        if platform == "linux":
            return self._scan_linux_recent_files()
        return _log_unsupported("recent_file_types")

    def _scan_windows_recent_files(self) -> List[Dict]:
        """Read Windows Recent folder to detect recently opened file type patterns.

        Windows stores .lnk shortcut files named like ``Document.docx.lnk``.
        We strip the .lnk suffix, extract the real extension, and group by
        category to infer work patterns.

        Returns:
            List of profile fact dicts for file-type usage patterns.
        """
        facts: List[Dict] = []
        appdata = os.environ.get("APPDATA", "")
        if not appdata:
            return facts

        recent_dir = Path(appdata) / "Microsoft" / "Windows" / "Recent"
        if not recent_dir.exists():
            return facts

        category_counts: Dict[str, tuple] = {}  # category -> (count, description)

        try:
            for entry in os.scandir(str(recent_dir)):
                if not entry.is_file():
                    continue
                fname = entry.name
                if not fname.lower().endswith(".lnk"):
                    continue

                # Strip the .lnk suffix to get the original filename
                real_name = fname[:-4]  # remove ".lnk"
                # Extract the real extension
                ext = os.path.splitext(real_name)[1].lower()
                if not ext:
                    continue

                cat_info = _FILE_TYPE_CATEGORIES.get(ext)
                if cat_info is None:
                    continue

                category, description = cat_info
                if category in category_counts:
                    prev_count, prev_desc = category_counts[category]
                    category_counts[category] = (prev_count + 1, prev_desc)
                else:
                    category_counts[category] = (1, description)
        except (PermissionError, OSError) as e:
            logger.debug("_scan_windows_recent_files error: %s", e)
            return facts

        # Emit facts for categories with >= 2 occurrences
        for category, (count, description) in sorted(
            category_counts.items(), key=lambda x: x[1][0], reverse=True
        ):
            if count >= 2:
                facts.append(
                    _make_profile_fact(
                        f"Regularly works with {description} (found in recent files)",
                        confidence=0.7,
                    )
                )

        return facts

    def _scan_macos_recent_files(self) -> List[Dict]:
        """Scan recently modified files in standard macOS user directories.

        Checks ~/Downloads, ~/Documents, and ~/Desktop for files modified
        within the last 30 days and groups them by file-type category.

        Returns:
            List of profile fact dicts for file-type usage patterns.
        """
        import time as _time

        facts: List[Dict] = []
        cutoff = _time.time() - (30 * 86400)
        category_counts: Dict[str, tuple] = {}

        for scan_dir in [
            self._home / "Downloads",
            self._home / "Documents",
            self._home / "Desktop",
        ]:
            if not scan_dir.exists():
                continue
            try:
                for entry in os.scandir(str(scan_dir)):
                    if not entry.is_file(follow_symlinks=False):
                        continue
                    try:
                        if entry.stat().st_mtime < cutoff:
                            continue
                    except OSError:
                        continue
                    ext = os.path.splitext(entry.name)[1].lower()
                    cat_info = _FILE_TYPE_CATEGORIES.get(ext)
                    if cat_info is None:
                        continue
                    category, description = cat_info
                    if category in category_counts:
                        count, desc = category_counts[category]
                        category_counts[category] = (count + 1, desc)
                    else:
                        category_counts[category] = (1, description)
            except (PermissionError, OSError):
                pass

        for category, (count, description) in sorted(
            category_counts.items(), key=lambda x: x[1][0], reverse=True
        ):
            if count >= 2:
                facts.append(
                    _make_profile_fact(
                        f"Regularly works with {description} (found in recent files)",
                        confidence=0.65,
                    )
                )

        return facts

    def _scan_linux_recent_files(self) -> List[Dict]:
        """Parse ~/.local/share/recently-used.xbel for Linux recent file patterns.

        The XBEL file contains ``<bookmark href="file:///...">`` entries.
        We extract extensions from the file URIs and group by category.

        Returns:
            List of profile fact dicts for file-type usage patterns.
        """
        import xml.etree.ElementTree as ET
        from urllib.parse import unquote

        xbel_path = self._home / ".local" / "share" / "recently-used.xbel"
        if not xbel_path.exists():
            return []

        facts: List[Dict] = []
        category_counts: Dict[str, tuple] = {}

        try:
            tree = ET.parse(str(xbel_path))
            root = tree.getroot()
            for bookmark in root.findall("bookmark"):
                href = bookmark.get("href", "")
                if not href.startswith("file://"):
                    continue
                path = unquote(href[7:])  # strip "file://"
                ext = os.path.splitext(path)[1].lower()
                cat_info = _FILE_TYPE_CATEGORIES.get(ext)
                if cat_info is None:
                    continue
                category, description = cat_info
                if category in category_counts:
                    count, desc = category_counts[category]
                    category_counts[category] = (count + 1, desc)
                else:
                    category_counts[category] = (1, description)
        except Exception as e:
            logger.debug("_scan_linux_recent_files failed: %s", e)
            return facts

        for category, (count, description) in sorted(
            category_counts.items(), key=lambda x: x[1][0], reverse=True
        ):
            if count >= 2:
                facts.append(
                    _make_profile_fact(
                        f"Regularly works with {description} (found in recent files)",
                        confidence=0.65,
                    )
                )

        return facts

    # ------------------------------------------------------------------
    # Gaming and Media
    # ------------------------------------------------------------------

    def scan_gaming_and_media(self) -> List[Dict]:
        """Detect gaming platforms and local media collections.

        Checks for Steam, Epic Games, Xbox Game Pass libraries, local music
        collections, photography (RAW files), and video production tools.

        Returns:
            List of profile fact dicts for gaming and media usage.
        """
        facts: List[Dict] = []

        # --- Steam ---
        try:
            steam_paths = [
                # Windows (default install location)
                Path("C:/Program Files (x86)/Steam/steamapps/common"),
                Path("C:/Program Files/Steam/steamapps/common"),
                # macOS
                self._home
                / "Library"
                / "Application Support"
                / "Steam"
                / "steamapps"
                / "common",
                # Linux
                self._home / ".local" / "share" / "Steam" / "steamapps" / "common",
                self._home / ".steam" / "steam" / "steamapps" / "common",
            ]
            for steam_path in steam_paths:
                if steam_path.exists() and steam_path.is_dir():
                    try:
                        game_count = sum(
                            1 for e in os.scandir(str(steam_path)) if e.is_dir()
                        )
                    except (PermissionError, OSError):
                        game_count = 0
                    if game_count > 0:
                        facts.append(
                            _make_profile_fact(
                                f"Has Steam gaming library with ~{game_count} installed games",
                                context="personal",
                                confidence=0.9,
                            )
                        )
                        break  # Don't double-count
        except (PermissionError, OSError) as e:
            logger.debug("scan_gaming_and_media Steam error: %s", e)

        # --- Epic Games ---
        try:
            epic_path = Path("C:/Program Files/Epic Games")
            if epic_path.exists() and epic_path.is_dir():
                try:
                    game_names = [
                        e.name
                        for e in os.scandir(str(epic_path))
                        if e.is_dir()
                        and e.name.lower() not in ("launcher", "directxredist")
                    ]
                except (PermissionError, OSError):
                    game_names = []
                if game_names:
                    facts.append(
                        _make_profile_fact(
                            "Has Epic Games library",
                            context="personal",
                            confidence=0.8,
                        )
                    )
        except (PermissionError, OSError) as e:
            logger.debug("scan_gaming_and_media Epic error: %s", e)

        # --- Xbox Game Pass ---
        try:
            xbox_path = Path("C:/XboxGames")
            if xbox_path.exists() and xbox_path.is_dir():
                facts.append(
                    _make_profile_fact(
                        "Has Xbox Game Pass library",
                        context="personal",
                        confidence=0.8,
                    )
                )
        except (PermissionError, OSError) as e:
            logger.debug("scan_gaming_and_media Xbox error: %s", e)

        # --- Local music collection ---
        try:
            music_dir = self._home / "Music"
            if music_dir.exists() and music_dir.is_dir():
                music_exts = {".mp3", ".flac", ".aac", ".wav", ".m4a"}
                track_count = 0
                for depth, (_dirpath, dirnames, filenames) in enumerate(
                    os.walk(str(music_dir))
                ):
                    if depth >= 2:
                        dirnames.clear()
                        continue
                    for fname in filenames:
                        if os.path.splitext(fname)[1].lower() in music_exts:
                            track_count += 1
                if track_count > 20:
                    facts.append(
                        _make_profile_fact(
                            f"Has local music collection (~{track_count} tracks)",
                            context="personal",
                            confidence=0.85,
                        )
                    )
        except (PermissionError, OSError) as e:
            logger.debug("scan_gaming_and_media music error: %s", e)

        # --- Photography (RAW files) ---
        try:
            pictures_dir = self._home / "Pictures"
            if pictures_dir.exists() and pictures_dir.is_dir():
                raw_exts = {".raw", ".cr2", ".cr3", ".nef", ".arw", ".dng"}
                raw_count = 0
                for depth, (_dirpath, dirnames, filenames) in enumerate(
                    os.walk(str(pictures_dir))
                ):
                    if depth >= 2:
                        dirnames.clear()
                        continue
                    for fname in filenames:
                        if os.path.splitext(fname)[1].lower() in raw_exts:
                            raw_count += 1
                if raw_count > 5:
                    facts.append(
                        _make_profile_fact(
                            "Photographer with local RAW image collection",
                            context="personal",
                            confidence=0.85,
                        )
                    )
        except (PermissionError, OSError) as e:
            logger.debug("scan_gaming_and_media photo error: %s", e)

        # --- Video production ---
        try:
            davinci_paths = [
                Path("C:/Program Files/Blackmagic Design/DaVinci Resolve"),
                Path("C:/Program Files/Blackmagic Design/DaVinci Resolve/Resolve.exe"),
            ]
            for dv_path in davinci_paths:
                if dv_path.exists():
                    facts.append(
                        _make_profile_fact(
                            "Has DaVinci Resolve installed (video production)",
                            context="personal",
                            confidence=0.85,
                        )
                    )
                    break
        except (PermissionError, OSError) as e:
            logger.debug("scan_gaming_and_media video error: %s", e)

        return facts

    # ------------------------------------------------------------------
    # macOS App Usage
    # ------------------------------------------------------------------

    def scan_macos_app_usage(self) -> List[Dict]:
        """Detect frequently used apps on macOS via Application Support directories.

        Checks known app data directory names inside
        ``~/Library/Application Support/`` to identify which consumer
        applications are installed and actively used.

        Returns:
            List of profile fact dicts for detected macOS applications. Empty on
            a platform with no branch — reported, never silent.
        """
        if unsupported_reason("macos_app_usage"):
            return _log_unsupported("macos_app_usage")

        # Known app data dir names -> friendly app names
        APP_SUPPORT_MAP = {
            "Spotify": "Spotify",
            "Slack": "Slack",
            "discord": "Discord",
            "zoom.us": "Zoom",
            "Microsoft Teams": "Microsoft Teams",
            "Microsoft Outlook": "Microsoft Outlook",
            "Microsoft Word": "Microsoft Word",
            "Microsoft Excel": "Microsoft Excel",
            "Microsoft PowerPoint": "Microsoft PowerPoint",
            "Notion": "Notion",
            "Obsidian": "Obsidian",
            "1Password 7 - Password Manager": "1Password",
            "1Password": "1Password",
            "Figma": "Figma",
            "com.adobe.Photoshop": "Adobe Photoshop",
            "Adobe Illustrator": "Adobe Illustrator",
            "Adobe Premiere Pro": "Adobe Premiere Pro",
            "Final Cut Pro": "Final Cut Pro",
            "Logic Pro": "Logic Pro X",
            "Blender": "Blender",
            "Steam": "Steam",
            "OBS": "OBS Studio",
            "VLC": "VLC Media Player",
            "Plex Media Server": "Plex Media Server",
            "JetBrains": "JetBrains IDE",
        }

        app_support = self._home / "Library" / "Application Support"
        if not app_support.exists():
            return []

        found_apps: List[str] = []
        try:
            for entry in os.scandir(str(app_support)):
                if not entry.is_dir():
                    continue
                for key, friendly in APP_SUPPORT_MAP.items():
                    if key.lower() in entry.name.lower() and friendly not in found_apps:
                        found_apps.append(friendly)
                        break
        except (PermissionError, OSError):
            pass

        facts: List[Dict] = []
        for app_name in found_apps[:20]:
            facts.append(
                _make_profile_fact(
                    f"Uses {app_name}",
                    confidence=0.75,
                )
            )
        return facts

    # ------------------------------------------------------------------
    # scan_all — Run selected sources
    # ------------------------------------------------------------------

    def scan_all(
        self,
        sources: Optional[List[str]] = None,
        paths: Optional[List[Path]] = None,
        history_days: int = 30,
    ) -> Dict[str, List[Dict]]:
        """Run selected discovery sources and return results grouped by source.

        Args:
            sources: List of source names to scan. Default: all sources.
                Valid names: "file_system", "git_repos", "installed_apps",
                "browser_bookmarks", "browser_history", "email_accounts",
                "git_identity", "shell_config", "project_manifests",
                "ssh_config", "home_structure", "personal_files",
                "windows_userassist", "recent_file_types",
                "gaming_and_media", "macos_app_usage"
            paths: Override scan paths for file_system and git_repos.
            history_days: Days of browser history to scan.

        Returns:
            Dict mapping source name -> list of discovered fact dicts.
            Example: {"file_system": [...], "git_repos": [...], ...}
            A source with no scanner for the running platform is logged by name
            and mapped to an empty list, never silently omitted.
        """
        all_sources = [
            "file_system",
            "git_repos",
            "git_identity",
            "shell_config",
            "project_manifests",
            "ssh_config",
            "home_structure",
            "personal_files",
            "installed_apps",
            "browser_bookmarks",
            "browser_history",
            "email_accounts",
            "windows_userassist",
            "recent_file_types",
            "gaming_and_media",
            "macos_app_usage",
        ]

        if sources is None:
            sources = all_sources
        else:
            # Validate source names
            sources = [s for s in sources if s in all_sources]

        scan_map = {
            "file_system": lambda: self.scan_file_system(paths=paths),
            "git_repos": lambda: self.scan_git_repos(paths=paths),
            "git_identity": self.scan_git_identity,
            "shell_config": self.scan_shell_config,
            "project_manifests": self.scan_project_manifests,
            "ssh_config": self.scan_ssh_config,
            "home_structure": self.scan_home_structure,
            "personal_files": self.scan_personal_files,
            "installed_apps": self.scan_installed_apps,
            "browser_bookmarks": self.scan_browser_bookmarks,
            "browser_history": lambda: self.scan_browser_history(days=history_days),
            "email_accounts": self.scan_email_accounts,
            "windows_userassist": self.scan_windows_userassist,
            "recent_file_types": self.scan_recent_file_types,
            "gaming_and_media": self.scan_gaming_and_media,
            "macos_app_usage": self.scan_macos_app_usage,
        }

        results: Dict[str, List[Dict]] = {}

        for source_name in sources:
            if unsupported_reason(source_name):
                results[source_name] = _log_unsupported(source_name)
                continue
            scanner = scan_map.get(source_name)
            if scanner is None:
                continue
            try:
                results[source_name] = scanner()
            except Exception as e:
                logger.error(
                    "Discovery scan '%s' failed unexpectedly: %s",
                    source_name,
                    e,
                    exc_info=True,
                )
                results[source_name] = []

        return results
