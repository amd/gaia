# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A code index whose files don't belong together fails loudly (#3540).

Search used to return [] (count mismatch) or confidently wrong hits (same
count, wrong metadata) while get_status reported the cache as indexed.
"""

import hashlib
import json
import pathlib
import shutil

import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("faiss", reason="Real vector-index tests require faiss-cpu")

from gaia.code_index.sdk import (  # noqa: E402
    CodeIndexCacheError,
    CodeIndexConfig,
    CodeIndexSDK,
)

DIM = 16


def _vector(text: str) -> list:
    seed = int.from_bytes(hashlib.sha256(text.encode("utf-8")).digest()[:4], "little")
    return np.random.default_rng(seed).random(DIM).astype("float32").tolist()


def _sdk(repo, cache, embedded=None):
    sdk = CodeIndexSDK(CodeIndexConfig(repo_path=str(repo), cache_dir=str(cache)))
    sdk._load_embedder = lambda: None

    def embed(batch):
        if embedded is not None:
            embedded.extend(batch)
        return [_vector(t) for t in batch]

    sdk._embed_batch_call = embed
    return sdk


def _repo(root, name, functions):
    repo = root / name
    repo.mkdir()
    for fname, body in functions.items():
        (repo / f"{fname}.py").write_text(
            f"def {fname}():\n    {body}\n", encoding="utf-8"
        )
    return repo


@pytest.fixture
def cache(tmp_path):
    return tmp_path / "cache"


@pytest.fixture
def repo(tmp_path):
    return _repo(
        tmp_path,
        "repo",
        {"clock_divider": "return 'divide the clock'", "uart": "return 'serial'"},
    )


def _built(repo, cache):
    sdk = _sdk(repo, cache)
    sdk.index_repository()
    return sdk


def test_a_fresh_index_is_fingerprinted_and_searchable(repo, cache):
    sdk = _built(repo, cache)
    meta = json.loads(sdk._meta_path.read_text(encoding="utf-8"))
    assert (
        meta["index_sha256"] == hashlib.sha256(sdk._index_path.read_bytes()).hexdigest()
    )

    fresh = _sdk(repo, cache)
    status = fresh.get_status()
    assert status["indexed"] is True
    assert "error" not in status
    assert fresh.search("clock divider")


def test_same_size_metadata_from_another_index_is_caught(tmp_path, repo, cache):
    sdk = _built(repo, cache)
    other = _repo(tmp_path, "other", {"alpha": "return 1", "beta": "return 2"})
    other_sdk = _built(other, cache)
    other_meta = json.loads(other_sdk._meta_path.read_text(encoding="utf-8"))
    assert len(other_meta["chunks"]) == len(
        json.loads(sdk._meta_path.read_text(encoding="utf-8"))["chunks"]
    )
    shutil.copyfile(other_sdk._meta_path, sdk._meta_path)

    fresh = _sdk(repo, cache)
    with pytest.raises(CodeIndexCacheError, match="re-index|Re-index"):
        fresh.search("clock divider")

    status = _sdk(repo, cache).get_status()
    assert status["indexed"] is False
    assert str(sdk._cache_dir) in status["error"]
    assert "Re-index" in status["error"]


def test_a_count_mismatch_raises_instead_of_returning_nothing(repo, cache):
    sdk = _built(repo, cache)
    meta = json.loads(sdk._meta_path.read_text(encoding="utf-8"))
    meta["chunks"] = meta["chunks"][:1]
    del meta["index_sha256"]
    sdk._meta_path.write_text(json.dumps(meta), encoding="utf-8")

    with pytest.raises(CodeIndexCacheError, match="vectors but the metadata"):
        _sdk(repo, cache).search("clock divider")
    assert _sdk(repo, cache).get_status()["indexed"] is False


def test_a_tampered_index_file_is_caught(repo, cache):
    sdk = _built(repo, cache)
    data = bytearray(sdk._index_path.read_bytes())
    data[-1] ^= 0xFF
    sdk._index_path.write_bytes(bytes(data))

    with pytest.raises(CodeIndexCacheError, match="does not match its metadata"):
        _sdk(repo, cache).search("uart")


def test_a_crash_between_the_two_renames_is_caught(repo, cache, monkeypatch):
    _built(repo, cache)
    # Same chunk count, different content: only a fingerprint can tell.
    (repo / "uart.py").write_text("def uart():\n    return 'rs232'\n", "utf-8")

    real_replace = pathlib.Path.replace
    calls = []

    def crash_on_second(self, target):
        calls.append(self.name)
        if len(calls) == 2:
            raise OSError("simulated crash")
        return real_replace(self, target)

    monkeypatch.setattr(pathlib.Path, "replace", crash_on_second)
    with pytest.raises(OSError, match="simulated crash"):
        _sdk(repo, cache).index_repository()
    monkeypatch.setattr(pathlib.Path, "replace", real_replace)

    assert calls[0].endswith(".json"), "metadata must be published first"
    with pytest.raises(CodeIndexCacheError):
        _sdk(repo, cache).search("uart")


def test_reindexing_repairs_an_inconsistent_cache(tmp_path, repo, cache):
    sdk = _built(repo, cache)
    other = _repo(tmp_path, "other", {"alpha": "return 1", "beta": "return 2"})
    shutil.copyfile(_built(other, cache)._meta_path, sdk._meta_path)

    embedded = []
    repair = _sdk(repo, cache, embedded)
    repair.index_repository()

    assert embedded, "nothing from the inconsistent cache may be reused"
    status = _sdk(repo, cache).get_status()
    assert status["indexed"] is True
    loaded = _sdk(repo, cache)
    assert loaded._ensure_index_loaded()
    for row, chunk_dict in enumerate(loaded._metadata["chunks"]):
        text = loaded._chunk_to_embed_text(loaded._dict_to_chunk(chunk_dict))
        np.testing.assert_array_equal(
            loaded._faiss_index.reconstruct(row), np.float32(_vector(text))
        )


def test_an_unchanged_reindex_still_reuses_embeddings(repo, cache):
    _built(repo, cache)
    embedded = []
    _sdk(repo, cache, embedded).index_repository()
    assert embedded == []


def test_a_cache_written_before_fingerprints_still_loads(repo, cache):
    sdk = _built(repo, cache)
    meta = json.loads(sdk._meta_path.read_text(encoding="utf-8"))
    del meta["index_sha256"]
    sdk._meta_path.write_text(json.dumps(meta), encoding="utf-8")

    assert _sdk(repo, cache).get_status()["indexed"] is True
    assert _sdk(repo, cache).search("uart")


def test_no_index_is_still_not_an_error(repo, cache):
    sdk = _sdk(repo, cache)
    assert sdk.search("anything") == []
    status = sdk.get_status()
    assert status["indexed"] is False
    assert "error" not in status


def test_status_and_search_agree(repo, cache):
    """Whatever get_status says, search must not contradict it."""
    sdk = _built(repo, cache)
    sdk._index_path.write_bytes(sdk._index_path.read_bytes()[:-8])

    status = _sdk(repo, cache).get_status()
    assert status["indexed"] is False
    with pytest.raises(RuntimeError):
        _sdk(repo, cache).search("uart")
