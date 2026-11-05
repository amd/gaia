# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Start (or stop) a fake Home Assistant and give the flagship its tools.

``install`` starts ``fake_ha.py`` on a free loopback port with the eval house
(``eval_house.json``) and registers it the way the connector does: an
``http`` entry at ``/api/mcp/assist`` with ``writes_need_person`` and
``private_http_only``, active for the flagship. It is registered under the
name ``home_assistant`` (the connector's tool prefix), not the catalog id,
with its throwaway token inline: the eval runs with the null keyring, and the
OS keyring is shared by every GAIA home, so a real Home Assistant's token is
never read, replaced or deleted. The connector's own setup path is covered
by ``tests/unit/connectors/test_home_assistant_connector.py``.

``remove`` deletes the entry and stops the server. Run ``install`` before the
``gaia_home`` category and ``remove`` right after, from the eval's venv:

    python tests/fixtures/home_assistant/stage_fake_ha.py install
    python tests/fixtures/home_assistant/stage_fake_ha.py remove
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[2]
NAME = "home_assistant"
FLAGSHIP_AGENT_ID = "installed:gaia"


def _state_dir() -> Path:
    from gaia.config import gaia_home

    path = gaia_home() / "eval-fake-ha"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _edit_servers(change) -> Path:
    from gaia.config import mcp_servers_path

    path = mcp_servers_path()
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    change(data.setdefault("mcpServers", {}))
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(data, indent=2) + "\n")
        os.replace(tmp, path)
    except BaseException:
        os.unlink(tmp)
        raise
    return path


def install() -> None:
    import gaia.connectors.catalog  # noqa: F401  # pylint: disable=unused-import
    from gaia.connectors.activations import activate_agent
    from gaia.connectors.registry import REGISTRY

    note = REGISTRY.get("mcp-home-assistant").mcp_tool_note

    state = _state_dir()
    remove(quiet=True)
    ready = state / "base_url"
    ready.unlink(missing_ok=True)
    token = secrets.token_urlsafe(24)
    env = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join(
            [str(REPO_ROOT), os.environ.get("PYTHONPATH", "")]
        ),
    }
    proc = subprocess.Popen(  # noqa: S603 - our own fixture
        [
            sys.executable,
            "-m",
            "tests.fixtures.home_assistant.fake_ha",
            "--token",
            token,
            "--house",
            str(HERE / "eval_house.json"),
            "--ready-file",
            str(ready),
        ],
        cwd=REPO_ROOT,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    (state / "pid").write_text(str(proc.pid), encoding="utf-8")
    deadline = time.time() + 20
    while not ready.exists() or not ready.read_text(encoding="utf-8"):
        if proc.poll() is not None or time.time() > deadline:
            raise SystemExit(
                "The fake Home Assistant did not start; run fake_ha.py by hand to see why."
            )
        time.sleep(0.2)
    base = ready.read_text(encoding="utf-8").strip()

    def add(servers):
        servers[NAME] = {
            "type": "http",
            "url": base + "/api/mcp/assist",
            "headers": {"Authorization": f"Bearer {token}"},
            "private_http_only": True,
            "writes_need_person": True,
            "tool_note": note,
        }

    path = _edit_servers(add)
    activate_agent(NAME, FLAGSHIP_AGENT_ID)
    print(f"fake Home Assistant at {base} -> {path} (active for {FLAGSHIP_AGENT_ID})")


def remove(quiet: bool = False) -> None:
    from gaia.connectors.activations import deactivate_agent

    state = _state_dir()
    _edit_servers(lambda servers: servers.pop(NAME, None))
    deactivate_agent(NAME, FLAGSHIP_AGENT_ID)
    pid_file = state / "pid"
    if pid_file.exists():
        try:
            os.kill(int(pid_file.read_text(encoding="utf-8")), signal.SIGTERM)
        except (OSError, ValueError):
            pass
        pid_file.unlink(missing_ok=True)
    if not quiet:
        print("fake Home Assistant stopped and unregistered")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("action", choices=["install", "remove"])
    action = parser.parse_args(argv).action
    install() if action == "install" else remove()
    return 0


if __name__ == "__main__":
    sys.exit(main())
