# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The training worker's loss mask, and the toolchain helpers around it."""

import importlib.util
import io
import tarfile
from unittest.mock import patch

import pytest

from gaia.learning import environment, trainer
from gaia.learning.config import LearningConfig
from gaia.learning.errors import LearningError
from gaia.learning.models import trainable_base
from gaia.learning.paths import worker_script


def _worker():
    spec = importlib.util.spec_from_file_location("train_lora", worker_script())
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class WordTokenizer:
    """One token per word of ``<role> content </role>``; a generation prompt
    appends ``<assistant>``."""

    def apply_chat_template(
        self,
        messages,
        tools=None,
        tokenize=True,
        add_generation_prompt=False,
        return_dict=True,
    ):
        words = []
        for m in messages:
            words += (
                [f"<{m['role']}>"]
                + (m.get("content") or "").split()
                + [f"</{m['role']}>"]
            )
        if add_generation_prompt:
            words.append("<assistant>")
        ids = [abs(hash(w)) % 10_000 for w in words]
        self.last_words = words
        return {"input_ids": ids}


def test_loss_covers_assistant_tokens_only():
    w = _worker()
    tok = WordTokenizer()
    row = {
        "messages": [
            {"role": "user", "content": "what is the rollup"},
            {"role": "assistant", "content": "the weekly digest"},
        ]
    }
    ids, labels = w._encode(tok, row, 1000)
    words = tok.last_words
    trained = [words[i] for i, label in enumerate(labels) if label != -100]
    assert trained == ["the", "weekly", "digest", "</assistant>"]
    assert len(ids) == len(labels)


def test_a_row_with_nothing_to_learn_is_dropped():
    w = _worker()
    row = {
        "messages": [
            {"role": "user", "content": "hi " * 50},
            {"role": "assistant", "content": "ok"},
        ]
    }
    assert w._encode(WordTokenizer(), row, 10) is None  # truncated before the reply


def test_worker_imports_nothing_from_gaia():
    src = worker_script().read_text(encoding="utf-8")
    assert "from gaia" not in src and "import gaia" not in src


def test_job_carries_the_base_models_settings(tmp_path):
    base = trainable_base("Gemma-4-E4B-it-GGUF")
    job = trainer.build_job(
        LearningConfig(rank=8),
        base,
        tmp_path / "w",
        {"train": tmp_path / "t.jsonl", "eval": tmp_path / "e.jsonl"},
        tmp_path / "out",
        tmp_path / "STOP",
    )
    assert job["rank"] == 8 and job["attn_implementation"] == "eager"
    assert "language_model" in job["target_modules"]


def test_training_without_a_venv_says_how_to_fix(lhome):
    with pytest.raises(LearningError, match="gaia learn setup"):
        trainer.train({"output_dir": "x"}, lhome / "run", lambda p: None)


# -- environment ------------------------------------------------------------


def test_gpu_arch_from_system_info():
    info = {"devices": {"amd_gpu": [{"available": True, "family": "gfx1151"}]}}
    assert environment.gpu_arch(info) == "gfx1151"
    with pytest.raises(LearningError, match="no available AMD GPU"):
        environment.gpu_arch({"devices": {"amd_gpu": []}})


def test_llama_server_binary_from_the_loaded_model():
    health = {
        "all_models_loaded": [{"launch_command": ["C:/lm/llama-server.exe", "-m", "x"]}]
    }
    assert environment.llama_server_binary(health).name == "llama-server.exe"


def test_setup_refuses_unsupported_gpus(lhome):
    with pytest.raises(LearningError, match="No ROCm PyTorch build for gfx906"):
        environment.setup(arch="gfx906", server_commit="abc1234", log_fn=lambda m: None)


def _tarball(files):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, text in files.items():
            data = text.encode()
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_converter_install_extracts_only_what_it_needs(lhome):
    blob = _tarball(
        {
            "llama.cpp-abc1234/convert_lora_to_gguf.py": "x",
            "llama.cpp-abc1234/convert_hf_to_gguf.py": "x",
            "llama.cpp-abc1234/gguf-py/gguf/__init__.py": "x",
            "llama.cpp-abc1234/conversion/gemma.py": "x",
            "llama.cpp-abc1234/src/llama.cpp": "not needed",
        }
    )
    with patch("urllib.request.urlopen", return_value=_Resp(blob)):
        dest = environment.install_converter("abc1234", lambda m: None)
    assert (dest / "conversion" / "gemma.py").exists()
    assert not (dest / "src").exists()
    assert environment.current_converter() == dest


def test_check_without_a_venv_reports_the_fix(lhome):
    st = environment.check()
    assert not st.ready and "gaia learn setup" in st.problems[0]


def test_unsupported_base_model_names_the_supported_ones():
    with pytest.raises(LearningError, match="Gemma-4-E4B-it-GGUF"):
        trainable_base("Qwen3.6-35B-A3B-GGUF")


def test_progress_lines_are_parsed(lhome, tmp_path, monkeypatch):
    """The trainer streams the worker's tagged progress lines to the run record."""
    monkeypatch.setattr(trainer, "_python", lambda: __import__("sys").executable)
    script = tmp_path / "w.py"
    out = tmp_path / "out"
    script.write_text(
        "import json,sys,pathlib\n"
        "p=json.load(open(sys.argv[2]))\n"
        "o=pathlib.Path(p['output_dir']); o.mkdir(parents=True, exist_ok=True)\n"
        "print('noise {not json')\n"
        "print('GAIA_PROGRESS '+json.dumps({'phase':'training','step':1}))\n"
        "(o/'metrics.json').write_text(json.dumps({'steps':1}))\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(trainer, "worker_script", lambda: script)
    (lhome.parent / "train").mkdir(parents=True, exist_ok=True)
    seen = []
    metrics = trainer.train({"output_dir": str(out)}, tmp_path / "run", seen.append)
    assert seen == [{"phase": "training", "step": 1}]
    assert metrics == {"steps": 1}
    assert "noise" in (tmp_path / "run" / "train.log").read_text(encoding="utf-8")


def test_converter_install_refuses_unsafe_archives(lhome):
    blob = _tarball(
        {
            "llama.cpp-abc1234/convert_lora_to_gguf.py": "x",
            "llama.cpp-abc1234/gguf-py/../../../../evil.py": "x",
        }
    )
    with patch("urllib.request.urlopen", return_value=_Resp(blob)):
        with pytest.raises(LearningError, match="unsafe member"):
            environment.install_converter("abc1234", lambda m: None)
    with pytest.raises(LearningError, match="not a llama.cpp commit"):
        environment.install_converter("main; rm -rf", lambda m: None)
