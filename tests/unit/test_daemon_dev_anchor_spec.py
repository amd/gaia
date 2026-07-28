# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Failing (red-phase) spec for issue #2588: the caller-side mode / dev-src-dir
resolution primitives that fix the daemon dev-mode checkout-anchoring bug.

None of the following exist yet:
  - gaia.daemon.sidecars.spec.agent_dev_src_dir
  - gaia.daemon.sidecars.spec.resolve_caller_mode
  - gaia.daemon.sidecars.spec.resolve_caller_dev_src_dir
  - gaia.daemon.sidecars.errors.DevSrcDirResolutionError

Every test here runs entirely in-process: no daemon, no subprocess sidecars.
git invocations inside resolve_caller_dev_src_dir are monkeypatched at the
``subprocess.run`` seam (``gaia.daemon.sidecars.spec.subprocess.run``) so this
suite never depends on -- or is broken by -- the ambient checkout's real git
state.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

# ===========================================================================
# agent_dev_src_dir()
# ===========================================================================


def test_agent_dev_src_dir_joins_repo_root_hub_agents_id_python():
    from gaia.daemon.sidecars.spec import agent_dev_src_dir

    root = Path("/some/repo/root")
    assert (
        agent_dev_src_dir(root, "email") == root / "hub" / "agents" / "email" / "python"
    )


def test_agent_dev_src_dir_uses_the_given_agent_id_not_a_hardcoded_one():
    from gaia.daemon.sidecars.spec import agent_dev_src_dir

    root = Path("/some/repo/root")
    assert (
        agent_dev_src_dir(root, "toy-dev")
        == root / "hub" / "agents" / "toy-dev" / "python"
    )


# ===========================================================================
# gaia.daemon.sidecars.errors.DevSrcDirResolutionError
# ===========================================================================


def test_dev_src_dir_resolution_error_is_a_sidecar_error():
    from gaia.daemon.sidecars.errors import DevSrcDirResolutionError, SidecarError

    assert issubclass(DevSrcDirResolutionError, SidecarError)


# ===========================================================================
# resolve_caller_mode()
# ===========================================================================


class _StubSpec:
    def __init__(self, mode_env_var):
        self.mode_env_var = mode_env_var


def test_resolve_caller_mode_explicit_override_wins_over_everything(monkeypatch):
    from gaia.daemon.sidecars import spec as spec_module

    monkeypatch.setattr(
        spec_module,
        "builtin_specs",
        lambda: {"toy-dev": _StubSpec("GAIA_TOY_DEV_2588_MODE")},
    )
    monkeypatch.setenv("GAIA_TOY_DEV_2588_MODE", "user")

    assert spec_module.resolve_caller_mode("toy-dev", override="dev") == "dev"


def test_resolve_caller_mode_falls_back_to_the_agents_own_env_var(monkeypatch):
    from gaia.daemon.sidecars import spec as spec_module

    monkeypatch.setattr(
        spec_module,
        "builtin_specs",
        lambda: {"toy-dev": _StubSpec("GAIA_TOY_DEV_2588_MODE")},
    )
    monkeypatch.setenv("GAIA_TOY_DEV_2588_MODE", "dev")

    assert spec_module.resolve_caller_mode("toy-dev") == "dev"


def test_resolve_caller_mode_defaults_to_user_when_env_var_unset(monkeypatch):
    from gaia.daemon.sidecars import spec as spec_module

    monkeypatch.setattr(
        spec_module,
        "builtin_specs",
        lambda: {"toy-dev": _StubSpec("GAIA_TOY_DEV_2588_MODE")},
    )
    monkeypatch.delenv("GAIA_TOY_DEV_2588_MODE", raising=False)

    assert spec_module.resolve_caller_mode("toy-dev") == "user"


def test_resolve_caller_mode_unknown_agent_id_has_no_env_var_to_consult(monkeypatch):
    """An agent_id absent from builtin_specs() cannot be resolved via any env
    var -- it must fall through to 'user', even if a variable that happens to
    share its (unrelated) name is set."""
    from gaia.daemon.sidecars import spec as spec_module

    monkeypatch.setattr(spec_module, "builtin_specs", lambda: {})
    monkeypatch.setenv("GAIA_NOBODY_2588_MODE", "dev")

    assert spec_module.resolve_caller_mode("nobody") == "user"


# ===========================================================================
# resolve_caller_dev_src_dir()
# ===========================================================================


def test_resolve_caller_dev_src_dir_rejects_relative_explicit_path(tmp_path):
    from gaia.daemon.sidecars.errors import DevSrcDirResolutionError
    from gaia.daemon.sidecars.spec import resolve_caller_dev_src_dir

    with pytest.raises(DevSrcDirResolutionError):
        resolve_caller_dev_src_dir(
            "toy-dev", explicit="relative/checkout", cwd=tmp_path
        )


def test_resolve_caller_dev_src_dir_explicit_absolute_path_bypasses_git(
    tmp_path, monkeypatch
):
    from gaia.daemon.sidecars import spec as spec_module

    def _forbidden_run(*args, **kwargs):
        raise AssertionError("git must not be invoked when --dev-src-dir is explicit")

    monkeypatch.setattr(spec_module.subprocess, "run", _forbidden_run)

    (tmp_path / "b").mkdir(parents=True)
    given = tmp_path / "a" / ".." / "b"
    result = spec_module.resolve_caller_dev_src_dir(
        "toy-dev", explicit=str(given), cwd=tmp_path
    )
    assert result == (tmp_path / "b").resolve()


def test_resolve_caller_dev_src_dir_explicit_wins_over_cwd_git_resolution(
    tmp_path, monkeypatch
):
    """Both explicit and cwd given -> explicit wins, git is never consulted."""
    from gaia.daemon.sidecars import spec as spec_module

    def _forbidden_run(*args, **kwargs):
        raise AssertionError("git must not be invoked when explicit is given")

    monkeypatch.setattr(spec_module.subprocess, "run", _forbidden_run)

    explicit_dir = tmp_path / "explicit-checkout"
    explicit_dir.mkdir()
    result = spec_module.resolve_caller_dev_src_dir(
        "toy-dev", explicit=str(explicit_dir), cwd=tmp_path / "unrelated"
    )
    assert result == explicit_dir.resolve()


def test_resolve_caller_dev_src_dir_git_toplevel_joins_agent_dev_src_dir(
    tmp_path, monkeypatch
):
    from gaia.daemon.sidecars import spec as spec_module

    repo_root = tmp_path / "checkout-b"
    repo_root.mkdir()
    seen_kwargs = {}
    seen_argv = []

    def _fake_run(argv, **kwargs):
        seen_argv.append(list(argv))
        seen_kwargs.update(kwargs)
        return subprocess.CompletedProcess(
            args=argv, returncode=0, stdout=str(repo_root) + "\n", stderr=""
        )

    monkeypatch.setattr(spec_module.subprocess, "run", _fake_run)

    result = spec_module.resolve_caller_dev_src_dir(
        "toy-dev", cwd=tmp_path / "some" / "subdir"
    )

    expected = (repo_root / "hub" / "agents" / "toy-dev" / "python").resolve()
    assert result == expected
    assert result.is_absolute()
    # It must actually invoke git rev-parse against the CALLER's cwd, not the
    # daemon's own -- the whole point of the fix (root cause A/B).
    assert any("git" in str(tok) for tok in seen_argv[0])
    assert any("rev-parse" in str(tok) for tok in seen_argv[0])
    cwd_kwarg = seen_kwargs.get("cwd")
    assert str(cwd_kwarg) == str(tmp_path / "some" / "subdir")


def test_resolve_caller_dev_src_dir_defaults_cwd_to_process_cwd(tmp_path, monkeypatch):
    from gaia.daemon.sidecars import spec as spec_module

    repo_root = tmp_path / "checkout-default"
    repo_root.mkdir()
    monkeypatch.chdir(repo_root)
    seen_kwargs = {}

    def _fake_run(argv, **kwargs):
        seen_kwargs.update(kwargs)
        return subprocess.CompletedProcess(
            args=argv, returncode=0, stdout=str(repo_root) + "\n", stderr=""
        )

    monkeypatch.setattr(spec_module.subprocess, "run", _fake_run)

    spec_module.resolve_caller_dev_src_dir("toy-dev")

    assert str(seen_kwargs.get("cwd")) == str(repo_root)


def test_resolve_caller_dev_src_dir_git_missing_raises_naming_dev_src_dir_flag(
    tmp_path, monkeypatch
):
    from gaia.daemon.sidecars import spec as spec_module
    from gaia.daemon.sidecars.errors import DevSrcDirResolutionError

    def _fake_run(*args, **kwargs):
        raise FileNotFoundError("git not found")

    monkeypatch.setattr(spec_module.subprocess, "run", _fake_run)

    with pytest.raises(DevSrcDirResolutionError) as exc_info:
        spec_module.resolve_caller_dev_src_dir("toy-dev", cwd=tmp_path)
    assert "--dev-src-dir" in str(exc_info.value)


def test_resolve_caller_dev_src_dir_not_a_work_tree_raises_naming_dev_src_dir_flag(
    tmp_path, monkeypatch
):
    from gaia.daemon.sidecars import spec as spec_module
    from gaia.daemon.sidecars.errors import DevSrcDirResolutionError

    def _fake_run(argv, **kwargs):
        raise subprocess.CalledProcessError(
            128, argv, output="", stderr="fatal: not a git repository"
        )

    monkeypatch.setattr(spec_module.subprocess, "run", _fake_run)

    with pytest.raises(DevSrcDirResolutionError) as exc_info:
        spec_module.resolve_caller_dev_src_dir("toy-dev", cwd=tmp_path)
    assert "--dev-src-dir" in str(exc_info.value)


def test_resolve_caller_dev_src_dir_result_is_always_expanduser_resolved(
    tmp_path, monkeypatch
):
    from gaia.daemon.sidecars import spec as spec_module

    real_root = tmp_path / "checkout-c"
    real_root.mkdir(parents=True)
    dotted_root = real_root / "nested" / ".."  # normalizes back to real_root

    def _fake_run(argv, **kwargs):
        return subprocess.CompletedProcess(
            args=argv, returncode=0, stdout=str(dotted_root) + "\n", stderr=""
        )

    monkeypatch.setattr(spec_module.subprocess, "run", _fake_run)

    result = spec_module.resolve_caller_dev_src_dir("toy-dev", cwd=tmp_path)
    expected = (real_root / "hub" / "agents" / "toy-dev" / "python").resolve()
    assert result == expected


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
