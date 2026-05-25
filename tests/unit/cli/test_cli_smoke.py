"""Smoke-test every gaia subcommand and standalone console script with --help."""

from __future__ import annotations

import argparse
import importlib.metadata as md
import shutil
import subprocess
import sys
from typing import Iterator

import pytest

# ---- Discovery helpers ----


def _iter_subcommands(
    parser: argparse.ArgumentParser,
    prefix: tuple[str, ...] = (),
    seen: set[int] | None = None,
) -> Iterator[tuple[str, ...]]:
    """Recursively yield every subcommand path; deduplicates parser aliases by id()."""
    if seen is None:
        seen = set()

    if prefix:
        yield prefix

    subparsers_action = next(
        (a for a in parser._actions if isinstance(a, argparse._SubParsersAction)),
        None,
    )
    if subparsers_action is None:
        return

    for name, child in subparsers_action.choices.items():
        if id(child) in seen:
            continue
        seen.add(id(child))
        yield from _iter_subcommands(child, prefix + (name,), seen)


def _discover_subcommands() -> list[tuple[str, ...]]:
    """Return the full sorted list of subcommand paths discovered from build_parser()."""
    from gaia.cli import build_parser  # deferred — runs cli.py module side-effects

    return sorted(_iter_subcommands(build_parser()))


def _discover_console_scripts() -> list[tuple[str, str]]:
    """Return (name, module) for every `gaia-*` console_scripts entry.

    Excludes `gaia` (covered by in-process subcommand walk) and `gaia-cli`
    (a documented alias of `gaia` with no new signal).
    """
    eps = md.entry_points(group="console_scripts")
    out = []
    for ep in eps:
        if not ep.name.startswith("gaia-"):
            continue
        if ep.name == "gaia-cli":
            continue
        module = ep.value.split(":")[0]
        out.append((ep.name, module))
    return sorted(out)


def _all_gaia_binaries() -> list[str]:
    """Every gaia* binary name from console_scripts (including `gaia` itself)."""
    eps = md.entry_points(group="console_scripts")
    return sorted(
        ep.name for ep in eps if ep.name == "gaia" or ep.name.startswith("gaia-")
    )


# ---- Module-level discovery (collection-phase) ----

_SUBCOMMANDS = _discover_subcommands()
_CONSOLE_SCRIPTS = _discover_console_scripts()
_GAIA_BINARIES = _all_gaia_binaries()


# ---- Shared fixture ----


@pytest.fixture(scope="module")
def root_parser():
    """Module-scoped argparse parser — built once, shared across all subcommand tests."""
    from gaia.cli import build_parser

    return build_parser()


# ---- Subcommand smoke tests (in-process) ----


@pytest.mark.parametrize(
    "cmd",
    _SUBCOMMANDS,
    ids=["-".join(c) for c in _SUBCOMMANDS],
)
def test_subcommand_help(
    cmd: tuple[str, ...], capsys: pytest.CaptureFixture, root_parser
) -> None:
    """`gaia <cmd> --help` exits 0 and prints a usage line."""
    with pytest.raises(SystemExit) as excinfo:
        root_parser.parse_args([*cmd, "--help"])

    assert (
        excinfo.value.code == 0
    ), f"gaia {' '.join(cmd)} --help exited {excinfo.value.code}"
    captured = capsys.readouterr()
    assert (
        "usage:" in captured.out.lower()
    ), f"gaia {' '.join(cmd)} --help stdout missing 'usage:':\n{captured.out[:2000]}"


def test_root_help(capsys: pytest.CaptureFixture, root_parser) -> None:
    """`gaia --help` exits 0 and prints a usage line."""
    with pytest.raises(SystemExit) as excinfo:
        root_parser.parse_args(["--help"])
    assert excinfo.value.code == 0
    captured = capsys.readouterr()
    assert "usage:" in captured.out.lower()


def test_discovery_minimum_count() -> None:
    """Sentinel: fail loudly if subcommand discovery returns fewer than expected.

    With intermediate-group coverage, expect ~60 paths: ~52 leaves plus
    ~8 intermediate groups (telegram, eval, mcp, cache, memory, agent,
    connectors, connectors-grants). Threshold set at 55 — five paths may
    disappear before the alert fires. Raise this if intentional growth
    pushes the count up.
    """
    assert len(_SUBCOMMANDS) >= 55, (
        f"Subcommand discovery returned only {len(_SUBCOMMANDS)} paths; expected >= 55. "
        f"Did a recent refactor break parser-tree introspection?\n"
        f"Discovered: {['-'.join(c) for c in _SUBCOMMANDS]}"
    )


# ---- Console-script smoke tests (subprocess) ----


@pytest.mark.parametrize(
    "name_and_module",
    _CONSOLE_SCRIPTS,
    ids=[name for name, _ in _CONSOLE_SCRIPTS],
)
def test_console_script_help(name_and_module: tuple[str, str]) -> None:
    """`python -m <module> --help` exits 0 and prints a usage line.

    Uses `[sys.executable, "-m", <module>]` rather than the bare binary
    name to match the project subprocess convention (tests/test_eval.py)
    and avoid PATH dependency. The PATH/shim wiring is verified separately
    by `test_gaia_binary_on_path`.
    """
    name, module = name_and_module
    result = subprocess.run(
        [sys.executable, "-m", module, "--help"],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, (
        f"python -m {module} --help (entry {name!r}) exited {result.returncode}\n"
        f"stdout: {result.stdout[:2000]}\n"
        f"stderr: {result.stderr[:2000]}"
    )
    assert (
        "usage:" in result.stdout.lower()
    ), f"python -m {module} --help stdout missing 'usage:':\n{result.stdout[:2000]}"


def test_console_script_minimum_count() -> None:
    """Sentinel: fail loudly if console_scripts discovery returned nothing.

    `_discover_console_scripts()` returns [] when the package is not
    installed (no metadata to read). Without this assert, the parametrize
    above would silently generate 0 tests — a silent fallback.
    """
    assert len(_CONSOLE_SCRIPTS) >= 3, (
        f"Console-script discovery returned only {len(_CONSOLE_SCRIPTS)} entries; "
        f"expected >= 3 (gaia-mcp, gaia-emr, gaia-code). "
        f"Is the package installed? Run `pip install -e .`\n"
        f"Found: {_CONSOLE_SCRIPTS}"
    )


# ---- Binary-on-PATH check ----


@pytest.mark.parametrize("binary", _GAIA_BINARIES)
@pytest.mark.skipif(
    not _GAIA_BINARIES,
    reason="package not installed; run `pip install -e .` to enable binary PATH checks",
)
def test_gaia_binary_on_path(binary: str) -> None:
    """Every `gaia*` console_scripts shim is reachable on PATH after `pip install`."""
    assert shutil.which(binary) is not None, (
        f"Binary {binary!r} not found on PATH. The console_scripts shim "
        f"from setup.py may not have been installed. Run `pip install -e .`"
    )


def test_gaia_binary_minimum_count() -> None:
    """Sentinel: confirm `_all_gaia_binaries()` found at least `gaia` itself."""
    assert "gaia" in _GAIA_BINARIES, (
        f"`gaia` binary missing from console_scripts metadata. "
        f"Found: {_GAIA_BINARIES}. Is the package installed?"
    )
