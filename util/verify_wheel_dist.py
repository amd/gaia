#!/usr/bin/env python3
# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Verify the GAIA Agent UI ``dist/`` bundle is well-formed for shipping inside
the Python wheel.

Two modes:

    python util/verify_wheel_dist.py <directory>
        Validate a freshly built ``src/gaia/apps/webui/dist/`` directory.

    python util/verify_wheel_dist.py --wheel <path-to-wheel>
        Validate a built wheel contains a clean Agent UI bundle and is under
        PyPI's size limits.

Both modes enforce the same deny-list (no sourcemaps, dotfiles, ``node_modules``,
or leaked ``VITE_*`` build-time env values) and require ``index.html`` plus at
least one entry under ``assets/``. The wheel mode additionally enforces size
guards: hard-fail at 95 MB compressed (5 MB headroom under PyPI's 100 MB limit)
and 250 MB uncompressed; a GitHub Actions warning is emitted at 50 MB
compressed so size growth is visible early.

Exit code 0 on pass, 1 on any failure. Errors are printed loudly with the
offending file path; no silent skips.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import zipfile
from pathlib import Path
from typing import Iterable

# ---------------------------------------------------------------------------
# Deny-list rules
# ---------------------------------------------------------------------------

# Wheel-relative path prefix where the bundle lives once installed.
WHEEL_DIST_PREFIX = "gaia/apps/webui/dist/"

# Hard wheel-size limits. PyPI rejects > 100 MB compressed; the 95 MB
# threshold gives 5 MB headroom so we fail loudly *before* we waste an upload.
WHEEL_SIZE_HARD_FAIL_BYTES = 95 * 1024 * 1024
WHEEL_SIZE_WARN_BYTES = 50 * 1024 * 1024
WHEEL_UNCOMPRESSED_HARD_FAIL_BYTES = 250 * 1024 * 1024

# Files we permit in the bundle. Everything else is rejected — keeping this
# list narrow forces a deliberate update when vite emits a new asset type.
ALLOWED_EXTENSIONS = {
    ".html",
    ".js",
    ".css",
    ".svg",
    ".png",
    ".ico",
    ".webmanifest",
    ".json",
    ".woff",
    ".woff2",
    ".ttf",
    ".jpg",
    ".jpeg",
    ".webp",
    ".txt",
}

# Inverted VITE leak detection — see _check_vite_leak below.
_VITE_LEAK_RE = re.compile(
    r"""VITE_[A-Z0-9_]+\s*[:=]\s*["'](?!__VITE_|import\.meta)[^"'\\]{1,}["']"""
)
_INLINE_SOURCEMAP_RE = re.compile(rb"sourceMappingURL=data:")


# ---------------------------------------------------------------------------
# Result helpers
# ---------------------------------------------------------------------------


def _err(msg: str) -> None:
    """Emit a fail-loud error to stderr."""
    print(f"ERROR: {msg}", file=sys.stderr)


def _warn(msg: str) -> None:
    """Emit a GitHub Actions warning (visible in the run summary)."""
    print(f"::warning::{msg}")


# ---------------------------------------------------------------------------
# Directory mode
# ---------------------------------------------------------------------------


def verify_directory(dist_dir: Path) -> list[str]:
    """Run every directory-mode check; return a list of error messages."""
    errors: list[str] = []

    if not dist_dir.exists():
        return [f"dist directory does not exist: {dist_dir}"]
    if not dist_dir.is_dir():
        return [f"dist path is not a directory: {dist_dir}"]

    files = [p for p in dist_dir.rglob("*") if p.is_file()]
    if not files:
        return [f"dist directory is empty: {dist_dir}"]

    index_html = dist_dir / "index.html"
    if not index_html.is_file():
        errors.append(f"missing required file: {index_html}")

    has_assets = any(p.is_file() and (dist_dir / "assets") in p.parents for p in files)
    if not has_assets:
        errors.append(
            f"no files under assets/: expected {dist_dir / 'assets'} — "
            f"vite output structure may have changed"
        )

    relative_paths = [p.relative_to(dist_dir).as_posix() for p in files]
    errors.extend(_check_denylist(relative_paths))
    errors.extend(_check_inline_sourcemaps(files))
    errors.extend(_check_vite_leak(files))
    errors.extend(_check_extensions(files, dist_dir))

    if not errors:
        total_bytes = sum(p.stat().st_size for p in files)
        print(
            f"OK: dist contains {len(files)} files, "
            f"{total_bytes // 1024} KB, no denylist hits"
        )

    return errors


def _check_extensions(files: Iterable[Path], root: Path) -> list[str]:
    """Reject any file with an extension not on the allow-list."""
    errors: list[str] = []
    for p in files:
        suffix = p.suffix.lower()
        if suffix and suffix not in ALLOWED_EXTENSIONS:
            errors.append(
                f"unexpected file extension {suffix!r}: {p.relative_to(root)}"
            )
    return errors


# ---------------------------------------------------------------------------
# Wheel mode
# ---------------------------------------------------------------------------


def verify_wheel(wheel_path: Path) -> list[str]:
    """Run every wheel-mode check; return a list of error messages."""
    errors: list[str] = []

    if not wheel_path.exists():
        return [f"wheel does not exist: {wheel_path}"]
    if not zipfile.is_zipfile(wheel_path):
        return [f"file is not a valid zip/wheel: {wheel_path}"]

    compressed_size = wheel_path.stat().st_size
    if compressed_size > WHEEL_SIZE_HARD_FAIL_BYTES:
        errors.append(
            f"wheel is {compressed_size // (1024 * 1024)} MB compressed, "
            f"exceeds {WHEEL_SIZE_HARD_FAIL_BYTES // (1024 * 1024)} MB hard limit "
            f"(PyPI rejects > 100 MB)"
        )
    elif compressed_size > WHEEL_SIZE_WARN_BYTES:
        _warn(
            f"wheel is {compressed_size // (1024 * 1024)} MB compressed — "
            f"approaching PyPI 100 MB limit"
        )

    with zipfile.ZipFile(wheel_path) as zf:
        names = zf.namelist()
        uncompressed_size = sum(zi.file_size for zi in zf.infolist())

        if uncompressed_size > WHEEL_UNCOMPRESSED_HARD_FAIL_BYTES:
            errors.append(
                f"wheel uncompressed size is "
                f"{uncompressed_size // (1024 * 1024)} MB, exceeds "
                f"{WHEEL_UNCOMPRESSED_HARD_FAIL_BYTES // (1024 * 1024)} MB hard limit"
            )

        webui_entries = [n for n in names if n.startswith(WHEEL_DIST_PREFIX)]
        if not webui_entries:
            errors.append(
                f"wheel ships no files under {WHEEL_DIST_PREFIX}: "
                f"frontend bundle missing"
            )
            return errors

        if f"{WHEEL_DIST_PREFIX}index.html" not in names:
            errors.append(
                f"wheel missing required entry: {WHEEL_DIST_PREFIX}index.html"
            )

        assets_prefix = f"{WHEEL_DIST_PREFIX}assets/"
        if not any(n.startswith(assets_prefix) for n in names):
            errors.append(
                f"wheel ships no files under {assets_prefix}: "
                f"vite output structure may have changed"
            )

        relative_paths = [n[len(WHEEL_DIST_PREFIX) :] for n in webui_entries]
        errors.extend(_check_denylist(relative_paths))
        errors.extend(_check_inline_sourcemaps_in_wheel(zf, webui_entries))
        errors.extend(_check_vite_leak_in_wheel(zf, webui_entries))

    if not errors:
        print(
            f"OK: wheel contains {len(webui_entries)} webui assets, "
            f"compressed {compressed_size // 1024} KB, "
            f"uncompressed {uncompressed_size // 1024} KB"
        )

    return errors


# ---------------------------------------------------------------------------
# Shared deny-list checks (operate on path strings + content readers)
# ---------------------------------------------------------------------------


def _check_denylist(relative_paths: Iterable[str]) -> list[str]:
    """Reject sourcemaps, dotfiles, and ``node_modules`` anywhere in the tree."""
    errors: list[str] = []
    for rel in relative_paths:
        if not rel:
            continue
        if rel.endswith(".map"):
            errors.append(f"sourcemap file present: {rel}")
            continue
        parts = rel.split("/")
        if "node_modules" in parts:
            errors.append(f"node_modules entry present: {rel}")
            continue
        # Dotfile check: any path component starting with "." (other than ".")
        for part in parts:
            if part.startswith(".") and part not in (".", ".."):
                errors.append(f"dotfile present: {rel}")
                break
    return errors


def _check_inline_sourcemaps(files: Iterable[Path]) -> list[str]:
    """Reject ``sourceMappingURL=data:`` in any js/css file."""
    errors: list[str] = []
    for p in files:
        if p.suffix.lower() not in {".js", ".css"}:
            continue
        try:
            data = p.read_bytes()
        except OSError as e:
            errors.append(f"cannot read {p}: {e}")
            continue
        if _INLINE_SOURCEMAP_RE.search(data):
            errors.append(f"inline sourcemap (data: URI) present: {p}")
    return errors


def _check_inline_sourcemaps_in_wheel(
    zf: zipfile.ZipFile, entries: Iterable[str]
) -> list[str]:
    errors: list[str] = []
    for name in entries:
        if not name.endswith((".js", ".css")):
            continue
        with zf.open(name) as fh:
            data = fh.read()
        if _INLINE_SOURCEMAP_RE.search(data):
            errors.append(f"inline sourcemap (data: URI) present in wheel: {name}")
    return errors


def _check_vite_leak(files: Iterable[Path]) -> list[str]:
    """
    VITE leak detection — inverted from the original regex-on-bundle approach.

    Fail if any ``VITE_*`` env var is set with a non-empty, non-placeholder
    value at the time the verifier runs (i.e. inside the workflow step that
    just produced the bundle). This makes the build *environment* the source
    of truth — far more reliable than scanning minified output.

    As a backstop, we also scan ``index.html`` and ``assets/*.js`` for literal
    ``VITE_FOO:"value"`` strings that survived minification.
    """
    errors: list[str] = []

    # 1. Env-side check.
    for key, value in os.environ.items():
        if not key.startswith("VITE_"):
            continue
        if not value:
            continue
        if value.startswith("__VITE_") and value.endswith("__"):
            continue  # placeholder, not a leak
        errors.append(
            f"environment variable {key} is set with a non-empty value at "
            f"verify time — would be inlined by vite into the bundle. "
            f"Unset {key} before running `npm run build`."
        )

    # 2. Output-scan backstop.
    for p in files:
        if p.suffix.lower() not in {".html", ".js"}:
            continue
        # Only scan top-level html and direct assets/*.js — avoid sourcemaps
        # and deeply nested chunks where the regex over minified code is noisy.
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if _VITE_LEAK_RE.search(text):
            errors.append(f"possible leaked VITE_* literal in bundle: {p}")
    return errors


def _check_vite_leak_in_wheel(zf: zipfile.ZipFile, entries: Iterable[str]) -> list[str]:
    errors: list[str] = []
    # Env-side check (same as directory mode).
    for key, value in os.environ.items():
        if not key.startswith("VITE_") or not value:
            continue
        if value.startswith("__VITE_") and value.endswith("__"):
            continue
        errors.append(
            f"environment variable {key} is set with a non-empty value at "
            f"verify time — vite would have inlined it. Unset before build."
        )

    # Bundle scan backstop (HTML + JS entries only).
    for name in entries:
        if not (name.endswith(".html") or name.endswith(".js")):
            continue
        with zf.open(name) as fh:
            data = fh.read()
        try:
            text = data.decode("utf-8", errors="replace")
        except UnicodeDecodeError:
            continue
        if _VITE_LEAK_RE.search(text):
            errors.append(f"possible leaked VITE_* literal in wheel: {name}")
    return errors


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Verify a freshly built Agent UI dist/ directory or a Python "
            "wheel containing it."
        )
    )
    parser.add_argument(
        "path",
        nargs="?",
        help="Path to a built dist/ directory (omit when using --wheel).",
    )
    parser.add_argument(
        "--wheel",
        metavar="WHEEL",
        help="Path to a built .whl file to inspect instead of a directory.",
    )
    args = parser.parse_args(argv)

    if args.wheel and args.path:
        parser.error("pass either a directory path or --wheel, not both")
    if not args.wheel and not args.path:
        parser.error("must pass a directory path or --wheel <path>")

    if args.wheel:
        errors = verify_wheel(Path(args.wheel))
    else:
        errors = verify_directory(Path(args.path))

    if errors:
        for e in errors:
            _err(e)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
