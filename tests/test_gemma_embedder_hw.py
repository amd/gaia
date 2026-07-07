# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Hardware validation for the NPU-native FLM embedder (#1744).

Unlike ``tests/unit/test_npu_flm_embedder.py`` (fully mocked), these tests run
``embed-gemma-300m-FLM`` on a **real** FLM/NPU-enabled Lemonade Server and prove
the feature's actual purpose: the FLM embedder produces valid vectors on the NPU
and stays co-resident with the FLM chat model (no NPU<->Vulkan eviction).

Gating: ``embed-gemma-300m-FLM`` is an FLM-recipe model that only exists on a
Lemonade Server running the FLM backend on Ryzen AI NPU hardware -- it is not in
the mainline llamacpp/Vulkan catalog. Every test here therefore SKIPS unless the
live server actually advertises the model, so the suite is safe on CPU/GPU boxes
and in Vulkan CI while still executing for real on an NPU runner.

Run on NPU hardware with:
    python -m pytest tests/test_npu_flm_embedder_hw.py -v -rs
"""

import math

import pytest

from gaia.agents.registry import get_embedding_model_for_device
from gaia.llm.lemonade_client import LemonadeClient, LemonadeClientError

pytestmark = [pytest.mark.integration, pytest.mark.real_model]

# The NPU profile's embedder id, resolved from the same source the agent uses so
# a rename in the registry can't silently desync this test from production.
FLM_EMBEDDER = get_embedding_model_for_device("npu")  # "embed-gemma-300m-FLM"
FLM_CHAT_MODEL = "gemma4-it-e2b-FLM"  # NPU profile chat model (registry.py)

# EmbeddingGemma 300M is 768-dim; the agent derives this at runtime rather than
# hardcoding it, so a mismatch here means the wrong model/vector space loaded.
EXPECTED_DIM = 768


def _catalog_ids(client: LemonadeClient) -> set:
    """Model ids the live server advertises (empty set if unreachable)."""
    try:
        catalog = client.list_models(show_all=True)
    except LemonadeClientError:
        return set()
    data = catalog.get("data", catalog) if isinstance(catalog, dict) else catalog
    ids = set()
    for entry in data or []:
        if isinstance(entry, dict) and entry.get("id"):
            ids.add(entry["id"])
    return ids


@pytest.fixture(scope="module")
def client():
    return LemonadeClient()


@pytest.fixture
def npu_embedder(client, require_lemonade):
    """Skip unless the FLM embedder is available on the live server, then load it.

    ``require_lemonade`` skips when no server is up; this then skips when the
    server is up but not FLM/NPU-enabled (the Vulkan-catalog case).
    """
    if FLM_EMBEDDER not in _catalog_ids(client):
        pytest.skip(
            f"{FLM_EMBEDDER} not in the live Lemonade catalog -- requires an "
            "FLM/NPU-enabled server on Ryzen AI hardware"
        )
    # Built-in FLM model: pull/load by name, no recipe (#1655-safe).
    client.load_model(FLM_EMBEDDER, auto_download=True, prompt=False)
    return FLM_EMBEDDER


class TestFlmEmbedderOnNpu:
    def test_single_embedding_is_valid(self, client, npu_embedder):
        """One text -> one finite, non-zero, correctly-sized vector."""
        resp = client.embeddings(
            "resilient local AI on the NPU", model=npu_embedder, timeout=120
        )

        assert isinstance(resp, dict) and "data" in resp
        assert len(resp["data"]) == 1
        vec = resp["data"][0]["embedding"]
        assert len(vec) == EXPECTED_DIM, f"expected {EXPECTED_DIM}-dim, got {len(vec)}"
        assert all(isinstance(x, float) and math.isfinite(x) for x in vec)
        assert any(x != 0.0 for x in vec), "embedding must not be all zeros"

    def test_batch_embedding_shapes(self, client, npu_embedder):
        """A batch returns one same-dim vector per input, in order."""
        texts = [
            "The NPU runs the FLM embedder.",
            "Vulkan runs the GGUF embedder.",
            "Chat and embeddings stay co-resident.",
        ]
        resp = client.embeddings(texts, model=npu_embedder, timeout=180)

        assert len(resp["data"]) == len(texts)
        dims = {len(item["embedding"]) for item in resp["data"]}
        assert dims == {EXPECTED_DIM}, f"inconsistent/unexpected dims: {dims}"

    def test_embedding_is_deterministic(self, client, npu_embedder):
        """Same input -> identical vector (no sampling in embeddings)."""
        text = "determinism check"
        a = client.embeddings([text], model=npu_embedder, timeout=120)["data"][0][
            "embedding"
        ]
        b = client.embeddings([text], model=npu_embedder, timeout=120)["data"][0][
            "embedding"
        ]
        assert a == b

    def test_distinct_texts_differ(self, client, npu_embedder):
        """Different inputs -> different vectors (model is actually encoding)."""
        a = client.embeddings(["apples"], model=npu_embedder, timeout=120)["data"][0][
            "embedding"
        ]
        b = client.embeddings(
            ["quantum chromodynamics"], model=npu_embedder, timeout=120
        )["data"][0]["embedding"]
        assert a != b


class TestFlmCoresidency:
    """The core #1744 guarantee: the FLM chat model and FLM embedder are both
    resident at once, so a chat turn does not evict the embedder (and vice
    versa) the way the Vulkan GGUF embedder did on a shared-memory APU."""

    def test_chat_and_embedder_coresident(self, client, npu_embedder):
        if FLM_CHAT_MODEL not in _catalog_ids(client):
            pytest.skip(
                f"{FLM_CHAT_MODEL} not in the live catalog -- cannot test co-residency"
            )

        client.load_model(
            FLM_CHAT_MODEL, auto_download=True, prompt=False, ctx_size=32768
        )
        # Touch the embedder after the chat model loads; on the old Vulkan path
        # this is exactly the call that evicted the FLM chat model.
        client.load_model(npu_embedder, auto_download=True, prompt=False)

        health = client._send_request("GET", f"{client.base_url}/health", timeout=15)
        loaded = health.get("all_models_loaded") or []
        loaded_ids = {
            (
                (m.get("id") or m.get("model_name") or m.get("model"))
                if isinstance(m, dict)
                else m
            )
            for m in loaded
        }

        assert FLM_EMBEDDER in loaded_ids, (
            f"{FLM_EMBEDDER} not resident after loading the chat model "
            f"(loaded={sorted(str(i) for i in loaded_ids)}) -- NPU<->Vulkan eviction regressed"
        )
        assert FLM_CHAT_MODEL in loaded_ids, (
            f"{FLM_CHAT_MODEL} was evicted when the embedder loaded "
            f"(loaded={sorted(str(i) for i in loaded_ids)})"
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s", "-rs"])
