# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""No test may open a database in the developer's real ~/.gaia."""

import os
import sqlite3
import uuid
from pathlib import Path

import pytest

from gaia.filesystem.index import FileSystemIndexService
from gaia.scratchpad.service import ScratchpadService

REAL_GAIA_HOME = (Path.home() / ".gaia").resolve()


def _is_under_real_home(path: Path) -> bool:
    real = os.path.normcase(str(REAL_GAIA_HOME))
    candidate = os.path.normcase(str(Path(path).resolve()))
    return candidate == real or candidate.startswith(real + os.sep)


def _opened_file(service) -> Path:
    """The file SQLite actually opened for *service*."""
    rows = service._db.execute("PRAGMA database_list").fetchall()
    return Path(next(row["file"] for row in rows if row["name"] == "main"))


@pytest.mark.parametrize(
    "service_cls, name",
    [(FileSystemIndexService, "file_index.db"), (ScratchpadService, "scratchpad.db")],
)
@pytest.mark.parametrize("pass_path", [False, True], ids=["default", "explicit"])
def test_default_database_never_opens_in_the_real_home(service_cls, name, pass_path):
    # ChatAgentConfig passes the default path in explicitly, so check both.
    db_path = str(REAL_GAIA_HOME / name) if pass_path else None
    service = service_cls(db_path=db_path)
    try:
        opened = _opened_file(service)
    finally:
        service.close_db()
    assert opened.name == name
    assert not _is_under_real_home(opened), opened


def test_a_test_that_relocates_gaia_home_keeps_its_own_path(tmp_path, monkeypatch):
    monkeypatch.setenv("GAIA_HOME", str(tmp_path))
    service = FileSystemIndexService()
    try:
        assert _opened_file(service).resolve() == (tmp_path / "file_index.db").resolve()
    finally:
        service.close_db()


# The probes name a folder that does not exist, so even an unguarded connect
# could not create anything in the real home.
def test_opening_a_database_in_the_real_home_fails_the_test():
    probe = REAL_GAIA_HOME / f"missing-{uuid.uuid4().hex}" / "probe.db"
    with pytest.raises(pytest.fail.Exception, match="real home"):
        sqlite3.connect(str(probe))
    with pytest.raises(pytest.fail.Exception, match="real home"):
        sqlite3.dbapi2.connect(probe)


def test_opening_one_by_uri_fails_the_test():
    probe = REAL_GAIA_HOME / f"missing-{uuid.uuid4().hex}" / "probe.db"
    with pytest.raises(pytest.fail.Exception, match="real home"):
        sqlite3.connect(f"{probe.as_uri()}?mode=ro", uri=True)
    with pytest.raises(pytest.fail.Exception, match="real home"):
        sqlite3.connect(
            f"{probe.as_uri()}?mode=ro",
            5.0,
            0,
            None,
            True,
            sqlite3.Connection,
            128,
            True,
        )


def test_in_memory_and_tmp_databases_still_open(tmp_path):
    sqlite3.connect(":memory:").close()
    sqlite3.connect(str(tmp_path / "ok.db")).close()


def test_tui_history_is_not_the_real_home():
    assert not _is_under_real_home(Path(os.environ["GAIA_TUI_HOME"]))
