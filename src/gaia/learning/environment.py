# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The training toolchain: its own venv, AMD's ROCm PyTorch, and llama.cpp's
LoRA converter.

``gaia learn setup`` installs it under ``~/.gaia/train`` (``GAIA_TRAIN_HOME``
moves it). It is a separate venv because ROCm PyTorch is several GB and pins
versions GAIA's own environment must not inherit.

Versions are pinned to the set validated on a Radeon 8060S (gfx1151) under
Windows; ``ROCM_TORCH`` moves with AMD's index, not with PyPI.
"""

import io
import json
import os
import re
import shutil
import subprocess
import tarfile
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional

from gaia.learning.errors import LearningError
from gaia.learning.paths import converter_dir, train_home, train_venv_python

AMD_TORCH_INDEX = "https://repo.amd.com/rocm/whl-multi-arch/"
ROCM_TAG = "rocm7.14.0"
ROCM_TORCH = "2.11.0"
ROCM_TORCHVISION = "0.26.0"
# AMD publishes these device extras for RDNA 2-4 and Strix.
SUPPORTED_ARCHES = (
    "gfx1030", "gfx1031", "gfx1032", "gfx1034", "gfx1035", "gfx1036",
    "gfx1100", "gfx1101", "gfx1102", "gfx1103",
    "gfx1150", "gfx1151", "gfx1152", "gfx1153",
    "gfx1200", "gfx1201",
)  # fmt: skip
TRAINING_LIBS = [
    "transformers==5.17.0",
    "peft==0.21.2",
    "bitsandbytes==0.50.2",
    "accelerate==1.15.0",
    "safetensors==0.8.0",
    "sentencepiece==0.2.2",
    "huggingface_hub==1.33.0",
    "numpy==2.5.4",
    "protobuf",
]
UNSLOTH_LIBS = ["unsloth==2026.10.7", "unsloth_zoo==2026.10.7"]
LLAMA_CPP_TARBALL = "https://codeload.github.com/ggml-org/llama.cpp/tar.gz/{commit}"

Log = Callable[[str], None]


@dataclass
class ToolchainStatus:
    venv: bool = False
    torch: Optional[str] = None
    gpu: Optional[str] = None
    peft: bool = False
    unsloth: bool = False
    converter_commit: Optional[str] = None
    server_commit: Optional[str] = None
    problems: List[str] = field(default_factory=list)
    checked_at: str = ""

    @property
    def ready(self) -> bool:
        return not self.problems

    def to_dict(self) -> Dict:
        d = asdict(self)
        d["ready"] = self.ready
        return d


def _run(cmd: List[str], log_fn: Log, *, env=None, cwd=None, timeout=3600) -> str:
    log_fn("$ " + " ".join(cmd))
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            env=env,
            cwd=cwd,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except FileNotFoundError as e:
        raise LearningError(f"Could not run {cmd[0]}: {e}") from e
    except subprocess.TimeoutExpired as e:
        raise LearningError(f"{' '.join(cmd[:3])} did not finish in {timeout}s.") from e
    out = (proc.stdout or "") + (proc.stderr or "")
    for line in out.splitlines()[-15:]:
        log_fn("  " + line)
    if proc.returncode != 0:
        raise LearningError(
            f"`{' '.join(cmd[:4])} ...` failed (exit {proc.returncode}):\n"
            + "\n".join(out.splitlines()[-20:])
        )
    return out


def _uv() -> str:
    uv = shutil.which("uv")
    if not uv:
        raise LearningError(
            "`gaia learn setup` needs uv to build the training venv. Install it "
            "from https://docs.astral.sh/uv/ and run setup again."
        )
    return uv


def gpu_arch(system_info: Dict) -> str:
    """The AMD GPU's gfx target, from Lemonade's ``/system-info``."""
    gpus = (system_info.get("devices") or {}).get("amd_gpu") or []
    for gpu in gpus:
        family = gpu.get("family")
        if gpu.get("available") and family:
            return family
    raise LearningError(
        "Lemonade reports no available AMD GPU, and training needs one. "
        "Nightly learning runs on Radeon GPUs and Ryzen AI Max / Strix iGPUs."
    )


def llama_server_binary(health: Dict) -> Path:
    """The llama-server Lemonade runs chat models with."""
    for entry in health.get("all_models_loaded") or []:
        cmd = entry.get("launch_command") or []
        if cmd and "llama-server" in Path(cmd[0]).name:
            return Path(cmd[0])
    found = sorted(
        (Path.home() / ".cache" / "lemonade" / "bin" / "llamacpp").glob(
            "*/llama-server*"
        )
    )
    if found:
        return found[0]
    raise LearningError(
        "Could not find Lemonade's llama-server to match the LoRA converter to. "
        "Load a chat model once (e.g. `gaia llm hello`) and run setup again."
    )


def llama_server_commit(binary: Path) -> str:
    out = subprocess.run(
        [str(binary), "--version"],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    m = re.search(r"commit ([0-9a-f]{7,40})", (out.stdout or "") + (out.stderr or ""))
    if not m:
        raise LearningError(
            f"{binary} --version printed no commit; can't pick a converter."
        )
    return m.group(1)


def install_converter(commit: str, log_fn: Log) -> Path:
    """llama.cpp's ``convert_lora_to_gguf.py`` and its imports at *commit* —
    the same commit as the server, so it writes tensors that server reads."""
    if not re.fullmatch(r"[0-9a-f]{7,40}", commit):
        raise LearningError(f"{commit!r} is not a llama.cpp commit hash.")
    dest = converter_dir() / commit
    if (dest / "convert_lora_to_gguf.py").exists():
        return dest
    url = LLAMA_CPP_TARBALL.format(commit=commit)
    log_fn(f"Downloading llama.cpp {commit} converter from {url}")
    try:
        with urllib.request.urlopen(
            url, timeout=300
        ) as resp:  # nosec B310 - fixed https host
            blob = resp.read()
    except OSError as e:
        raise LearningError(f"Could not download {url}: {e}") from e
    wanted = re.compile(
        r"^[^/]+/(convert_lora_to_gguf\.py|convert_hf_to_gguf\.py|conversion/.*|gguf-py/.*)$"
    )
    tmp = dest.with_suffix(".partial")
    shutil.rmtree(tmp, ignore_errors=True)
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tar:
        for member in tar.getmembers():
            m = wanted.match(member.name)
            if not m or not (member.isfile() or member.isdir()):
                continue
            rel = member.name.split("/", 1)[1]
            target = (tmp / rel).resolve()
            if not target.is_relative_to(tmp.resolve()):
                raise LearningError(f"{url} has an unsafe member path {member.name!r}.")
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            src = tar.extractfile(member)
            if src is not None:
                target.write_bytes(src.read())
    if not (tmp / "convert_lora_to_gguf.py").exists():
        raise LearningError(
            f"The llama.cpp {commit} archive has no convert_lora_to_gguf.py."
        )
    shutil.rmtree(dest, ignore_errors=True)
    tmp.rename(dest)
    (converter_dir() / "current.json").write_text(
        json.dumps({"commit": commit, "path": str(dest)}), encoding="utf-8"
    )
    return dest


def current_converter() -> Optional[Path]:
    meta = converter_dir() / "current.json"
    if not meta.exists():
        return None
    path = Path(json.loads(meta.read_text(encoding="utf-8"))["path"])
    return path if (path / "convert_lora_to_gguf.py").exists() else None


def setup(
    *,
    arch: str,
    server_commit: str,
    backend: str = "peft",
    log_fn: Log,
) -> ToolchainStatus:
    if arch not in SUPPORTED_ARCHES:
        raise LearningError(
            f"No ROCm PyTorch build for {arch}. Supported GPUs: {', '.join(SUPPORTED_ARCHES)}."
        )
    uv = _uv()
    py = train_venv_python()
    home = train_home()
    home.mkdir(parents=True, exist_ok=True)
    if not py.exists():
        _run([uv, "venv", str(py.parent.parent), "--python", "3.12"], log_fn)
    torch_pin = f"torch[device-{arch}]=={ROCM_TORCH}+{ROCM_TAG}"
    vision_pin = f"torchvision[device-{arch}]=={ROCM_TORCHVISION}+{ROCM_TAG}"
    constraints = home / "constraints.txt"
    constraints.write_text(
        f"torch=={ROCM_TORCH}+{ROCM_TAG}\ntorchvision=={ROCM_TORCHVISION}+{ROCM_TAG}\n",
        encoding="utf-8",
    )
    pip = [
        uv,
        "pip",
        "install",
        "--python",
        str(py),
        "--extra-index-url",
        AMD_TORCH_INDEX,
    ]
    _run(pip + [torch_pin, vision_pin], log_fn)
    _run(pip + ["-c", str(constraints)] + TRAINING_LIBS, log_fn)
    if backend == "unsloth":
        _run(pip + ["-c", str(constraints)] + UNSLOTH_LIBS, log_fn)
        # unsloth pulls a CUDA xformers build that breaks import on ROCm.
        _run([uv, "pip", "uninstall", "--python", str(py), "xformers"], log_fn)
    install_converter(server_commit, log_fn)
    return check(server_commit=server_commit)


_PROBE = r"""
import json, importlib.util
out = {}
import torch
out["torch"] = torch.__version__
out["gpu"] = torch.cuda.get_device_name(0) if torch.cuda.is_available() else None
out["peft"] = all(importlib.util.find_spec(m) for m in ("peft", "transformers", "bitsandbytes"))
try:
    import unsloth  # noqa: F401
    out["unsloth"] = True
except Exception as e:
    out["unsloth"] = False
    out["unsloth_error"] = f"{type(e).__name__}: {e}"[:300]
print("GAIA_PROBE " + json.dumps(out))
"""


def check(
    *, server_commit: Optional[str] = None, backend: str = "peft"
) -> ToolchainStatus:
    """Probe the training venv. Imports torch in a subprocess (~10 s)."""
    st = ToolchainStatus(checked_at=datetime.now(timezone.utc).isoformat())
    st.server_commit = server_commit
    py = train_venv_python()
    st.venv = py.exists()
    if not st.venv:
        st.problems.append("The training venv isn't installed. Run `gaia learn setup`.")
        return st
    proc = subprocess.run(
        [str(py), "-I", "-c", _PROBE],
        capture_output=True,
        text=True,
        timeout=300,
        cwd=str(train_home()),
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    line = next(
        (x for x in proc.stdout.splitlines() if x.startswith("GAIA_PROBE ")), None
    )
    if line is None:
        st.problems.append(
            "The training venv is broken (the probe didn't run): "
            + "\n".join((proc.stderr or "").splitlines()[-5:])
            + "\nRun `gaia learn setup` to repair it."
        )
        return st
    data = json.loads(line[len("GAIA_PROBE ") :])
    st.torch, st.gpu = data.get("torch"), data.get("gpu")
    st.peft, st.unsloth = bool(data.get("peft")), bool(data.get("unsloth"))
    if not st.gpu:
        st.problems.append(
            f"PyTorch {st.torch} in the training venv can't see a GPU. Run "
            "`gaia learn setup` to install the ROCm build for this GPU."
        )
    if not st.peft:
        st.problems.append(
            "transformers/peft/bitsandbytes are missing. Run `gaia learn setup`."
        )
    if backend == "unsloth" and not st.unsloth:
        hint = (
            " On Windows, Unsloth's Triton kernels need Visual Studio Build Tools "
            "(C++ workload); or use `gaia learn config --backend peft`."
            if os.name == "nt"
            else ""
        )
        st.problems.append(
            f"Unsloth won't import: {data.get('unsloth_error', 'unknown error')}.{hint}"
        )
    conv = current_converter()
    st.converter_commit = conv.name if conv else None
    if conv is None:
        st.problems.append(
            "llama.cpp's LoRA converter isn't installed. Run `gaia learn setup`."
        )
    elif (
        server_commit
        and not server_commit.startswith(conv.name)
        and not conv.name.startswith(server_commit)
    ):
        st.problems.append(
            f"The LoRA converter is from llama.cpp {conv.name}, but Lemonade now runs "
            f"{server_commit}. Run `gaia learn setup` to match them."
        )
    status_file = train_home() / "status.json"
    status_file.parent.mkdir(parents=True, exist_ok=True)
    status_file.write_text(json.dumps(st.to_dict(), indent=2), encoding="utf-8")
    return st


def cached_status() -> Optional[Dict]:
    """The last :func:`check` result, without re-probing."""
    path = train_home() / "status.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise LearningError(f"Could not read {path}: {e}") from e


__all__ = [
    "ToolchainStatus",
    "cached_status",
    "check",
    "current_converter",
    "gpu_arch",
    "install_converter",
    "llama_server_binary",
    "llama_server_commit",
    "setup",
]
