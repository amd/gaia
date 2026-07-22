# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Unit spec for broker-wiring the direct model-load surfaces (#2248).

V2-11 (#2151) put a lease around ``_ensure_model_loaded`` only, so the surfaces
that call ``load_model`` directly — the UI backend's startup preload and
per-request pre-flight, the RAG embedder warm-up, CLI runs — still raced the
single Lemonade model slot. This spec pins the three things that fix:

1. **The chokepoint.** ``LemonadeClient.load_model`` itself takes the lease, so
   every direct caller is covered without having to remember to wrap itself.
2. **Re-entrancy.** Callers that wrap a multi-step ``unload``→``load`` sequence
   in an outer lease must not self-deadlock on the inner one — the broker grants
   exactly one lease at a time.
3. **Host-side discovery.** Only daemon-*spawned* processes inherit the broker
   URL, so host-side processes must attach to a live daemon themselves or the
   lease code never activates.

Serialization is proven against a REAL :class:`ModelSlotBroker` (its true
queueing/priority logic) with only the HTTP hop faked — a mock that merely
records "a lease was requested" would prove invocation, not mutual exclusion
(CLAUDE.md: assert the call is valid, not just that it happened).
"""

from __future__ import annotations

import os
import threading
import time
from typing import List, Optional, Tuple

import pytest

from gaia.daemon import broker_client
from gaia.daemon.broker import ModelSlotBroker
from gaia.daemon.constants import (
    BROKER_TOKEN_ENV_VAR,
    BROKER_URL_ENV_VAR,
)


@pytest.fixture(autouse=True)
def _clean_broker_env(monkeypatch):
    """Every test starts AND ends with no broker configured and no lease held.

    Two hygiene points, both learned the hard way:

    - Explicit ``os.environ`` cleanup rather than ``monkeypatch.delenv``:
      :func:`attach_broker_env` sets the vars directly, and monkeypatch records
      no undo for a variable that was absent when it was asked to delete it — so
      the address one test attaches to would leak into every later test in the
      session and make unrelated model-load specs dial a dead broker.
    - ``attach`` is stubbed to "no daemon" and discovery is reset to opt-OUT.
      Discovery is process-global, so a test that enables it would otherwise
      leak into later tests, which would then find whatever daemon happens to be
      running on the developer's machine — green in CI, red on a dev box.
    """
    import os

    def _clear():
        for var in (BROKER_URL_ENV_VAR, BROKER_TOKEN_ENV_VAR):
            os.environ.pop(var, None)

    _clear()
    broker_client._held.depth = 0
    broker_client._held.model = None
    monkeypatch.setattr(broker_client, "_discovery_enabled", False)
    monkeypatch.setattr(broker_client, "_attached", None)
    monkeypatch.setattr(broker_client, "_broker_unsupported", False)
    monkeypatch.setattr("gaia.daemon.client.attach", lambda *a, **k: None)
    yield
    _clear()
    broker_client._held.depth = 0
    broker_client._held.model = None


class _LocalBroker:
    """Routes ``broker_client``'s lease calls into a real in-process broker.

    Substitutes only the HTTP hop; the queueing, priority ordering and
    one-lease-at-a-time invariant under test are the broker's own.
    """

    def __init__(self, monkeypatch, *, url: str = "http://127.0.0.1:0"):
        self.broker = ModelSlotBroker()
        self.acquired: List[Tuple[str, str]] = []  # (model, priority)
        self.released: List[str] = []
        self._leases = {}
        monkeypatch.setenv(BROKER_URL_ENV_VAR, url)
        monkeypatch.setenv(BROKER_TOKEN_ENV_VAR, "test-token")
        monkeypatch.setattr(broker_client, "acquire_lease", self._acquire)
        monkeypatch.setattr(broker_client, "release_lease", self._release)

    def _acquire(self, model, *, priority=None, timeout=None, request_timeout=310.0):
        prio = priority or "background"
        self.acquired.append((model, prio))
        lease = self.broker.acquire(model, priority=prio, holder="host")
        self._leases[lease.lease_id] = lease
        return {
            "lease_id": lease.lease_id,
            "model": lease.model,
            "waited": False,
            "switching": False,
        }

    def _release(self, lease_id, *, request_timeout=10.0):
        self.released.append(lease_id)
        self.broker.release(lease_id)


# ── Re-entrancy ──────────────────────────────────────────────────────────────


def test_nested_model_lease_does_not_self_deadlock(monkeypatch):
    """An outer lease + an inner one on the same thread must acquire ONCE.

    The broker hands out one lease at a time, so a genuine second acquire here
    would block forever waiting for the lease this very thread holds. This is
    the load-bearing guarantee for the unload→load callers.
    """
    local = _LocalBroker(monkeypatch)

    with broker_client.model_lease("m1", priority="background") as outer:
        assert outer is not None
        assert broker_client.holding_lease()
        with broker_client.model_lease("m1") as inner:
            assert inner is None, "nested lease must be a pass-through no-op"

    assert len(local.acquired) == 1
    assert len(local.released) == 1
    assert not broker_client.holding_lease()


def test_lease_released_and_depth_cleared_on_exception(monkeypatch):
    """An exception inside the block must not leak the lease or the depth flag."""
    local = _LocalBroker(monkeypatch)

    with pytest.raises(RuntimeError):
        with broker_client.model_lease("m1"):
            raise RuntimeError("boom")

    assert len(local.released) == 1
    assert not broker_client.holding_lease()
    # The slot is genuinely free again — a fresh acquire returns immediately.
    with broker_client.model_lease("m2") as lease:
        assert lease is not None


def test_reentrancy_is_per_thread(monkeypatch):
    """Holding the slot on one thread must NOT let another thread skip the queue."""
    local = _LocalBroker(monkeypatch)
    other_saw_lease: List[Optional[dict]] = []
    released = threading.Event()

    with broker_client.model_lease("m1"):

        def _other():
            # Not a no-op: this thread holds nothing, so it must really queue.
            with broker_client.model_lease("m2") as lease:
                other_saw_lease.append(lease)
            released.set()

        t = threading.Thread(target=_other, daemon=True)
        t.start()
        # It must still be blocked while we hold the slot.
        assert not released.wait(timeout=0.3), "second thread bypassed the lease"

    t.join(timeout=5)
    assert released.is_set()
    assert other_saw_lease and other_saw_lease[0] is not None
    assert len(local.acquired) == 2


# ── The load_model chokepoint ────────────────────────────────────────────────


class _FakeClient:
    """LemonadeClient with the HTTP load body replaced, lease path intact."""

    def __init__(self, on_load=None):
        from gaia.llm.lemonade_client import LemonadeClient

        self.cls = LemonadeClient
        self._on_load = on_load
        self.loads: List[str] = []

    def build(self, monkeypatch):
        client = object.__new__(self.cls)
        client.model_lease_priority = "background"
        import logging

        client.log = logging.getLogger("test.lemonade")

        def _leased(model_name, **kwargs):
            self.loads.append(model_name)
            if self._on_load is not None:
                self._on_load(model_name)
            return {"status": "loaded", "model": model_name}

        monkeypatch.setattr(client, "_load_model_leased", _leased)
        return client


def test_load_model_takes_a_lease(monkeypatch):
    """A DIRECT load_model call — the #2248 bypass — must hold a lease."""
    local = _LocalBroker(monkeypatch)
    fake = _FakeClient()
    client = fake.build(monkeypatch)

    client.load_model("Gemma-4-E4B-it-GGUF", ctx_size=8192, prompt=False)

    assert fake.loads == ["Gemma-4-E4B-it-GGUF"]
    assert local.acquired == [("Gemma-4-E4B-it-GGUF", "background")]
    assert len(local.released) == 1


def test_load_model_holds_the_lease_across_the_load(monkeypatch):
    """The lease must span the load, not merely bracket it."""
    local = _LocalBroker(monkeypatch)
    held_during_load: List[bool] = []
    fake = _FakeClient(
        on_load=lambda m: held_during_load.append(broker_client.holding_lease())
    )
    client = fake.build(monkeypatch)

    client.load_model("m1")

    assert held_during_load == [True]


def test_load_model_is_a_noop_without_a_broker(monkeypatch):
    """Standalone (no daemon, no broker URL) must be completely unaffected.

    This is the absence of a broker, not a fallback around a failed one: with no
    daemon there are no sidecars and nothing else holds the slot.
    """
    calls: List[str] = []
    monkeypatch.setattr(
        broker_client,
        "acquire_lease",
        lambda *a, **k: calls.append("acquire") or {},
    )
    fake = _FakeClient()
    client = fake.build(monkeypatch)

    client.load_model("m1")

    assert fake.loads == ["m1"], "the load must still happen"
    assert calls == [], "no broker configured → no lease traffic"


def test_load_model_fails_loudly_when_broker_unreachable(monkeypatch):
    """Broker configured but dead → raise, never a direct race-evicting load.

    The unreachable broker is simulated at ``acquire_lease`` rather than by
    dialling a closed port: ``requests`` honours ``HTTP_PROXY``/``ALL_PROXY``, so
    a real connect would route through a dev box's proxy and hang on the 310s
    request timeout instead of failing fast.
    """
    monkeypatch.setenv(BROKER_URL_ENV_VAR, "http://127.0.0.1:1")
    monkeypatch.setenv(BROKER_TOKEN_ENV_VAR, "test-token")

    def _dead(*a, **k):
        raise broker_client.BrokerUnavailableError("daemon is down")

    monkeypatch.setattr(broker_client, "acquire_lease", _dead)
    fake = _FakeClient()
    client = fake.build(monkeypatch)

    with pytest.raises(broker_client.BrokerUnavailableError):
        client.load_model("m1")

    assert fake.loads == [], "must NOT fall back to a direct load"


def test_load_model_forwards_every_kwarg_through_the_lease(monkeypatch):
    """The lease wrapper must not drop load parameters.

    ``load_model`` re-lists its kwargs into ``_load_model_leased`` by hand.
    Silently dropping ``ctx_size`` there would reintroduce the #1030 class of
    bug — a model loaded at the wrong context window — with every other test
    still green.
    """
    _LocalBroker(monkeypatch)
    seen = {}

    from gaia.llm.lemonade_client import LemonadeClient

    client = object.__new__(LemonadeClient)
    client.model_lease_priority = "background"
    import logging

    client.log = logging.getLogger("test.lemonade")
    monkeypatch.setattr(
        client,
        "_load_model_leased",
        lambda model_name, **kwargs: seen.update({"model": model_name}, **kwargs),
    )

    client.load_model(
        "m1",
        timeout=123,
        auto_download=True,
        llamacpp_args="--ubatch-size 2048",
        ctx_size=65536,
        save_options=True,
        prompt=False,
        load_retries=7,
    )

    assert seen["model"] == "m1"
    assert seen["timeout"] == 123
    assert seen["auto_download"] is True
    assert seen["llamacpp_args"] == "--ubatch-size 2048"
    assert seen["ctx_size"] == 65536
    assert seen["save_options"] is True
    assert seen["prompt"] is False
    assert seen["load_retries"] == 7


def test_nested_lease_for_a_different_model_is_loud(monkeypatch):
    """Folding a different model into an outer lease must raise, not pass.

    The broker booked the slot against the outer model; quietly loading a
    different one under that lease desyncs its accounting — a race the broker
    would report as prevented.
    """
    _LocalBroker(monkeypatch)

    with broker_client.model_lease("model-a"):
        with pytest.raises(broker_client.BrokerUnavailableError, match="model-a"):
            with broker_client.model_lease("model-b"):
                pass


def test_outer_lease_plus_load_model_acquires_once(monkeypatch):
    """The unload→load pattern used by RAG / the UI pre-flight.

    The caller takes an outer lease to make the pair atomic; load_model's own
    lease folds into it. Exactly one acquire — a second would deadlock.
    """
    local = _LocalBroker(monkeypatch)
    fake = _FakeClient()
    client = fake.build(monkeypatch)

    with broker_client.model_lease("embed", priority="background"):
        client.load_model("embed", llamacpp_args="--ubatch-size 2048")

    assert local.acquired == [("embed", "background")]
    assert fake.loads == ["embed"]


# ── Serialization: the property the broker exists to provide ────────────────


def test_concurrent_direct_loads_serialize(monkeypatch):
    """Two threads doing DIRECT load_model calls must never overlap.

    This is the #2248 regression in miniature: pre-fix both threads would enter
    the load body concurrently and race-evict each other's model.
    """
    local = _LocalBroker(monkeypatch)
    concurrent = []
    active = {"n": 0}
    guard = threading.Lock()

    def _on_load(_model):
        with guard:
            active["n"] += 1
            concurrent.append(active["n"])
        time.sleep(0.05)  # widen the window a racing thread would slip into
        with guard:
            active["n"] -= 1

    fake = _FakeClient(on_load=_on_load)
    errors: List[BaseException] = []

    def _worker(model):
        try:
            client = fake.build(monkeypatch)
            client.load_model(model)
        except BaseException as e:  # surfaced below; never swallowed
            errors.append(e)

    threads = [
        threading.Thread(target=_worker, args=(f"model-{i}",), daemon=True)
        for i in range(4)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)

    assert not errors, f"worker(s) failed: {errors}"
    assert len(fake.loads) == 4, "every load must still complete"
    assert max(concurrent) == 1, f"loads overlapped: peak concurrency {max(concurrent)}"
    assert len(local.acquired) == 4 and len(local.released) == 4


# ── The inference lease (#2380) ──────────────────────────────────────────────


def _bare_chat_client():
    """A LemonadeClient carrying only the attrs the chat paths touch."""
    import logging

    from gaia.llm.lemonade_client import LemonadeClient

    client = object.__new__(LemonadeClient)
    client.model_lease_priority = "background"
    client.base_url = "http://127.0.0.1:0/api/v1"
    client.api_key = None
    client.log = logging.getLogger("test.lemonade")
    return client


def test_non_streaming_chat_holds_the_lease_across_the_inference_request(monkeypatch):
    """The lease must span the /chat/completions call, not just the load (#2380).

    Before the fix the lease closed as soon as ``_ensure_model_loaded`` returned,
    so a second sidecar could acquire the freed slot and evict this model while
    the request below was still generating. This pins the lease held for BOTH.
    """
    local = _LocalBroker(monkeypatch)
    client = _bare_chat_client()

    held: dict = {}
    monkeypatch.setattr(
        client,
        "_ensure_model_loaded",
        lambda model, auto_download=True: held.__setitem__(
            "ensure", broker_client.holding_lease()
        ),
    )

    class _Resp:
        status_code = 200

        @staticmethod
        def json():
            held["request"] = broker_client.holding_lease()
            return {"choices": [{"message": {"content": "hi"}}]}

    monkeypatch.setattr(
        "gaia.llm.lemonade_client.requests.post", lambda *a, **k: _Resp()
    )

    result = client.chat_completions(
        model="m1", messages=[{"role": "user", "content": "hi"}]
    )

    assert result["choices"][0]["message"]["content"] == "hi"
    assert held == {"ensure": True, "request": True}
    # One lease for the whole load+inference; released after the return.
    assert len(local.acquired) == 1 and len(local.released) == 1
    assert not broker_client.holding_lease()


def test_streaming_chat_holds_the_lease_across_the_whole_generation(monkeypatch):
    """Streaming must keep the slot leased for every chunk, not just the load.

    The lease is acquired on first iteration and released when the stream is
    exhausted, so no other sidecar can evict the model mid-stream (#2380).
    """
    local = _LocalBroker(monkeypatch)
    client = _bare_chat_client()
    monkeypatch.setattr(
        client, "_ensure_model_loaded", lambda model, auto_download=True: None
    )

    held_per_chunk: List[bool] = []

    class _Delta:
        role = "assistant"
        content = "x"

    class _Choice:
        index = 0
        finish_reason = None
        delta = _Delta()

    class _Chunk:
        id = "0"
        created = 0
        model = "m1"
        choices = [_Choice()]

    class _FakeStream:
        def __iter__(self):
            for _ in range(3):
                held_per_chunk.append(broker_client.holding_lease())
                yield _Chunk()

    class _FakeOpenAI:
        def __init__(self, *a, **k):
            self.chat = self

        @property
        def completions(self):
            return self

        def create(self, **kwargs):
            return _FakeStream()

    monkeypatch.setattr("gaia.llm.lemonade_client.OpenAI", _FakeOpenAI)

    gen = client.chat_completions(
        model="m1", messages=[{"role": "user", "content": "hi"}], stream=True
    )
    # Lazy: nothing acquired until the consumer starts iterating.
    assert not local.acquired
    chunks = list(gen)

    assert len(chunks) == 3
    assert held_per_chunk == [True, True, True]
    assert len(local.acquired) == 1 and len(local.released) == 1
    assert not broker_client.holding_lease()


# ── The wired call sites ─────────────────────────────────────────────────────


def test_rag_embedder_warmup_holds_one_lease_across_unload_and_load(monkeypatch):
    """The RAG warm-up's unload→load pair must be atomic under ONE lease.

    ``load_model``'s own lease covers the load but NOT the gap between the
    unload and it. Without the outer lease another process can take the slot in
    that window; without re-entrancy the inner acquire would deadlock. This
    pins both, and fails if the outer lease at rag/sdk.py is removed.
    """
    local = _LocalBroker(monkeypatch)
    held_during: List[Tuple[str, bool]] = []

    class _Client:
        def unload_model(self, model, ignore_if_not_loaded=False):
            held_during.append(("unload", broker_client.holding_lease()))

        def load_model(self, model, **kwargs):
            held_during.append(("load", broker_client.holding_lease()))

    from gaia.rag.sdk import RAGSDK

    sdk = object.__new__(RAGSDK)
    sdk.embedder = None
    sdk.log = __import__("logging").getLogger("test.rag")
    sdk.llm_client = _Client()

    class _Config:
        embedding_model = "nomic-embed-text-v1-GGUF"  # not a "user." model

    sdk.config = _Config()

    sdk._load_embedder()

    assert held_during == [("unload", True), ("load", True)]
    # ONE lease for the whole swap — not one per operation.
    assert len(local.acquired) == 1
    assert local.acquired[0][1] == "background", "a warm-up must not be interactive"


def test_host_entry_points_opt_into_broker_discovery():
    """The CLI and the UI backend must declare themselves broker participants.

    Discovery is opt-in, so deleting the single ``enable_broker_discovery()``
    call from either entry point silently restores #2248 with every behavioural
    test still green. This pins the wiring itself.
    """
    import ast
    import inspect

    import gaia.cli
    import gaia.ui.server

    for module, func_name in ((gaia.cli, "main"), (gaia.ui.server, "create_app")):
        source = inspect.getsource(module)
        tree = ast.parse(source)
        target = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == func_name
        )
        called = {
            node.func.id
            for node in ast.walk(target)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        assert "enable_broker_discovery" in called, (
            f"{module.__name__}.{func_name} no longer opts into broker "
            "discovery — its model loads would silently bypass the broker"
        )


# ── End-to-end over real HTTP ────────────────────────────────────────────────


def test_host_side_lease_works_against_a_real_daemon_over_http(monkeypatch):
    """Drive a REAL daemon over HTTP: does the host credential actually work?

    Every other test here fakes the HTTP hop, which proves the client *asks* for
    a lease but never that the daemon would *accept* the request — the exact gap
    CLAUDE.md calls out (#1655). This stands up the real ``/host/v1`` route on an
    ephemeral port and checks both halves of the contract: the discovered daemon
    client token authenticates as the ``"host"`` caller, and four concurrent
    loads genuinely serialize end to end.
    """
    uvicorn = pytest.importorskip("uvicorn")

    import socket

    from gaia.daemon.app import create_app
    from gaia.daemon.broker import ModelSlotBroker

    token = "e2e-daemon-client-token"
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()

    app = create_app(
        token=token,
        port=port,
        pid=os.getpid(),
        started_at=time.time(),
        broker=ModelSlotBroker(),
    )
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        deadline = time.time() + 15
        while not server.started and time.time() < deadline:
            time.sleep(0.05)
        assert server.started, "test daemon did not come up"

        class _Inst:
            base_url = f"http://127.0.0.1:{port}"
            token = "e2e-daemon-client-token"

        monkeypatch.setattr("gaia.daemon.client.attach", lambda *a, **k: _Inst())
        monkeypatch.setattr(broker_client, "_discovery_enabled", True)

        granted: List[str] = []
        active = {"n": 0}
        peak = {"v": 0}
        guard = threading.Lock()
        errors: List[BaseException] = []

        def _worker(i):
            try:
                with broker_client.model_lease(f"model-{i}") as lease:
                    assert lease is not None, "expected a real lease from the daemon"
                    with guard:
                        active["n"] += 1
                        peak["v"] = max(peak["v"], active["n"])
                    time.sleep(0.1)
                    with guard:
                        active["n"] -= 1
                    granted.append(lease["lease_id"])
            except BaseException as e:  # surfaced below; never swallowed
                errors.append(e)

        workers = [
            threading.Thread(target=_worker, args=(i,), daemon=True) for i in range(4)
        ]
        for w in workers:
            w.start()
        for w in workers:
            w.join(timeout=60)

        assert not errors, f"worker(s) failed: {errors}"
        assert len(granted) == 4, "the daemon must grant every lease"
        assert len(set(granted)) == 4, "each grant must be a distinct lease"
        assert peak["v"] == 1, f"loads overlapped: peak concurrency {peak['v']}"
    finally:
        server.should_exit = True
        thread.join(timeout=15)


# ── Host-side discovery ──────────────────────────────────────────────────────


class _FakeInstance:
    base_url = "http://127.0.0.1:54321"
    token = "daemon-client-token"


def test_attach_broker_env_adopts_a_live_daemon(monkeypatch):
    """Host-side processes aren't daemon-spawned, so they must discover it."""
    monkeypatch.setattr("gaia.daemon.client.attach", lambda *a, **k: _FakeInstance())

    assert broker_client.attach_broker_env() is True
    assert broker_client.broker_configured()
    assert broker_client._broker_base() == _FakeInstance.base_url
    # The daemon client token is what /host/v1 resolves to the "host" caller.
    assert broker_client._credential() == _FakeInstance.token


def test_model_lease_attaches_lazily_to_a_late_daemon(monkeypatch):
    """A daemon that starts AFTER this process must still capture its loads.

    The UI backend outlives many daemon lifecycles. Discovering only at process
    start would leave every subsequent load in a long-lived host process
    permanently unbrokered — the #2248 bug reintroduced through the back door.
    """
    live = {"yes": False}
    monkeypatch.setattr(broker_client, "_discovery_enabled", True)
    monkeypatch.setattr(
        "gaia.daemon.client.attach",
        lambda *a, **k: _FakeInstance() if live["yes"] else None,
    )
    acquired: List[str] = []
    monkeypatch.setattr(
        broker_client,
        "acquire_lease",
        lambda model, **k: acquired.append(model) or {"lease_id": "L1"},
    )
    monkeypatch.setattr(broker_client, "release_lease", lambda *a, **k: None)

    # No daemon yet — unbrokered, and nothing cached that would block attaching.
    with broker_client.model_lease("m1") as lease:
        assert lease is None
    assert acquired == []

    # Daemon comes up; the very next load must route through it.
    live["yes"] = True
    with broker_client.model_lease("m2") as lease:
        assert lease is not None
    assert acquired == ["m2"]


def test_library_use_never_probes_for_a_daemon(monkeypatch):
    """Without an explicit opt-in, a load must not go looking for a daemon.

    Importing LemonadeClient must not make an arbitrary script — or a unit test
    on a developer box that happens to run a daemon — silently join that
    daemon's broker. Entry points opt in; library code does not.
    """

    def _boom(*a, **k):
        raise AssertionError("library code must not probe for a daemon")

    monkeypatch.setattr("gaia.daemon.client.attach", _boom)
    fake = _FakeClient()
    client = fake.build(monkeypatch)

    client.load_model("m1")

    assert fake.loads == ["m1"]


def test_enable_broker_discovery_does_not_probe(monkeypatch):
    """Opting in only sets a flag — the probe is deferred to the first load.

    This is what makes it safe for ``gaia <anything>`` to opt in unconditionally
    without adding daemon-probe latency to commands that never load a model.
    """

    def _boom(*a, **k):
        raise AssertionError("enable_broker_discovery must not probe")

    monkeypatch.setattr("gaia.daemon.client.attach", _boom)
    monkeypatch.setattr(broker_client, "_discovery_enabled", False)

    broker_client.enable_broker_discovery()

    assert broker_client.discovery_enabled()
    assert not broker_client.broker_configured()


def test_reattaches_after_a_daemon_restart_rotates_its_token(monkeypatch):
    """A restarted daemon must not permanently brick a long-lived host process.

    `gaia daemon restart` rotates both port and token. The UI backend outlives
    that, so without re-discovery every subsequent load would 401 forever — the
    daemon's own 401 text tells the caller to re-attach.
    """

    class _New:
        base_url = "http://127.0.0.1:60000"
        token = "rotated-token"

    monkeypatch.setattr(broker_client, "_discovery_enabled", True)
    monkeypatch.setattr(broker_client, "_attached", ("http://127.0.0.1:54321", "stale"))

    monkeypatch.setattr("gaia.daemon.client.attach", lambda *a, **k: _New())
    attempts = []

    def _acquire(model, **kwargs):
        attempts.append(broker_client._attached)
        if len(attempts) == 1:
            raise broker_client.BrokerUnavailableError("HTTP 401: token rotated")
        return {"lease_id": "L-new"}

    monkeypatch.setattr(broker_client, "acquire_lease", _acquire)
    monkeypatch.setattr(broker_client, "release_lease", lambda *a, **k: None)

    with broker_client.model_lease("m1") as lease:
        assert lease == {"lease_id": "L-new"}

    assert len(attempts) == 2, "should retry once after re-discovering"
    assert broker_client._attached == (_New.base_url, _New.token)


def test_daemon_without_a_broker_route_does_not_brick_model_loading(monkeypatch):
    """A daemon predating the broker (404) must not fail every model load.

    Found by driving a real daemon: an older build has no lease route, and
    treating its 404 as a hard failure would break model loading outright for
    anyone who hadn't restarted their daemon. No lease route means no arbiter —
    the absence of a broker, like no daemon at all — so loads proceed, warned.
    Sticky, so it warns and re-probes once rather than on every load.
    """
    monkeypatch.setattr(broker_client, "_discovery_enabled", True)
    monkeypatch.setattr("gaia.daemon.client.attach", lambda *a, **k: _FakeInstance())
    calls = []

    def _not_found(model, **kwargs):
        calls.append(model)
        raise broker_client.BrokerUnavailableError("Not Found", status_code=404)

    monkeypatch.setattr(broker_client, "acquire_lease", _not_found)
    fake = _FakeClient()
    client = fake.build(monkeypatch)

    client.load_model("m1")
    client.load_model("m2")

    assert fake.loads == ["m1", "m2"], "loads must still happen"
    assert calls == ["m1"], "must not re-probe a daemon known to have no broker"
    assert broker_client._broker_unsupported


def test_a_404_does_not_trigger_a_pointless_reattach(monkeypatch):
    """Only stale-credential failures justify re-discovery.

    Retrying a 404 re-probes the daemon and logs a misleading "it likely
    rotated its token" — noise that sends the reader after the wrong cause.
    """
    monkeypatch.setattr(broker_client, "_discovery_enabled", True)
    monkeypatch.setattr(broker_client, "_attached", ("http://127.0.0.1:1", "tok"))
    attaches = []
    monkeypatch.setattr(
        "gaia.daemon.client.attach",
        lambda *a, **k: attaches.append(1) or _FakeInstance(),
    )
    monkeypatch.setattr(
        broker_client,
        "acquire_lease",
        lambda model, **k: (_ for _ in ()).throw(
            broker_client.BrokerUnavailableError("Not Found", status_code=404)
        ),
    )

    with broker_client.model_lease("m1") as lease:
        assert lease is None

    assert attaches == [], "a 404 must not trigger re-discovery"


def test_server_error_propagates_instead_of_reattaching(monkeypatch):
    """A broker that IS there and broken (5xx) must fail loudly, not retry."""
    monkeypatch.setattr(broker_client, "_discovery_enabled", True)
    monkeypatch.setattr(broker_client, "_attached", ("http://127.0.0.1:1", "tok"))
    monkeypatch.setattr("gaia.daemon.client.attach", lambda *a, **k: _FakeInstance())
    monkeypatch.setattr(
        broker_client,
        "acquire_lease",
        lambda model, **k: (_ for _ in ()).throw(
            broker_client.BrokerUnavailableError("boom", status_code=500)
        ),
    )
    fake = _FakeClient()
    client = fake.build(monkeypatch)

    with pytest.raises(broker_client.BrokerUnavailableError):
        client.load_model("m1")

    assert fake.loads == [], "must not fall back to a direct load"


def test_version_skewed_daemon_does_not_break_model_loading(monkeypatch):
    """A stale-MAJOR daemon must not hard-fail commands that merely load a model.

    `gaia llm` has no business dying because a skewed daemon was left running;
    the load proceeds unbrokered and the skew is reported loudly instead.
    """
    from gaia.daemon.errors import DaemonVersionError

    def _skewed(*a, **k):
        raise DaemonVersionError("daemon speaks v1, client speaks v2")

    monkeypatch.setattr(broker_client, "_discovery_enabled", True)
    monkeypatch.setattr("gaia.daemon.client.attach", _skewed)
    fake = _FakeClient()
    client = fake.build(monkeypatch)

    client.load_model("m1")

    assert fake.loads == ["m1"]


def test_daemon_token_is_never_exported_to_the_environment(monkeypatch):
    """The discovered credential must stay in-process.

    The daemon client token is not broker-scoped — it also guards daemon
    shutdown and the sidecar control plane. Exporting it would hand that
    authority to every child process and leave it inspectable in the process
    environment.
    """
    import os

    monkeypatch.setattr("gaia.daemon.client.attach", lambda *a, **k: _FakeInstance())

    assert broker_client.attach_broker_env() is True
    assert broker_client.broker_configured()
    assert BROKER_TOKEN_ENV_VAR not in os.environ
    assert BROKER_URL_ENV_VAR not in os.environ
    # ...but it IS usable for signing a lease request.
    assert broker_client._credential() == _FakeInstance.token


def test_attach_broker_env_without_a_daemon_leaves_env_clean(monkeypatch):
    """No daemon → no sidecars → nothing to serialize against. Stay unbrokered."""
    import os

    monkeypatch.setattr("gaia.daemon.client.attach", lambda *a, **k: None)

    assert broker_client.attach_broker_env() is False
    assert not broker_client.broker_configured()
    assert BROKER_URL_ENV_VAR not in os.environ
    assert BROKER_TOKEN_ENV_VAR not in os.environ


def test_attach_broker_env_preserves_an_inherited_config(monkeypatch):
    """A daemon-spawned process already has its credential — don't clobber it.

    Sidecars get a launch-token file/env from the manager; overwriting it with
    the daemon client token would change the caller identity the broker derives.
    """
    monkeypatch.setenv(BROKER_URL_ENV_VAR, "http://127.0.0.1:9999")
    monkeypatch.setenv(BROKER_TOKEN_ENV_VAR, "sidecar-launch-token")

    def _boom(*a, **k):
        raise AssertionError("must not probe when already configured")

    monkeypatch.setattr("gaia.daemon.client.attach", _boom)

    import os

    assert broker_client.attach_broker_env() is True
    assert os.environ[BROKER_URL_ENV_VAR] == "http://127.0.0.1:9999"
    assert os.environ[BROKER_TOKEN_ENV_VAR] == "sidecar-launch-token"
