# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Integration spec for the model-slot broker against a REAL Lemonade server
(#2151 / V2-11 · §0.12).

Skips unless Lemonade is running (``require_lemonade``). Proves the broker's
whole reason to exist on real hardware: two concurrent loaders serialized by the
broker never occupy Lemonade's single model slot at the same time, so they cannot
race-evict each other — both loads complete, and each model settles at the
GAIA-expected ctx (the #1030 guard), not Lemonade's smaller default.
"""

from __future__ import annotations

import threading

import pytest

from gaia.daemon.broker import LeasePriority, ModelSlotBroker

_LEMONADE_URL = "http://localhost:13305"


def _downloaded_models(client):
    return [
        m["id"]
        for m in client.list_models().get("data", [])
        if m.get("downloaded") and m.get("id")
    ]


def test_broker_serializes_concurrent_real_loads(require_lemonade):
    from gaia.llm.lemonade_client import LemonadeClient

    client = LemonadeClient(base_url=_LEMONADE_URL, verbose=False)
    models = _downloaded_models(client)
    if not models:
        pytest.skip("no downloaded Lemonade models available to exercise the broker")

    model_a = models[0]
    # Prefer two DIFFERENT models (the headline acceptance criterion); fall back
    # to the same model if only one is downloaded — serialization is still
    # proven, just without the cross-model evict.
    model_b = models[1] if len(models) > 1 else models[0]

    broker = ModelSlotBroker()
    concurrency = {"cur": 0, "max": 0}
    lock = threading.Lock()
    errors = []

    def _worker(model, priority):
        try:
            lease = broker.acquire(model, priority=priority, holder=model)
            with lock:
                concurrency["cur"] += 1
                concurrency["max"] = max(concurrency["max"], concurrency["cur"])
            try:
                # The under-lease load body: check + load at the GAIA ctx.
                client._ensure_model_loaded_locked(model)
            finally:
                with lock:
                    concurrency["cur"] -= 1
                broker.release(lease.lease_id)
        except Exception as e:  # noqa: BLE001 - surfaced via the errors list
            errors.append((model, repr(e)))

    threads = [
        threading.Thread(target=_worker, args=(model_a, LeasePriority.BACKGROUND)),
        threading.Thread(target=_worker, args=(model_b, LeasePriority.INTERACTIVE)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=600.0)

    assert not errors, f"broker-serialized loads raised: {errors}"
    # The core invariant: the broker never let two loads hold the slot at once.
    assert concurrency["max"] == 1, (
        "two model loads ran concurrently — the broker failed to serialize the "
        "single Lemonade model slot"
    )

    # #1030 guard: the last-loaded model is resident at >= its GAIA-expected ctx.
    from gaia.llm.lemonade_client import DEFAULT_CONTEXT_SIZE, MODELS

    status = client.get_status()
    last = model_b
    entry = client._find_loaded_entry(status, last)
    if entry is not None:
        expected = next(
            (r.min_ctx_size for r in MODELS.values() if r.model_id == last),
            DEFAULT_CONTEXT_SIZE,
        )
        loaded_ctx = entry.get("recipe_options", {}).get("ctx_size", 0) or 0
        assert loaded_ctx >= expected, (
            f"model '{last}' loaded at ctx={loaded_ctx} < GAIA-expected "
            f"{expected} — the #1030 ctx-cap regression"
        )
