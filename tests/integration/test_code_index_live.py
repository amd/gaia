# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
Integration tests for CodeIndexSDK against a real Lemonade Server.

These exercise the paths a unit test can't credibly fake: real embeddings
from the configured model (``user.embeddinggemma-300m-GGUF`` on port 13305),
incremental-reindex correctness end to end, and single-slot model eviction.
All tests skip cleanly via ``require_lemonade`` when Lemonade isn't running.
"""

import os
import shutil

import pytest

try:
    from gaia.code_index.sdk import CodeIndexConfig, CodeIndexSDK

    SDK_AVAILABLE = True
except ImportError as e:
    SDK_AVAILABLE = False
    IMPORT_ERROR = str(e)

try:
    import faiss  # noqa: F401

    FAISS_AVAILABLE = True
except ImportError:
    FAISS_AVAILABLE = False


pytestmark = [
    pytest.mark.skipif(
        not SDK_AVAILABLE,
        reason="code_index deps not available (pip install -e '.[rag]')",
    ),
    pytest.mark.skipif(not FAISS_AVAILABLE, reason="faiss not installed"),
]

_EMBEDDING_MODEL = "user.embeddinggemma-300m-GGUF"

#: Honors the same env override every LemonadeClient consumer does.
_BASE_URL = os.getenv("LEMONADE_BASE_URL", "http://localhost:13305/api/v1")


def make_sdk(tmp_path) -> "CodeIndexSDK":
    config = CodeIndexConfig(
        repo_path=str(tmp_path / "repo"),
        cache_dir=str(tmp_path / "cache"),
        embedding_model=_EMBEDDING_MODEL,
        embedding_base_url=_BASE_URL,
    )
    return CodeIndexSDK(config)


class TestIncrementalReindexCorrectness:
    def test_unchanged_files_reuse_embeddings_changed_files_reembed(
        self, require_lemonade, tmp_path
    ):
        """The canonical correctness proof for incremental reindex:
        index two files, change one, reindex, and verify (a) the reused
        chunk's vector is bit-identical to its first embedding (proving
        real reuse, not a lucky re-embed match) and (b) the changed file's
        new content is actually searchable — a stale reuse would keep
        serving the old answer forever.
        """
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "alpha.py").write_text(
            "def alpha_only_marker_xyz():\n    return 'alpha'\n", encoding="utf-8"
        )
        (repo / "beta.py").write_text(
            "def beta_original_marker_abc():\n    return 'beta v1'\n", encoding="utf-8"
        )

        sdk = make_sdk(tmp_path)
        result1 = sdk.index_repository()
        assert result1.chunks_created == 2
        assert result1.files_indexed == 2  # both new on first index

        # Capture the reused chunk's vector before the change.
        alpha_vec_before = sdk._faiss_index.reconstruct(
            next(
                i
                for i, c in enumerate(sdk._metadata["chunks"])
                if c["file_path"] == "alpha.py"
            )
        ).copy()

        # Change only beta.py.
        (repo / "beta.py").write_text(
            "def beta_changed_marker_def():\n    return 'beta v2 totally different'\n",
            encoding="utf-8",
        )

        result2 = sdk.index_repository()
        assert result2.chunks_created == 2  # still 2 chunks total
        assert result2.files_indexed == 1  # only beta.py re-parsed/re-embedded

        chunks_after = sdk._metadata["chunks"]
        alpha_idx_after = next(
            i for i, c in enumerate(chunks_after) if c["file_path"] == "alpha.py"
        )
        beta_idx_after = next(
            i for i, c in enumerate(chunks_after) if c["file_path"] == "beta.py"
        )

        # (a) Reused chunk's embedding must be bit-identical — proof of
        # real reuse rather than a coincidental re-embed match.
        alpha_vec_after = sdk._faiss_index.reconstruct(alpha_idx_after).copy()
        assert (alpha_vec_before == alpha_vec_after).all()

        # And its content/symbol metadata is untouched.
        assert chunks_after[alpha_idx_after]["symbol_name"] == "alpha_only_marker_xyz"

        # (b) Changed file's chunk reflects the NEW content, not stale data.
        assert chunks_after[beta_idx_after]["symbol_name"] == "beta_changed_marker_def"
        assert "beta v2" in chunks_after[beta_idx_after]["content"]

        # (c) Search for the new content actually finds it (proves the new
        # embedding, not just the metadata, was updated).
        results = sdk.search("beta_changed_marker_def totally different", top_k=3)
        assert any(r.chunk.file_path == "beta.py" for r in results)

    def test_second_index_with_no_changes_reembeds_nothing(
        self, require_lemonade, tmp_path
    ):
        """A no-op reindex must not touch the embedding backend at all —
        that's the whole point of the file-hash cache.
        """
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "a.py").write_text("def a(): return 1\n", encoding="utf-8")

        sdk = make_sdk(tmp_path)
        sdk.index_repository()

        # Second call: nothing changed, so no embedding calls should happen.
        # Prove it by breaking the embedder and confirming index_repository
        # still succeeds (it never needed to call it).
        sdk._embedder = None
        sdk._llm_client = None

        def _boom(*a, **kw):
            raise AssertionError("embedder was invoked for an unchanged file")

        sdk._load_embedder = _boom  # only called when there's something to embed

        result = sdk.index_repository()
        assert result.files_indexed == 0
        assert result.chunks_created == 1


class TestCacheKeying:
    def test_different_repo_paths_get_different_cache_dirs(self, tmp_path):
        repo_a = tmp_path / "repo_a"
        repo_b = tmp_path / "repo_b"
        repo_a.mkdir()
        repo_b.mkdir()

        sdk_a = CodeIndexSDK(
            CodeIndexConfig(repo_path=str(repo_a), cache_dir=str(tmp_path / "cache"))
        )
        sdk_b = CodeIndexSDK(
            CodeIndexConfig(repo_path=str(repo_b), cache_dir=str(tmp_path / "cache"))
        )

        assert sdk_a._cache_dir != sdk_b._cache_dir

    def test_same_repo_path_is_stable_across_instances(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()

        sdk_1 = CodeIndexSDK(
            CodeIndexConfig(repo_path=str(repo), cache_dir=str(tmp_path / "cache"))
        )
        sdk_2 = CodeIndexSDK(
            CodeIndexConfig(repo_path=str(repo), cache_dir=str(tmp_path / "cache"))
        )

        assert sdk_1._cache_dir == sdk_2._cache_dir


class TestSingleSlotEviction:
    def test_index_repository_reloads_embedder_after_eviction_by_another_model(
        self, require_lemonade, tmp_path
    ):
        """Lemonade's embedding slot holds exactly one model. Loading a
        different embedder must not corrupt or hang the next index — the
        SDK should reload its own model on demand and index correctly.
        """
        from gaia.llm.lemonade_client import LemonadeClient

        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "a.py").write_text(
            "def evict_marker_fn(): return 42\n", encoding="utf-8"
        )

        client = LemonadeClient(base_url=_BASE_URL)
        try:
            client.unload_model(_EMBEDDING_MODEL)
        except Exception as e:  # noqa: BLE001 — precondition, not the assertion
            # "Not loaded yet" is fine, but say so — a swallowed connection
            # error here would let the test proceed against a dead server and
            # fail later with a misleading message.
            print(f"unload_model precondition: {e}")

        # Evict by loading a different embedding model into the single slot.
        other_model = "nomic-embed-text-v2-moe-GGUF"
        try:
            client.load_model(other_model)
        except Exception as e:
            pytest.skip(f"could not load a competing embedder to force eviction: {e}")

        sdk = make_sdk(tmp_path)
        result = sdk.index_repository()

        assert result.chunks_created == 1
        results = sdk.search("evict_marker_fn", top_k=1)
        assert results and results[0].chunk.file_path == "a.py"
