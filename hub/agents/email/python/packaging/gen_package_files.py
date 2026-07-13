# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Emit ``package-files.json`` — the listing of files inside the whole-package
zip — for the hub's package file-list display.

The release workflow stages the package (``binaries/`` + ``dist/`` + docs + lock +
manifest + LICENSE), runs this over the staging dir, and POSTs the result as the
``package_files`` part on ``/publish`` (see ``publish_to_r2.py``). The Worker pairs
it with the published ``.zip`` artifact to build the catalog's ``package`` entry.

Usage:
    gen_package_files.py <staging-dir> <out.json>

Output shape (paths are relative to the staging dir, sorted, forward slashes):
    {"files": [{"name": "README.md", "size_bytes": 13000}, ...]}
"""

from __future__ import annotations

import json
import os
import sys


def collect(root: str) -> list[dict]:
    files: list[dict] = []
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            path = os.path.join(dirpath, name)
            rel = os.path.relpath(path, root).replace(os.sep, "/")
            files.append({"name": rel, "size_bytes": os.path.getsize(path)})
    files.sort(key=lambda f: f["name"])
    return files


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 2:
        raise SystemExit("usage: gen_package_files.py <staging-dir> <out.json>")
    root, out = argv
    if not os.path.isdir(root):
        raise SystemExit(f"error: staging dir not found: {root}")
    files = collect(root)
    if not files:
        raise SystemExit(
            f"error: no files under {root} — refusing to emit an empty package list"
        )
    with open(out, "w", encoding="utf-8") as fh:
        json.dump({"files": files}, fh)
    print(f"[package] {out}: {len(files)} files", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
