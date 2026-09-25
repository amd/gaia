# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The OpenMP guards that keep memory search from killing the process.

faiss and torch each link their own OpenMP runtime; whichever initialises
second aborts with "OMP: Error #15" — a SIGABRT no ``except`` can catch, so the
mixin's own ImportError/Exception guards can never fire. Two directions are
fatal and both are pinned here: importing torch where faiss is resident, and
searching faiss where torch is resident.
"""

import importlib.util
import os
import subprocess
import sys
import textwrap

import numpy as np
import pytest

from gaia.agents.base import memory as memory_mod
from gaia.agents.base.procedural_memory import ProceduralMemoryMixin


def _stub_sentence_transformers(monkeypatch, on_import):
    """Stand in for the real package.

    Every fall-through case MUST use this. Dropping "faiss" from sys.modules
    does not unload its native library, so the OpenMP runtime stays initialised
    and a real ``import torch`` still aborts the interpreter — a test that
    reaches the true import takes the whole suite down with it.
    """
    mod = type(sys)("sentence_transformers")
    mod.CrossEncoder = on_import
    monkeypatch.setitem(sys.modules, "sentence_transformers", mod)


@pytest.fixture(autouse=True)
def _reset_cross_encoder_cache(monkeypatch):
    """The result is cached at module level; each case starts from cold."""
    monkeypatch.setattr(memory_mod, "_cross_encoder_model", None, raising=False)
    monkeypatch.setattr(memory_mod, "_CROSS_ENCODER_UNAVAILABLE", False, raising=False)
    monkeypatch.delenv(memory_mod._OMP_OVERRIDE_ENV, raising=False)


def test_faiss_loaded_without_torch_refuses_the_fatal_import(monkeypatch, caplog):
    monkeypatch.setitem(sys.modules, "faiss", object())
    monkeypatch.delitem(sys.modules, "torch", raising=False)

    assert memory_mod._get_cross_encoder() is None
    assert "reranking disabled" in caplog.text, "a disabled feature must say so"
    # Sticky: the process must not re-attempt the import on every search.
    assert memory_mod._CROSS_ENCODER_UNAVAILABLE is True


def test_torch_already_loaded_is_not_blocked(monkeypatch):
    """Resident torch adds no new runtime, so this import is not the fatal step.

    The search that follows still is — ``assert_faiss_omp_safe`` covers it.
    """
    monkeypatch.setitem(sys.modules, "faiss", object())
    monkeypatch.setitem(sys.modules, "torch", object())

    reached = {}

    def _mark(*_a, **_k):
        reached["import"] = True
        raise ImportError("stubbed")

    _stub_sentence_transformers(monkeypatch, _mark)
    memory_mod._get_cross_encoder()
    assert reached.get("import"), "the guard short-circuited a safe host"


def test_the_override_keeps_reranking_on_a_host_that_handles_omp(monkeypatch):
    """The guard cannot tell a crashy host from a healthy one, so it errs
    toward staying alive — and lets an operator who knows better opt back in."""
    monkeypatch.setitem(sys.modules, "faiss", object())
    monkeypatch.delitem(sys.modules, "torch", raising=False)
    monkeypatch.setenv(memory_mod._OMP_OVERRIDE_ENV, "1")

    called = {}

    def _boom(*_a, **_k):
        called["tried"] = True
        raise ImportError("sentence_transformers not installed")

    _stub_sentence_transformers(monkeypatch, _boom)
    memory_mod._get_cross_encoder()
    assert called.get("tried"), "the override did not reach the import"


def test_no_faiss_is_unaffected(monkeypatch):
    monkeypatch.delitem(sys.modules, "faiss", raising=False)
    monkeypatch.delitem(sys.modules, "torch", raising=False)
    _stub_sentence_transformers(
        monkeypatch, lambda *_a, **_k: (_ for _ in ()).throw(ImportError("stubbed"))
    )
    memory_mod._get_cross_encoder()  # must not raise


class _ProcHost(ProceduralMemoryMixin):
    """Bare host for the procedures search — the real mixin method, real faiss."""

    def __init__(self, index, id_map):
        self._proc_faiss_index = index
        self._proc_faiss_id_map = id_map


def _real_proc_index(dim=8, n=3):
    """A real faiss index. Never stubbed: the native layer is what aborts."""
    faiss = pytest.importorskip("faiss")
    index = faiss.IndexFlatIP(dim)
    index.add(np.ascontiguousarray(np.eye(n, dim), dtype=np.float32))
    return _ProcHost(index, [f"proc-{i}" for i in range(n)])


@pytest.fixture
def _no_live_conflict():
    """Skip when this process really does hold two runtimes.

    The unguarded calls below would then abort the whole run — the very crash
    these tests exist to prevent.
    """
    if len(memory_mod._loaded_omp_runtimes()) > 1:
        pytest.skip("two OpenMP runtimes are resident; a real faiss search aborts")


def test_search_refuses_instead_of_aborting_the_process(monkeypatch):
    """The macOS smoke crash: torch resident, then a procedure recall."""
    host = _real_proc_index()
    monkeypatch.setattr(
        memory_mod,
        "_loaded_omp_runtimes",
        lambda: ("/x/faiss/.dylibs/libomp.dylib", "/x/torch/lib/libomp.dylib"),
    )

    with pytest.raises(RuntimeError) as excinfo:
        host._proc_faiss_search(np.ones(8, dtype=np.float32), 2)

    message = str(excinfo.value)
    assert "OpenMP" in message, "the error must name what is wrong"
    assert memory_mod._OMP_OVERRIDE_ENV in message, "and how to override it"


def test_a_single_runtime_searches_normally(_no_live_conflict, monkeypatch):
    """The guard must not disable recall on a healthy host."""
    host = _real_proc_index()
    monkeypatch.setattr(
        memory_mod, "_loaded_omp_runtimes", lambda: ("/x/faiss/.dylibs/libomp.dylib",)
    )

    hits = host._proc_faiss_search(np.eye(1, 8, dtype=np.float32)[0], 2)

    assert [pid for pid, _ in hits] == ["proc-0", "proc-1"]


def test_the_override_lets_the_search_through(_no_live_conflict, monkeypatch):
    monkeypatch.setenv(memory_mod._OMP_OVERRIDE_ENV, "1")
    host = _real_proc_index()
    monkeypatch.setattr(
        memory_mod,
        "_loaded_omp_runtimes",
        lambda: ("/a/libomp.dylib", "/b/libomp.dylib"),
    )

    assert host._proc_faiss_search(np.ones(8, dtype=np.float32), 1)


def test_a_stale_index_says_so_instead_of_recalling_nothing(_no_live_conflict):
    """A width mismatch is a wrong-model index, not an empty memory."""
    host = _real_proc_index(dim=8)

    with pytest.raises(RuntimeError, match="different embedding model|dimensions"):
        host._proc_faiss_search(np.ones(4, dtype=np.float32), 2)


def test_top_k_below_one_is_a_caller_bug(_no_live_conflict):
    host = _real_proc_index()

    with pytest.raises(ValueError, match="top_k"):
        host._proc_faiss_search(np.ones(8, dtype=np.float32), 0)


@pytest.mark.skipif(sys.platform != "darwin", reason="the abort is macOS dyld's")
def test_both_runtimes_resident_raises_rather_than_killing_the_interpreter(tmp_path):
    """The end-to-end proof, in a child process.

    Nothing here is stubbed — the child really imports torch and faiss, and
    really calls the mixin. Without the guard the child dies with SIGABRT
    (-6 / 134); the assertion is that it exits cleanly having raised.
    """
    # find_spec, never importorskip: importing torch HERE is the fatal move
    # this test is about — faiss's runtime is already initialised by the cases
    # above, so the parent would abort before the child ever ran.
    if importlib.util.find_spec("faiss") is None:
        pytest.skip("faiss-cpu is not installed")
    if importlib.util.find_spec("torch") is None:
        pytest.skip("torch is not installed; there is no second runtime to hit")

    script = tmp_path / "both_runtimes.py"
    script.write_text(textwrap.dedent("""
            import numpy as np
            import torch  # noqa: F401  (loads the second libomp)
            import faiss

            from gaia.agents.base.memory import _loaded_omp_runtimes
            from gaia.agents.base.procedural_memory import ProceduralMemoryMixin

            if len(_loaded_omp_runtimes()) < 2:
                print("SINGLE_RUNTIME")
                raise SystemExit(0)

            class Host(ProceduralMemoryMixin):
                def __init__(self):
                    index = faiss.IndexFlatIP(8)
                    index.add(np.ascontiguousarray(np.eye(2, 8), dtype=np.float32))
                    self._proc_faiss_index = index
                    self._proc_faiss_id_map = ["a", "b"]

            try:
                Host()._proc_faiss_search(np.ones(8, dtype=np.float32), 2)
            except RuntimeError:
                print("REFUSED")
                raise SystemExit(0)
            print("SEARCHED")
            """))

    result = subprocess.run(
        [sys.executable, str(script)],
        capture_output=True,
        text=True,
        timeout=300,
        # The child inherits this run's import path, so it tests THIS checkout
        # whether or not gaia is installed in the environment.
        env={**os.environ, "PYTHONPATH": os.pathsep.join(sys.path)},
    )

    assert result.returncode == 0, (
        "the guard let a fatal faiss search through: "
        f"rc={result.returncode} stderr={result.stderr[-400:]}"
    )
    assert result.stdout.strip() in {"REFUSED", "SINGLE_RUNTIME"}
