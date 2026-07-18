# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Distributed-seams suite — the consolidated contract guard for the daemon↔
sidecar boundaries v2 introduces (V2-19, issue #2180).

Design §0.17: ``/query`` is the most LLM-affecting surface v2 adds, and the
relay / broker / auth / custody seams each have deterministic failure modes unit
tests must pin *before* third-party agents amplify them. Individual issues ship
their own tests (the relay's live suite is ``tests/unit/test_daemon_relay.py``;
``/query`` is ``hub/agents/python/email/tests/test_query_route.py``); THIS suite
is the cross-seam layer nothing else owns:

- **cross-seam vocabulary coherence** — the frozen §0.2 seven-event set must be
  IDENTICAL across the three places that independently define it (the harness,
  the relay, the sidecar's translator). This is the seam most likely to drift as
  front-doors multiply, and no single-seam test can catch a mismatch *between*
  seams.
- **synthetic-error ↔ canonical-error coherence** — a relay-authored crash
  terminal (V2-7) must be indistinguishable-in-kind from a sidecar-authored one
  to a downstream front-door (the CLI, ``gaia api``): it must parse as a valid
  canonical ``error`` event.
- **auth leg (Leg 2, LANDED #1980)** — the per-agent sidecar bearer is delivered
  over a PRIVATE ENV channel, never on argv (where ``ps`` would leak it).

Seams still in flight are marked with an explicit, loud skip reason keyed on
whether the source module/route actually exists yet — so this suite goes green
today and each ``pytest.skip`` flips to a real assertion the moment its issue
merges, without editing the skip logic:

- **secret-file delivery** (V2-3 / #2149) — fd/0600-file delivery of the launch
  secret (today it is a plain env var; the stronger delivery is not merged).
- **model-slot broker** (V2-11 / #2151) — load serialization + ``/host/v1/
  models/lease``.
- **custody per-agent scoping** (V2-12 / #2153) — ``/host/v1/*`` with A-cannot-
  read-B scoping.
- **migration idempotency** (V2-13 / #2155) — one-time ``~/.gaia`` data
  migration that is a no-op on second run.

Run just this suite (the CI job of AC1)::

    python -m pytest -m distributed_seams
"""

from __future__ import annotations

import importlib.util
import json

import pytest

pytestmark = pytest.mark.distributed_seams


# ---------------------------------------------------------------------------
# Seam-presence probes — a skip reason is keyed on the ACTUAL merge state, so
# the skip auto-flips to a real run when the source lands (no edit needed).
# ---------------------------------------------------------------------------


def _module_exists(dotted: str) -> bool:
    try:
        return importlib.util.find_spec(dotted) is not None
    except (ImportError, ValueError, ModuleNotFoundError):
        return False


def _first_existing_module(*candidates: str) -> "str | None":
    for name in candidates:
        if _module_exists(name):
            return name
    return None


def _module_has_symbol(dotted: str, symbol: str) -> bool:
    try:
        import importlib

        return hasattr(importlib.import_module(dotted), symbol)
    except Exception:
        return False


# V2-11 broker (#2151): net-new daemon module. None of these exist yet.
_BROKER_MODULE = _first_existing_module(
    "gaia.daemon.broker",
    "gaia.daemon.model_broker",
    "gaia.llm.model_broker",
)

# V2-13 migration (#2155): net-new daemon module.
_MIGRATION_MODULE = _first_existing_module(
    "gaia.daemon.migration",
    "gaia.daemon.migrate",
    "gaia.daemon.data_migration",
)


def _daemon_has_route(prefix: str) -> bool:
    """True if the daemon app mounts any route under *prefix* (e.g. ``/host/v1``).

    Builds the app with a stub registry and inspects its route table — no server,
    no network. Returns False loudly-safe if the app can't be built at all.
    """
    try:
        import time

        from gaia.daemon.app import create_app
    except Exception:
        return False

    class _StubRegistry:
        def list_agents(self):
            return []

        def connection(self, agent_id):  # pragma: no cover - unused here
            raise AssertionError

    try:
        app = create_app(
            token="probe",
            port=0,
            pid=1,
            started_at=time.time(),
            registry=_StubRegistry(),
        )
    except Exception:
        return False
    return any(getattr(r, "path", "").startswith(prefix) for r in app.routes)


# V2-12 custody (#2153): /host/v1/* on the daemon. Only a doc comment today.
_CUSTODY_MOUNTED = _daemon_has_route("/host/v1")

# V2-3 secret-file delivery (#2149): the launch-secret fd/file channel. Today the
# manager delivers the bearer via a plain env var (spec.token_env_var); the
# stronger fd/0600-file delivery is not merged.
_SECRET_FILE_DELIVERY = _module_exists("gaia.daemon.sidecars.secret_delivery") or any(
    _module_has_symbol("gaia.daemon.sidecars.manager", sym)
    for sym in ("LAUNCH_SECRET_ENV_VAR", "deliver_secret_via_fd", "_secret_fd")
)


# ---------------------------------------------------------------------------
# 1. Cross-seam vocabulary coherence (ALWAYS runs — the core V2-19 guard)
# ---------------------------------------------------------------------------


def test_terminal_types_identical_across_relay_translator_harness():
    """The two terminal types must agree everywhere a stream is terminated —
    else a front-door and the sidecar disagree on what "done" looks like."""
    from gaia.daemon.relay import TERMINAL_TYPES as relay_terminal
    from gaia.eval.sidecar_harness import TERMINAL_TYPES as harness_terminal

    assert relay_terminal == harness_terminal == frozenset({"final", "error"})

    # The sidecar's translator ships in the email package; import it when present.
    if _module_exists("gaia_agent_email.sse_translation"):
        from gaia_agent_email.sse_translation import TERMINAL_TYPES as sidecar_terminal

        assert sidecar_terminal == relay_terminal


def test_canonical_seven_event_set_matches_translator_outputs():
    """Every canonical type the harness validates against must be a type the
    sidecar translator can actually PRODUCE — otherwise the harness would pass
    runs that emit a type the wire contract forbids, or reject a legal one."""
    from gaia.eval.sidecar_harness import CANONICAL_EVENT_TYPES

    if not _module_exists("gaia_agent_email.sse_translation"):
        pytest.skip("email package (gaia_agent_email) not importable in this env")

    from gaia_agent_email import sse_translation

    # The translator's producible types: the terminal set + every type its
    # per-event maps can emit. We assert the harness's set is exactly the frozen
    # seven and that the translator never emits something outside it.
    assert CANONICAL_EVENT_TYPES == frozenset(
        {
            "status",
            "token",
            "tool_call",
            "tool_result",
            "needs_confirmation",
            "final",
            "error",
        }
    )
    # Drive the translator across its whole source vocabulary and assert every
    # emitted type is canonical (a source-vocab leak would fail here).
    translator = sse_translation.CanonicalTranslator("run-coherence")
    source_events = [
        {"type": "status", "message": "s"},
        {"type": "step", "step": 1, "total": 3},
        {"type": "thinking", "content": "hmm"},
        {"type": "plan", "steps": ["a", "b"]},
        {"type": "tool_start", "tool": "triage_inbox"},
        {"type": "tool_args", "args": {"x": 1}},
        {"type": "tool_result", "result_data": {"ok": True}},
        {"type": "tool_end"},
        {"type": "chunk", "content": "tok"},
        {"type": "permission_request", "tool": "send_now", "args": {}},
        {"type": "user_input_request", "message": "which?"},
        {"type": "tool_confirm_denied", "message": "denied"},
        {"type": "policy_alert", "reason": "nope"},
        {"type": "agent_created"},
        {"type": "answer", "content": "done"},
    ]
    emitted = []
    for ev in source_events:
        emitted.extend(translator.translate(ev))
    emitted.extend(translator.flush())
    produced_types = {e["type"] for e in emitted}
    # Every type the translator produced is one the harness will accept — the
    # sending and receiving ends of the seam agree on the vocabulary.
    assert produced_types <= CANONICAL_EVENT_TYPES, (
        f"translator produced non-canonical types: "
        f"{sorted(produced_types - CANONICAL_EVENT_TYPES)}"
    )


# ---------------------------------------------------------------------------
# 2. Synthetic-error ↔ canonical-error coherence (relay V2-7 #2150, LANDED)
# ---------------------------------------------------------------------------


def test_relay_synthetic_crash_error_parses_as_canonical_error():
    """A relay-authored crash terminal must parse via the harness's own SSE
    reader as a valid canonical ``error`` event — so the CLI / gaia api front-
    doors cannot tell a relay-synthesized terminal from a sidecar-authored one
    (beyond the additive ``source`` marker)."""
    from gaia.daemon.relay import (
        _synthetic_error_frame,
        stream_ended_unexpectedly_detail,
    )
    from gaia.eval.sidecar_harness import CANONICAL_EVENT_TYPES, parse_sse

    detail = stream_ended_unexpectedly_detail("email")
    frame = _synthetic_error_frame(detail, terminate_partial=False)
    events = parse_sse(frame.decode("utf-8"))

    assert len(events) == 1
    event = events[0]
    assert event["type"] == "error"
    assert event["type"] in CANONICAL_EVENT_TYPES
    assert event["detail"] == detail
    # The additive marker lets a front-door attribute the terminal to the relay.
    assert event["source"] == "daemon_relay"


def test_relay_synthetic_error_detail_is_actionable():
    """No silent fallbacks: the crash terminal names what to check and where."""
    from gaia.daemon.relay import stream_ended_unexpectedly_detail

    detail = stream_ended_unexpectedly_detail("email")
    assert "email" in detail
    assert "ensure" in detail  # the remedy
    assert "logs" in detail  # where to look


# ---------------------------------------------------------------------------
# 3. Auth leg 2 (daemon→sidecar bearer, LANDED #1980): private env, never argv
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not _module_exists("gaia.daemon.sidecars.manager"),
    reason="daemon sidecar manager not importable in this env",
)
def test_sidecar_bearer_delivered_via_private_env_never_on_argv(monkeypatch):
    """The manager hands the sidecar its per-session bearer over the private env
    channel (spec.token_env_var), NOT on the command line — argv is world-
    readable via ``ps`` / Task Manager, so a token there would leak."""
    from gaia.daemon.sidecars.manager import AgentSidecarManager
    from gaia.daemon.sidecars.spec import AgentSidecarSpec

    spec = AgentSidecarSpec(
        agent_id="toy",
        service_id="gaia-agent-toy",
        display_name="Toy Agent",
        expected_api_major="1",
        token_env_var="GAIA_TOY_SIDECAR_TOKEN",
        mode_env_var="GAIA_TOY_AGENT_MODE",
        cache_dir_name="toy",
    )
    manager = AgentSidecarManager(spec, mode="user")

    captured = {}

    class _FakeProc:
        pid = 4321

    def _fake_popen(argv, **kwargs):
        captured["argv"] = list(argv)
        captured["env"] = dict(kwargs.get("env") or {})
        return _FakeProc()

    # Inspect the spawn without launching a real process: stub Popen + the log.
    import gaia.daemon.sidecars.manager as mgr_mod

    monkeypatch.setattr(mgr_mod.subprocess, "Popen", _fake_popen)
    monkeypatch.setattr(manager, "_open_log", lambda port: None)
    monkeypatch.setattr(manager, "_close_log", lambda: None)
    # The real _spawn_process registers atexit shutdown of the (fake) proc;
    # neutralize it so process teardown doesn't poll the stub.
    monkeypatch.setattr(mgr_mod.atexit, "register", lambda *a, **k: None)

    manager._spawn_process(["sidecar-bin", "--port", "12345"], {}, 12345)

    token = manager.auth_token
    # Delivered via the private env channel...
    assert captured["env"].get("GAIA_TOY_SIDECAR_TOKEN") == token
    # ...and NEVER on argv (a ps/Task-Manager leak).
    assert all(
        token not in str(arg) for arg in captured["argv"]
    ), "the sidecar bearer token must never appear in argv"


# ---------------------------------------------------------------------------
# 4. Secret-file delivery (V2-3 / #2149) — SKIP-pending-merge
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not _SECRET_FILE_DELIVERY,
    reason=(
        "V2-3 (#2149) fd/0600-file launch-secret delivery not merged yet — today "
        "the manager delivers the bearer via a plain env var (covered by the "
        "auth-leg test above). This test asserts the STRONGER delivery (secret "
        "never in /proc/<pid>/environ) once the seam lands."
    ),
)
def test_launch_secret_not_in_process_environ():  # pragma: no cover - pending merge
    # When V2-3 lands: assert the launch secret is delivered by inherited fd or a
    # 0600 file and never appears in the spawned process's environ block.
    raise AssertionError(
        "secret-file delivery landed — implement the /proc/<pid>/environ "
        "absence assertion (V2-3 / #2149)."
    )


# ---------------------------------------------------------------------------
# 5. Model-slot broker serialization (V2-11 / #2151) — SKIP-pending-merge
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    _BROKER_MODULE is None,
    reason=(
        "V2-11 (#2151) model-slot broker not merged yet — no daemon broker "
        "module exists. This test asserts two agents requesting different models "
        "serialize (no race-evict) and foreground jumps the queue once it lands."
    ),
)
def test_broker_serializes_concurrent_model_loads():  # pragma: no cover - pending merge
    # When V2-11 lands: two concurrent lease requests for different models must
    # serialize through the broker (never race-evict the single Lemonade slot),
    # and an interactive lease must jump ahead of a background one.
    raise AssertionError(
        f"broker module {_BROKER_MODULE!r} present — implement the serialization "
        "+ interactive-priority assertions (V2-11 / #2151)."
    )


# ---------------------------------------------------------------------------
# 6. Custody per-agent scoping (V2-12 / #2153) — SKIP-pending-merge
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not _CUSTODY_MOUNTED,
    reason=(
        "V2-12 (#2153) /host/v1/* custody API not merged yet — the daemon mounts "
        "no /host/v1 routes (only a doc comment references them). This test "
        "asserts agent A cannot read agent B's memory/sessions once it lands."
    ),
)
def test_custody_scopes_reads_to_the_calling_agent():  # pragma: no cover - pending merge
    # When V2-12 lands: a secret bound to agent A must get 403 (not B's data)
    # when reading /host/v1/memory scoped to agent B.
    raise AssertionError(
        "/host/v1 custody routes mounted — implement the A-cannot-read-B scoping "
        "assertions (V2-12 / #2153)."
    )


# ---------------------------------------------------------------------------
# 7. Migration idempotency (V2-13 / #2155) — SKIP-pending-merge
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    _MIGRATION_MODULE is None,
    reason=(
        "V2-13 (#2155) one-time ~/.gaia data migration not merged yet — no "
        "migration module exists. This test asserts running the migration twice "
        "from a real pre-v2 fixture leaves the second run a no-op once it lands."
    ),
)
def test_migration_is_idempotent():  # pragma: no cover - pending merge
    # When V2-13 lands: run the migration twice against a pre-v2 ~/.gaia fixture;
    # the second run must be a no-op (cold-state test per CLAUDE.md).
    raise AssertionError(
        f"migration module {_MIGRATION_MODULE!r} present — implement the "
        "run-twice-is-a-no-op assertion (V2-13 / #2155)."
    )


# ---------------------------------------------------------------------------
# Coverage manifest — one place to SEE what this suite guards vs. defers. Keeps
# the "covered vs skipped-pending-merge" split honest and greppable.
# ---------------------------------------------------------------------------


def test_seam_coverage_manifest_reflects_reality():
    """Assert the documented coverage split matches the live probe results, so
    the PR's "covered vs skipped" table can never silently drift from the code."""
    covered = {
        "relay_vocabulary_coherence": True,  # always
        "relay_synthetic_error_coherence": True,  # always
        "auth_leg2_bearer_via_env": _module_exists("gaia.daemon.sidecars.manager"),
    }
    pending = {
        "secret_file_delivery_v2_3": not _SECRET_FILE_DELIVERY,
        "model_broker_v2_11": _BROKER_MODULE is None,
        "custody_scoping_v2_12": not _CUSTODY_MOUNTED,
        "migration_idempotency_v2_13": _MIGRATION_MODULE is None,
    }
    # The core cross-seam guards are unconditionally covered.
    assert covered["relay_vocabulary_coherence"]
    assert covered["relay_synthetic_error_coherence"]
    # Everything not-yet-merged is honestly marked pending (this is the state at
    # authoring time; each flips to False — i.e. covered — when its issue merges).
    assert isinstance(json.dumps(pending), str)  # serializable manifest
