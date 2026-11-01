# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""How an AMD integrated GPU's memory is split with the CPU, and changing it.

The split is :func:`gaia.llm.model_fit.integrated_gpu_memory`, so this step
and the model picker read the same numbers. What a bigger share unlocks is
model_fit's fit rule run at that share.

Changing it:

* **Linux** — AMD's documented path (the ROCm RDNA3.5 guide and AMD's
  ``amd-ttm`` tool): ``options ttm pages_limit=<4 KiB pages>`` in
  ``/etc/modprobe.d/ttm.conf``, the initramfs rebuilt, then a restart. GAIA
  writes it as root through ``pkexec`` only after the user confirms, and keeps
  the file's previous contents so Settings can undo it. Anything that would
  override that file (a kernel command line value, another modprobe.d file,
  ttm built into the kernel, an ostree system) makes it a guided step instead.
* **Windows** — guided. AMD's only API for Variable Graphics Memory
  (ADLX ``IADLXVariableGraphicsMemory::SetOption``) restarts the PC itself,
  and GAIA never restarts the PC.

A pending change is kept in ``~/.gaia/gpu_memory.json`` so the next launch
can say whether it took effect, judged from a fresh reading, never from a
"done" click.
"""

from __future__ import annotations

import json
import math
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from gaia.logger import get_logger

log = get_logger(__name__)

#: Memory the CPU always keeps for the OS and apps, in GB.
RESERVE_GB = 16.0
#: The smallest share GAIA will set.
MIN_SETTING_GB = 4.0
#: Spacing of the offered presets, in GB.
PRESET_STEP_GB = 16.0
#: AMD Variable Graphics Memory converts at most this share of RAM.
WINDOWS_VGM_MAX_FRACTION = 0.75
#: Every ``*_gb`` here is decimal (1 GB = 10**9 bytes), as in ``model_fit``.
GB = 10**9
#: ``ttm.pages_limit`` counts 4 KiB pages.
PAGE_BYTES = 4096
#: Lemonade, AMD Software and ``amd-ttm`` count GiB (2**30 bytes), labelled GB.
GIB = 2**30
#: Lemonade's GiB in decimal GB: memory is converted once, where it comes in.
GB_PER_GIB = GIB / GB
#: A reading within this many GB of the target counts as the target.
TOOK_EFFECT_TOLERANCE_GB = 1.0

METHOD_APPLY = "apply"
METHOD_GUIDED = "guided"
METHOD_UNSUPPORTED = "unsupported"

PENDING_RESTART = "restart_required"
PENDING_TOOK_EFFECT = "took_effect"
PENDING_NOT_APPLIED = "not_applied"

#: User-facing step text, kept here so it can be corrected in one place.
WINDOWS_STEPS = (
    "Right-click the desktop and open AMD Software: Adrenalin Edition.",
    "Go to Performance > Tuning and find Variable Graphics Memory.",
    "Choose Custom and set it to {amd} GB, as AMD Software shows it, or the "
    "closest option above it. {why}",
    "Restart your PC, then come back here. GAIA reads the new split.",
    "No Variable Graphics Memory option? Set UMA Frame Buffer Size in your "
    "PC's BIOS to the same value instead. With AMD Software, leave the BIOS "
    "on Auto.",
)
#: Why the AMD value is smaller than the GPU total: with Windows' shared memory,
#: and without it (AMD Software's "GB" are larger than GAIA's).
WINDOWS_WHY_SHARED = (
    "That is less than the {gpu} GB GPU total GAIA shows, because the total "
    "also counts memory Windows shares with the GPU."
)
WINDOWS_WHY_UNITS = (
    "AMD Software's GB are a little larger than GAIA's, so its number is a "
    "little smaller than the {gpu} GB GAIA shows."
)
LINUX_MODPROBE_STEPS = (
    "Run: echo 'options {module} pages_limit={pages}' | "
    "sudo tee /etc/modprobe.d/ttm.conf",
    "Rebuild the boot image. Ubuntu: sudo update-initramfs -u · "
    "Fedora: sudo dracut --regenerate-all -f",
    "Restart your PC, then come back here. GAIA reads the new split.",
)
LINUX_CMDLINE_STEPS = (
    "Set {module}.pages_limit={pages} on the kernel command line, replacing "
    "any value already there.",
    "Ubuntu: add it to GRUB_CMDLINE_LINUX_DEFAULT in /etc/default/grub, then "
    "run sudo update-grub · Fedora: sudo grubby --update-kernel=ALL "
    "--args='{module}.pages_limit={pages}'",
    "Restart your PC, then come back here. GAIA reads the new split.",
)
LINUX_OSTREE_STEPS = (
    "Run: sudo rpm-ostree kargs --append-if-missing={module}.pages_limit={pages}",
    "Restart your PC, then come back here. GAIA reads the new split.",
)
LINUX_BIOS_STEP = (
    "Or raise UMA Frame Buffer Size in the BIOS. AMD recommends keeping that "
    "small on Linux and raising this limit instead."
)

_CMDLINE_KEYS = ("ttm.pages_limit", "amdttm.pages_limit", "amdgpu.gttsize")
_OPTIONS_LINE = re.compile(
    r"^\s*options\s+((amd)?ttm\b.*\bpages_limit=|amdgpu\b.*\bgttsize=)", re.M
)
_CONF_NAME = "ttm.conf"
#: The only lines GAIA writes to the conf as root: blanks, comments, ttm options.
_SAFE_CONF_LINE = re.compile(
    r"^\s*(#.*)?$|^\s*options\s+(amd)?ttm(\s+[a-z_]+=\d+)+\s*$"
)
_CONF_HEADER = "# GPU memory limit set by GAIA. Undo it in GAIA Settings > GPU memory."

# Runs as root: writes (or with empty content removes) the conf, then rebuilds
# the initramfs. Values arrive as positional arguments, never in the script.
_ELEVATED_SCRIPT = r"""set -e
conf="$1"; content="$2"; tool="$3"
if [ -n "$content" ]; then
  printf '%s\n' "$content" > "$conf.gaia-new"
  chmod 644 "$conf.gaia-new"
  mv "$conf.gaia-new" "$conf"
else
  rm -f "$conf"
fi
case "$tool" in
  update-initramfs) update-initramfs -u ;;
  dracut) dracut --regenerate-all -f ;;
  mkinitcpio) mkinitcpio -P ;;
  none) ;;
  *) echo "unknown initramfs tool: $tool" >&2; exit 2 ;;
esac
"""
#: pkexec: the user dismissed the prompt / no prompt could be shown or allowed.
_PKEXEC_DISMISSED = 126
_PKEXEC_NOT_AUTHORIZED = 127
_ELEVATED_TIMEOUT_S = 900


class GpuMemoryError(Exception):
    """A GPU memory request failed; ``status`` is the HTTP code to answer with."""

    status = 500


class NotConfirmed(GpuMemoryError):
    status = 400


class PermissionNotGiven(GpuMemoryError):
    status = 403


class NotPossible(GpuMemoryError):
    status = 409


class OutOfRange(GpuMemoryError):
    status = 422


class Unavailable(GpuMemoryError):
    status = 503


@dataclass(frozen=True)
class SystemPaths:
    """Where Linux keeps what this module reads and writes; tests point it elsewhere."""

    sys_module: Path = Path("/sys/module")
    proc: Path = Path("/proc")
    modprobe_d: Path = Path("/etc/modprobe.d")
    ostree_marker: Path = Path("/run/ostree-booted")


PATHS = SystemPaths()


def _platform() -> str:
    if sys.platform.startswith("linux"):
        return "linux"
    if sys.platform == "win32":
        return "windows"
    return sys.platform


def _state_path() -> Path:
    from gaia.config import gaia_home

    return gaia_home() / "gpu_memory.json"


def _boot_time() -> float:
    import psutil

    return psutil.boot_time()


def _now() -> float:
    return time.time()


# --- Linux: the ttm limit ------------------------------------------------------


@dataclass(frozen=True)
class _LinuxSetup:
    method: str
    reason: str
    #: ``ttm``, or ``amdttm`` under AMD's DKMS driver.
    module: str
    #: ``modprobe`` / ``cmdline`` / ``ostree``: which steps fit this machine.
    steps: str
    live_pages: Optional[int]


def _read_int(path: Path) -> Optional[int]:
    try:
        value = int(path.read_text(encoding="ascii").strip())
    except (OSError, ValueError):
        return None
    return value if value > 0 else None


def _linux_setup() -> _LinuxSetup:
    module = "ttm"
    if (PATHS.sys_module / "amdttm" / "parameters" / "pages_limit").is_file():
        module = "amdttm"
    live = _read_int(PATHS.sys_module / module / "parameters" / "pages_limit")

    def guided(reason: str, steps: str = "modprobe") -> _LinuxSetup:
        return _LinuxSetup(METHOD_GUIDED, reason, module, steps, live)

    if PATHS.ostree_marker.exists():
        return guided(
            "This is an image-based (ostree) system, so the limit goes on the "
            "kernel command line through rpm-ostree.",
            "ostree",
        )
    if live is None:
        return guided(
            f"GAIA can't read the GPU memory limit from "
            f"{PATHS.sys_module / module / 'parameters' / 'pages_limit'}, so it "
            "won't change it. The kernel may be too old for this setting."
        )
    if not (PATHS.sys_module / module / "initstate").exists():
        return guided(
            f"The {module} driver is built into this kernel, so the limit goes "
            "on the kernel command line.",
            "cmdline",
        )
    cmdline_path = PATHS.proc / "cmdline"
    try:
        tokens = shlex.split(cmdline_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return guided(
            f"GAIA couldn't read the kernel command line ({cmdline_path}: {e}), "
            "so it can't tell whether a change would take effect.",
            "cmdline",
        )
    for token in tokens:
        key = token.split("=", 1)[0]
        if key in _CMDLINE_KEYS:
            return guided(
                f"The kernel command line already sets {token}, which overrides "
                "the setting GAIA writes. Change it there instead.",
                "cmdline",
            )
    try:
        own = _read_conf()
    except GpuMemoryError as e:
        return guided(str(e))
    if own is not None and not _is_safe_conf(own):
        return guided(
            f"{_conf_path()} holds settings GAIA doesn't manage, so GAIA "
            "couldn't put it back exactly. Edit it yourself."
        )
    for conf in sorted(PATHS.modprobe_d.glob("*.conf")):
        if conf.name == _CONF_NAME:
            continue
        try:
            text = conf.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as e:
            return guided(f"GAIA couldn't read {conf} ({e}) to check for conflicts.")
        if _OPTIONS_LINE.search(text):
            return guided(
                f"{conf} already sets the GPU memory limit. Change it there, "
                "or remove it so GAIA can manage the setting."
            )
    if not _is_root() and _which("pkexec") is None:
        return guided(
            "pkexec isn't installed, so GAIA can't ask for administrator "
            "permission. Run these commands yourself."
        )
    return _LinuxSetup(METHOD_APPLY, "", module, "modprobe", live)


def _is_root() -> bool:
    return hasattr(os, "geteuid") and os.geteuid() == 0


def _which(name: str) -> Optional[str]:
    path = os.pathsep.join(
        [os.environ.get("PATH", ""), "/usr/local/sbin", "/usr/sbin", "/sbin"]
    )
    return shutil.which(name, path=path)


def _initramfs_tool() -> str:
    for tool in ("update-initramfs", "dracut", "mkinitcpio"):
        if _which(tool):
            return tool
    return "none"


def _conf_path() -> Path:
    return PATHS.modprobe_d / _CONF_NAME


def _read_conf() -> Optional[str]:
    path = _conf_path()
    if not path.exists():
        return None
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        raise GpuMemoryError(
            f"Couldn't read {path} to save it for undo: {e}. Nothing was changed."
        ) from e


def _is_safe_conf(text: str) -> bool:
    lines = text.splitlines()
    return "\x00" not in text and all(_SAFE_CONF_LINE.match(x) for x in lines)


def _run_elevated(argv: List[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        argv,
        capture_output=True,
        text=True,
        timeout=_ELEVATED_TIMEOUT_S,
        check=False,
    )


def _write_conf_as_root(content: Optional[str]) -> None:
    """Write (or with None remove) the ttm conf as root and rebuild the initramfs.

    Refuses anything but ttm options and comments: undo's content comes back
    from a file in the user's home, which any of their processes can edit.
    """
    if content is not None and not _is_safe_conf(content):
        raise NotPossible(
            f"GAIA won't write this to {_conf_path()}: it holds more than GPU "
            f"memory settings. Edit {_conf_path()} yourself, then restart."
        )
    argv = ["/bin/sh", "-c", _ELEVATED_SCRIPT, "gaia-gpu-memory"]
    argv += [str(_conf_path()), content or "", _initramfs_tool()]
    if not _is_root():
        argv = [_which("pkexec") or "pkexec"] + argv
    try:
        done = _run_elevated(argv)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise GpuMemoryError(
            f"Couldn't run the administrator step: {e}. Check {_conf_path()} "
            "before restarting."
        ) from e
    if not _is_root() and done.returncode == _PKEXEC_DISMISSED:
        raise PermissionNotGiven(
            "Administrator permission wasn't given, so nothing changed."
        )
    if not _is_root() and done.returncode == _PKEXEC_NOT_AUTHORIZED:
        raise PermissionNotGiven(
            "GAIA couldn't get administrator permission: no password prompt "
            "could be shown, or this account isn't allowed to give it. Nothing "
            "changed. Follow the steps under 'Show me how' instead."
        )
    if done.returncode != 0:
        detail = (done.stderr or done.stdout or "").strip()[-400:]
        raise GpuMemoryError(
            f"The administrator step failed (exit {done.returncode}): {detail}. "
            f"Check {_conf_path()}, and rebuild the boot image (Ubuntu: sudo "
            "update-initramfs -u · Fedora: sudo dracut --regenerate-all -f) "
            "before restarting."
        )


# --- The split and what it unlocks ---------------------------------------------


@dataclass(frozen=True)
class _Machine:
    os: str
    total_gb: float
    carve_out_gb: float
    shared_gb: float
    windows_shared: bool
    #: RAM the OS sees (Linux MemTotal), which can be less than installed RAM.
    os_visible_gb: Optional[float] = None

    @property
    def setting_gb(self) -> float:
        """The value of the knob the user changes."""
        return self.carve_out_gb if self.os == "windows" else self.shared_gb

    @property
    def max_setting_gb(self) -> float:
        if self.os == "windows":
            # Keep cpu_gb (what the GPU can never use) at RESERVE_GB or more.
            if self.windows_shared:
                room = self.total_gb - 2 * RESERVE_GB
            else:
                room = self.total_gb - RESERVE_GB - self.shared_gb
            cap = min(self.total_gb * WINDOWS_VGM_MAX_FRACTION, room)
        else:
            cap = self.total_gb - RESERVE_GB - self.carve_out_gb
            if self.os_visible_gb is not None:
                cap = min(cap, self.os_visible_gb - RESERVE_GB)
        return float(max(0, math.floor(cap)))

    def gpu_gb_at(self, setting_gb: float) -> float:
        """The pool model_fit would report with the knob at *setting_gb*."""
        if self.os == "windows":
            if self.windows_shared:
                from gaia.llm.model_fit import windows_shared_gb

                return setting_gb + windows_shared_gb(self.total_gb, setting_gb)
            return setting_gb + self.shared_gb
        return self.carve_out_gb + setting_gb


def _meminfo_total_gb() -> float:
    """Linux MemTotal in decimal GB: the RAM the OS sees, without the carve-out."""
    meminfo = PATHS.proc / "meminfo"
    try:
        text = meminfo.read_text(encoding="ascii")
    except OSError as e:
        raise GpuMemoryError(f"Couldn't read {meminfo}: {e}") from e
    match = re.search(r"^MemTotal:\s+(\d+)\s+kB", text, re.M)
    if not match:
        raise GpuMemoryError(f"{meminfo} has no MemTotal line.")
    return int(match.group(1)) * 1024 / GB


def _total_ram_gb(info: Dict[str, Any], carve_out_gb: float) -> float:
    from gaia.llm.model_fit import physical_memory_gb

    reported = physical_memory_gb(info)
    if reported > 0:
        return reported * GB_PER_GIB
    if _platform() == "linux":
        # The OS doesn't count the carve-out; installed RAM does.
        return _meminfo_total_gb() + carve_out_gb
    raise GpuMemoryError(
        "Couldn't tell how much memory this PC has: Lemonade didn't report "
        "'Physical Memory'. Update Lemonade (`gaia init`) and retry."
    )


def _candidates():
    from gaia.llm.lemonade_client import (
        DEFAULT_MODEL_LADDER,
        FLASH_OPTION_MODEL_NAME,
        OPTIONAL_CHAT_MODELS,
        find_model_requirement,
    )

    for model_id in (
        *DEFAULT_MODEL_LADDER,
        *OPTIONAL_CHAT_MODELS,
        FLASH_OPTION_MODEL_NAME,
    ):
        default = model_id in DEFAULT_MODEL_LADDER
        yield model_id, find_model_requirement(model_id), default


def _fit(model_id: str, requirement, memory_gb: float) -> Tuple[bool, float]:
    """``(fits, need_gb)`` of one model on a GPU pool of *memory_gb*."""
    from gaia.llm.lemonade_client import OPTIONAL_CHAT_MODELS, kv_cache_for
    from gaia.llm.model_fit import (
        MachineCapacity,
        check_fit,
        kv_cache_gb,
        required_memory_gb,
    )

    # model_fit judges in Lemonade's GiB.
    capacity = MachineCapacity(memory_gb / GB_PER_GIB, "AMD iGPU", None)
    if model_id in OPTIONAL_CHAT_MODELS:
        kv = kv_cache_gb(requirement.kv_bytes_per_token, requirement.min_ctx_size)
    else:
        kv = kv_cache_for(requirement, capacity)
    return (
        check_fit(requirement.size_gb, capacity, kv).fits,
        required_memory_gb(requirement.size_gb, kv) * GB_PER_GIB,
    )


def _models(machine: _Machine) -> List[Dict[str, Any]]:
    now = machine.gpu_gb_at(machine.setting_gb)
    grid = range(int(math.ceil(machine.setting_gb)), int(machine.max_setting_gb) + 1)
    out = []
    for model_id, mr, default in _candidates():
        if mr is None or not mr.size_gb:
            continue
        fits_now, need = _fit(model_id, mr, now)
        fits_at = None
        if not fits_now:
            fits_at = next(
                (
                    float(s)
                    for s in grid
                    if _offerable(machine, s)
                    and _fit(model_id, mr, machine.gpu_gb_at(s))[0]
                ),
                None,
            )
        out.append(
            {
                "id": model_id,
                "size_gb": mr.size_gb,
                "need_gb": round(need, 1),
                "default": default,
                "fits_now": fits_now,
                "fits_at_setting_gb": fits_at,
            }
        )
    return out


def _offerable(machine: _Machine, setting_gb: float) -> bool:
    """A raise GAIA may suggest: one apply and record_pending accept."""
    return (
        MIN_SETTING_GB <= setting_gb <= machine.max_setting_gb
        and setting_gb > machine.setting_gb + TOOK_EFFECT_TOLERANCE_GB
    )


def _presets(machine: _Machine, models: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    top = machine.max_setting_gb
    values = [
        PRESET_STEP_GB * k
        for k in range(1, int(top // PRESET_STEP_GB) + 1)
        if _offerable(machine, PRESET_STEP_GB * k)
    ]
    if _offerable(machine, top) and top not in values:
        values.append(top)
    return [
        {
            "setting_gb": v,
            "gpu_gb": round(machine.gpu_gb_at(v), 1),
            "unlocks": [
                m["id"]
                for m in models
                if m["fits_at_setting_gb"] is not None and m["fits_at_setting_gb"] <= v
            ],
        }
        for v in values
    ]


def _guided_steps(
    machine: _Machine, setup: Optional[_LinuxSetup], gb: float
) -> Tuple[List[str], str]:
    """The steps for a setting of *gb*, and the one value they ask the user to enter.

    On Windows that value is in AMD Software's own units: it counts GiB and
    labels them GB, rounded up to a whole number.
    """
    if machine.os == "windows":
        amd = _fmt(math.ceil(gb * GB / GIB))
        gpu = _fmt(round(machine.gpu_gb_at(gb)))
        shared = machine.gpu_gb_at(gb) - gb >= TOOK_EFFECT_TOLERANCE_GB
        why = (WINDOWS_WHY_SHARED if shared else WINDOWS_WHY_UNITS).format(gpu=gpu)
        return [s.format(amd=amd, why=why) for s in WINDOWS_STEPS], amd
    module = setup.module if setup else "ttm"
    pages = pages_for(gb)
    steps = {
        "cmdline": LINUX_CMDLINE_STEPS,
        "ostree": LINUX_OSTREE_STEPS,
    }.get(setup.steps if setup else "modprobe", LINUX_MODPROBE_STEPS)
    text = [s.format(module=module, pages=pages) for s in steps]
    return text + [LINUX_BIOS_STEP], str(pages)


def _fmt(gb: float) -> str:
    return f"{gb:.0f}" if float(gb).is_integer() else f"{gb:.1f}"


def pages_for(gb: float) -> int:
    """``ttm.pages_limit`` for a limit of *gb* decimal GB."""
    return int(round(gb * GB / PAGE_BYTES))


def gb_for(pages: int) -> float:
    """Decimal GB in *pages* 4 KiB pages."""
    return pages * PAGE_BYTES / GB


# --- Pending change -------------------------------------------------------------


def _load_state() -> Optional[Dict[str, Any]]:
    path = _state_path()
    if not path.exists():
        return None
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise GpuMemoryError(
            f"{path} is unreadable ({e}). Delete it to forget GAIA's pending "
            "GPU memory change; the setting itself is untouched."
        ) from e
    if not isinstance(state, dict) or "setting_gb" not in state:
        raise GpuMemoryError(
            f"{path} isn't a GPU memory record. Delete it to forget GAIA's "
            "pending GPU memory change; the setting itself is untouched."
        )
    return state


def _save_state(state: Optional[Dict[str, Any]]) -> None:
    path = _state_path()
    if state is None:
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _pending(
    state: Optional[Dict[str, Any]], machine: _Machine, live_pages: Optional[int]
) -> Optional[Dict[str, Any]]:
    if not state or state.get("dismissed"):
        return None
    if _boot_time() <= float(state.get("recorded_at_epoch", 0)):
        result = PENDING_RESTART
    elif state.get("pages") and live_pages is not None:
        result = (
            PENDING_TOOK_EFFECT
            if abs(live_pages - state["pages"]) <= pages_for(TOOK_EFFECT_TOLERANCE_GB)
            else PENDING_NOT_APPLIED
        )
    else:
        result = (
            PENDING_TOOK_EFFECT
            if _reached(machine, state["setting_gb"], state.get("previous_setting_gb"))
            else PENDING_NOT_APPLIED
        )
    return {
        "setting_gb": state["setting_gb"],
        "previous_setting_gb": state.get("previous_setting_gb"),
        "source": state.get("source"),
        "recorded_at": state.get("recorded_at"),
        "state": result,
    }


def _reached(machine: _Machine, target: float, previous: Optional[float]) -> bool:
    """Whether the split now reads as *target*.

    On Windows the steps round up to AMD Software's whole GiB, so a raise
    counts once the reading is at or above the target, and a cut once it is
    at or below that rounded-up value.
    """
    now, tol = machine.setting_gb, TOOK_EFFECT_TOLERANCE_GB
    if machine.os != "windows":
        return abs(now - target) <= tol
    if previous is None or target >= previous:
        return now >= target - tol
    return now <= math.ceil(target * GB / GIB) * GB_PER_GIB + tol


def _record(
    os_name: str,
    setting_gb: float,
    source: str,
    previous_setting_gb: float,
    linux: Optional[Dict[str, Any]] = None,
    pages: Optional[int] = None,
) -> None:
    now = _now()
    _save_state(
        {
            "os": os_name,
            "setting_gb": setting_gb,
            "previous_setting_gb": previous_setting_gb,
            "source": source,
            "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
            "recorded_at_epoch": now,
            "pages": pages,
            "linux": linux,
        }
    )


# --- Public API -------------------------------------------------------------------


def _hidden(reason: str, os_name: str) -> Dict[str, Any]:
    return {
        "applies": False,
        "show_in_setup": False,
        "hidden_reason": reason,
        "os": os_name,
        "method": METHOD_UNSUPPORTED,
        "method_reason": reason,
    }


def _machine(info: Dict[str, Any], os_name: str):
    """``(machine, None)`` for an AMD iGPU, else ``(None, why the step is hidden)``."""
    from gaia.llm.model_fit import (
        SYSTEM_RAM,
        ModelFitError,
        capacity_from_system_info,
        integrated_gpu_memory,
    )

    try:
        capacity = capacity_from_system_info(info)
        split = integrated_gpu_memory(info)
    except ModelFitError as e:
        raise Unavailable(str(e)) from e
    if split is None:
        if capacity.memory_source == SYSTEM_RAM:
            return None, "No GPU was found, so models run in system memory."
        return None, (
            f"This PC's GPU ({capacity.memory_source}) has its own memory, so "
            "there is nothing to split."
        )
    if os_name not in ("linux", "windows"):
        return None, "GAIA can only show this on Windows and Linux."
    carve_out = split.carve_out_gb * GB_PER_GIB
    return (
        _Machine(
            os=os_name,
            total_gb=_total_ram_gb(info, carve_out),
            os_visible_gb=_meminfo_total_gb() if os_name == "linux" else None,
            carve_out_gb=carve_out,
            shared_gb=split.shared_gb * GB_PER_GIB,
            windows_shared=split.reported_by == "windows",
        ),
        None,
    )


def describe(
    info: Dict[str, Any],
    base_url: Optional[str] = None,
    setting_gb: Optional[float] = None,
    check_setting: bool = True,
) -> Dict[str, Any]:
    """The GPU memory step for a Lemonade ``/system-info`` body.

    *base_url* is the Lemonade the body came from: a remote server's memory
    isn't this PC's, so the step is hidden. *setting_gb* renders the guided
    steps for that value instead of the recommended one; ``check_setting``
    False skips its range check, for a value already recorded. Lemonade
    reports GiB; every ``*_gb`` returned is decimal GB.
    """
    from gaia.llm.lemonade_client import _is_local_url

    os_name = _platform()
    if base_url is not None and not _is_local_url(base_url):
        return _hidden(
            "GAIA's model server runs on another machine. Change its GPU "
            "memory on that machine.",
            os_name,
        )
    machine, why = _machine(info, os_name)
    if machine is None:
        return _hidden(why, os_name)
    if setting_gb is not None and check_setting:
        _check_range(setting_gb, MIN_SETTING_GB, machine.max_setting_gb)

    setup = _linux_setup() if machine.os == "linux" else None
    if setup is not None:
        method, reason = setup.method, setup.reason
    else:
        method = METHOD_GUIDED
        reason = (
            "On Windows this is set in AMD Software. AMD's way to set it from "
            "an app restarts the PC by itself, so GAIA shows you where instead."
        )
    models = _models(machine)
    presets = _presets(machine, models)
    recommended = next(
        (
            p["setting_gb"]
            for p in presets
            if any(m["default"] and m["id"] in p["unlocks"] for m in models)
        ),
        None,
    )
    state = _load_state()
    linux_record = (state or {}).get("linux")
    gpu = machine.gpu_gb_at(machine.setting_gb)
    # With nothing above the current setting to offer, steps would only lower it.
    steps_gb = (
        setting_gb or recommended or (presets[-1]["setting_gb"] if presets else None)
    )
    if steps_gb is not None:
        steps, guided_value = _guided_steps(machine, setup, steps_gb)
    else:
        steps, guided_value = [], None
    return {
        "applies": True,
        "show_in_setup": recommended is not None,
        "hidden_reason": None,
        "os": machine.os,
        "total_gb": round(machine.total_gb, 1),
        "gpu_gb": round(gpu, 1),
        "cpu_gb": round(max(0.0, machine.total_gb - gpu), 1),
        "setting": {
            "name": (
                "Variable Graphics Memory"
                if machine.os == "windows"
                else "GPU memory limit (GTT)"
            ),
            "current_gb": round(machine.setting_gb, 1),
            "min_gb": MIN_SETTING_GB,
            "max_gb": machine.max_setting_gb,
        },
        "reserve_gb": RESERVE_GB,
        "method": method,
        "method_reason": reason,
        "needs_admin": True,
        "needs_restart": True,
        "presets": presets,
        "recommended_setting_gb": recommended,
        "models": models,
        "pending": _pending(state, machine, setup.live_pages if setup else None),
        "revert": {
            "available": bool(linux_record) and state.get("source") == "apply",
            "previous_setting_gb": (
                state.get("previous_setting_gb") if linux_record else None
            ),
        },
        "guided_steps": steps,
        "guided_value": guided_value,
    }


def _read_system_info() -> Tuple[Dict[str, Any], str]:
    from gaia.llm.lemonade_client import (
        LemonadeClient,
        LemonadeClientError,
        resolve_lemonade_base_url,
    )

    url = resolve_lemonade_base_url()
    try:
        info = LemonadeClient(base_url=url, verbose=False).get_system_info(timeout=15)
    except LemonadeClientError as e:
        raise Unavailable(
            f"GPU memory is read from the model server at {url}, which didn't "
            f"answer: {e}. Finish installing it in setup, then try again."
        ) from e
    return info, url


def status(setting_gb: Optional[float] = None) -> Dict[str, Any]:
    """:func:`describe` for the Lemonade GAIA uses, read now."""
    info, url = _read_system_info()
    return describe(info, url, setting_gb)


def _check_range(setting_gb: float, low: float, high: float) -> float:
    if not isinstance(setting_gb, (int, float)) or not math.isfinite(setting_gb):
        raise OutOfRange("The GPU memory value must be a number of GB.")
    if high < low:
        raise OutOfRange(
            f"This PC can't give the GPU more memory and still keep "
            f"{_fmt(RESERVE_GB)} GB for the OS and your apps."
        )
    if setting_gb > high:
        raise OutOfRange(
            f"{_fmt(setting_gb)} GB would leave the CPU less than "
            f"{_fmt(RESERVE_GB)} GB for the OS and your apps. The most this PC "
            f"can give the GPU is {_fmt(high)} GB."
        )
    if setting_gb < low:
        raise OutOfRange(f"The GPU needs at least {_fmt(low)} GB.")
    return float(setting_gb)


def _checked(setting_gb: float, current: Dict[str, Any]) -> float:
    limits = current["setting"]
    return _check_range(setting_gb, limits["min_gb"], limits["max_gb"])


def _require_applies(current: Dict[str, Any]) -> None:
    if not current["applies"]:
        raise NotPossible(current["hidden_reason"])


def apply(setting_gb: float, confirm: bool) -> Dict[str, Any]:
    """Set the GPU memory limit (Linux), effective after a restart.

    Asks for administrator permission; keeps the previous setting for
    :func:`revert`. Never restarts the PC.
    """
    if confirm is not True:
        raise NotConfirmed(
            "Changing GPU memory needs your confirmation: it asks for "
            "administrator permission and takes effect after a restart."
        )
    info, url = _read_system_info()
    current = describe(info, url)
    _require_applies(current)
    if current["method"] != METHOD_APPLY:
        raise NotPossible(
            f"GAIA can't change this here: {current['method_reason']} "
            "Follow the steps under 'Show me how'."
        )
    value = _checked(setting_gb, current)
    setup = _linux_setup()
    pages = pages_for(value)
    content = f"{_CONF_HEADER}\noptions {setup.module} pages_limit={pages}"
    on_disk = _read_conf()
    # Within 0.1 GB: the API rounds to one decimal.
    live_matches = setup.live_pages is not None and abs(
        setup.live_pages - pages
    ) <= pages_for(0.1)
    if on_disk is not None and on_disk.strip() == content:
        if live_matches:
            raise NotPossible(f"GPU memory is already set to {_fmt(value)} GB.")
        raise NotPossible(
            f"GPU memory is already set to {_fmt(value)} GB. Restart your PC to use it."
        )
    if on_disk is None and live_matches:
        raise NotPossible(f"GPU memory is already {_fmt(value)} GB.")

    state = _load_state()
    earlier = (
        (state or {}).get("linux") if (state or {}).get("source") == "apply" else None
    )
    # Undo goes back to what was there before GAIA's first change.
    linux = {
        "module": setup.module,
        "previous_conf": earlier["previous_conf"] if earlier else on_disk,
        "previous_pages": earlier["previous_pages"] if earlier else setup.live_pages,
    }
    previous_gb = (
        state["previous_setting_gb"] if earlier else current["setting"]["current_gb"]
    )
    try:
        _write_conf_as_root(content)
    except PermissionNotGiven:
        raise
    except GpuMemoryError:
        # A failed initramfs rebuild still leaves the new conf: keep Undo possible.
        if _read_conf() == content + "\n":
            _record(current["os"], value, "apply", previous_gb, linux, pages)
        raise
    _record(current["os"], value, "apply", previous_gb, linux, pages)
    log.info(f"GPU memory limit set to {value} GB ({pages} pages); restart pending")
    return describe(info, url, value)


def revert(confirm: bool) -> Dict[str, Any]:
    """Put back the setting GAIA replaced, effective after a restart."""
    if confirm is not True:
        raise NotConfirmed(
            "Undoing the GPU memory change needs your confirmation: it asks "
            "for administrator permission and takes effect after a restart."
        )
    state = _load_state()
    linux = (state or {}).get("linux")
    if not linux or state.get("source") != "apply":
        raise NotPossible(
            "GAIA has no record of changing GPU memory on this PC, so there is "
            "nothing to undo. To reset it yourself, remove "
            f"{_conf_path()} (or run amd-ttm --clear) and restart."
        )
    info, url = _read_system_info()
    current = describe(info, url)
    _require_applies(current)
    if current["os"] != "linux":
        raise NotPossible("Undo is only possible where GAIA made the change.")
    previous = (linux.get("previous_conf") or "").rstrip("\n")
    limit = pages_for(current["setting"]["max_gb"])
    for found in re.findall(r"\bpages_limit=(\d+)", previous):
        if int(found) > limit:
            raise NotPossible(
                f"The saved setting gives the GPU {_fmt(round(gb_for(int(found)), 1))}"
                f" GB, which would leave the CPU less than {_fmt(RESERVE_GB)} GB. "
                f"Edit {_conf_path()} yourself, or run amd-ttm --clear, then "
                "restart."
            )
    _write_conf_as_root(previous or None)
    previous_gb = state.get("previous_setting_gb")
    _record(
        current["os"],
        previous_gb,
        "revert",
        state["setting_gb"],
        None,
        linux.get("previous_pages"),
    )
    log.info("GPU memory limit change undone; restart pending")
    return describe(info, url, previous_gb, check_setting=False)


def record_pending(setting_gb: float) -> Dict[str, Any]:
    """Remember a value the user is setting by hand, to confirm after a restart."""
    info, url = _read_system_info()
    current = describe(info, url)
    _require_applies(current)
    if current["method"] == METHOD_APPLY:
        raise NotPossible("GAIA can set this itself here; use apply instead.")
    value = _checked(setting_gb, current)
    pages = pages_for(value) if current["os"] == "linux" else None
    previous_gb = current["setting"]["current_gb"]
    _record(current["os"], value, "guided", previous_gb, None, pages)
    return describe(info, url, value)


def dismiss_pending() -> Dict[str, Any]:
    """Stop reporting the pending change. Keeps what Undo needs."""
    state = _load_state()
    if state is not None:
        if state.get("linux") and state.get("source") == "apply":
            _save_state({**state, "dismissed": True})
        else:
            _save_state(None)
    return status()
