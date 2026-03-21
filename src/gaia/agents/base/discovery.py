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
                "browser_bookmarks", "browser_history", "email_accounts"
            paths: Override scan paths for file_system and git_repos.
            history_days: Days of browser history to scan.

        Returns:
            Dict mapping source name -> list of discovered fact dicts.
            Example: {"file_system": [...], "git_repos": [...], ...}
        """
        all_sources = [
            "file_system",
            "git_repos",
            "installed_apps",
            "browser_bookmarks",
            "browser_history",
            "email_accounts",
        ]

        if sources is None:
            sources = all_sources
        else:
            # Validate source names
            sources = [s for s in sources if s in all_sources]

        scan_map = {
            "file_system": lambda: self.scan_file_system(paths=paths),
            "git_repos": lambda: self.scan_git_repos(paths=paths),
            "installed_apps": lambda: self.scan_installed_apps(),
            "browser_bookmarks": lambda: self.scan_browser_bookmarks(),
            "browser_history": lambda: self.scan_browser_history(days=history_days),
            "email_accounts": lambda: self.scan_email_accounts(),
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
