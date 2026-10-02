# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Register (or remove) the ``gaia_mcp`` stub servers for the flagship agent.

``install`` merges two entries into ``~/.gaia/mcp_servers.json`` and activates
them for the flagship in ``~/.gaia/connectors/activations.json`` — the two
things ChatAgent reads before it surfaces an MCP server's tools:

- ``acme_orders``   → ``mcp_acme_orders_lookup_order`` (answers)
- ``acme_shipping`` → ``mcp_acme_shipping_track_shipment`` (always fails)

``remove`` deletes exactly those entries and leaves everything else in both
files untouched, so it is safe on a developer machine with real servers.

Run ``install`` before the ``gaia_mcp`` category and ``remove`` right after:
each session builds a fresh agent that reads the config, so the stub joins
every session in between. The command uses this interpreter, so run it from
the eval's venv.

    python tests/fixtures/gaia/mcp_stub/stage_mcp_stub.py install
    python tests/fixtures/gaia/mcp_stub/stage_mcp_stub.py remove
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

from gaia.connectors.activations import activate_agent, deactivate_agent

STUB = Path(__file__).resolve().parent / "acme_mcp.py"

#: Registry id the flagship carries in the Agent UI backend (registry.py).
FLAGSHIP_AGENT_ID = "installed:gaia"

SERVERS = {"acme_orders": "orders", "acme_shipping": "shipping"}


def _config_path() -> Path:
    return Path.home() / ".gaia" / "mcp_servers.json"


def _read_config(path: Path) -> dict:
    if not path.exists():
        return {"mcpServers": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(
            f"{path} is not valid JSON ({exc}); fix or move it, then re-run."
        ) from exc
    if not isinstance(data, dict):
        raise SystemExit(f"{path} must hold a JSON object; fix or move it.")
    return data


def _servers_key(data: dict) -> str:
    # Mirror MCPConfig: `mcpServers` wins, `servers` is the legacy spelling.
    return "servers" if "servers" in data and "mcpServers" not in data else "mcpServers"


def _write_config(path: Path, data: dict) -> None:
    # Atomic: a crash mid-write must not corrupt a developer's real config.
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(data, indent=2) + "\n")
        os.replace(tmp, path)
    except BaseException:
        os.unlink(tmp)
        raise


def install() -> None:
    path = _config_path()
    data = _read_config(path)
    servers = data.setdefault(_servers_key(data), {})
    for name, persona in SERVERS.items():
        servers[name] = {
            "command": sys.executable,
            "args": [str(STUB), "--server", persona],
        }
    _write_config(path, data)
    for name in SERVERS:
        activate_agent(name, FLAGSHIP_AGENT_ID)
    print(f"staged {sorted(SERVERS)} -> {path} (active for {FLAGSHIP_AGENT_ID})")


def remove() -> None:
    path = _config_path()
    if path.exists():
        data = _read_config(path)
        servers = data.get(_servers_key(data), {})
        for name in SERVERS:
            servers.pop(name, None)
        _write_config(path, data)
    for name in SERVERS:
        deactivate_agent(name, FLAGSHIP_AGENT_ID)
    print(f"removed {sorted(SERVERS)} from {path}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("action", choices=["install", "remove"])
    action = parser.parse_args(argv).action
    install() if action == "install" else remove()
    return 0


if __name__ == "__main__":
    sys.exit(main())
