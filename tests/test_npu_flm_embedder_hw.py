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

Set ``GAIA_REQUIRE_FLM=1`` to turn that skip into a failure. CI does, because a
runner that is meant to have the FLM backend and silently skips reports green
while validating nothing.

Run on NPU hardware with:
    python -m pytest tests/test_npu_flm_embedder_hw.py -v -rs
"""

import math
import os

import pytest

from gaia.agents.registry import get_embedding_model_for_device
from gaia.llm.lemonade_client import LemonadeClient

pytestmark = [pytest.mark.integration, pytest.mark.real_model]

# The NPU profile's embedder id, resolved from the same source the agent uses so
# a rename in the registry can't silently desync this test from production.
FLM_EMBEDDER = get_embedding_model_for_device("npu")  # "embed-gemma-300m-FLM"
FLM_CHAT_MODEL = "gemma4-it-e2b-FLM"  # NPU profile chat model (registry.py)

# EmbeddingGemma 300M is 768-dim; the agent derives this at runtime rather than
# hardcoding it, so a mismatch here means the wrong model/vector space loaded.
EXPECTED_DIM = 768

# Repeat embeddings may drift in the last bits on the NPU; a different vector
# (e.g. a swapped model or another input's row) lands far below this.
DETERMINISM_MIN_COSINE = 0.999


def cosine(a, b) -> float:
    """Cosine similarity; fails loudly on mismatched or zero-norm vectors."""
    if len(a) != len(b):
        raise ValueError(f"vector lengths differ: {len(a)} vs {len(b)}")
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        raise ValueError("cosine is undefined for a zero-norm vector")
    return sum(x * y for x, y in zip(a, b)) / (norm_a * norm_b)


def _loaded_model_ids(client: LemonadeClient) -> list:
    """Model ids resident on the server, from ``/health``."""
    health = client._send_request("GET", f"{client.base_url}/health", timeout=15)
    loaded = health.get("all_models_loaded") or []
    return sorted(
        str(
            (m.get("id") or m.get("model_name") or m.get("model"))
            if isinstance(m, dict)
            else m
        )
        for m in loaded
    )


def _catalog_ids(client: LemonadeClient) -> set:
    """Model ids the live server advertises.

    Deliberately does not swallow the error: an unreachable server must not be
    reported as "the FLM backend is missing", which is what an empty set would
    make the caller say.
    """
    catalog = client.list_models(show_all=True)
    data = catalog.get("data", catalog) if isinstance(catalog, dict) else catalog
    ids = set()
    for entry in data or []:
        if isinstance(entry, dict) and entry.get("id"):
            ids.add(entry["id"])
    return ids


def _require_in_catalog(client: LemonadeClient, model: str) -> None:
    """Skip when the server has no FLM backend -- or fail, if CI asked us to."""
    if model in _catalog_ids(client):
        return
    reason = (
        f"{model} not in the live Lemonade catalog -- FLM models are contributed "
        "by the flm:npu backend, which is not installed on this server"
    )
    # Fail closed on any truthy spelling: a gate meant to stop silent passes
    # must not revert to skipping because someone wrote "true" instead of "1".
    if os.getenv("GAIA_REQUIRE_FLM", "").strip().lower() in {"1", "true", "yes", "on"}:
        pytest.fail(reason)
    pytest.skip(f"{reason}; requires an FLM/NPU-enabled server on Ryzen AI hardware")


@pytest.fixture(scope="module")
def client():
    return LemonadeClient()


@pytest.fixture
def npu_embedder(client, require_lemonade):
    """Skip unless the FLM embedder is available on the live server, then load it.

    ``require_lemonade`` skips when no server is up; this then skips when the
    server is up but not FLM/NPU-enabled (the Vulkan-catalog case).
    """
    _require_in_catalog(client, FLM_EMBEDDER)
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
        """Same input -> the same vector, up to NPU precision drift."""
        text = "determinism check"
        a = client.embeddings([text], model=npu_embedder, timeout=120)["data"][0][
            "embedding"
        ]
        b = client.embeddings([text], model=npu_embedder, timeout=120)["data"][0][
            "embedding"
        ]
        sim = cosine(a, b)
        assert sim >= DETERMINISM_MIN_COSINE, (
            f"repeat embedding of the same text diverged: cosine={sim:.4f} "
            f"(need >= {DETERMINISM_MIN_COSINE}); "
            f"loaded models={_loaded_model_ids(client)}"
        )

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
        _require_in_catalog(client, FLM_CHAT_MODEL)

        client.load_model(
            FLM_CHAT_MODEL, auto_download=True, prompt=False, ctx_size=32768
        )
        # Touch the embedder after the chat model loads; on the old Vulkan path
        # this is exactly the call that evicted the FLM chat model.
        client.load_model(npu_embedder, auto_download=True, prompt=False)

        loaded_ids = _loaded_model_ids(client)

        assert FLM_EMBEDDER in loaded_ids, (
            f"{FLM_EMBEDDER} not resident after loading the chat model "
            f"(loaded={loaded_ids}) -- NPU<->Vulkan eviction regressed"
        )
        assert FLM_CHAT_MODEL in loaded_ids, (
            f"{FLM_CHAT_MODEL} was evicted when the embedder loaded "
            f"(loaded={loaded_ids})"
        )


#: Six questions, each with a paraphrase. The paraphrases are the "documents".
_PARAPHRASES = [
    (
        "How do I change my password?",
        "Steps to reset the password on your account.",
    ),
    (
        "What do penguins eat?",
        "Penguins feed on fish, krill and squid they catch while diving.",
    ),
    (
        "Why did revenue grow last quarter?",
        "Sales rose in the third quarter because European orders increased.",
    ),
    (
        "How long should I boil an egg?",
        "Cook eggs in boiling water for about nine minutes for a firm yolk.",
    ),
    (
        "Which port does the web server listen on?",
        "The gateway accepts HTTP connections on port 8080.",
    ),
    (
        "When does the library open on weekends?",
        "On Saturdays and Sundays the library opens at 10 a.m.",
    ),
]


def _finds_each_paraphrase(client: LemonadeClient, model: str) -> list:
    """Questions whose nearest paraphrase isn't their own (empty when all are)."""

    def vectors(texts):
        data = client.embeddings(texts, model=model, timeout=300)["data"]
        out = []
        for item in sorted(data, key=lambda d: d["index"]):
            v = item["embedding"]
            n = math.sqrt(sum(x * x for x in v))
            out.append([x / n for x in v])
        return out

    # A first call after load can differ from the rest (FLM 1.0.7); warm up.
    vectors(["warm-up"])
    questions = vectors([q for q, _ in _PARAPHRASES])
    answers = vectors([a for _, a in _PARAPHRASES])
    missed = []
    for i, q in enumerate(questions):
        scores = [sum(x * y for x, y in zip(q, a)) for a in answers]
        if max(range(len(scores)), key=scores.__getitem__) != i:
            missed.append(_PARAPHRASES[i][0])
    return missed


class TestSearchQuality:
    """Vectors of the right shape prove nothing about search. Each question's
    nearest paraphrase must be its own; chance gets all six right ~1 in 47,000."""

    def test_the_gpu_embedder_finds_each_paraphrase(self, client, require_lemonade):
        from gaia.agents.registry import DEFAULT_EMBEDDING_MODEL

        client.load_model(DEFAULT_EMBEDDING_MODEL, auto_download=True, prompt=False)
        assert _finds_each_paraphrase(client, DEFAULT_EMBEDDING_MODEL) == []

    @pytest.mark.xfail(
        strict=True,
        reason="embed-gemma-300m-FLM ranks text no better than chance on FastFlowLM "
        "1.0.7, so GAIA embeds on the GPU. Passing means a newer build fixed it: "
        "prove it on a full recall run, then add npu to "
        "GaiaConfig.TESTED_EMBEDDING_DEVICES and drop this marker.",
    )
    def test_the_npu_embedder_finds_each_paraphrase(self, client, npu_embedder):
        assert _finds_each_paraphrase(client, npu_embedder) == []


class TestGpuEmbedderBesideNpuChat:
    """An NPU PC embeds on the GPU; Lemonade holds one model per type, so the
    GPU embedder must not evict the FLM chat model."""

    def test_npu_chat_and_gpu_embedder_coresident(self, client, require_lemonade):
        from gaia.agents.registry import DEFAULT_EMBEDDING_MODEL

        # A fresh --profile npu install has these two, not the FLM embedder.
        _require_in_catalog(client, FLM_CHAT_MODEL)
        client.load_model(
            FLM_CHAT_MODEL, auto_download=True, prompt=False, ctx_size=32768
        )
        client.embeddings(["warm"], model=DEFAULT_EMBEDDING_MODEL, timeout=300)
        health = client._send_request("GET", f"{client.base_url}/health", timeout=15)
        loaded_ids = {
            m.get("model_name") or m.get("id")
            for m in health.get("all_models_loaded") or []
            if isinstance(m, dict)
        }
        assert FLM_CHAT_MODEL in loaded_ids, (
            f"the GPU embedder evicted {FLM_CHAT_MODEL} "
            f"(loaded={sorted(str(i) for i in loaded_ids)})"
        )
        assert DEFAULT_EMBEDDING_MODEL.removeprefix("user.") in {
            str(i).removeprefix("user.") for i in loaded_ids
        }


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s", "-rs"])
