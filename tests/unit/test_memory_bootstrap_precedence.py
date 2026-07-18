# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Unit tests for `_handle_memory_bootstrap`'s 7-way flag precedence (#2001).

``_handle_memory_bootstrap`` (src/gaia/cli.py) is an ``elif`` ladder dispatching
one of seven operations from six independent boolean flags:

    reset_system > system > reset > chat_only > discover > infer > (default)

Two of those operations are destructive (``_bootstrap_reset_system`` clears
system-context entries; ``_bootstrap_reset`` clears discovery memories). This
file asserts, per flag combination, exactly which underlying
``_bootstrap_*`` function runs — and, critically, that the destructive ones
never run for a read-only flag or combination that doesn't request them.

Every ``_bootstrap_*`` function is mocked; no real ``MemoryStore`` is touched.
The filename keeps the ``test_memory_`` prefix so
``tests/unit/conftest.py`` clears ``GAIA_MEMORY_DISABLED`` for it, matching
the convention in ``test_memory_bootstrap.py``.
"""

from types import SimpleNamespace

import pytest

from gaia import cli

_ALL_FLAGS = ("reset_system", "system", "reset", "chat_only", "discover", "infer")

_BOOTSTRAP_FNS = (
    "_bootstrap_reset_system",
    "_bootstrap_system",
    "_bootstrap_reset",
    "_bootstrap_chat",
    "_bootstrap_discover",
    "_bootstrap_infer",
)

#: The two destructive operations — must never fire for a read-only request.
_DESTRUCTIVE_FNS = ("_bootstrap_reset_system", "_bootstrap_reset")


def _args(**overrides):
    base = {flag: False for flag in _ALL_FLAGS}
    base.update(overrides)
    return SimpleNamespace(**base)


class _ChatResult:
    def __init__(self, cancelled=False):
        self.cancelled = cancelled


@pytest.fixture
def tracked_calls(monkeypatch):
    """Mock every ``_bootstrap_*`` function on the ``cli`` module.

    Returns the list of call records in invocation order. ``_bootstrap_chat``
    returns a non-cancelled result by default so the default (no-flags) path
    falls through to ``_bootstrap_discover`` as the real code does.
    """
    calls = []

    def _recorder(name, return_value=None):
        def _fn(*args, **kwargs):
            calls.append((name, args, kwargs))
            return return_value

        return _fn

    for name in _BOOTSTRAP_FNS:
        return_value = (
            _ChatResult(cancelled=False) if name == "_bootstrap_chat" else None
        )
        monkeypatch.setattr(cli, name, _recorder(name, return_value))

    return calls


def _names(calls):
    return [name for name, _args, _kwargs in calls]


# ---------------------------------------------------------------------------
# Single-flag dispatch — each flag alone runs exactly its own operation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "flag,expected_fn",
    [
        ("reset_system", "_bootstrap_reset_system"),
        ("system", "_bootstrap_system"),
        ("reset", "_bootstrap_reset"),
        ("chat_only", "_bootstrap_chat"),
        ("discover", "_bootstrap_discover"),
        ("infer", "_bootstrap_infer"),
    ],
)
def test_single_flag_runs_only_its_own_operation(tracked_calls, flag, expected_fn):
    cli._handle_memory_bootstrap(_args(**{flag: True}))

    assert _names(tracked_calls) == [expected_fn]


def test_no_flags_runs_the_default_chat_then_discover_sequence(tracked_calls):
    cli._handle_memory_bootstrap(_args())

    assert _names(tracked_calls) == ["_bootstrap_chat", "_bootstrap_discover"]


def test_default_skips_discover_when_chat_is_cancelled(monkeypatch):
    calls = []
    monkeypatch.setattr(
        cli,
        "_bootstrap_chat",
        lambda: calls.append("_bootstrap_chat") or _ChatResult(cancelled=True),
    )
    monkeypatch.setattr(
        cli, "_bootstrap_discover", lambda: calls.append("_bootstrap_discover")
    )

    cli._handle_memory_bootstrap(_args())

    assert calls == ["_bootstrap_chat"]


# ---------------------------------------------------------------------------
# Precedence — the ladder's declared order wins when multiple flags are set
# ---------------------------------------------------------------------------

#: (kwargs set True, expected sole operation) — each row also sets every flag
#: *after* the winner in ladder order, to prove later flags are ignored.
_PRECEDENCE_CASES = [
    pytest.param(
        dict(
            reset_system=True,
            system=True,
            reset=True,
            chat_only=True,
            discover=True,
            infer=True,
        ),
        "_bootstrap_reset_system",
        id="reset_system-beats-everything",
    ),
    pytest.param(
        dict(system=True, reset=True, chat_only=True, discover=True, infer=True),
        "_bootstrap_system",
        id="system-beats-reset-and-read-only-flags",
    ),
    pytest.param(
        dict(reset=True, chat_only=True, discover=True, infer=True),
        "_bootstrap_reset",
        id="reset-beats-read-only-flags",
    ),
    pytest.param(
        dict(chat_only=True, discover=True, infer=True),
        "_bootstrap_chat",
        id="chat_only-beats-discover-and-infer",
    ),
    pytest.param(
        dict(discover=True, infer=True),
        "_bootstrap_discover",
        id="discover-beats-infer",
    ),
]


@pytest.mark.parametrize("flags,expected_fn", _PRECEDENCE_CASES)
def test_precedence_ladder_runs_only_the_winner(tracked_calls, flags, expected_fn):
    cli._handle_memory_bootstrap(_args(**flags))

    assert _names(tracked_calls) == [expected_fn]


@pytest.mark.parametrize("flags,expected_fn", _PRECEDENCE_CASES)
def test_precedence_ladder_never_runs_a_destructive_op_for_a_non_destructive_winner(
    tracked_calls, flags, expected_fn
):
    """Belt-and-suspenders: when a read-only/less-destructive flag should win,
    neither destructive operation may have fired — regardless of what else
    changes about the dispatch in the future.
    """
    cli._handle_memory_bootstrap(_args(**flags))

    if expected_fn not in _DESTRUCTIVE_FNS:
        assert not (set(_names(tracked_calls)) & set(_DESTRUCTIVE_FNS))


@pytest.mark.parametrize(
    "flag", ["chat_only", "discover", "infer"], ids=lambda f: f"only-{f}"
)
def test_read_only_flags_never_trigger_a_destructive_reset(tracked_calls, flag):
    cli._handle_memory_bootstrap(_args(**{flag: True}))

    assert not (set(_names(tracked_calls)) & set(_DESTRUCTIVE_FNS))


def test_default_no_flags_never_triggers_a_destructive_reset(tracked_calls):
    cli._handle_memory_bootstrap(_args())

    assert not (set(_names(tracked_calls)) & set(_DESTRUCTIVE_FNS))


# ---------------------------------------------------------------------------
# system is always called with force=True
# ---------------------------------------------------------------------------


def test_system_flag_forces_a_full_rescan(tracked_calls):
    cli._handle_memory_bootstrap(_args(system=True))

    name, args, kwargs = tracked_calls[0]
    assert name == "_bootstrap_system"
    assert kwargs == {"force": True}


# ---------------------------------------------------------------------------
# Fail-loudly: a RuntimeError from any operation exits 1, not silently
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "flag,fn_name",
    [
        ("reset_system", "_bootstrap_reset_system"),
        ("reset", "_bootstrap_reset"),
        ("discover", "_bootstrap_discover"),
    ],
)
def test_runtime_error_from_the_dispatched_op_exits_1(
    monkeypatch, capsys, flag, fn_name
):
    monkeypatch.setattr(
        cli,
        fn_name,
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("store is locked")),
    )

    with pytest.raises(SystemExit) as excinfo:
        cli._handle_memory_bootstrap(_args(**{flag: True}))

    assert excinfo.value.code == 1
    assert "store is locked" in capsys.readouterr().out
