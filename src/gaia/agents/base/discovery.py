# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
SystemDiscovery: Local system scanner for day-zero bootstrap.

Scans the user's machine to discover projects, installed apps, browser data,
git repos, and email accounts. Each method returns a list of dicts (discovered
facts) that are NOT stored directly — the caller presents them for user review.

stdlib only. Windows-focused. Never crashes — catches exceptions, returns
partial results.
"""

import configparser
import json
import logging
import os
import re
import shutil
import sqlite3
import tempfile
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import urlparse

try:
    import winreg  # Windows only
except ImportError:
    winreg = None  # Linux / macOS

logger = logging.getLogger(__name__)

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


def _detect_languages(path: Path, max_depth: int = 2) -> List[str]:
    """Detect programming languages in a directory by file extensions."""
    lang_counts: Dict[str, int] = {}
    try:
        for depth, (dirpath, dirnames, filenames) in enumerate(os.walk(path)):
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
    db_path: Path, query: str, params: tuple = ()
) -> List[tuple]:
    """Copy a SQLite DB to temp dir and query it (avoids lock issues).

    Browsers hold locks on their databases; copying first is required.
    Returns empty list on any error.
    """
    if not db_path.exists():
        return []
    tmp_path = None
    try:
        tmp_fd, tmp_path = tempfile.mkstemp(suffix=".db")
        os.close(tmp_fd)
        shutil.copy2(str(db_path), tmp_path)
        conn = sqlite3.connect(tmp_path)
        try:
            cursor = conn.execute(query, params)
            return cursor.fetchall()
        finally:
            conn.close()
    except (OSError, sqlite3.Error) as e:
        logger.debug("SQLite query failed for %s: %s", db_path, e)
        return []
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


def _categorize_app(app_name: str) -> str:
    """Categorize an application by its name."""
    name_lower = app_name.lower()
    for category, keywords in _APP_CATEGORIES.items():
        for keyword in keywords:
            if keyword in name_lower:
                return category
    return "Other"


# ============================================================================
# SystemDiscovery
# ============================================================================


class SystemDiscovery:
    """Local system scanner for bootstrap. No agent dependencies.

    Each method returns a list of discovered fact dicts, NOT stored directly.
    The caller (MemoryMixin.run_bootstrap) presents them for user review.

    All methods catch exceptions internally and return partial results.
    """

    def __init__(self):
        self._home = Path.home()

    # ------------------------------------------------------------------
    # File System Scan
    # ------------------------------------------------------------------

    def scan_file_system(self, paths: Optional[List[Path]] = None) -> List[Dict]:
        """Walk project directories (top 2 levels). Returns project names + languages.

        Scans ~/Work, ~/Documents, ~/Projects by default.
        Only reads folder names and file extensions — never file contents.
        Skips hidden directories, node_modules, .git, etc.

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

                    context = _classify_path(project_path)
                    content = (
                        f"Project '{project_name}' in {base_path.name}/ "
                        f"— {lang_str}"
                    )
                    if subfolder_count > 0:
                        content += f" ({subfolder_count} subfolders)"

                    results.append(
                        _make_fact(
                            content=content,
                            context=context,
                            entity=f"project:{project_name.lower().replace(' ', '_')}",
                        )
                    )
            except (PermissionError, OSError) as e:
                logger.debug("scan_file_system error for %s: %s", base_path, e)

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
    # Installed Apps Scan (Windows Registry + Start Menu)
    # ------------------------------------------------------------------

    def scan_installed_apps(self) -> List[Dict]:
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

    # ------------------------------------------------------------------
    # Browser Bookmarks Scan
    # ------------------------------------------------------------------

    def scan_browser_bookmarks(self) -> List[Dict]:
        """Read Chrome/Edge bookmark JSON and Firefox SQLite bookmarks.

        Groups bookmarks by domain. Flags banking/finance as sensitive.

        Returns:
            List of discovered fact dicts with bookmark domains and categories.
        """
        results: List[Dict] = []
        domain_urls: Dict[str, int] = {}

        # Chrome and Edge use identical JSON format
        chromium_paths = [
            (
                "Chrome",
                self._home
                / "AppData"
                / "Local"
                / "Google"
                / "Chrome"
                / "User Data"
                / "Default"
                / "Bookmarks",
            ),
            (
                "Edge",
                self._home
                / "AppData"
                / "Local"
                / "Microsoft"
                / "Edge"
                / "User Data"
                / "Default"
                / "Bookmarks",
            ),
        ]

        for browser_name, bookmark_path in chromium_paths:
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
        browser_name: str,
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
        """Extract bookmarks from Firefox places.sqlite."""
        firefox_root = (
            self._home / "AppData" / "Roaming" / "Mozilla" / "Firefox" / "Profiles"
        )
        if not firefox_root.exists():
            return

        # Find profile directories
        try:
            for entry in os.scandir(str(firefox_root)):
                if not entry.is_dir():
                    continue
                places_db = Path(entry.path) / "places.sqlite"
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
                )
                for _title, url in rows:
                    domain = _extract_domain(url)
                    if domain:
                        domain_urls[domain] = domain_urls.get(domain, 0) + 1
        except (PermissionError, OSError):
            pass

    # ------------------------------------------------------------------
    # Browser History Scan
    # ------------------------------------------------------------------

    def scan_browser_history(self, days: int = 30) -> List[Dict]:
        """Read browser history (Chrome/Edge/Firefox). Returns top domains only.

        Copies DB to temp file first to avoid browser lock issues.
        ALL results are flagged sensitive=True.

        Args:
            days: Number of days of history to scan. Default 30.

        Returns:
            List of discovered fact dicts. All marked sensitive.
        """
        domain_counts: Dict[str, int] = {}

        # Chrome epoch: Jan 1, 1601 (microseconds)
        # Convert days to Chrome timestamp
        import time

        now_unix = time.time()
        cutoff_unix = now_unix - (days * 86400)
        # Chrome timestamp = (Unix timestamp + 11644473600) * 1000000
        chrome_cutoff = int((cutoff_unix + 11644473600) * 1_000_000)

        # Chrome and Edge use identical History SQLite format
        chromium_paths = [
            (
                "Chrome",
                self._home
                / "AppData"
                / "Local"
                / "Google"
                / "Chrome"
                / "User Data"
                / "Default"
                / "History",
            ),
            (
                "Edge",
                self._home
                / "AppData"
                / "Local"
                / "Microsoft"
                / "Edge"
                / "User Data"
                / "Default"
                / "History",
            ),
        ]

        for browser_name, history_path in chromium_paths:
            try:
                rows = _safe_copy_and_query_sqlite(
                    history_path,
                    """
                    SELECT url, visit_count
                    FROM urls
                    WHERE last_visit_time > ?
                    ORDER BY visit_count DESC
                    LIMIT 500
                    """,
                    (chrome_cutoff,),
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
        """Extract history from Firefox places.sqlite."""
        firefox_root = (
            self._home / "AppData" / "Roaming" / "Mozilla" / "Firefox" / "Profiles"
        )
        if not firefox_root.exists():
            return

        try:
            for entry in os.scandir(str(firefox_root)):
                if not entry.is_dir():
                    continue
                places_db = Path(entry.path) / "places.sqlite"
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
                )
                for url, visit_count in rows:
                    domain = _extract_domain(url)
                    if domain:
                        domain_counts[domain] = domain_counts.get(domain, 0) + (
                            visit_count or 0
                        )
        except (PermissionError, OSError):
            pass

    # ------------------------------------------------------------------
    # Email Accounts Scan
    # ------------------------------------------------------------------

    def scan_email_accounts(self) -> List[Dict]:
        """Discover email accounts from Credential Manager, Thunderbird, Outlook.

        Reads addresses only — never email content.
        ALL results are flagged sensitive=True.

        Returns:
            List of discovered fact dicts with email addresses. All sensitive.
        """
        results: List[Dict] = []
        seen_emails: set = set()

        # 1. Windows Credential Manager (via cmdkey)
        try:
            self._scan_credential_manager(seen_emails, results)
        except Exception as e:
            logger.debug("Credential Manager scan failed: %s", e)

        # 2. Thunderbird profiles (prefs.js)
        try:
            self._scan_thunderbird(seen_emails, results)
        except Exception as e:
            logger.debug("Thunderbird scan failed: %s", e)

        # 3. Outlook registry
        try:
            self._scan_outlook_registry(seen_emails, results)
        except Exception as e:
            logger.debug("Outlook registry scan failed: %s", e)

        return results

    def _scan_credential_manager(self, seen_emails: set, results: List[Dict]) -> None:
        """Scan Windows Credential Manager for email-related credentials."""
        import subprocess
        import sys

        if sys.platform != "win32":
            return

        try:
            output = subprocess.check_output(
                ["cmdkey", "/list"],
                text=True,
                timeout=10,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        except (subprocess.SubprocessError, FileNotFoundError, OSError):
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

    def _scan_thunderbird(self, seen_emails: set, results: List[Dict]) -> None:
        """Scan Thunderbird prefs.js for email account addresses."""
        thunderbird_root = (
            self._home / "AppData" / "Roaming" / "Thunderbird" / "Profiles"
        )
        if not thunderbird_root.exists():
            return

        email_pref_pattern = re.compile(
            r'user_pref\("mail\.identity\.id\d+\.useremail"\s*,\s*"([^"]+)"\)'
        )

        try:
            for entry in os.scandir(str(thunderbird_root)):
                if not entry.is_dir():
                    continue
                prefs_path = Path(entry.path) / "prefs.js"
                if not prefs_path.exists():
                    continue

                try:
                    content = prefs_path.read_text(encoding="utf-8", errors="replace")
                    for match in email_pref_pattern.finditer(content):
                        email = match.group(1).lower().strip()
                        if email in seen_emails:
                            continue
                        seen_emails.add(email)

                        domain = email.split("@")[1] if "@" in email else "unknown"
                        provider = domain.split(".")[0]

                        results.append(
                            _make_fact(
                                content=f"Email account: {email} (Thunderbird)",
                                context="unclassified",
                                entity=f"service:{provider}",
                                sensitive=True,
                            )
                        )
                except (OSError, UnicodeDecodeError):
                    pass
        except (PermissionError, OSError):
            pass

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
                    name, data, _vtype = winreg.EnumValue(key, i)
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
    # Windows UserAssist (actually-launched app frequency)
    # ------------------------------------------------------------------

    def scan_windows_userassist(self) -> List[Dict]:
        """Read Windows UserAssist registry to get actually-launched app frequency.

        The UserAssist key stores ROT13-encoded app paths with binary run-count
        data.  We decode the paths, extract the exe filename, and map to
        friendly app names.

        Returns:
            List of profile fact dicts for frequently launched applications.
        """
        if os.name != "nt" or winreg is None:
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
            List of profile fact dicts for file-type usage patterns.
        """
        import sys

        if sys.platform == "win32":
            return self._scan_windows_recent_files()
        elif sys.platform == "darwin":
            return self._scan_macos_recent_files()
        elif sys.platform.startswith("linux"):
            return self._scan_linux_recent_files()
        return []

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
                for depth, (dirpath, dirnames, filenames) in enumerate(
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
                for depth, (dirpath, dirnames, filenames) in enumerate(
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
                Path(
                    "C:/Program Files/Blackmagic Design" "/DaVinci Resolve/Resolve.exe"
                ),
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
            List of profile fact dicts for detected macOS applications.
        """
        import sys

        if sys.platform != "darwin":
            return []

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
                "ssh_config", "home_structure", "windows_userassist",
                "recent_file_types", "gaming_and_media",
                "macos_app_usage"
            paths: Override scan paths for file_system and git_repos.
            history_days: Days of browser history to scan.

        Returns:
            Dict mapping source name -> list of discovered fact dicts.
            Example: {"file_system": [...], "git_repos": [...], ...}
        """
        all_sources = [
            "file_system",
            "git_repos",
            "git_identity",
            "shell_config",
            "project_manifests",
            "ssh_config",
            "home_structure",
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
            "git_identity": lambda: self.scan_git_identity(),
            "shell_config": lambda: self.scan_shell_config(),
            "project_manifests": lambda: self.scan_project_manifests(),
            "ssh_config": lambda: self.scan_ssh_config(),
            "home_structure": lambda: self.scan_home_structure(),
            "installed_apps": lambda: self.scan_installed_apps(),
            "browser_bookmarks": lambda: self.scan_browser_bookmarks(),
            "browser_history": lambda: self.scan_browser_history(days=history_days),
            "email_accounts": lambda: self.scan_email_accounts(),
            "windows_userassist": lambda: self.scan_windows_userassist(),
            "recent_file_types": lambda: self.scan_recent_file_types(),
            "gaming_and_media": lambda: self.scan_gaming_and_media(),
            "macos_app_usage": lambda: self.scan_macos_app_usage(),
        }

        results: Dict[str, List[Dict]] = {}

        for source_name in sources:
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
