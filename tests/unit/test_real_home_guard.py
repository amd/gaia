# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The session guard on the real ``~/.gaia`` fails a run that touched it (#8790)."""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from tests.unit.real_home_guard import (
    ALLOW_ENV,
    ENFORCE_ENV,
    FAIL,
    SKIP,
    WARN,
    changes,
    guard_mode,
    list_home,
    settle,
)

_GUARD_DIR = Path(__file__).resolve().parent


def _seed(home: Path) -> Path:
    gaia = home / ".gaia"
    (gaia / "chat").mkdir(parents=True)
    (gaia / "config.json").write_text("{}", encoding="utf-8")
    (gaia / "chat" / "history.db").write_bytes(b"rows")
    return gaia


def test_in_ci_a_session_that_writes_into_the_real_home_fails_and_names_the_paths(
    tmp_path,
):
    """One pytest run under a stand-in HOME: a child's write and an edit in place."""
    home = tmp_path / "home"
    _seed(home)
    suite = tmp_path / "suite"
    suite.mkdir()
    (suite / "conftest.py").write_text(
        textwrap.dedent(f"""
            import sys

            sys.path.insert(0, {str(_GUARD_DIR)!r})
            from real_home_guard import _guard_real_gaia_home_session  # noqa: F401
            """),
        encoding="utf-8",
    )
    (suite / "test_leaks.py").write_text(
        textwrap.dedent("""
            import subprocess
            import sys
            from pathlib import Path

            GAIA = Path.home() / ".gaia"


            def test_a_child_process_writes_a_log():
                code = "import sys; open(sys.argv[1], 'w').write('x')"
                subprocess.run(
                    [sys.executable, "-c", code, str(GAIA / "gaia.log")], check=True
                )


            def test_a_database_grows_in_place():
                with open(GAIA / "chat" / "history.db", "ab") as handle:
                    handle.write(b"more rows")
            """),
        encoding="utf-8",
    )
    env = {**os.environ, "HOME": str(home), "USERPROFILE": str(home), "CI": "true"}
    env.pop("RUNNER_ENVIRONMENT", None)
    env.pop(ALLOW_ENV, None)
    env.pop(ENFORCE_ENV, None)
    command = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "."]

    run = subprocess.run(
        command, cwd=suite, env=env, capture_output=True, text=True, check=False
    )

    report = run.stdout + run.stderr
    assert run.returncode == 1, report
    assert "2 passed, 1 error" in report
    assert "created gaia.log" in report
    assert f"changed {os.path.join('chat', 'history.db')}" in report
    assert "config.json" not in report
    assert ALLOW_ENV in report


@pytest.mark.parametrize(
    "environ, mode",
    [
        ({}, WARN),
        ({"CI": ""}, WARN),
        ({"CI": "false"}, WARN),
        ({"CI": "true"}, FAIL),
        ({"CI": "1"}, FAIL),
        ({"CI": "true", "RUNNER_ENVIRONMENT": "github-hosted"}, FAIL),
        ({"CI": "true", "RUNNER_ENVIRONMENT": "self-hosted"}, WARN),
        ({ENFORCE_ENV: "1", "RUNNER_ENVIRONMENT": "self-hosted"}, FAIL),
        ({ENFORCE_ENV: "1"}, FAIL),
        ({ALLOW_ENV: "1"}, SKIP),
        ({ALLOW_ENV: "1", "CI": "true", ENFORCE_ENV: "1"}, SKIP),
    ],
)
def test_the_environment_picks_fail_warn_or_skip(environ, mode):
    assert guard_mode(environ) == mode


def test_on_a_developer_machine_a_changed_home_is_one_warning(tmp_path):
    with pytest.warns(pytest.PytestWarning) as caught:
        settle(["created gaia.log"], tmp_path / ".gaia", WARN)

    (warning,) = caught
    text = str(warning.message)
    assert "created gaia.log" in text
    assert ALLOW_ENV in text
    assert ENFORCE_ENV in text


def test_enforced_a_changed_home_fails_with_the_same_paths(tmp_path):
    with pytest.raises(pytest.fail.Exception, match="created gaia.log"):
        settle(["created gaia.log"], tmp_path / ".gaia", FAIL)


@pytest.mark.parametrize("found, mode", [(["created gaia.log"], SKIP), ([], FAIL)])
def test_allowed_writes_and_an_untouched_home_say_nothing(
    tmp_path, recwarn, found, mode
):
    settle(found, tmp_path / ".gaia", mode)

    assert not recwarn.list


def test_a_home_that_is_left_alone_reports_nothing(tmp_path):
    gaia = _seed(tmp_path)
    before = list_home(gaia)

    assert changes(before, list_home(gaia)) == []
    assert set(before.entries) == {
        "",
        "chat",
        "config.json",
        os.path.join("chat", "history.db"),
    }


def test_created_changed_and_removed_paths_are_each_named(tmp_path):
    gaia = _seed(tmp_path)
    before = list_home(gaia)

    (gaia / "config.json").write_text('{"default_model": "x"}', encoding="utf-8")
    (gaia / "chat" / "history.db").unlink()
    (gaia / "logs").mkdir()

    found = changes(before, list_home(gaia))
    assert "changed config.json" in found
    assert f"removed {os.path.join('chat', 'history.db')}" in found
    assert "created logs" in found


def test_a_missing_home_is_not_created_and_its_creation_is_reported(tmp_path):
    gaia = tmp_path / ".gaia"
    before = list_home(gaia)

    assert before.entries is None
    assert not gaia.exists()
    assert changes(before, list_home(gaia)) == []
    gaia.mkdir()
    assert changes(before, list_home(gaia)) == ["created ."]


def test_a_listing_cut_short_still_reports_a_changed_file(tmp_path):
    gaia = _seed(tmp_path)
    for index in range(20):
        (gaia / f"extra-{index:02}.txt").write_text("x", encoding="utf-8")
    before = list_home(gaia, max_entries=5)
    assert not before.complete
    assert len(before.entries) == 5

    # Sorts first, so it pushes another entry out of the cut listing.
    (gaia / "a-new-file.txt").write_text("x", encoding="utf-8")
    (gaia / "config.json").write_text('{"changed": true}', encoding="utf-8")

    found = changes(before, list_home(gaia, max_entries=5))
    assert "changed config.json" in found
    assert not [line for line in found if line.startswith(("created", "removed"))]


def test_a_few_thousand_files_are_listed_whole(tmp_path):
    gaia = tmp_path / ".gaia"
    for folder in range(30):
        (gaia / f"d{folder}").mkdir(parents=True)
        for index in range(100):
            (gaia / f"d{folder}" / f"f{index}").write_bytes(b"")

    listing = list_home(gaia)

    assert listing.complete
    assert len(listing.entries) == 3031
