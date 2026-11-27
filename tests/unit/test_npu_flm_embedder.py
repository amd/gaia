# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Unit tests for the NPU-native FLM embedder wiring (#1744).

Covers:
- The device->embedder resolver and DeviceConfig.embedding_model field.
- The embed-gemma-300m-FLM entry in the Lemonade MODELS registry.
- The NPU init profile downloading the FLM embedder.
- MemoryMixin deriving the embedding dimension from the live embedder
  (no hardcoded 768) and invalidating stored vectors when the embedder
  model changes (same dim, different vector space).

All tests mock the embedder; no Lemonade server or NPU required.
"""

from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from gaia.agents.base.memory_store import MemoryStore
from tests.unit.faiss_support import require_faiss

# ---------------------------------------------------------------------------
# Device -> embedder resolution
# ---------------------------------------------------------------------------


class TestDeviceEmbedderResolution:
    def test_npu_uses_flm_embedder(self):
        from gaia.agents.registry import get_embedding_model_for_device

        assert get_embedding_model_for_device("npu") == "embed-gemma-300m-FLM"

    @pytest.mark.parametrize("device", ["gpu", "cpu", None, "unknown-device"])
    def test_non_npu_uses_the_embedder_setup_downloads(self, device):
        """An embedder `gaia init` never downloads fails on first use (#4449)."""
        from gaia.agents.registry import get_embedding_model_for_device
        from gaia.installer.init_command import INIT_PROFILES

        embedder = get_embedding_model_for_device(device)
        assert embedder == "user.embeddinggemma-300m-GGUF"
        assert embedder in INIT_PROFILES["gaia"]["models"]

    @pytest.mark.parametrize(
        "keys, embedder",
        [
            # The NPU build's search isn't proven, so an NPU PC embeds on the GPU.
            ({"default_device": "npu"}, "user.embeddinggemma-300m-GGUF"),
            ({"default_device": "gpu"}, "user.embeddinggemma-300m-GGUF"),
            # A saved choice from before it was withdrawn still loads.
            ({"embedding_device": "npu"}, "embed-gemma-300m-FLM"),
        ],
    )
    def test_one_embedder_per_pc(self, keys, embedder, tmp_path, monkeypatch):
        """One per PC: a chat on the other device must not change it (#6008)."""
        import json

        from gaia import config as config_mod
        from gaia.agents.registry import machine_embedding_model

        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps(keys), encoding="utf-8")
        monkeypatch.setattr(config_mod, "GAIA_CONFIG_FILE", config_file)
        assert machine_embedding_model() == embedder

    @pytest.mark.parametrize(
        "before, profile, after",
        [
            ("npu", "gaia", "npu"),  # the app's own setup on an NPU PC
            ("gpu", "gaia", "gpu"),
            ("npu", "npu", "npu"),
            ("gpu", "npu", "npu"),
            ("npu", "chat", "gpu"),  # a GPU-only profile chosen on purpose
        ],
    )
    def test_setup_keeps_an_npu_pcs_device(
        self, before, profile, after, tmp_path, monkeypatch
    ):
        """The app's own setup leaves an NPU PC's chat on the NPU."""
        import json

        from gaia import config as config_mod
        from gaia.config import GaiaConfig
        from gaia.installer.init_command import InitCommand

        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({"default_device": before}), encoding="utf-8")
        monkeypatch.setattr(config_mod, "GAIA_CONFIG_FILE", config_file)
        init = InitCommand.__new__(InitCommand)
        init.profile = profile
        init._record_chat_choice = lambda config: None
        GaiaConfig.update(init._apply_profile_to)
        assert GaiaConfig.load().default_device == after

    def test_device_config_carries_embedder(self):
        """The embedder choice lives next to the chat model in DeviceConfig."""
        from gaia.agents.registry import DEFAULT_DEVICE_CONFIGS

        by_device = {dc.device: dc for dc in DEFAULT_DEVICE_CONFIGS}
        assert by_device["npu"].embedding_model == "embed-gemma-300m-FLM"
        assert by_device["gpu"].embedding_model == "user.embeddinggemma-300m-GGUF"
        assert by_device["cpu"].embedding_model == "user.embeddinggemma-300m-GGUF"


# ---------------------------------------------------------------------------
# Lemonade model registry + download manifest
# ---------------------------------------------------------------------------


class TestModelRegistry:
    def test_flm_embedder_registered(self):
        from gaia.llm.lemonade_client import MODELS, ModelType

        mr = MODELS["embed-gemma-flm"]
        assert mr.model_id == "embed-gemma-300m-FLM"
        assert mr.model_type == ModelType.EMBEDDING
        # FLM/NPU server 500s on a tools payload, like the e2b chat model.
        assert mr.tool_calling is False

    def test_npu_init_profile_downloads_the_gpu_embedder(self):
        """An NPU PC embeds on the GPU, so setup must download that embedder."""
        from gaia.agents.registry import DEFAULT_EMBEDDING_MODEL
        from gaia.installer.init_command import INIT_PROFILES

        models = INIT_PROFILES["npu"]["models"]
        assert DEFAULT_EMBEDDING_MODEL in models
        assert "embed-gemma-300m-FLM" not in models
        # Chat model must remain so both pull on `gaia init --profile npu`.
        assert "gemma4-it-e4b-FLM" in models


# ---------------------------------------------------------------------------
# MemoryMixin: dynamic dim + embedder-change invalidation
# ---------------------------------------------------------------------------


class _FakeAgent:
    """Minimal host so MemoryMixin can be exercised without a real Agent."""

    def __init__(self):
        self._system_prompt_cache = None

    def register_tool(self, *a, **k):
        pass


def _make_host():
    from gaia.agents.base.memory import MemoryMixin

    class _Host(MemoryMixin, _FakeAgent):
        pass

    return _Host()


def _init_with_embedder(host, db_path, *, model, dim):
    """Run init_memory with a mock embedder of a given model id and dim."""
    from gaia.agents.base.memory import MemoryMixin

    vec = np.random.rand(dim).astype(np.float32)
    with (
        patch.object(MemoryMixin, "_get_embedder", return_value=MagicMock()),
        patch.object(MemoryMixin, "_embed_text", return_value=vec),
        patch.object(MemoryMixin, "_backfill_embeddings", return_value=0),
        patch.object(MemoryMixin, "init_system_context", return_value=None),
    ):
        host.init_memory(db_path=db_path, embedding_model=model)


class TestDynamicDim:
    def test_dim_derived_from_live_embedder(self, tmp_path, monkeypatch):
        """A 512-dim embedder yields a 512-dim FAISS index — no hardcoded 768."""
        monkeypatch.delenv("GAIA_MEMORY_DISABLED", raising=False)
        host = _make_host()
        _init_with_embedder(
            host, tmp_path / "memory.db", model="embed-gemma-300m-FLM", dim=512
        )
        assert host._embedding_dim == 512
        assert host._embedding_model == "embed-gemma-300m-FLM"
        # When faiss is available, the index is built at the derived dim, not
        # the module default. faiss is optional, so skip this leg without it.
        require_faiss()
        assert host._faiss_index is not None
        assert host._faiss_index.d == 512

    def test_default_embedder_when_unspecified(self, tmp_path, monkeypatch):
        monkeypatch.delenv("GAIA_MEMORY_DISABLED", raising=False)
        from gaia.agents.base.memory import EMBEDDING_MODEL

        host = _make_host()
        _init_with_embedder(host, tmp_path / "memory.db", model=None, dim=768)
        # Default GGUF embedder is EmbeddingGemma 300M.
        assert host._embedding_model == EMBEDDING_MODEL
        assert host._embedding_model == "user.embeddinggemma-300m-GGUF"
        assert host._embedding_dim == 768

    def test_zero_length_vector_degrades_memory(self, tmp_path, monkeypatch):
        """A 0-length embedding trips the guard and degrades memory loudly,
        rather than building a malformed index."""
        monkeypatch.delenv("GAIA_MEMORY_DISABLED", raising=False)
        from gaia.agents.base.memory import MemoryMixin

        host = _make_host()
        empty = np.empty(0, dtype=np.float32)
        with (
            patch.object(MemoryMixin, "_get_embedder", return_value=MagicMock()),
            patch.object(MemoryMixin, "_embed_text", return_value=empty),
            patch.object(MemoryMixin, "init_system_context", return_value=None),
        ):
            host.init_memory(
                db_path=tmp_path / "memory.db", embedding_model="broken-embedder"
            )
        # The RuntimeError is caught and memory is disabled for the session.
        assert host._memory_store is None


class TestEmbedderChangeInvalidation:
    def test_switching_embedder_clears_stored_vectors(self, tmp_path, monkeypatch):
        """Same dim, different model => stored vectors must be invalidated."""
        monkeypatch.delenv("GAIA_MEMORY_DISABLED", raising=False)
        db_path = tmp_path / "memory.db"

        # Seed a store as if a prior nomic session wrote a 768-dim vector.
        store = MemoryStore(db_path=db_path)
        kid = store.store(category="fact", content="the sky is blue")
        store.store_embedding(kid, np.zeros(768, dtype=np.float32).tobytes())
        store.set_embedder_id("nomic-embed-text-v2-moe-GGUF")
        assert store.get_embedding_coverage()["without_embedding"] == 0
        store.close()

        # New session on the FLM embedder (also 768-dim, different space).
        host = _make_host()
        _init_with_embedder(host, db_path, model="embed-gemma-300m-FLM", dim=768)

        # Marker updated and the stale vector was cleared for re-embedding.
        assert host._memory_store.get_embedder_id() == "embed-gemma-300m-FLM"
        assert host._memory_store.get_embedding_coverage()["without_embedding"] == 1

    def test_switch_then_backfill_repopulates(self, tmp_path, monkeypatch):
        """After a switch the cleared vectors are re-embedded with the new model
        (full round trip: clear -> backfill -> coverage restored)."""
        monkeypatch.delenv("GAIA_MEMORY_DISABLED", raising=False)
        from gaia.agents.base.memory import MemoryMixin

        db_path = tmp_path / "memory.db"
        store = MemoryStore(db_path=db_path)
        kid = store.store(category="fact", content="the sky is blue")
        store.store_embedding(kid, np.zeros(768, dtype=np.float32).tobytes())
        store.set_embedder_id("nomic-embed-text-v2-moe-GGUF")
        store.close()

        # Real backfill this time (only _embed_text is mocked).
        host = _make_host()
        vec = np.random.rand(768).astype(np.float32)
        with (
            patch.object(MemoryMixin, "_get_embedder", return_value=MagicMock()),
            patch.object(MemoryMixin, "_embed_text", return_value=vec),
            patch.object(MemoryMixin, "init_system_context", return_value=None),
        ):
            host.init_memory(db_path=db_path, embedding_model="embed-gemma-300m-FLM")

        cov = host._memory_store.get_embedding_coverage()
        assert cov["without_embedding"] == 0  # re-embedded with the new model
        assert host._memory_store.get_embedder_id() == "embed-gemma-300m-FLM"

    def test_same_embedder_keeps_vectors(self, tmp_path, monkeypatch):
        monkeypatch.delenv("GAIA_MEMORY_DISABLED", raising=False)
        db_path = tmp_path / "memory.db"

        store = MemoryStore(db_path=db_path)
        kid = store.store(category="fact", content="water is wet")
        store.store_embedding(kid, np.zeros(768, dtype=np.float32).tobytes())
        store.set_embedder_id("user.embeddinggemma-300m-GGUF")
        store.close()

        host = _make_host()
        with patch(
            "gaia.agents.base.memory_store.MemoryStore.clear_all_embeddings"
        ) as clear:
            _init_with_embedder(
                host, db_path, model="user.embeddinggemma-300m-GGUF", dim=768
            )
            clear.assert_not_called()

    def test_unstamped_legacy_vectors_are_reembedded(self, tmp_path, monkeypatch):
        """Memory written before the embedder was stamped holds nomic vectors;
        they must be cleared and re-embedded, not mixed with the new space."""
        monkeypatch.delenv("GAIA_MEMORY_DISABLED", raising=False)
        from gaia.agents.base.memory import MemoryMixin

        db_path = tmp_path / "memory.db"
        store = MemoryStore(db_path=db_path)
        kid = store.store(category="fact", content="the sky is blue")
        legacy = np.zeros(768, dtype=np.float32)
        store.store_embedding(kid, legacy.tobytes())
        assert store.get_embedder_id() is None
        store.close()

        host = _make_host()
        vec = np.ones(768, dtype=np.float32)
        with (
            patch.object(MemoryMixin, "_get_embedder", return_value=MagicMock()),
            patch.object(MemoryMixin, "_embed_text", return_value=vec),
            patch.object(MemoryMixin, "init_system_context", return_value=None),
        ):
            host.init_memory(
                db_path=db_path, embedding_model="user.embeddinggemma-300m-GGUF"
            )

        store = host._memory_store
        assert store.get_embedder_id() == "user.embeddinggemma-300m-GGUF"
        assert store.get_embedding_coverage()["without_embedding"] == 0
        (item,) = store.get_items_with_embeddings()
        assert not np.array_equal(
            np.frombuffer(item["embedding"], dtype=np.float32), legacy
        )


# ---------------------------------------------------------------------------
# MemoryStore embedder marker + clear
# ---------------------------------------------------------------------------


class TestMemoryStoreEmbedderMarker:
    def test_marker_round_trip(self, tmp_path):
        store = MemoryStore(db_path=tmp_path / "memory.db")
        try:
            assert store.get_embedder_id() is None  # fresh DB
            store.set_embedder_id("embed-gemma-300m-FLM")
            assert store.get_embedder_id() == "embed-gemma-300m-FLM"
        finally:
            store.close()

    def test_clear_all_embeddings(self, tmp_path):
        store = MemoryStore(db_path=tmp_path / "memory.db")
        try:
            kid = store.store(category="fact", content="hello")
            store.store_embedding(kid, np.zeros(768, dtype=np.float32).tobytes())
            assert store.get_embedding_coverage()["without_embedding"] == 0
            cleared = store.clear_all_embeddings()
            assert cleared == 1
            assert store.get_embedding_coverage()["without_embedding"] == 1
        finally:
            store.close()

    def test_reconcile_embedder(self, tmp_path):
        store = MemoryStore(db_path=tmp_path / "memory.db")
        try:
            assert store.reconcile_embedder("a") == 0  # fresh DB: stamp only
            assert store.get_embedder_id() == "a"
            kid = store.store(category="fact", content="hello")
            store.store_embedding(kid, np.zeros(768, dtype=np.float32).tobytes())
            assert store.reconcile_embedder("a") == 0  # unchanged: keep
            assert store.reconcile_embedder("b") == 1  # changed: clear
            assert store.get_embedder_id() == "b"
            assert store.get_embedding_coverage()["without_embedding"] == 1
        finally:
            store.close()

    def test_reconcile_clears_unstamped_vectors(self, tmp_path):
        """Vectors with no stamp predate the marker (nomic era) — cleared."""
        store = MemoryStore(db_path=tmp_path / "memory.db")
        try:
            kid = store.store(category="fact", content="hello")
            store.store_embedding(kid, np.zeros(768, dtype=np.float32).tobytes())
            assert store.reconcile_embedder("user.embeddinggemma-300m-GGUF") == 1
            assert store.get_embedding_coverage()["without_embedding"] == 1
        finally:
            store.close()
