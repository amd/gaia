# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Every catalog MCP server really starts and lists tools at its pinned version.

A pin test proves the spec names an exact version; only launching it proves
that version exists, installs, and speaks MCP. Placeholder credentials are
enough for most servers, which read their keys at startup but only use them per
call. A server that checks its key before speaking MCP is instead held to
reaching that check and rejecting the placeholder.
"""

import json
import os
import queue
import shutil
import subprocess
import threading

import pytest

from gaia.connectors.catalog.mcp_servers import _ALL_SPECS

pytestmark = [pytest.mark.integration, pytest.mark.network]

_LAUNCH_TIMEOUT_S = 180

# Servers that sign in before answering MCP, mapped to the stderr text of their
# placeholder-credential rejection.
_CHECKS_CREDENTIALS_AT_START = {
    "mcp-slack": "invalid_auth",
}


def _rpc(proc, lines, msg_id, method, params=None):
    msg = {"jsonrpc": "2.0", "id": msg_id, "method": method, "params": params or {}}
    proc.stdin.write(json.dumps(msg) + "\n")
    proc.stdin.flush()
    while True:
        line = lines.get(timeout=_LAUNCH_TIMEOUT_S)
        if line is None:
            raise AssertionError(f"server exited before answering {method}")
        try:
            reply = json.loads(line)
        except json.JSONDecodeError:
            continue  # a banner on stdout
        if reply.get("id") == msg_id:
            return reply


def _kill_tree(proc):
    # On Windows npx is a .cmd shim, so killing it alone leaves node running.
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
            capture_output=True,
            check=False,
        )
    else:
        proc.kill()
    proc.wait(timeout=10)


@pytest.mark.parametrize("spec", _ALL_SPECS, ids=lambda s: s.id)
def test_catalog_server_launches_and_lists_tools(spec):
    if os.environ.get("GAIA_SKIP_NETWORK_TESTS") == "1":
        pytest.skip("GAIA_SKIP_NETWORK_TESTS=1")
    launcher = shutil.which(spec.mcp_command)
    if launcher is None:
        pytest.skip(f"{spec.mcp_command} is not on PATH")

    env = {**os.environ, **{k: "placeholder-not-a-real-key" for k in spec.mcp_env_keys}}
    proc = subprocess.Popen(
        [launcher, *spec.mcp_args],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        env=env,
    )
    lines: queue.Queue = queue.Queue()
    stderr: list = []

    def pump():
        for line in proc.stdout:
            lines.put(line)
        lines.put(None)

    def drain_stderr():
        for line in proc.stderr:
            stderr.append(line)

    threading.Thread(target=pump, daemon=True).start()
    err_reader = threading.Thread(target=drain_stderr, daemon=True)
    err_reader.start()
    try:
        rejection = _CHECKS_CREDENTIALS_AT_START.get(spec.id)
        if rejection is not None:
            proc.wait(timeout=_LAUNCH_TIMEOUT_S)
            err_reader.join(timeout=10)
            output = "".join(stderr)
            assert (
                rejection in output
            ), f"{spec.id} never reached its credential check; stderr:\n{output[-2000:]}"
            return
        init = _rpc(
            proc,
            lines,
            1,
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "gaia-catalog-test", "version": "0"},
            },
        )
        assert "result" in init, f"{spec.id} initialize failed: {init}"
        proc.stdin.write(
            json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n"
        )
        proc.stdin.flush()
        tools = _rpc(proc, lines, 2, "tools/list")
        names = [t["name"] for t in tools.get("result", {}).get("tools", [])]
        assert names, f"{spec.id} listed no tools: {tools}"
    finally:
        _kill_tree(proc)
