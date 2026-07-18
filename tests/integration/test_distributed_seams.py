# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Distributed-seams suite — the consolidated contract guard for the daemon↔
sidecar boundaries v2 introduces (V2-19, issue #2180).

Design §0.17: ``/query`` is the most LLM-affecting surface v2 adds, and the
relay / broker / auth / custody seams each have deterministic failure modes unit
tests must pin *before* third-party agents amplify them. Individual issues ship
their own tests (the relay's live suite is ``tests/unit/test_daemon_relay.py``;
``/query`` is ``hub/agents/email/python/tests/test_query_route.py``); THIS suite
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
merges, without editing the skip logic. Two of the four have since LANDED and
now run real assertions here:

- **model-slot broker** (V2-11 / #2151, LANDED) — load serialization + interactive
  priority (``test_broker_serializes_concurrent_model_loads``).
- **migration idempotency** (V2-13 / #2155, LANDED) — one-time ``~/.gaia`` data
  migration that is a no-op on second run (``test_migration_is_idempotent``).

Still pending merge (skip auto-flips when their source lands):

- **secret-file delivery** (V2-3 / #2149) — fd/0600-file delivery of the launch
  secret (today it is a plain env var; the stronger delivery is not merged).
- **custody per-agent scoping** (V2-12 / #2153) — ``/host/v1/*`` with A-cannot-
  read-B scoping.

Run just this suite (the CI job of AC1)::

    python -m pytest -m distributed_seams
"""

from __future__ import annotations

import importlib.util
import json
import time

import pytest

pytestmark = pytest.mark.distributed_seams


def _wait_until(predicate, timeout: float = 2.0, interval: float = 0.005) -> None:
    """Poll *predicate* until true or raise — no arbitrary sleeps in threaded tests."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(interval)
    raise AssertionError(f"condition not met within {timeout}s")


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
def test_broker_serializes_concurrent_model_loads():
    """Two agents requesting *different* models must serialize through the single
    Lemonade slot (never both hold it at once → the race-evict CLAUDE.md documents),
    and an interactive lease must jump ahead of a *queued* background one."""
    import threading

    from gaia.daemon.broker import LeasePriority, ModelSlotBroker

    broker = ModelSlotBroker()

    # -- Serialization: while agent A holds the slot for model-A, agent B's
    #    request for model-B must WAIT, not evict A. One lease at a time. --
    lease_a = broker.acquire("model-A", holder="agentA")
    assert broker.snapshot()["active"]["model"] == "model-A"

    granted_b: list = []
    b_queued = threading.Event()

    def _acquire_b():
        granted_b.append(
            broker.acquire(
                "model-B",
                holder="agentB",
                priority=LeasePriority.INTERACTIVE,
                on_wait=lambda _reason: b_queued.set(),
                timeout=5.0,
            )
        )

    tb = threading.Thread(target=_acquire_b, daemon=True)
    tb.start()
    assert b_queued.wait(timeout=2.0), "B's request never queued behind the held slot"
    # A still solely holds the slot — B did not race-evict it.
    snap = broker.snapshot()
    assert snap["active"]["model"] == "model-A"
    assert [w["model"] for w in snap["waiting"]] == ["model-B"]
    assert granted_b == []

    # Releasing A hands the slot to B: loads are serialized, never concurrent.
    broker.release(lease_a.lease_id)
    tb.join(timeout=3.0)
    assert not tb.is_alive(), "B never acquired after A released"
    assert len(granted_b) == 1 and granted_b[0].model == "model-B"
    broker.release(granted_b[0].lease_id)

    # -- Interactive priority: a foreground turn jumps ahead of a background job
    #    already QUEUED for the slot (priority beats arrival order). --
    gate = broker.acquire("model-hold", holder="host")  # occupy the slot
    order: list = []
    order_lock = threading.Lock()

    def _worker(model, holder, priority, queued_evt):
        lease = broker.acquire(
            model,
            holder=holder,
            priority=priority,
            on_wait=lambda _reason: queued_evt.set(),
            timeout=5.0,
        )
        with order_lock:
            order.append(holder)
        broker.release(lease.lease_id)

    bg_queued, fg_queued = threading.Event(), threading.Event()
    bg = threading.Thread(
        target=_worker,
        args=("bg-model", "bg", LeasePriority.BACKGROUND, bg_queued),
        daemon=True,
    )
    bg.start()
    # Background enqueues FIRST — so a FIFO broker would grant it first.
    assert bg_queued.wait(timeout=2.0)
    _wait_until(lambda: [w["holder"] for w in broker.snapshot()["waiting"]] == ["bg"])

    fg = threading.Thread(
        target=_worker,
        args=("fg-model", "fg", LeasePriority.INTERACTIVE, fg_queued),
        daemon=True,
    )
    fg.start()
    assert fg_queued.wait(timeout=2.0)
    _wait_until(
        lambda: {w["holder"] for w in broker.snapshot()["waiting"]} == {"bg", "fg"}
    )

    # Both queued; free the slot. The interactive request must win despite the
    # background one arriving earlier.
    broker.release(gate.lease_id)
    bg.join(timeout=3.0)
    fg.join(timeout=3.0)
    assert not bg.is_alive() and not fg.is_alive()
    assert order == ["fg", "bg"], f"expected interactive-first, got {order}"


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
def test_migration_is_idempotent(tmp_path, monkeypatch):
    """Run the migration twice against a real, populated pre-v2 ``~/.gaia``
    fixture: the first run relocates the legacy stores into custody, the second
    is a no-op that never rewrites them (the schema stamp gates the re-run)."""
    from gaia.agents.base.memory_store import MemoryStore
    from gaia.daemon import migrate
    from gaia.ui.database import ChatDatabase

    # Isolate BOTH the legacy root and the v2 custody host dir under tmp so the
    # test never touches the developer's real state (cold-state discipline).
    legacy = tmp_path / "gaia"
    monkeypatch.setenv("GAIA_CONFIG_DIR", str(legacy))
    monkeypatch.setenv("GAIA_DAEMON_HOME", str(legacy / "host"))

    # Seed a populated pre-v2 install with the ACTUAL store classes an upgrader
    # has on disk — not a hand-rolled stand-in.
    db = ChatDatabase(db_path=str(legacy / "chat" / "gaia_chat.db"))
    try:
        session_id = db.create_session(title="pre-v2 chat")["id"]
        db.add_message(session_id, role="user", content="hello from before v2")
    finally:
        db.close()
    store = MemoryStore(db_path=legacy / "memory.db")
    try:
        store.store(category="preference", content="user prefers dark mode")
    finally:
        store.close()

    first = migrate.run_migrations()
    assert first["status"] == migrate.MigrationState.MIGRATED
    assert first["from_version"] == 0
    assert first["to_version"] == migrate.SCHEMA_VERSION
    assert migrate.custody_sessions_db().exists()
    assert migrate.custody_memory_db().exists()

    # Fingerprint the migrated custody store; a second run must not rewrite it.
    stamp_before = migrate.schema_stamp_path().read_text(encoding="utf-8")
    sessions_dst = migrate.custody_sessions_db()
    mtime_before = sessions_dst.stat().st_mtime_ns

    second = migrate.run_migrations()
    assert second["status"] == migrate.MigrationState.CURRENT
    assert second["applied"] == []
    assert second["from_version"] == migrate.SCHEMA_VERSION
    # Idempotent: the already-migrated store and the stamp are untouched.
    assert sessions_dst.stat().st_mtime_ns == mtime_before
    assert migrate.schema_stamp_path().read_text(encoding="utf-8") == stamp_before


# ---------------------------------------------------------------------------
# Coverage manifest — one place to SEE what this suite guards vs. defers. Keeps
# the "covered vs skipped-pending-merge" split honest and greppable.
# ---------------------------------------------------------------------------


def test_seam_coverage_manifest_reflects_reality():
    """Pin the covered-vs-pending split to the CURRENT tree so it can never
    silently drift: the LIVE seam probes must equal the explicit expected map
    below. When a pending seam merges its probe flips, this assertion FAILS, and
    whoever merged it must flip the manifest here and light up the real test —
    exactly the forcing function a hardcoded ``True`` cannot provide."""
    covered = {
        "relay_vocabulary_coherence": True,  # always
        "relay_synthetic_error_coherence": True,  # always
        "auth_leg2_bearer_via_env": _module_exists("gaia.daemon.sidecars.manager"),
        "model_broker_v2_11": _BROKER_MODULE is not None,
        "migration_idempotency_v2_13": _MIGRATION_MODULE is not None,
    }
    pending = {
        "secret_file_delivery_v2_3": not _SECRET_FILE_DELIVERY,
        "custody_scoping_v2_12": not _CUSTODY_MOUNTED,
    }

    # Expected as of this tree: Leg-2 auth (LANDED #1980), the model-slot broker
    # (LANDED V2-11 #2151), and the ~/.gaia migration (LANDED V2-13 #2155) are
    # all covered with real assertions above. Secret-file delivery (V2-3 #2149)
    # and custody scoping (V2-12 #2153) are NOT merged, so each is still pending.
    # Any drift is a real event — a pending seam merged (flip it to covered and
    # implement its test) or a landed module moved (fix its probe). Do NOT soften
    # these to make the test pass.
    assert covered == {
        "relay_vocabulary_coherence": True,
        "relay_synthetic_error_coherence": True,
        "auth_leg2_bearer_via_env": True,
        "model_broker_v2_11": True,
        "migration_idempotency_v2_13": True,
    }
    assert pending == {
        "secret_file_delivery_v2_3": True,
        "custody_scoping_v2_12": True,
    }
    # The manifest stays JSON-serializable so front-doors / CI can emit it.
    assert isinstance(json.dumps({"covered": covered, "pending": pending}), str)
