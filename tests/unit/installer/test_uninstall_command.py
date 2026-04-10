# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Tests for ``gaia uninstall`` (gaia.installer.uninstall_command).

The tests use ``pyfakefs`` to construct a fake filesystem so we can verify
the removal behavior without touching real user data, and work the same on
POSIX-style and Windows-style paths.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import List

import pytest

from gaia.installer import uninstall_command as uc

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ns(**kwargs) -> argparse.Namespace:
    """Build an argparse.Namespace with the standard uninstall flag defaults."""
    defaults = dict(
        venv=False,
        purge=False,
        purge_lemonade=False,
        purge_models=False,
        purge_hf_cache=False,
        dry_run=False,
        yes=False,
    )
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


class _Capture:
    """Tiny printer stand-in that captures every line written to it."""

    def __init__(self) -> None:
        self.lines: List[str] = []

    def __call__(self, msg: str = "") -> None:
        self.lines.append(msg)

    @property
    def text(self) -> str:
        return "\n".join(self.lines)


def _seed_gaia_tree(home: Path) -> None:
    """Create a fake ``~/.gaia`` tree covering every Tier-3 path."""
    gaia = home / ".gaia"
    gaia.mkdir(parents=True, exist_ok=True)

    venv = gaia / "venv"
    (venv / "bin").mkdir(parents=True, exist_ok=True)
    (venv / "bin" / "python").write_text("#!/bin/sh\n")
    (venv / "pyvenv.cfg").write_text("home = /fake\n")

    chat = gaia / "chat"
    chat.mkdir(parents=True, exist_ok=True)
    (chat / "history.db").write_text("fake-db")

    docs = gaia / "documents"
    docs.mkdir(parents=True, exist_ok=True)
    (docs / "note.txt").write_text("keep me? no")

    (gaia / "electron-config.json").write_text("{}")
    (gaia / "gaia.log").write_text("log line")
    (gaia / "electron-install-state.json").write_text('{"state":"done"}')
    (gaia / "electron-install.log").write_text("install log")

    # Neighbour file we should NEVER remove (makes sure we don't nuke the
    # whole ~/.gaia directory).
    (gaia / "mcp_servers.json").write_text("{}")


def _seed_models_cache(home: Path) -> Path:
    models = home / ".cache" / "lemonade" / "models"
    models.mkdir(parents=True, exist_ok=True)
    (models / "Qwen3-0.6B-GGUF.gguf").write_text("fake model bytes")
    # Sibling directory that must NOT be removed (we only target models/).
    other = home / ".cache" / "lemonade" / "logs"
    other.mkdir(parents=True, exist_ok=True)
    (other / "lemonade.log").write_text("logs")
    return models


@pytest.fixture
def fake_home(fs, monkeypatch):
    """Return a fake ``~`` on ``pyfakefs`` and patch ``Path.home`` to use it."""
    home = Path("/fake/home/user")
    fs.create_dir(home)
    monkeypatch.setattr(Path, "home", lambda: home)
    # Auto-yes path: default to interactive-tty FALSE so prompts never block.
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    return home


# ---------------------------------------------------------------------------
# Plan construction
# ---------------------------------------------------------------------------


class TestBuildPlan:
    def test_no_flags_yields_empty_plan(self, fake_home):
        plan = uc.build_plan(
            venv=False,
            purge=False,
            purge_lemonade=False,
            purge_models=False,
            home=fake_home,
        )
        assert plan.is_empty()

    def test_venv_only_lists_just_venv(self, fake_home):
        plan = uc.build_plan(
            venv=True,
            purge=False,
            purge_lemonade=False,
            purge_models=False,
            home=fake_home,
        )
        assert plan.unique_paths() == [fake_home / ".gaia" / "venv"]
        assert not plan.purge_lemonade
        assert plan.purge_models_path is None

    def test_purge_implies_venv_and_adds_everything(self, fake_home):
        plan = uc.build_plan(
            venv=True,  # both passed, --purge wins
            purge=True,
            purge_lemonade=False,
            purge_models=False,
            home=fake_home,
        )
        gaia = fake_home / ".gaia"
        assert plan.unique_paths() == [
            gaia / "venv",
            gaia / "chat",
            gaia / "documents",
            gaia / "electron-config.json",
            gaia / "gaia.log",
            gaia / "electron-install-state.json",
            gaia / "electron-install.log",
        ]

    def test_purge_models_populates_path(self, fake_home):
        plan = uc.build_plan(
            venv=False,
            purge=True,
            purge_lemonade=False,
            purge_models=True,
            home=fake_home,
        )
        assert plan.purge_models_path == (fake_home / ".cache" / "lemonade" / "models")


# ---------------------------------------------------------------------------
# Dry-run smoke tests: every flag combination, no filesystem changes
# ---------------------------------------------------------------------------


class TestDryRun:
    @pytest.mark.parametrize(
        "kwargs,expected_substrings",
        [
            (dict(venv=True, dry_run=True), [".gaia/venv"]),
            (
                dict(purge=True, dry_run=True),
                [
                    ".gaia/venv",
                    ".gaia/chat",
                    ".gaia/documents",
                    "electron-config.json",
                    "gaia.log",
                    "electron-install-state.json",
                    "electron-install.log",
                ],
            ),
            (
                dict(
                    purge=True,
                    purge_lemonade=True,
                    purge_models=True,
                    dry_run=True,
                ),
                [
                    ".gaia/venv",
                    ".cache/lemonade/models",
                    "Lemonade Server",
                ],
            ),
        ],
    )
    def test_dry_run_lists_expected_paths_and_changes_nothing(
        self, fake_home, kwargs, expected_substrings
    ):
        _seed_gaia_tree(fake_home)
        _seed_models_cache(fake_home)
        captured = _Capture()

        exit_code = uc.run(_ns(**kwargs), printer=captured)

        assert exit_code == uc.EXIT_OK, captured.text
        for needle in expected_substrings:
            assert (
                needle in captured.text
            ), f"missing {needle!r} in dry-run output:\n{captured.text}"
        # Filesystem must be unchanged.
        gaia = fake_home / ".gaia"
        assert (gaia / "venv" / "bin" / "python").exists()
        assert (gaia / "chat" / "history.db").exists()
        assert (gaia / "documents" / "note.txt").exists()
        assert (gaia / "electron-config.json").exists()
        assert (fake_home / ".cache" / "lemonade" / "models").exists()


# ---------------------------------------------------------------------------
# Real removal scenarios
# ---------------------------------------------------------------------------


class TestVenvRemoval:
    def test_removes_venv_only(self, fake_home):
        _seed_gaia_tree(fake_home)
        captured = _Capture()

        exit_code = uc.run(_ns(venv=True, yes=True), printer=captured)

        assert exit_code == uc.EXIT_OK, captured.text
        gaia = fake_home / ".gaia"
        assert not (gaia / "venv").exists(), "venv should be gone"
        # Everything else under ~/.gaia must be untouched.
        assert (gaia / "chat" / "history.db").exists()
        assert (gaia / "documents" / "note.txt").exists()
        assert (gaia / "electron-config.json").exists()
        assert (gaia / "gaia.log").exists()
        assert (gaia / "mcp_servers.json").exists()
        assert gaia.exists()  # ~/.gaia itself must remain


class TestPurgeRemoval:
    def test_removes_all_tier3_paths_but_keeps_gaia_root(self, fake_home):
        _seed_gaia_tree(fake_home)
        captured = _Capture()

        exit_code = uc.run(_ns(purge=True, yes=True), printer=captured)

        assert exit_code == uc.EXIT_OK, captured.text
        gaia = fake_home / ".gaia"

        for sub in (
            "venv",
            "chat",
            "documents",
            "electron-config.json",
            "gaia.log",
            "electron-install-state.json",
            "electron-install.log",
        ):
            assert not (gaia / sub).exists(), f"{sub} should be gone"

        # The ~/.gaia directory itself MUST remain (other tools live here).
        assert gaia.exists()
        # And our neighbour file stays put.
        assert (gaia / "mcp_servers.json").exists()

    def test_does_not_touch_lemonade_without_opt_in(self, fake_home, monkeypatch):
        """--purge alone must NOT run Lemonade removal."""
        _seed_gaia_tree(fake_home)
        _seed_models_cache(fake_home)

        called = {"n": 0}

        def _should_not_run(*_args, **_kwargs):
            called["n"] += 1
            return True

        monkeypatch.setattr(uc, "_remove_lemonade", _should_not_run)
        captured = _Capture()

        exit_code = uc.run(_ns(purge=True, yes=True), printer=captured)

        assert exit_code == uc.EXIT_OK, captured.text
        assert (
            called["n"] == 0
        ), "Lemonade removal must not run without --purge-lemonade"
        # Models cache stays (no --purge-models).
        assert (fake_home / ".cache" / "lemonade" / "models").exists()


class TestPurgeModels:
    def test_removes_models_subdirectory_only(self, fake_home):
        _seed_gaia_tree(fake_home)
        models = _seed_models_cache(fake_home)
        captured = _Capture()

        exit_code = uc.run(
            _ns(purge=True, purge_models=True, yes=True),
            printer=captured,
        )

        assert exit_code == uc.EXIT_OK, captured.text
        assert not models.exists(), "models cache should be removed"
        # Sibling lemonade/logs directory MUST be untouched.
        assert (fake_home / ".cache" / "lemonade" / "logs" / "lemonade.log").exists()
        # Parent lemonade/ dir stays (we only remove models/).
        assert (fake_home / ".cache" / "lemonade").exists()


# ---------------------------------------------------------------------------
# Validation errors
# ---------------------------------------------------------------------------


class TestValidationErrors:
    def test_purge_lemonade_without_purge_errors(self, fake_home):
        captured = _Capture()
        exit_code = uc.run(_ns(purge_lemonade=True, yes=True), printer=captured)
        assert exit_code == uc.EXIT_USAGE
        assert "--purge-lemonade requires --purge" in captured.text

    def test_purge_models_without_purge_errors(self, fake_home):
        captured = _Capture()
        exit_code = uc.run(_ns(purge_models=True, yes=True), printer=captured)
        assert exit_code == uc.EXIT_USAGE
        assert "--purge-models requires --purge" in captured.text

    def test_purge_hf_cache_without_purge_errors(self, fake_home):
        captured = _Capture()
        exit_code = uc.run(_ns(purge_hf_cache=True, yes=True), printer=captured)
        assert exit_code == uc.EXIT_USAGE
        assert "--purge-hf-cache requires --purge" in captured.text


# ---------------------------------------------------------------------------
# No-flags behavior (interactive and non-interactive)
# ---------------------------------------------------------------------------


class TestNoFlagsHelp:
    def test_interactive_no_flags_prints_help_and_exits_zero(
        self, fake_home, monkeypatch
    ):
        monkeypatch.setattr("sys.stdin.isatty", lambda: True)
        captured = _Capture()
        exit_code = uc.run(_ns(), printer=captured)
        assert exit_code == uc.EXIT_OK
        assert "gaia uninstall" in captured.text
        assert "--venv" in captured.text
        assert "--purge" in captured.text
        assert "--dry-run" in captured.text

    def test_non_interactive_no_flags_still_prints_help_and_exits_zero(
        self, fake_home, monkeypatch
    ):
        monkeypatch.setattr("sys.stdin.isatty", lambda: False)
        captured = _Capture()
        exit_code = uc.run(_ns(), printer=captured)
        assert exit_code == uc.EXIT_OK
        assert "gaia uninstall" in captured.text


# ---------------------------------------------------------------------------
# Permission errors and missing paths
# ---------------------------------------------------------------------------


class TestErrorHandling:
    def test_permission_error_reports_exit_code_2(self, fake_home, monkeypatch):
        _seed_gaia_tree(fake_home)

        def _boom(path, *args, **kwargs):
            raise PermissionError(f"mock denied: {path}")

        monkeypatch.setattr(uc.shutil, "rmtree", _boom)

        captured = _Capture()
        exit_code = uc.run(_ns(venv=True, yes=True), printer=captured)
        assert exit_code == uc.EXIT_FS_ERROR
        assert "permission denied" in captured.text
        # Because rmtree was mocked out, the venv dir still exists.
        assert (fake_home / ".gaia" / "venv").exists()

    def test_missing_path_is_a_noop(self, fake_home):
        # Do NOT seed ~/.gaia; nothing to remove.
        (fake_home / ".gaia").mkdir()
        captured = _Capture()
        exit_code = uc.run(_ns(purge=True, yes=True), printer=captured)
        assert exit_code == uc.EXIT_OK, captured.text
        assert "does not exist" in captured.text
        # ~/.gaia itself is still there.
        assert (fake_home / ".gaia").exists()

    def test_purge_models_when_cache_absent_is_noop(self, fake_home):
        _seed_gaia_tree(fake_home)
        captured = _Capture()
        exit_code = uc.run(
            _ns(purge=True, purge_models=True, yes=True),
            printer=captured,
        )
        assert exit_code == uc.EXIT_OK, captured.text


# ---------------------------------------------------------------------------
# Confirmation prompt
# ---------------------------------------------------------------------------


class TestConfirmationPrompt:
    def test_interactive_prompt_declined_aborts_with_exit_1(
        self, fake_home, monkeypatch
    ):
        _seed_gaia_tree(fake_home)
        monkeypatch.setattr("sys.stdin.isatty", lambda: True)

        captured = _Capture()
        answers = iter(["n"])

        def _input(_prompt: str) -> str:
            return next(answers)

        exit_code = uc.run(
            _ns(venv=True),
            printer=captured,
            input_fn=_input,
        )
        assert exit_code == uc.EXIT_ABORTED
        assert "Aborted" in captured.text
        # Nothing should have been removed.
        assert (fake_home / ".gaia" / "venv").exists()

    def test_interactive_prompt_accepted_removes(self, fake_home, monkeypatch):
        _seed_gaia_tree(fake_home)
        monkeypatch.setattr("sys.stdin.isatty", lambda: True)

        captured = _Capture()
        answers = iter(["y"])

        def _input(_prompt: str) -> str:
            return next(answers)

        exit_code = uc.run(
            _ns(venv=True),
            printer=captured,
            input_fn=_input,
        )
        assert exit_code == uc.EXIT_OK, captured.text
        assert not (fake_home / ".gaia" / "venv").exists()

    def test_non_tty_stdin_auto_skips_prompt(self, fake_home, monkeypatch):
        """Silent NSIS uninstall relies on this — no --yes needed."""
        _seed_gaia_tree(fake_home)
        monkeypatch.setattr("sys.stdin.isatty", lambda: False)

        captured = _Capture()

        def _input(_prompt: str) -> str:  # pragma: no cover - must not be called
            pytest.fail("input() must not be called when stdin is not a tty")

        exit_code = uc.run(
            _ns(venv=True),
            printer=captured,
            input_fn=_input,
        )
        assert exit_code == uc.EXIT_OK, captured.text
        assert not (fake_home / ".gaia" / "venv").exists()


# ---------------------------------------------------------------------------
# Cross-platform path handling
# ---------------------------------------------------------------------------


class TestCrossPlatform:
    """pyfakefs lets us pretend we're on a different OS for path style
    verification. We only need to prove the module doesn't hardcode ``/``.
    """

    def test_posix_style_paths(self, fs, monkeypatch):
        home = Path("/home/tester")
        fs.create_dir(home)
        monkeypatch.setattr(Path, "home", lambda: home)
        monkeypatch.setattr("sys.stdin.isatty", lambda: False)
        _seed_gaia_tree(home)

        captured = _Capture()
        exit_code = uc.run(_ns(purge=True, yes=True), printer=captured)
        assert exit_code == uc.EXIT_OK, captured.text
        assert not (home / ".gaia" / "venv").exists()
        assert (home / ".gaia").exists()

    def test_windows_localappdata_env_wins_for_models_path(self, monkeypatch):
        """On Windows, ``_lemonade_models_dir`` must use ``%LOCALAPPDATA%``.

        We can't fully emulate Windows ``Path`` on a POSIX host, but the
        branching logic lives in a single function — pin it down directly.
        """
        monkeypatch.setattr("sys.platform", "win32")
        monkeypatch.setenv("LOCALAPPDATA", "/fake/AppData/Local")

        p = uc._lemonade_models_dir(home=Path("/fake/home"))
        # Expect the LOCALAPPDATA path to win.
        assert str(p).endswith("AppData/Local/lemonade/models") or str(p).endswith(
            "AppData\\Local\\lemonade\\models"
        )

    def test_windows_without_localappdata_falls_back_to_home_cache(self, monkeypatch):
        monkeypatch.setattr("sys.platform", "win32")
        monkeypatch.delenv("LOCALAPPDATA", raising=False)

        p = uc._lemonade_models_dir(home=Path("/fake/home"))
        assert p == Path("/fake/home") / ".cache" / "lemonade" / "models"

    def test_posix_models_path(self, monkeypatch):
        monkeypatch.setattr("sys.platform", "linux")
        p = uc._lemonade_models_dir(home=Path("/home/tester"))
        assert p == Path("/home/tester/.cache/lemonade/models")


# ---------------------------------------------------------------------------
# Containment guard: _remove_path refuses anything outside allowed roots
# ---------------------------------------------------------------------------


class TestContainmentGuard:
    """Defense-in-depth: even if a plan construction bug produced a path
    outside ~/.gaia, _remove_path must refuse to touch it.
    """

    def test_refuses_delete_outside_allowed_roots(self, fake_home):
        # Forge a path OUTSIDE ~/.gaia and confirm _remove_path raises
        # rather than silently deleting it.
        outside = fake_home / "not-gaia" / "important.txt"
        outside.parent.mkdir(parents=True, exist_ok=True)
        outside.write_text("do not delete me")

        allowed = uc._safe_roots(home=fake_home)
        with pytest.raises(RuntimeError, match="outside allowed roots"):
            uc._remove_path(outside, allowed_roots=allowed)

        assert outside.exists(), "file outside allowed roots must be preserved"

    def test_refuses_delete_at_root_via_traversal(self, fake_home):
        # Classic traversal: path that resolves outside the allowed tree.
        sneaky = fake_home / ".gaia" / ".." / "elsewhere"
        (fake_home / "elsewhere").mkdir(parents=True, exist_ok=True)
        (fake_home / "elsewhere" / "data").write_text("keep")

        allowed = uc._safe_roots(home=fake_home)
        with pytest.raises(RuntimeError, match="outside allowed roots"):
            uc._remove_path(sneaky, allowed_roots=allowed)

        assert (fake_home / "elsewhere" / "data").exists()

    def test_allows_delete_inside_gaia_home(self, fake_home):
        _seed_gaia_tree(fake_home)
        target = fake_home / ".gaia" / "venv"
        allowed = uc._safe_roots(home=fake_home)

        ok = uc._remove_path(target, allowed_roots=allowed)
        assert ok
        assert not target.exists()

    def test_safe_roots_returns_expected_roots(self, fake_home, monkeypatch):
        monkeypatch.delenv("HF_HOME", raising=False)
        monkeypatch.delenv("GAIA_HOME", raising=False)
        roots = uc._safe_roots(home=fake_home)
        joined = " ".join(str(r) for r in roots)
        assert ".gaia" in joined
        assert "lemonade" in joined
        assert "huggingface" in joined


# ---------------------------------------------------------------------------
# Silent-purge safety: --purge on non-TTY must require an explicit --yes
# ---------------------------------------------------------------------------


class TestSilentPurgeRefusal:
    """Tier-2 auto-skips confirmation on non-TTY stdin so silent NSIS
    uninstall flows work. Tier-3 (--purge) must NOT auto-skip — cron or a
    misconfigured pipe could otherwise silently nuke ~/.gaia/chat.
    """

    def test_purge_without_yes_on_non_tty_refuses(self, fake_home, monkeypatch):
        _seed_gaia_tree(fake_home)
        monkeypatch.setattr("sys.stdin.isatty", lambda: False)

        captured = _Capture()
        exit_code = uc.run(_ns(purge=True, yes=False), printer=captured)

        assert exit_code == uc.EXIT_ABORTED, captured.text
        assert "Refusing to --purge" in captured.text
        # All tier-3 paths must still exist.
        gaia = fake_home / ".gaia"
        assert (gaia / "venv" / "bin" / "python").exists()
        assert (gaia / "chat" / "history.db").exists()
        assert (gaia / "documents" / "note.txt").exists()

    def test_purge_with_yes_on_non_tty_proceeds(self, fake_home, monkeypatch):
        _seed_gaia_tree(fake_home)
        monkeypatch.setattr("sys.stdin.isatty", lambda: False)

        captured = _Capture()
        exit_code = uc.run(_ns(purge=True, yes=True), printer=captured)

        assert exit_code == uc.EXIT_OK, captured.text
        assert not (fake_home / ".gaia" / "venv").exists()

    def test_dry_run_purge_without_yes_on_non_tty_is_allowed(
        self, fake_home, monkeypatch
    ):
        """Dry-run is read-only, so the --yes guardrail doesn't apply."""
        _seed_gaia_tree(fake_home)
        monkeypatch.setattr("sys.stdin.isatty", lambda: False)

        captured = _Capture()
        exit_code = uc.run(
            _ns(purge=True, yes=False, dry_run=True), printer=captured
        )
        assert exit_code == uc.EXIT_OK, captured.text
        # Nothing removed.
        assert (fake_home / ".gaia" / "venv").exists()

    def test_venv_tier2_still_auto_skips_on_non_tty(self, fake_home, monkeypatch):
        """Regression guard: the new --purge guardrail must not break the
        existing Tier-2 silent-uninstall path relied on by NSIS postrm.
        """
        _seed_gaia_tree(fake_home)
        monkeypatch.setattr("sys.stdin.isatty", lambda: False)

        captured = _Capture()
        exit_code = uc.run(_ns(venv=True, yes=False), printer=captured)

        assert exit_code == uc.EXIT_OK, captured.text
        assert not (fake_home / ".gaia" / "venv").exists()


# ---------------------------------------------------------------------------
# GAIA_HOME environment variable override
# ---------------------------------------------------------------------------


class TestGaiaHomeEnvVar:
    def test_env_var_overrides_home(self, fs, monkeypatch):
        alt = Path("/srv/gaia-alt")
        fs.create_dir(alt)
        monkeypatch.setenv("GAIA_HOME", str(alt))
        monkeypatch.setattr(Path, "home", lambda: Path("/fake/home/user"))

        resolved = uc._gaia_home()
        assert Path(resolved) == alt

    def test_env_var_unset_uses_home_dot_gaia(self, fs, monkeypatch):
        monkeypatch.delenv("GAIA_HOME", raising=False)
        home = Path("/fake/home/user")
        fs.create_dir(home)
        monkeypatch.setattr(Path, "home", lambda: home)

        assert uc._gaia_home() == home / ".gaia"


# ---------------------------------------------------------------------------
# Log handler cleanup before purge (Windows data-loss guard)
# ---------------------------------------------------------------------------


class TestLogHandlerCleanup:
    """On Windows, leaving gaia.log held open by a FileHandler causes
    shutil.rmtree to raise PermissionError mid-delete. ``_close_gaia_log_handlers``
    detaches and closes any FileHandler pointing inside ~/.gaia before
    the purge begins.
    """

    def test_closes_filehandler_inside_gaia_home(self, fake_home):
        import logging

        _seed_gaia_tree(fake_home)
        log_path = fake_home / ".gaia" / "gaia.log"

        handler = logging.FileHandler(str(log_path), mode="a", encoding="utf-8")
        root = logging.getLogger()
        root.addHandler(handler)

        try:
            uc._close_gaia_log_handlers(home=fake_home)
        finally:
            # Keep the logger clean regardless of assertion outcome.
            if handler in root.handlers:
                root.removeHandler(handler)

        assert handler not in logging.getLogger().handlers
        # Underlying stream must be closed.
        stream = getattr(handler, "stream", None)
        assert stream is None or stream.closed

    def test_leaves_external_filehandler_alone(self, fake_home):
        import logging

        # A handler pointing OUTSIDE ~/.gaia/ — must be preserved.
        external_log = fake_home / "other" / "app.log"
        external_log.parent.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(str(external_log), mode="a", encoding="utf-8")
        root = logging.getLogger()
        root.addHandler(handler)

        try:
            uc._close_gaia_log_handlers(home=fake_home)
            assert handler in logging.getLogger().handlers
        finally:
            root.removeHandler(handler)
            handler.close()


# ---------------------------------------------------------------------------
# Lemonade Python interpreter resolution
# ---------------------------------------------------------------------------


class TestLemonadePythonResolution:
    """When called from the GAIA installer bundle, ``sys.executable`` is
    the GAIA venv, NOT where Lemonade was installed — so pip-uninstalling
    against it is a no-op. ``_resolve_lemonade_python`` locates the real
    interpreter by inspecting the lemonade-server console script.
    """

    def test_resolves_direct_shebang_posix(self, fake_home, monkeypatch):
        lemonade = fake_home / "bin" / "lemonade-server"
        lemonade.parent.mkdir(parents=True, exist_ok=True)
        lemonade.write_bytes(b"#!/opt/venvs/lemon/bin/python\n# rest\n")

        monkeypatch.setattr("sys.platform", "linux")
        monkeypatch.setattr(uc.shutil, "which", lambda name: str(lemonade))

        assert uc._resolve_lemonade_python() == "/opt/venvs/lemon/bin/python"

    def test_resolves_env_shebang_posix(self, fake_home, monkeypatch):
        lemonade = fake_home / "bin" / "lemonade-server"
        lemonade.parent.mkdir(parents=True, exist_ok=True)
        lemonade.write_bytes(b"#!/usr/bin/env python3\n# rest\n")

        monkeypatch.setattr("sys.platform", "linux")
        monkeypatch.setattr(uc.shutil, "which", lambda name: str(lemonade))

        assert uc._resolve_lemonade_python() == "python3"

    def test_not_on_path_returns_none(self, monkeypatch):
        monkeypatch.setattr("sys.platform", "linux")
        monkeypatch.setattr(uc.shutil, "which", lambda name: None)
        assert uc._resolve_lemonade_python() is None

    def test_script_without_shebang_returns_none(self, fake_home, monkeypatch):
        lemonade = fake_home / "bin" / "lemonade-server"
        lemonade.parent.mkdir(parents=True, exist_ok=True)
        lemonade.write_bytes(b"# no shebang here\nprint('hi')\n")

        monkeypatch.setattr("sys.platform", "linux")
        monkeypatch.setattr(uc.shutil, "which", lambda name: str(lemonade))

        assert uc._resolve_lemonade_python() is None
