# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A Python run the agent starts never sees a secret a project ``.env`` set."""

import sys

from gaia_agent.chat_agent import _python_script_run_context
from gaia_agent.python_jobs import run_python_job

from gaia import env as gaia_env

DOTENV_SECRET = "s3cr3t-from-project-dotenv"


def test_python_tool_child_does_not_see_a_dotenv_secret(monkeypatch, tmp_path):
    """The environment run_python and execute_python_file give their subprocess."""
    monkeypatch.setenv("PROJECT_SECRET", DOTENV_SECRET)
    monkeypatch.setattr(
        gaia_env, "_dotenv_names", {gaia_env._env_name("PROJECT_SECRET")}
    )
    monkeypatch.delenv(gaia_env.DOTENV_KEYS_ENV_VAR, raising=False)
    script = tmp_path / "dump.py"
    script.write_text(
        "import os\nprint('PATH' in os.environ, os.environ.get('PROJECT_SECRET'))\n"
    )

    run_dir, env = _python_script_run_context(script, None)
    job = run_python_job(
        [sys.executable, str(script)],
        cwd=str(run_dir),
        env=env,
        timeout=60,
        should_stop=lambda: False,
    )

    assert job.run.stdout.strip() == "True None", job.run
