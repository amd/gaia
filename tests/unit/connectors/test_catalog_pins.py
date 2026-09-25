# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Every catalog MCP server must launch at an exact, pinned version (#4351).

An unpinned ``npx``/``uvx`` spec runs whatever the registry serves today, so an
upstream tool rename breaks users' connectors with no GAIA change.
"""

import re

import pytest

from gaia.connectors.catalog.mcp_servers import _ALL_SPECS

_EXACT = r"\d+\.\d+\.\d+"
_NPM_PINNED = re.compile(rf"^(@[a-z0-9._-]+/)?[a-z0-9._-]+@{_EXACT}$")
_PYPI_PINNED = re.compile(rf"^[A-Za-z0-9._-]+=={_EXACT}$")


def package_spec_problem(command, args):
    """Return why ``command args`` doesn't pin its package, or ``None``."""
    positional = [a for a in args if not a.startswith("-")]
    if not positional:
        return f"no package spec in {command} {list(args)}"
    if command in ("npx", "pnpx"):
        spec = positional[0]
        if not _NPM_PINNED.match(spec):
            return f"npm spec {spec!r} is not pinned to an exact x.y.z version"
        return None
    if command in ("uvx", "pipx"):
        if command == "pipx":
            if positional[0] != "run":
                return f"pipx must be invoked as `pipx run <pkg>==x.y.z`, got {args}"
            positional = positional[1:]
        if "--from" in args:
            spec = args[args.index("--from") + 1]
        else:
            spec = positional[0] if positional else ""
        if not _PYPI_PINNED.match(spec):
            return f"PyPI spec {spec!r} is not pinned with ==x.y.z"
        return None
    return (
        f"unknown launcher {command!r}: teach test_catalog_pins.py how to "
        "check its package pin before adding it to the catalog"
    )


@pytest.mark.parametrize(
    "command,args",
    [
        ("npx", ("-y", "tavily-mcp@latest")),
        ("npx", ("-y", "@modelcontextprotocol/server-github")),
        ("npx", ("-y", "@scope/pkg@^1.2.3")),
        ("npx", ("-y", "pkg@1.2")),
        ("uvx", ("mcp-server-git",)),
        ("uvx", ("mcp-server-git>=2026.8.18",)),
        ("pipx", ("run", "mcp-server-git")),
        ("docker", ("run", "ghcr.io/github/github-mcp-server")),
    ],
)
def test_unpinned_specs_are_rejected(command, args):
    assert package_spec_problem(command, args) is not None


@pytest.mark.parametrize(
    "command,args",
    [
        ("npx", ("-y", "tavily-mcp@0.2.22")),
        ("npx", ("-y", "@modelcontextprotocol/server-memory@2026.8.31")),
        ("uvx", ("mcp-server-git==2026.8.18",)),
        ("uvx", ("--from", "mcp-server-git==2026.8.18", "mcp-server-git")),
        ("pipx", ("run", "mcp-server-git==2026.8.18")),
    ],
)
def test_pinned_specs_are_accepted(command, args):
    assert package_spec_problem(command, args) is None


def test_catalog_is_not_empty():
    assert [s for s in _ALL_SPECS if s.type == "mcp_server"]


@pytest.mark.parametrize("spec", _ALL_SPECS, ids=lambda s: s.id)
def test_catalog_mcp_server_is_pinned(spec):
    if spec.type != "mcp_server":
        pytest.skip("not an MCP server")
    problem = package_spec_problem(spec.mcp_command, spec.mcp_args)
    assert problem is None, (
        f"{spec.id}: {problem}. Pin it and follow the refresh steps in "
        "docs/reference/dependency-management.mdx#mcp-connector-catalog-pins."
    )
