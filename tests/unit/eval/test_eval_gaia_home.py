# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""util/eval_gaia_home.py: each eval category starts on a fresh, empty GAIA home.

The check runs as a subprocess with the environment the backend would get,
because ``gaia.config`` resolves some roots at import time and this test
process has already imported it with the developer's environment.
"""

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "util" / "eval_gaia_home.py"
_spec = importlib.util.spec_from_file_location("eval_gaia_home", SCRIPT)
eval_gaia_home = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(eval_gaia_home)

_STRIP = (
    "GAIA_HOME",
    "GAIA_CONFIG_DIR",
    "GAIA_MEMORY_DB",
    "GAIA_DAEMON_HOME",
    "GAIA_CONFIG_FILE",
)


def _env(home: Path, **overrides) -> dict:
    env = {k: v for k, v in os.environ.items() if k not in _STRIP}
    env["PYTHONPATH"] = str(REPO_ROOT / "src")
    env.update(eval_gaia_home.env_for(home))
    for key, value in overrides.items():
        if value is None:
            env.pop(key, None)
        else:
            env[key] = value
    return env


def _check(home: Path, env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "check", "--home", str(home)],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def _seed_memory(env: dict, fact: str) -> None:
    code = (
        "from gaia.agents.base.memory_store import MemoryStore\n"
        "s = MemoryStore()\n"
        f"s.store(category='fact', content={fact!r})\n"
        "s.close()\n"
    )
    subprocess.run([sys.executable, "-c", code], env=env, check=True)


def test_every_state_root_moves_under_the_home(tmp_path):
    env = eval_gaia_home.env_for(tmp_path)

    assert env == {
        "GAIA_HOME": str(tmp_path),
        "GAIA_CONFIG_DIR": str(tmp_path),
        "GAIA_MEMORY_DB": str(tmp_path / "memory.db"),
        "GAIA_DAEMON_HOME": str(tmp_path / "host"),
    }


def test_env_prints_lines_the_workflow_can_export(tmp_path):
    out = subprocess.run(
        [sys.executable, str(SCRIPT), "env", "--home", str(tmp_path)],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()

    assert dict(line.split("=", 1) for line in out) == eval_gaia_home.env_for(tmp_path)


def test_a_fresh_home_passes(tmp_path):
    result = _check(tmp_path, _env(tmp_path))

    assert result.returncode == 0, result.stderr
    assert "isolated" in result.stdout


def test_a_schema_only_memory_database_passes(tmp_path):
    # The backend creates the file the moment it opens the store; empty is fine.
    env = _env(tmp_path)
    code = (
        "from gaia.agents.base.memory_store import MemoryStore; MemoryStore().close()"
    )
    subprocess.run([sys.executable, "-c", code], env=env, check=True)
    assert (tmp_path / "memory.db").exists()

    assert _check(tmp_path, env).returncode == 0


def test_a_remembered_fact_fails_the_check(tmp_path):
    # The rc16 leak: a fact stored by an earlier run, waiting for the next one.
    env = _env(tmp_path)
    _seed_memory(env, "Brian Patel is Acme's CFO")

    result = _check(tmp_path, env)

    assert result.returncode == 1
    assert "not empty" in result.stderr and "knowledge" in result.stderr


@pytest.mark.parametrize(
    "missing, root",
    [
        # Without GAIA_CONFIG_DIR the skills root stays in the real ~/.gaia.
        ("GAIA_CONFIG_DIR", "skills root"),
        # GAIA_CONFIG_DIR happens to cover for it today; the check still
        # insists, so a resolver that reads only GAIA_HOME cannot escape.
        ("GAIA_HOME", "GAIA_HOME is None"),
        ("GAIA_MEMORY_DB", "GAIA_MEMORY_DB"),
        ("GAIA_DAEMON_HOME", "GAIA_DAEMON_HOME"),
    ],
)
def test_a_half_isolated_environment_fails(tmp_path, missing, root):
    result = _check(tmp_path, _env(tmp_path, **{missing: None}))

    assert result.returncode == 1
    assert root in result.stderr


def test_a_memory_db_pointed_back_at_the_real_home_fails(tmp_path):
    elsewhere = tmp_path / "real-home" / ".gaia" / "memory.db"
    home = tmp_path / "category"
    home.mkdir()

    result = _check(home, _env(home, GAIA_MEMORY_DB=str(elsewhere)))

    assert result.returncode == 1
    assert "memory database resolves to" in result.stderr


def test_a_seeded_db_outside_the_home_is_still_read(tmp_path):
    # GAIA_MEMORY_DB is what the backend opens, so that file is the one checked,
    # even when it also fails the location check.
    elsewhere = tmp_path / "other" / "memory.db"
    home = tmp_path / "category"
    home.mkdir()
    env = _env(home, GAIA_MEMORY_DB=str(elsewhere))
    _seed_memory(env, "left over")

    result = _check(home, env)

    assert result.returncode == 1
    assert "not empty" in result.stderr
