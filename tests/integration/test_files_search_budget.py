# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""GET /api/files/search stays bounded on a large home directory.

Every test points HOME at a throwaway tree so the walk never touches the
real home directory.
"""

import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from gaia.ui.routers import files as files_router
from gaia.ui.server import create_app


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    assert Path.home() == home
    return home


@pytest.fixture
def client():
    return TestClient(create_app(db_path=":memory:"))


def _populate(directory: Path, count: int, prefix: str = "filler") -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for i in range(count):
        (directory / f"{prefix}_{i}.txt").write_text("x")


def _search(client, query, **params):
    resp = client.get("/api/files/search", params={"query": query, **params})
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_entry_cap_stops_the_walk_and_flags_truncation(client, fake_home, monkeypatch):
    monkeypatch.setattr(files_router, "SEARCH_MAX_ENTRIES", 25)
    _populate(fake_home / "Documents", 40)
    (fake_home / "Downloads").mkdir()
    (fake_home / "Downloads" / "target.txt").write_text("found me")

    data = _search(client, "target")

    assert data["truncated"] is True
    assert data["results"] == []
    assert str(fake_home / "Downloads") not in data["searched_locations"]


def test_time_budget_stops_the_walk_and_flags_truncation(
    client, fake_home, monkeypatch
):
    monkeypatch.setattr(files_router, "SEARCH_TIME_BUDGET_SEC", 0.0)
    for sub in ("Documents", "Downloads", "Desktop"):
        _populate(fake_home / sub, 20)

    start = time.monotonic()
    data = _search(client, "nomatch")
    elapsed = time.monotonic() - start

    assert data["truncated"] is True
    assert data["results"] == []
    assert elapsed < 5.0


def test_early_match_returns_without_walking_the_rest(client, fake_home):
    (fake_home / "Documents").mkdir()
    (fake_home / "Documents" / "budget_report.txt").write_text("q3")
    _populate(fake_home / "Downloads", 30)

    data = _search(client, "budget_report", max_results=1)

    assert data["total"] == 1
    assert data["results"][0]["name"] == "budget_report.txt"
    assert data["truncated"] is False
    assert data["searched_locations"] == [str(fake_home / "Documents")]


def test_full_walk_under_the_bounds_is_not_truncated(client, fake_home):
    _populate(fake_home / "Documents", 5)
    (fake_home / "projects").mkdir()
    (fake_home / "projects" / "notes_target.md").write_text("n")

    data = _search(client, "notes_target")

    assert data["truncated"] is False
    assert [r["name"] for r in data["results"]] == ["notes_target.md"]
    assert data["searched_locations"].count(str(fake_home / "Documents")) == 1


def test_stalled_directory_read_returns_partial_results_at_the_deadline(
    client, fake_home, monkeypatch
):
    monkeypatch.setattr(files_router, "SEARCH_TIME_BUDGET_SEC", 0.3)
    monkeypatch.setattr(files_router, "SEARCH_STALL_GRACE_SEC", 0.3)
    (fake_home / "Documents").mkdir()
    (fake_home / "Documents" / "plan_a.txt").write_text("a")
    stuck = fake_home / "Downloads"
    stuck.mkdir()

    release = threading.Event()
    real_iterdir = Path.iterdir

    def blocking_iterdir(self):
        if self == stuck:
            release.wait(timeout=30)
        return real_iterdir(self)

    monkeypatch.setattr(Path, "iterdir", blocking_iterdir)
    try:
        start = time.monotonic()
        data = _search(client, "plan_")
        elapsed = time.monotonic() - start
    finally:
        release.set()

    assert elapsed < 5.0
    assert data["truncated"] is True
    assert [r["name"] for r in data["results"]] == ["plan_a.txt"]
