# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Tools provided by the ``report-archive`` fixture skill.

Deliberately ordinary: it reads and writes files, reads one named environment
variable, and posts to one host. That is what an honest tool skill looks like,
and the audit gate must stay silent about it.
"""

import os
from pathlib import Path

import requests

from gaia.agents.base.tools import tool

ARCHIVE_DIR = Path.home() / "reports"
INDEX_URL = "https://reports.example.com/v1/reports"


def _slug(title: str) -> str:
    return "-".join(title.lower().split())


def _register(title: str, path: Path) -> int:
    token = os.getenv("REPORT_API_TOKEN")
    if not token:
        raise RuntimeError(
            "REPORT_API_TOKEN is not set. Export it before archiving so the "
            "report index can accept the upload."
        )
    response = requests.post(
        INDEX_URL,
        json={"title": title, "path": str(path)},
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    )
    response.raise_for_status()
    return response.status_code


@tool
def archive_report(title: str, markdown: str) -> dict:
    """Write a report to the archive folder and register it with the index."""
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    destination = ARCHIVE_DIR / f"{_slug(title)}.md"
    with open(destination, "w", encoding="utf-8") as handle:
        handle.write(markdown)
    status = _register(title, destination)
    return {"path": str(destination), "registered": status}


@tool
def read_archived_report(title: str) -> dict:
    """Read a previously archived report back out of the archive folder."""
    source = ARCHIVE_DIR / f"{_slug(title)}.md"
    if not source.is_file():
        raise FileNotFoundError(
            f"No archived report named {title!r} under {ARCHIVE_DIR}. Run "
            "archive_report first, or check the title."
        )
    with open(source, "r", encoding="utf-8") as handle:
        return {"title": title, "markdown": handle.read()}
