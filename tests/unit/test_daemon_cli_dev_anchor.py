# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Failing (red-phase) spec for issue #2588's CLI half: ``gaia daemon
start-agent --mode dev`` must resolve its OWN mode / dev-src-dir from the
CALLER's environment and checkout BEFORE ever talking to the daemon, and must
refuse loudly (never a false success) when the answering daemon predates this
fix.

Patch-seam assumption (documented here so the implementer can re-target these
tests if a different choice is made): every other ``_handle_daemon_*`` helper
in ``gaia/cli.py`` does a LAZY, in-function
``from gaia.daemon import client`` / ``import requests`` -- these tests
assume ``_handle_daemon_start_agent`` follows the same convention for
``from gaia.daemon.sidecars.spec import resolve_caller_mode,
resolve_caller_dev_src_dir`` and patch those names on
``gaia.daemon.sidecars.spec`` itself (never on ``gaia.cli``). If the
implementation instead imports them at module scope in ``cli.py``, these
monkeypatches will silently not apply and the affected tests will need to
re-target at ``gaia.cli.resolve_caller_mode`` / ``gaia.cli.resolve_caller_dev_src_dir``.

None of the following exist yet:
  - gaia.daemon.sidecars.spec.resolve_caller_mode
  - gaia.daemon.sidecars.spec.resolve_caller_dev_src_dir
  - gaia.daemon.sidecars.errors.DevSrcDirResolutionError
  - the CLI's ``daemon start-agent --dev-src-dir`` flag
  - `_handle_daemon_start_agent` resolving mode/dev_src_dir client-side at all
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

import gaia.cli as cli
from gaia.daemon.instance import DaemonInstance


def _args(agent_id="toy-dev", mode=None, dev_src_dir=None):
    return argparse.Namespace(agent_id=agent_id, mode=mode, dev_src_dir=dev_src_dir)


def _inst(**overrides):
    fields = dict(pid=999, port=54321, token="daemon-tok", api_version="1.1")
    fields.update(overrides)
    return DaemonInstance(**fields)


class _ForbiddenStartOrAttach:
    def __call__(self, *args, **kwargs):
        raise AssertionError(
            "the daemon must never be contacted once client-side dev-src-dir "
            "resolution has already failed"
        )


class _RecordingPost:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload
        self.calls = []

    def __call__(self, url, **kwargs):
        self.calls.append({"url": url, "json": kwargs.get("json")})
        return self

    def json(self):
        return self._payload


def _ensure_payload(**overrides):
    payload = {
        "agent_id": "toy-dev",
        "state": "running",
        "mode": "user",
        "pid": 1,
        "port": 2,
        "api_version": "1",
    }
    payload.update(overrides)
    return payload


# ===========================================================================
# `--dev-src-dir` parses
# ===========================================================================


def test_start_agent_parser_accepts_dev_src_dir_flag():
    parser = cli.build_parser()
    args = parser.parse_args(
        ["daemon", "start-agent", "email", "--dev-src-dir", "/some/checkout"]
    )
    assert args.dev_src_dir == "/some/checkout"


def test_start_agent_parser_defaults_dev_src_dir_to_none():
    parser = cli.build_parser()
    args = parser.parse_args(["daemon", "start-agent", "email"])
    assert args.dev_src_dir is None


# ===========================================================================
# Mode is always resolved client-side and sent explicitly (root cause A)
# ===========================================================================


def test_mode_is_always_resolved_explicitly_never_sent_as_none(monkeypatch):
    import requests

    import gaia.daemon.client as client_mod
    import gaia.daemon.sidecars.spec as spec_mod

    monkeypatch.setattr(client_mod, "start_or_attach", lambda **k: _inst())
    monkeypatch.setattr(
        spec_mod, "resolve_caller_mode", lambda agent_id, override=None: "user"
    )
    recorder = _RecordingPost(200, _ensure_payload(mode="user"))
    monkeypatch.setattr(requests, "post", recorder)

    cli._handle_daemon_start_agent(_args(mode=None))

    assert recorder.calls[0]["json"]["mode"] == "user"
    assert recorder.calls[0]["json"]["mode"] is not None


def test_resolve_caller_mode_receives_the_explicit_cli_override(monkeypatch):
    import requests

    import gaia.daemon.client as client_mod
    import gaia.daemon.sidecars.spec as spec_mod

    monkeypatch.setattr(client_mod, "start_or_attach", lambda **k: _inst())
    calls = []

    def _fake_resolve_mode(agent_id, override=None):
        calls.append((agent_id, override))
        return override or "user"

    monkeypatch.setattr(spec_mod, "resolve_caller_mode", _fake_resolve_mode)
    monkeypatch.setattr(
        requests, "post", _RecordingPost(200, _ensure_payload(mode="user"))
    )

    cli._handle_daemon_start_agent(_args(mode="user"))

    assert calls == [("toy-dev", "user")]


# ===========================================================================
# Dev-src-dir resolution failure exits before ever contacting the daemon
# ===========================================================================


def test_dev_src_dir_resolution_error_exits_before_contacting_daemon(
    monkeypatch, capsys
):
    import gaia.daemon.client as client_mod
    import gaia.daemon.sidecars.spec as spec_mod
    from gaia.daemon.sidecars.errors import DevSrcDirResolutionError

    monkeypatch.setattr(
        spec_mod, "resolve_caller_mode", lambda agent_id, override=None: "dev"
    )

    def _raise(*a, **k):
        raise DevSrcDirResolutionError("DISTINCTIVE-NO-GIT-WORKTREE")

    monkeypatch.setattr(spec_mod, "resolve_caller_dev_src_dir", _raise)
    monkeypatch.setattr(client_mod, "start_or_attach", _ForbiddenStartOrAttach())

    with pytest.raises(SystemExit) as exc_info:
        cli._handle_daemon_start_agent(_args(mode="dev"))
    assert exc_info.value.code == 1

    out = capsys.readouterr().out
    assert "❌" in out
    assert "DISTINCTIVE-NO-GIT-WORKTREE" in out


# ===========================================================================
# Dev mode: the resolved dev_src_dir rides the POST body
# ===========================================================================


def test_dev_mode_posts_resolved_dev_src_dir_in_body(monkeypatch):
    import requests

    import gaia.daemon.client as client_mod
    import gaia.daemon.sidecars.spec as spec_mod

    resolved = Path("/checkout-b/hub/agents/toy-dev/python")
    monkeypatch.setattr(
        spec_mod, "resolve_caller_mode", lambda agent_id, override=None: "dev"
    )
    monkeypatch.setattr(
        spec_mod, "resolve_caller_dev_src_dir", lambda agent_id, **kw: resolved
    )
    monkeypatch.setattr(client_mod, "start_or_attach", lambda **k: _inst())
    recorder = _RecordingPost(
        200, _ensure_payload(mode="dev", dev_src_dir=str(resolved))
    )
    monkeypatch.setattr(requests, "post", recorder)

    cli._handle_daemon_start_agent(_args(mode="dev"))

    sent = recorder.calls[0]["json"]
    assert sent["mode"] == "dev"
    assert sent["dev_src_dir"] == str(resolved)


def test_explicit_dev_src_dir_flag_is_passed_to_the_resolver(monkeypatch):
    import requests

    import gaia.daemon.client as client_mod
    import gaia.daemon.sidecars.spec as spec_mod

    monkeypatch.setattr(
        spec_mod, "resolve_caller_mode", lambda agent_id, override=None: "dev"
    )
    seen = {}

    def _fake_resolve_dev_src_dir(agent_id, *, explicit=None, cwd=None):
        seen["agent_id"] = agent_id
        seen["explicit"] = explicit
        return Path("/checkout-b/hub/agents/toy-dev/python")

    monkeypatch.setattr(
        spec_mod, "resolve_caller_dev_src_dir", _fake_resolve_dev_src_dir
    )
    monkeypatch.setattr(client_mod, "start_or_attach", lambda **k: _inst())
    monkeypatch.setattr(
        requests,
        "post",
        _RecordingPost(
            200,
            _ensure_payload(
                mode="dev", dev_src_dir="/checkout-b/hub/agents/toy-dev/python"
            ),
        ),
    )

    cli._handle_daemon_start_agent(_args(mode="dev", dev_src_dir="/explicit/checkout"))

    assert seen["agent_id"] == "toy-dev"
    assert seen["explicit"] == "/explicit/checkout"


# ===========================================================================
# User mode never mentions dev_src_dir at all
# ===========================================================================


def test_user_mode_posts_no_dev_src_dir_key(monkeypatch):
    import requests

    import gaia.daemon.client as client_mod
    import gaia.daemon.sidecars.spec as spec_mod

    monkeypatch.setattr(
        spec_mod, "resolve_caller_mode", lambda agent_id, override=None: "user"
    )
    monkeypatch.setattr(client_mod, "start_or_attach", lambda **k: _inst())
    recorder = _RecordingPost(200, _ensure_payload(mode="user"))
    monkeypatch.setattr(requests, "post", recorder)

    cli._handle_daemon_start_agent(_args(mode=None))

    assert "dev_src_dir" not in recorder.calls[0]["json"]


def test_user_mode_success_prints_no_source_line(monkeypatch, capsys):
    import requests

    import gaia.daemon.client as client_mod
    import gaia.daemon.sidecars.spec as spec_mod

    monkeypatch.setattr(
        spec_mod, "resolve_caller_mode", lambda agent_id, override=None: "user"
    )
    monkeypatch.setattr(client_mod, "start_or_attach", lambda **k: _inst())
    monkeypatch.setattr(
        requests, "post", _RecordingPost(200, _ensure_payload(mode="user"))
    )

    cli._handle_daemon_start_agent(_args(mode=None))

    assert "source:" not in capsys.readouterr().out


# ===========================================================================
# The pre-#2588-daemon safety net: a stale daemon must never yield a false
# success -- an omitted OR mismatched dev_src_dir in the response is refused.
# ===========================================================================


def test_stale_daemon_omitting_dev_src_dir_is_refused_not_a_false_success(
    monkeypatch, capsys
):
    import requests

    import gaia.daemon.client as client_mod
    import gaia.daemon.sidecars.spec as spec_mod

    resolved = Path("/checkout-b/hub/agents/toy-dev/python")
    monkeypatch.setattr(
        spec_mod, "resolve_caller_mode", lambda agent_id, override=None: "dev"
    )
    monkeypatch.setattr(
        spec_mod, "resolve_caller_dev_src_dir", lambda agent_id, **kw: resolved
    )
    monkeypatch.setattr(client_mod, "start_or_attach", lambda **k: _inst())
    # A pre-#2588 daemon 200s but never echoes dev_src_dir at all.
    recorder = _RecordingPost(200, _ensure_payload(mode="dev"))
    monkeypatch.setattr(requests, "post", recorder)

    with pytest.raises(SystemExit) as exc_info:
        cli._handle_daemon_start_agent(_args(mode="dev"))
    assert exc_info.value.code == 1

    out = capsys.readouterr().out
    assert "Python environment" in out
    assert "source:" not in out
    # The remedy must name the REPO ROOT (/checkout-b), not the agent source
    # dir (resolved) -- a bare "Python environment" substring check is what
    # let a wrong-path remedy through the first time (real-world evidence).
    assert "rooted at /checkout-b," in out
    assert f"rooted at {resolved}" not in out


def test_stale_daemon_returning_a_different_dev_src_dir_is_refused(monkeypatch, capsys):
    import requests

    import gaia.daemon.client as client_mod
    import gaia.daemon.sidecars.spec as spec_mod

    resolved = Path("/checkout-b/hub/agents/toy-dev/python")
    monkeypatch.setattr(
        spec_mod, "resolve_caller_mode", lambda agent_id, override=None: "dev"
    )
    monkeypatch.setattr(
        spec_mod, "resolve_caller_dev_src_dir", lambda agent_id, **kw: resolved
    )
    monkeypatch.setattr(client_mod, "start_or_attach", lambda **k: _inst())
    recorder = _RecordingPost(
        200,
        _ensure_payload(
            mode="dev", dev_src_dir="/checkout-a/hub/agents/toy-dev/python"
        ),
    )
    monkeypatch.setattr(requests, "post", recorder)

    with pytest.raises(SystemExit) as exc_info:
        cli._handle_daemon_start_agent(_args(mode="dev"))
    assert exc_info.value.code == 1

    out = capsys.readouterr().out
    assert "Python environment" in out
    # The remedy must name the caller's REPO ROOT (/checkout-b, from the
    # resolved dev_src_dir), not the stale daemon's reported agent subdir
    # (/checkout-a/...) nor the caller's own agent subdir.
    assert "rooted at /checkout-b," in out
    assert f"rooted at {resolved}" not in out
    assert "rooted at /checkout-a/hub/agents/toy-dev/python" not in out


def test_stale_daemon_mismatch_with_non_standard_explicit_dev_src_dir_does_not_crash(
    monkeypatch, capsys
):
    """An explicit --dev-src-dir that doesn't follow hub/agents/<id>/python
    has no repo root to derive -- this must degrade to an honest, actionable
    message (never a raw traceback, never a false "rooted at <subdir>" claim
    that reproduces the original bug)."""
    import requests

    import gaia.daemon.client as client_mod
    import gaia.daemon.sidecars.spec as spec_mod

    resolved = Path("/some/arbitrary/directory")
    monkeypatch.setattr(
        spec_mod, "resolve_caller_mode", lambda agent_id, override=None: "dev"
    )
    monkeypatch.setattr(
        spec_mod, "resolve_caller_dev_src_dir", lambda agent_id, **kw: resolved
    )
    monkeypatch.setattr(client_mod, "start_or_attach", lambda **k: _inst())
    recorder = _RecordingPost(200, _ensure_payload(mode="dev"))
    monkeypatch.setattr(requests, "post", recorder)

    with pytest.raises(SystemExit) as exc_info:
        cli._handle_daemon_start_agent(
            _args(mode="dev", dev_src_dir="/some/arbitrary/directory")
        )
    assert exc_info.value.code == 1

    out = capsys.readouterr().out
    assert f"rooted at {resolved}" not in out


# ===========================================================================
# Success prints the served source when (and only when) dev mode matches
# ===========================================================================


def test_dev_mode_success_prints_a_source_line_with_the_path(monkeypatch, capsys):
    import requests

    import gaia.daemon.client as client_mod
    import gaia.daemon.sidecars.spec as spec_mod

    resolved = Path("/checkout-b/hub/agents/toy-dev/python")
    monkeypatch.setattr(
        spec_mod, "resolve_caller_mode", lambda agent_id, override=None: "dev"
    )
    monkeypatch.setattr(
        spec_mod, "resolve_caller_dev_src_dir", lambda agent_id, **kw: resolved
    )
    monkeypatch.setattr(client_mod, "start_or_attach", lambda **k: _inst())
    recorder = _RecordingPost(
        200, _ensure_payload(mode="dev", dev_src_dir=str(resolved))
    )
    monkeypatch.setattr(requests, "post", recorder)

    cli._handle_daemon_start_agent(_args(mode="dev"))

    out = capsys.readouterr().out
    assert "source:" in out
    assert str(resolved) in out


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
