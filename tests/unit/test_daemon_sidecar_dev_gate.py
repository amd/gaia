# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Failing (red-phase) spec for issue #2588: ``SidecarRegistry.ensure()``'s new
``dev_src_dir`` comparison gate -- the fix for the daemon silently serving a
stale checkout (or the frozen binary) instead of the caller's actual dev
checkout.

Reuses the ``_FakeManager`` / ``_make_registry`` / ``_TOY_A`` fixtures already
established in ``tests/unit/test_daemon_agents_routes.py`` (same
manager-injection seam, no real subprocess/manager involved). None of this
exists yet:
  - ``SidecarRegistry.ensure(..., dev_src_dir=...)``
  - ``gaia.daemon.sidecars.errors.DevSrcDirResolutionError``
"""

from __future__ import annotations

import dataclasses
import os
from pathlib import Path

import pytest

from tests.unit.test_daemon_agents_routes import _TOY_A, _FakeManager, _make_registry

_CHECKOUT_A = Path("/fake/checkout-a/hub/agents/toy-dev/python")
_CHECKOUT_B = Path("/fake/checkout-b/hub/agents/toy-dev/python")

_TOY_DEV = dataclasses.replace(
    _TOY_A,
    agent_id="toy-dev",
    service_id="gaia-agent-toy-dev",
    display_name="Toy Dev",
    token_env_var="GAIA_TOY_DEV_SIDECAR_TOKEN",
    mode_env_var="GAIA_TOY_DEV_AGENT_MODE",
    cache_dir_name="toy-dev",
    dev_src_dir=_CHECKOUT_A,
)


def _registry(**kwargs):
    return _make_registry({"toy-dev": _TOY_DEV}, **kwargs)


def _entries(reg):
    return {e["agent_id"]: e for e in reg.list_agents()}


# ===========================================================================
# The gate's skip conditions -- never fires when it structurally cannot
# ===========================================================================


def test_dev_src_dir_none_skips_the_gate_entirely():
    reg = _registry()
    result = reg.ensure("toy-dev", mode="dev", dev_src_dir=None)
    assert result["state"] == "running"


def test_spec_with_no_dev_src_dir_skips_the_gate():
    """_TOY_A has no dev_src_dir configured -- the gate must not refuse an
    agent that has no dev mode at all, no matter what dev_src_dir a caller
    (incorrectly) supplies."""
    reg = _make_registry({"toy-a": _TOY_A})
    result = reg.ensure("toy-a", mode="dev", dev_src_dir=str(_CHECKOUT_B))
    assert result["state"] == "running"


def test_resolved_mode_user_skips_the_gate_even_with_a_mismatched_dev_src_dir():
    reg = _registry()
    result = reg.ensure("toy-dev", mode="user", dev_src_dir=str(_CHECKOUT_B))
    assert result["state"] == "running"


# ===========================================================================
# Validation: dev_src_dir must be absolute
# ===========================================================================


def test_non_absolute_dev_src_dir_raises_dev_src_dir_resolution_error():
    from gaia.daemon.sidecars.errors import DevSrcDirResolutionError

    reg = _registry()
    with pytest.raises(DevSrcDirResolutionError):
        reg.ensure("toy-dev", mode="dev", dev_src_dir="relative/checkout")


# ===========================================================================
# Same-source variants are all a no-op (never raise)
# ===========================================================================


def test_exact_match_dev_src_dir_is_a_noop():
    reg = _registry()
    result = reg.ensure("toy-dev", mode="dev", dev_src_dir=str(_CHECKOUT_A))
    assert result["state"] == "running"


def test_trailing_slash_dev_src_dir_compares_equal():
    reg = _registry()
    result = reg.ensure("toy-dev", mode="dev", dev_src_dir=str(_CHECKOUT_A) + "/")
    assert result["state"] == "running"


def test_dotdot_normalized_dev_src_dir_compares_equal():
    reg = _registry()
    variant = str(_CHECKOUT_A) + "/subdir/.."
    result = reg.ensure("toy-dev", mode="dev", dev_src_dir=variant)
    assert result["state"] == "running"


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink semantics only")
def test_symlink_to_the_same_tree_compares_equal(tmp_path):
    real = tmp_path / "real-checkout"
    real.mkdir()
    link = tmp_path / "linked-checkout"
    link.symlink_to(real)

    spec = dataclasses.replace(_TOY_DEV, dev_src_dir=real)
    reg = _make_registry({"toy-dev": spec})
    result = reg.ensure("toy-dev", mode="dev", dev_src_dir=str(link))
    assert result["state"] == "running"


@pytest.mark.skipif(
    os.name == "nt", reason="pathlib.WindowsPath compares case-insensitively"
)
def test_case_variant_of_the_same_string_is_a_different_tree_not_folded():
    """Proof the gate compares resolved Path objects, not lowered/folded
    strings: on POSIX, pathlib treats differently-cased paths as distinct
    regardless of the underlying filesystem's own case sensitivity."""
    from gaia.daemon.sidecars.errors import ModeConflictError

    reg = _registry()
    upper_variant = str(_CHECKOUT_A).upper()
    if upper_variant == str(_CHECKOUT_A):
        pytest.skip("path has no case-varying characters on this platform")
    with pytest.raises(ModeConflictError):
        reg.ensure("toy-dev", mode="dev", dev_src_dir=upper_variant)


# ===========================================================================
# Mismatch: refused, naming both paths + the "Python environment" remedy
# ===========================================================================


def test_mismatch_fresh_spawn_raises_naming_both_paths_and_python_environment():
    from gaia.daemon.sidecars.errors import ModeConflictError

    reg = _registry()
    with pytest.raises(ModeConflictError) as exc_info:
        reg.ensure("toy-dev", mode="dev", dev_src_dir=str(_CHECKOUT_B))
    msg = str(exc_info.value)
    assert str(_CHECKOUT_A.resolve()) in msg
    assert str(_CHECKOUT_B.resolve()) in msg
    assert "Python environment" in msg


def test_mismatch_fresh_spawn_does_not_name_stop_agent_command():
    """Nothing was ever running -- there is nothing to stop-agent."""
    from gaia.daemon.sidecars.errors import ModeConflictError

    reg = _registry()
    with pytest.raises(ModeConflictError) as exc_info:
        reg.ensure("toy-dev", mode="dev", dev_src_dir=str(_CHECKOUT_B))
    assert "gaia daemon stop-agent toy-dev" not in str(exc_info.value)


def test_mismatch_fresh_spawn_spawns_nothing_state_stays_stopped():
    from gaia.daemon.sidecars.errors import ModeConflictError

    reg = _registry()
    with pytest.raises(ModeConflictError):
        reg.ensure("toy-dev", mode="dev", dev_src_dir=str(_CHECKOUT_B))
    entries = _entries(reg)
    assert entries["toy-dev"]["state"] == "stopped"
    assert entries["toy-dev"]["pid"] is None


def test_mismatch_fresh_spawn_never_starts_a_manager_process():
    from gaia.daemon.sidecars.errors import ModeConflictError

    created = []

    class _TrackingManager(_FakeManager):
        def __init__(self, spec, mode=None, **kwargs):
            super().__init__(spec, mode=mode, **kwargs)
            created.append(self)

    reg = _make_registry({"toy-dev": _TOY_DEV}, manager_cls=_TrackingManager)
    with pytest.raises(ModeConflictError):
        reg.ensure("toy-dev", mode="dev", dev_src_dir=str(_CHECKOUT_B))

    assert all(m.start_calls == 0 for m in created)


def test_mismatch_against_already_running_dev_from_a_names_stop_agent_command():
    from gaia.daemon.sidecars.errors import ModeConflictError

    reg = _registry()
    first = reg.ensure("toy-dev", mode="dev", dev_src_dir=str(_CHECKOUT_A))
    assert first["state"] == "running"

    with pytest.raises(ModeConflictError) as exc_info:
        reg.ensure("toy-dev", mode="dev", dev_src_dir=str(_CHECKOUT_B))
    msg = str(exc_info.value)
    assert str(_CHECKOUT_A.resolve()) in msg
    assert str(_CHECKOUT_B.resolve()) in msg
    assert "Python environment" in msg
    assert "gaia daemon stop-agent toy-dev" in msg


def test_mismatch_against_already_running_dev_from_a_does_not_return_a_success():
    """The exception must be the only outcome -- a caller can never mistake
    the still-running A sidecar for a fresh B one."""
    from gaia.daemon.sidecars.errors import ModeConflictError

    reg = _registry()
    first = reg.ensure("toy-dev", mode="dev", dev_src_dir=str(_CHECKOUT_A))

    with pytest.raises(ModeConflictError):
        reg.ensure("toy-dev", mode="dev", dev_src_dir=str(_CHECKOUT_B))

    entries = _entries(reg)
    assert entries["toy-dev"]["state"] == "running"
    assert entries["toy-dev"]["pid"] == first["pid"]


def test_mismatch_after_stop_does_not_silently_reattach_the_stale_manager(
    monkeypatch,
):
    import gaia.daemon.sidecars.registry as registry_mod
    from gaia.daemon.sidecars.errors import ModeConflictError

    monkeypatch.setattr(registry_mod.psutil, "pid_exists", lambda pid: False)

    reg = _registry()
    reg.ensure("toy-dev", mode="dev", dev_src_dir=str(_CHECKOUT_A))
    reg.stop("toy-dev")
    assert _entries(reg)["toy-dev"]["state"] == "stopped"

    with pytest.raises(ModeConflictError) as exc_info:
        reg.ensure("toy-dev", mode="dev", dev_src_dir=str(_CHECKOUT_B))
    msg = str(exc_info.value)
    # Not running at the time of the check -- the stop-agent remedy doesn't apply.
    assert "gaia daemon stop-agent toy-dev" not in msg
    assert str(_CHECKOUT_A.resolve()) in msg
    assert str(_CHECKOUT_B.resolve()) in msg

    assert _entries(reg)["toy-dev"]["state"] == "stopped"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
