# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Run training and GGUF conversion in the training venv."""

import json
import os
import subprocess
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from gaia.learning.config import LearningConfig
from gaia.learning.environment import current_converter
from gaia.learning.errors import LearningError, LearningStopped
from gaia.learning.models import TrainableBase
from gaia.learning.paths import train_home, train_venv_python, worker_script

EXIT_STOPPED = 3
PROGRESS_PREFIX = "GAIA_PROGRESS "

Progress = Callable[[Dict[str, Any]], None]


def _python() -> str:
    py = train_venv_python()
    if not py.exists():
        raise LearningError(
            f"The training venv isn't installed ({py}). Run `gaia learn setup`."
        )
    return str(py)


def _env() -> Dict[str, str]:
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    env.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
    env.setdefault("TOKENIZERS_PARALLELISM", "false")
    return env


def ensure_base_weights(base: TrainableBase, log: Callable[[str], None]) -> Path:
    """Download (once) the full-precision checkpoint training reads."""
    log(f"Checking the {base.hf_repo} training weights")
    code = (
        "from huggingface_hub import snapshot_download as s;"
        f"print('GAIA_PATH ' + s({base.hf_repo!r}))"
    )
    proc = subprocess.run(
        [_python(), "-I", "-c", code],
        capture_output=True,
        text=True,
        env=_env(),
        cwd=str(train_home()),
        timeout=6 * 3600,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    line = next(
        (x for x in proc.stdout.splitlines() if x.startswith("GAIA_PATH ")), None
    )
    if proc.returncode != 0 or line is None:
        raise LearningError(
            f"Could not download {base.hf_repo} for training:\n"
            + "\n".join((proc.stderr or "").splitlines()[-10:])
        )
    return Path(line[len("GAIA_PATH ") :].strip())


def build_job(
    cfg: LearningConfig,
    base: TrainableBase,
    base_path: Path,
    files: Dict[str, Path],
    out_dir: Path,
    stop_file: Path,
) -> Dict[str, Any]:
    return {
        "base_model": str(base_path),
        "train_file": str(files["train"]),
        "eval_file": str(files["eval"]),
        "output_dir": str(out_dir),
        "backend": cfg.backend,
        "load_in_4bit": cfg.load_in_4bit,
        "rank": cfg.rank,
        "alpha": cfg.alpha,
        "learning_rate": cfg.learning_rate,
        "epochs": cfg.epochs,
        "max_steps": cfg.max_steps,
        "max_seq_len": cfg.max_seq_len,
        "target_modules": base.target_modules,
        "attn_implementation": base.attn_implementation,
        "stop_file": str(stop_file),
    }


def train(job: Dict[str, Any], run_dir: Path, on_progress: Progress) -> Dict[str, Any]:
    """Run the worker; return its metrics. Raises :class:`LearningStopped` when
    the stop file ended it, :class:`LearningError` on any other failure."""
    run_dir.mkdir(parents=True, exist_ok=True)
    job_file = run_dir / "job.json"
    job_file.write_text(json.dumps(job, indent=2), encoding="utf-8")
    log_file = run_dir / "train.log"
    with open(log_file, "w", encoding="utf-8") as log:
        proc = subprocess.Popen(
            [_python(), "-I", str(worker_script()), "--job", str(job_file)],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env=_env(),
            cwd=str(train_home()),
            encoding="utf-8",
            errors="replace",
        )
        assert proc.stdout is not None
        for line in proc.stdout:
            log.write(line)
            log.flush()
            if line.startswith(PROGRESS_PREFIX):
                on_progress(json.loads(line[len(PROGRESS_PREFIX) :]))
        code = proc.wait()
    if code == EXIT_STOPPED:
        raise LearningStopped("Training stopped because the user came back.")
    if code != 0:
        tail = log_file.read_text(encoding="utf-8", errors="replace").splitlines()[-12:]
        raise LearningError(
            f"Training failed (exit {code}). Full log: {log_file}\n" + "\n".join(tail)
        )
    metrics_file = Path(job["output_dir"]) / "metrics.json"
    if not metrics_file.exists():
        raise LearningError(f"Training exited cleanly but wrote no {metrics_file}.")
    return json.loads(metrics_file.read_text(encoding="utf-8"))


def convert(adapter_dir: Path, base_path: Path, out_gguf: Path, run_dir: Path) -> Path:
    """PEFT adapter → GGUF LoRA with the converter matching Lemonade's llama.cpp."""
    conv: Optional[Path] = current_converter()
    if conv is None:
        raise LearningError(
            "llama.cpp's LoRA converter isn't installed. Run `gaia learn setup`."
        )
    env = _env()
    env["PYTHONPATH"] = os.pathsep.join([str(conv / "gguf-py"), str(conv)])
    proc = subprocess.run(
        [
            _python(),
            str(conv / "convert_lora_to_gguf.py"),
            "--base",
            str(base_path),
            "--outfile",
            str(out_gguf),
            "--outtype",
            "f16",
            str(adapter_dir),
        ],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(train_home()),
        timeout=1800,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    (run_dir / "convert.log").write_text(
        (proc.stdout or "") + (proc.stderr or ""), encoding="utf-8"
    )
    if proc.returncode != 0 or not out_gguf.exists():
        raise LearningError(
            f"Converting the adapter to GGUF failed (exit {proc.returncode}). "
            f"Log: {run_dir / 'convert.log'}\n"
            + "\n".join((proc.stderr or "").splitlines()[-10:])
        )
    return out_gguf
