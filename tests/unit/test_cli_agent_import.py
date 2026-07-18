# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Unit tests for the `gaia agent import` trust gate (issue #1996).

``handle_agent_import`` (src/gaia/cli.py) is the confirmation boundary before
installing third-party Python code from a bundle into ``~/.gaia/agents/``. It
must:

  * refuse non-interactively (stdin not a TTY) unless ``--yes`` is given
  * ask a ``[y/N]`` prompt interactively, proceeding only on 'y'/'yes'
  * let ``--yes`` skip the prompt entirely
  * refuse a bundle whose ``bundle.json`` exceeds the 1 MB manifest cap,
    before ever touching the installer

Every case mocks ``gaia.installer.export_import.import_agent_bundle`` and
asserts whether it was invoked — the underlying import logic is covered
elsewhere; this file only covers the trust-gate wiring in the CLI handler.
"""

import json
import sys
import zipfile
from argparse import Namespace

import pytest

from gaia import cli

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _ImportResult:
    """Stand-in for ``gaia.installer.export_import.ImportResult``."""

    def __init__(self, imported=None, overwritten=None, errors=None):
        self.imported = imported or []
        self.overwritten = overwritten or []
        self.errors = errors or []


def _make_bundle(tmp_path, *, agent_ids=None, oversized=False, name="bundle.zip"):
    """Build a minimal .zip bundle with a bundle.json manifest entry."""
    path = tmp_path / name
    with zipfile.ZipFile(path, "w") as zf:
        if oversized:
            # Content doesn't need to be valid JSON — the size check runs
            # before the bundle.json bytes are ever parsed.
            zf.writestr("bundle.json", "x" * (1024 * 1024 + 1))
        else:
            manifest = {"agent_ids": agent_ids if agent_ids is not None else ["demo"]}
            zf.writestr("bundle.json", json.dumps(manifest))
    return path


def _import_args(path, yes=False):
    return Namespace(path=str(path), yes=yes)


def _patch_import_agent_bundle(monkeypatch, calls, result=None):
    def _fake(bundle_path):
        calls.append(bundle_path)
        return result if result is not None else _ImportResult(imported=["demo"])

    monkeypatch.setattr("gaia.installer.export_import.import_agent_bundle", _fake)


# ---------------------------------------------------------------------------
# Non-interactive refusal without --yes
# ---------------------------------------------------------------------------


def test_non_interactive_without_yes_refuses(tmp_path, monkeypatch):
    bundle = _make_bundle(tmp_path)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    calls = []
    _patch_import_agent_bundle(monkeypatch, calls)

    with pytest.raises(SystemExit) as excinfo:
        cli.handle_agent_import(_import_args(bundle, yes=False))

    assert excinfo.value.code == 1
    assert calls == []  # the installer must never run


def test_non_interactive_without_yes_never_calls_input(tmp_path, monkeypatch):
    """A non-TTY refusal must not even attempt to read a prompt answer."""
    bundle = _make_bundle(tmp_path)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    monkeypatch.setattr(
        "builtins.input",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("input() was called")),
    )
    calls = []
    _patch_import_agent_bundle(monkeypatch, calls)

    with pytest.raises(SystemExit):
        cli.handle_agent_import(_import_args(bundle, yes=False))

    assert calls == []


# ---------------------------------------------------------------------------
# Interactive [y/N] prompt
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("answer", ["y", "Y", "yes", "YES"])
def test_interactive_yes_answer_proceeds(tmp_path, monkeypatch, answer):
    bundle = _make_bundle(tmp_path)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda *a, **k: answer)
    calls = []
    _patch_import_agent_bundle(monkeypatch, calls)

    cli.handle_agent_import(_import_args(bundle, yes=False))

    assert calls == [bundle]


@pytest.mark.parametrize("answer", ["n", "N", "no", "", "  ", "maybe"])
def test_interactive_non_yes_answer_refuses(tmp_path, monkeypatch, answer):
    bundle = _make_bundle(tmp_path)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda *a, **k: answer)
    calls = []
    _patch_import_agent_bundle(monkeypatch, calls)

    with pytest.raises(SystemExit) as excinfo:
        cli.handle_agent_import(_import_args(bundle, yes=False))

    assert excinfo.value.code == 0  # a declined prompt is not an error exit
    assert calls == []


# ---------------------------------------------------------------------------
# --yes bypasses the prompt entirely
# ---------------------------------------------------------------------------


def test_yes_flag_bypasses_prompt(tmp_path, monkeypatch):
    bundle = _make_bundle(tmp_path)

    def _boom(*a, **k):
        raise AssertionError("input() must not be called when --yes is given")

    monkeypatch.setattr("builtins.input", _boom)
    # isatty is irrelevant once --yes is set; don't even patch it.
    calls = []
    _patch_import_agent_bundle(monkeypatch, calls)

    cli.handle_agent_import(_import_args(bundle, yes=True))

    assert calls == [bundle]


def test_yes_flag_works_non_interactively_too(tmp_path, monkeypatch):
    bundle = _make_bundle(tmp_path)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    calls = []
    _patch_import_agent_bundle(monkeypatch, calls)

    cli.handle_agent_import(_import_args(bundle, yes=True))

    assert calls == [bundle]


# ---------------------------------------------------------------------------
# 1 MB bundle.json cap
# ---------------------------------------------------------------------------


def test_oversized_bundle_json_refused_before_install(tmp_path, monkeypatch):
    bundle = _make_bundle(tmp_path, oversized=True)
    calls = []
    _patch_import_agent_bundle(monkeypatch, calls)

    # --yes so the trust-gate prompt can't be the thing that refuses.
    with pytest.raises(SystemExit) as excinfo:
        cli.handle_agent_import(_import_args(bundle, yes=True))

    assert excinfo.value.code == 1
    assert calls == []  # size cap must reject before the installer ever runs


def test_bundle_just_under_cap_is_accepted(tmp_path, monkeypatch):
    """Sanity check the cap is 1 MB, not an off-by-one that rejects valid bundles."""
    path = tmp_path / "bundle.zip"
    manifest = json.dumps({"agent_ids": ["demo"], "pad": "x" * (900 * 1024)})
    assert len(manifest.encode("utf-8")) < 1024 * 1024
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("bundle.json", manifest)

    calls = []
    _patch_import_agent_bundle(monkeypatch, calls)

    cli.handle_agent_import(_import_args(path, yes=True))

    assert calls == [path]


# ---------------------------------------------------------------------------
# Result handling — errors from the installer still exit non-zero
# ---------------------------------------------------------------------------


def test_installer_errors_exit_1(tmp_path, monkeypatch):
    bundle = _make_bundle(tmp_path)
    calls = []
    _patch_import_agent_bundle(
        monkeypatch, calls, result=_ImportResult(errors=["demo: boom"])
    )

    with pytest.raises(SystemExit) as excinfo:
        cli.handle_agent_import(_import_args(bundle, yes=True))

    assert excinfo.value.code == 1
    assert calls == [bundle]  # the installer did run; it just reported an error
