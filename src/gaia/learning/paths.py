# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Where learning keeps its state.

Per-user state (config, corpus, adapters, runs) follows ``GAIA_HOME``;
``GAIA_LEARNING_HOME`` moves just this tree. The
training toolchain is machine-wide, under the default ``~/.gaia/train``, so a
test run with its own ``GAIA_HOME`` reuses the multi-GB venv instead of
installing another. ``GAIA_TRAIN_HOME`` relocates it.
"""

import os
from pathlib import Path

from gaia.config import default_gaia_home, gaia_home


def learning_home() -> Path:
    raw = os.environ.get("GAIA_LEARNING_HOME", "").strip()
    if raw:
        return Path(os.path.expanduser(raw))
    return gaia_home() / "learning"


def config_path() -> Path:
    return learning_home() / "config.json"


def corpus_dir() -> Path:
    return learning_home() / "corpus"


def adapters_dir() -> Path:
    return learning_home() / "adapters"


def runs_dir() -> Path:
    return learning_home() / "runs"


def state_path() -> Path:
    return learning_home() / "state.json"


def cycle_lock_path() -> Path:
    return learning_home() / "cycle.lock"


def stop_file_path() -> Path:
    return learning_home() / "STOP"


def train_home() -> Path:
    raw = os.environ.get("GAIA_TRAIN_HOME", "").strip()
    if raw:
        return Path(os.path.expanduser(raw))
    return default_gaia_home() / "train"


def train_venv_python() -> Path:
    venv = train_home() / "venv"
    if os.name == "nt":
        return venv / "Scripts" / "python.exe"
    return venv / "bin" / "python"


def converter_dir() -> Path:
    return train_home() / "llama.cpp"


def worker_script() -> Path:
    return Path(__file__).resolve().parent / "worker" / "train_lora.py"
