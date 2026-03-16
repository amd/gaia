# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Auto-categorization of files by extension."""

from typing import Tuple

# Maps category -> set of extensions (lowercase, no leading dot)
CATEGORY_MAP = {
    "code": {
        "py",
        "js",
        "ts",
        "java",
        "c",
        "cpp",
        "h",
        "go",
        "rs",
        "rb",
        "php",
        "swift",
        "kt",
        "cs",
        "r",
        "scala",
        "sh",
        "bat",
        "ps1",
    },
    "document": {
        "pdf",
        "doc",
        "docx",
        "txt",
        "md",
        "rst",
        "rtf",
        "tex",
        "odt",
        "pages",
    },
    "spreadsheet": {"xlsx", "xls", "csv", "tsv", "ods", "numbers"},
    "presentation": {"pptx", "ppt", "odp", "key"},
    "image": {
        "jpg",
        "jpeg",
        "png",
        "gif",
        "bmp",
        "svg",
        "webp",
        "ico",
        "tiff",
        "raw",
        "psd",
        "ai",
    },
    "video": {"mp4", "avi", "mkv", "mov", "wmv", "flv", "webm"},
    "audio": {"mp3", "wav", "flac", "aac", "ogg", "wma", "m4a"},
    "data": {
        "json",
        "xml",
        "yaml",
        "yml",
        "toml",
        "ini",
        "cfg",
        "conf",
        "env",
        "properties",
    },
    "archive": {"zip", "tar", "gz", "bz2", "7z", "rar", "xz"},
    "config": {
        "gitignore",
        "dockerignore",
        "editorconfig",
        "eslintrc",
        "prettierrc",
    },
    "web": {"html", "htm", "css", "scss", "less", "sass"},
    "database": {"db", "sqlite", "sqlite3", "sql", "mdb"},
    "font": {"ttf", "otf", "woff", "woff2", "eot"},
}

# Subcategory refinements within major categories
_SUBCATEGORY_MAP = {
    # Code subcategories
    "py": ("code", "python"),
    "js": ("code", "javascript"),
    "ts": ("code", "typescript"),
    "java": ("code", "java"),
    "c": ("code", "c"),
    "cpp": ("code", "cpp"),
    "h": ("code", "c-header"),
    "go": ("code", "go"),
    "rs": ("code", "rust"),
    "rb": ("code", "ruby"),
    "php": ("code", "php"),
    "swift": ("code", "swift"),
    "kt": ("code", "kotlin"),
    "cs": ("code", "csharp"),
    "r": ("code", "r"),
    "scala": ("code", "scala"),
    "sh": ("code", "shell"),
    "bat": ("code", "batch"),
    "ps1": ("code", "powershell"),
    # Document subcategories
    "pdf": ("document", "pdf"),
    "doc": ("document", "word"),
    "docx": ("document", "word"),
    "txt": ("document", "plaintext"),
    "md": ("document", "markdown"),
    "rst": ("document", "restructuredtext"),
    "rtf": ("document", "richtext"),
    "tex": ("document", "latex"),
    "odt": ("document", "opendocument"),
    "pages": ("document", "pages"),
    # Spreadsheet subcategories
    "xlsx": ("spreadsheet", "excel"),
    "xls": ("spreadsheet", "excel"),
    "csv": ("spreadsheet", "csv"),
    "tsv": ("spreadsheet", "tsv"),
    "ods": ("spreadsheet", "opendocument"),
    "numbers": ("spreadsheet", "numbers"),
    # Presentation subcategories
    "pptx": ("presentation", "powerpoint"),
    "ppt": ("presentation", "powerpoint"),
    "odp": ("presentation", "opendocument"),
    "key": ("presentation", "keynote"),
    # Image subcategories
    "jpg": ("image", "jpeg"),
    "jpeg": ("image", "jpeg"),
    "png": ("image", "png"),
    "gif": ("image", "gif"),
    "bmp": ("image", "bitmap"),
    "svg": ("image", "vector"),
    "webp": ("image", "webp"),
    "ico": ("image", "icon"),
    "tiff": ("image", "tiff"),
    "raw": ("image", "raw"),
    "psd": ("image", "photoshop"),
    "ai": ("image", "illustrator"),
    # Video subcategories
    "mp4": ("video", "mp4"),
    "avi": ("video", "avi"),
    "mkv": ("video", "matroska"),
    "mov": ("video", "quicktime"),
    "wmv": ("video", "wmv"),
    "flv": ("video", "flash"),
    "webm": ("video", "webm"),
    # Audio subcategories
    "mp3": ("audio", "mp3"),
    "wav": ("audio", "wav"),
    "flac": ("audio", "flac"),
    "aac": ("audio", "aac"),
    "ogg": ("audio", "ogg"),
    "wma": ("audio", "wma"),
    "m4a": ("audio", "m4a"),
    # Data subcategories
    "json": ("data", "json"),
    "xml": ("data", "xml"),
    "yaml": ("data", "yaml"),
    "yml": ("data", "yaml"),
    "toml": ("data", "toml"),
    "ini": ("data", "ini"),
    "cfg": ("data", "config"),
    "conf": ("data", "config"),
    "env": ("data", "env"),
    "properties": ("data", "properties"),
    # Archive subcategories
    "zip": ("archive", "zip"),
    "tar": ("archive", "tar"),
    "gz": ("archive", "gzip"),
    "bz2": ("archive", "bzip2"),
    "7z": ("archive", "7zip"),
    "rar": ("archive", "rar"),
    "xz": ("archive", "xz"),
    # Config subcategories
    "gitignore": ("config", "git"),
    "dockerignore": ("config", "docker"),
    "editorconfig": ("config", "editor"),
    "eslintrc": ("config", "eslint"),
    "prettierrc": ("config", "prettier"),
    # Web subcategories
    "html": ("web", "html"),
    "htm": ("web", "html"),
    "css": ("web", "css"),
    "scss": ("web", "sass"),
    "less": ("web", "less"),
    "sass": ("web", "sass"),
    # Database subcategories
    "db": ("database", "generic"),
    "sqlite": ("database", "sqlite"),
    "sqlite3": ("database", "sqlite"),
    "sql": ("database", "sql"),
    "mdb": ("database", "access"),
    # Font subcategories
    "ttf": ("font", "truetype"),
    "otf": ("font", "opentype"),
    "woff": ("font", "woff"),
    "woff2": ("font", "woff2"),
    "eot": ("font", "eot"),
}

# Build reverse lookup: extension -> category (for fast lookup)
_EXTENSION_TO_CATEGORY: dict = {}
for _cat, _exts in CATEGORY_MAP.items():
    for _ext in _exts:
        _EXTENSION_TO_CATEGORY[_ext] = _cat


def auto_categorize(extension: str) -> Tuple[str, str]:
    """
    Categorize a file based on its extension.

    Args:
        extension: File extension, lowercase, without leading dot.
                   E.g., "py", "pdf", "jpg".

    Returns:
        Tuple of (category, subcategory). Returns ("other", "unknown")
        if the extension is not recognized.

    Examples:
        >>> auto_categorize("py")
        ('code', 'python')
        >>> auto_categorize("pdf")
        ('document', 'pdf')
        >>> auto_categorize("xyz")
        ('other', 'unknown')
    """
    ext = extension.lower().lstrip(".")
    if not ext:
        return ("other", "unknown")

    # Try detailed subcategory lookup first
    if ext in _SUBCATEGORY_MAP:
        return _SUBCATEGORY_MAP[ext]

    # Fall back to category-only lookup
    if ext in _EXTENSION_TO_CATEGORY:
        return (_EXTENSION_TO_CATEGORY[ext], "general")

    return ("other", "unknown")
