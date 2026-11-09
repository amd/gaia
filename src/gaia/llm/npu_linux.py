# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Whether this Linux PC is set up to run FastFlowLM on its Ryzen AI NPU.

Lemonade reports an NPU as available once the ``amdxdna`` driver is bound to
it, and its FLM backend as installable on top of that. Neither proves a model
will load: FastFlowLM also needs NPU firmware 1.1 or later, a ``/dev/accel``
node it can open, and locked memory to pin the model's weights. Lemonade checks
those only when the first model loads.

The locked-memory limit is what stops GAIA's own Lemonade on a stock desktop
(#6632): it starts as a child of the user's session, which systemd caps at
8 MB soft *and* hard, while Lemonade's own service runs with
``CAP_SYS_RESOURCE``, so FastFlowLM can lift its own limit there. A process
without that capability can't raise its hard limit, so the fix is
a one-time administrator step, :func:`apply_memlock_fix`, that lifts the limit
for user sessions; it takes effect after the PC restarts.

These checks read what FastFlowLM reads, before setup offers the NPU: sysfs
and the process limits, then FastFlowLM's own ``flm validate --json`` once
GAIA's Lemonade has installed the backend. The portable FLM build Lemonade
installs bundles its own XRT, so no system XRT is needed. They read this
machine, so they speak only for a Lemonade that runs here (:func:`check`).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from gaia.env import child_env
from gaia.logger import get_logger

log = get_logger(__name__)

#: NPU firmware FastFlowLM accepts, from ``flm validate``.
MIN_FIRMWARE = (1, 1)
#: How long one reading stands, so a load path that asks again doesn't probe again.
PROBLEMS_TTL_S = 30.0
VALIDATE_TIMEOUT_S = 30.0
MEMLOCK_FIX_TIMEOUT_S = 300.0

DRIVER = "driver"
DEVICE = "device"
FIRMWARE = "firmware"
ACCESS = "access"
MEMLOCK = "memlock"
RUNTIME = "runtime"

#: The one-time fix: unlimited locked memory for user sessions, as FastFlowLM's
#: and Lemonade's Linux guides set it. limits.d covers terminal logins (PAM);
#: the user@.service drop-in covers the desktop session systemd starts.
MEMLOCK_MARKER = (
    "# GAIA: FastFlowLM pins NPU models in locked memory "
    "(https://amd-gaia.ai/docs/guides/npu#linux)."
)
MEMLOCK_DROPINS: Dict[str, str] = {
    "/etc/security/limits.d/90-gaia-npu.conf": (
        f"{MEMLOCK_MARKER}\n* soft memlock unlimited\n* hard memlock unlimited\n"
    ),
    "/etc/systemd/system/user@.service.d/90-gaia-npu-memlock.conf": (
        f"{MEMLOCK_MARKER}\n[Service]\nLimitMEMLOCK=infinity\n"
    ),
}


@dataclass(frozen=True)
class NpuProblem:
    """One thing between this PC and running a model on its NPU."""

    code: str
    #: What is wrong, in words a user can read.
    title: str
    #: How to fix it, in words a user can follow.
    fix: str
    #: The values in *title* and *fix*, for the UI's own translations of them.
    params: Tuple[Tuple[str, str], ...] = ()

    def as_dict(self) -> Dict[str, Any]:
        return {
            "code": self.code,
            "title": self.title,
            "fix": self.fix,
            "params": dict(self.params),
        }


class MemlockFixError(RuntimeError):
    """The one-time locked-memory fix was not applied.

    ``code`` names why, for the UI's own words: ``not_linux``, ``no_pkexec``,
    ``timeout``, ``cancelled``, ``not_authorized`` or ``failed``.
    """

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _on_linux() -> bool:
    return sys.platform.startswith("linux")


def applies_to(system_info: Any) -> bool:
    """*system_info* is a Linux Lemonade's that reports an NPU, read on Linux."""
    if not _on_linux() or not isinstance(system_info, dict):
        return False
    os_version = str(system_info.get("OS Version") or "").lower()
    if os_version.startswith(("windows", "macos", "darwin")):
        return False
    npu = (system_info.get("devices") or {}).get("amd_npu")
    return isinstance(npu, dict) and bool(npu.get("available"))


# -- the problems, in words -----------------------------------------------------


def _driver_problem() -> NpuProblem:
    return NpuProblem(
        DRIVER,
        "The NPU driver (amdxdna) isn't loaded.",
        "It comes with Linux 6.14 and later. Update the kernel, or install "
        "AMD's NPU driver for your distribution, then restart the PC.",
    )


def _device_problem() -> NpuProblem:
    return NpuProblem(
        DEVICE,
        "The NPU driver is loaded, but the NPU didn't start.",
        "This is most often missing or old NPU firmware. Update your "
        "distribution's linux-firmware package, then restart the PC.",
    )


def _firmware_problem(version: str) -> NpuProblem:
    return NpuProblem(
        FIRMWARE,
        f"The NPU firmware is version {version}, and the NPU needs 1.1 or later.",
        "Update your distribution's linux-firmware package, or AMD's NPU "
        "driver package, then restart the PC.",
        (("version", version),),
    )


def _access_problem(node: Path, group: Optional[str]) -> NpuProblem:
    return NpuProblem(
        ACCESS,
        f"Your account can't open the NPU ({node.as_posix()}).",
        (
            f"Add your account to the {group} group, then sign out and back in."
            if group
            else "Give your account read and write access to it, then sign out "
            "and back in."
        ),
        (("device", node.as_posix()),) + ((("group", group),) if group else ()),
    )


def _memlock_problem(limit: str) -> NpuProblem:
    """Too little locked memory: fixable from GAIA, waiting for a restart, or
    still low after one, which only the NPU guide's steps can untangle."""
    if memlock_fix_installed():
        if restarted_since_fix():
            return NpuProblem(
                MEMLOCK,
                f"The locked-memory limit is still {limit} after a restart.",
                "Something on this PC sets a lower limit after GAIA's (often "
                "DefaultLimitMEMLOCK in /etc/systemd/user.conf). GAIA's NPU guide "
                "shows how to find it.",
                (("limit", limit), ("fix", "stuck")),
            )
        return NpuProblem(
            MEMLOCK,
            f"The locked-memory limit is still {limit}.",
            "GAIA has raised it for everyone on this PC. Restart the PC to finish.",
            (("limit", limit), ("fix", "restart")),
        )
    return NpuProblem(
        MEMLOCK,
        f"The locked-memory limit is {limit}, and the NPU needs it unlimited.",
        "GAIA can raise it for everyone on this PC, once, with an administrator "
        "password; then restart the PC.",
        (("limit", limit), ("fix", "apply")),
    )


def _runtime_problem(detail: str) -> NpuProblem:
    return NpuProblem(
        RUNTIME,
        f"The NPU runtime (FastFlowLM) didn't run: {detail}",
        "Restart the PC and check again. If it still fails, GAIA's log has "
        "the full error to report.",
        (("detail", detail),),
    )


# -- the machine --------------------------------------------------------------


def _driver_of(device: Path) -> Optional[str]:
    """The kernel driver bound to a sysfs device, from its ``driver`` link."""
    try:
        return os.path.basename(os.readlink(device / "driver"))
    except OSError:
        return None


def _can_open(node: Path) -> bool:
    return os.access(node, os.R_OK | os.W_OK)


def _bound_devices(root: Path) -> List[Tuple[str, Path]]:
    """``(accel name, PCI device dir)`` for each NPU bound to ``amdxdna``."""
    return [
        (accel.name, accel / "device")
        for accel in sorted((root / "sys/class/accel").glob("accel*"))
        if _driver_of(accel / "device") == "amdxdna"
    ]


def _firmware_version(device: Path) -> Optional[str]:
    try:
        return (device / "fw_version").read_text().strip() or None
    except OSError:
        return None


def _version_tuple(text: str) -> Tuple[int, ...]:
    parts = []
    for part in text.split("."):
        if not part.isdigit():
            break
        parts.append(int(part))
    return tuple(parts)


def _group_name(gid: int) -> Optional[str]:
    try:
        import grp

        name = grp.getgrgid(gid).gr_name
    except (ImportError, KeyError):
        return None
    return None if name == "root" else name


def _limit_text(limit: int) -> str:
    if limit < 1024 * 1024:
        return f"{limit // 1024} KB"
    if limit < 1024**3:
        return f"{limit // 1024**2} MB"
    return f"{limit / 1024**3:.1f} GB"


def _own_memlock_hard() -> Optional[int]:
    """This process's locked-memory hard limit; None when unlimited.

    GAIA's Lemonade and its FastFlowLM inherit it, and FastFlowLM raises its
    soft limit itself, so only the hard one can stop it.
    """
    try:
        import resource
    except ImportError:
        return None
    hard = resource.getrlimit(resource.RLIMIT_MEMLOCK)[1]
    return None if hard == resource.RLIM_INFINITY else hard


def memlock_needed_bytes() -> int:
    """Locked memory GAIA's NPU model pins: FastFlowLM maps its weights locked."""
    from gaia.llm.lemonade_client import NPU_DEFAULT_MODEL_NAME, find_model_requirement
    from gaia.llm.model_fit import required_memory_gb

    size_gb = find_model_requirement(NPU_DEFAULT_MODEL_NAME).size_gb
    return int(required_memory_gb(size_gb) * 1e9)


def raise_memlock_soft_limit() -> None:
    """Lift this process's locked-memory soft limit to its hard limit.

    The Lemonade GAIA starts, and the FastFlowLM server it starts, inherit it.
    Raising a soft limit up to the hard one needs no privilege.
    """
    try:
        import resource
    except ImportError:
        return
    soft, hard = resource.getrlimit(resource.RLIMIT_MEMLOCK)
    if soft == hard:
        return
    try:
        resource.setrlimit(resource.RLIMIT_MEMLOCK, (hard, hard))
    except (ValueError, OSError) as e:
        log.warning("Could not raise the locked-memory limit to %s: %s", hard, e)


_READ = object()


def host_problems(
    root: Path = Path("/"), memlock_hard: Any = _READ
) -> List[NpuProblem]:
    """What sysfs and the process limits say stops the NPU; empty when nothing.

    *root* and *memlock_hard* (None for unlimited or not GAIA's to judge) stand
    in for the real filesystem and limit in tests. The driver comes first:
    without it, nothing else can be judged.
    """
    if not (root / "sys/module/amdxdna").exists():
        return [_driver_problem()]
    devices = _bound_devices(root)
    if not devices:
        return [_device_problem()]
    found: List[NpuProblem] = []
    firmware = _firmware_version(devices[0][1])
    if firmware and _version_tuple(firmware) < MIN_FIRMWARE:
        found.append(_firmware_problem(firmware))
    present = [
        node
        for node in (root / "dev/accel" / name for name, _ in devices)
        if node.exists()
    ]
    if not present:
        found.append(_device_problem())
    elif not any(_can_open(node) for node in present):
        try:
            group = _group_name(present[0].stat().st_gid)
        except OSError:
            group = None
        found.append(_access_problem(present[0], group))
    hard = _own_memlock_hard() if memlock_hard is _READ else memlock_hard
    if hard is not None and hard < memlock_needed_bytes():
        found.append(_memlock_problem(_limit_text(hard)))
    return found


# -- FastFlowLM's own check ---------------------------------------------------


def _validate_problems(report: Dict[str, Any]) -> List[NpuProblem]:
    """Problems from ``flm validate --json``; empty when it says ``ready``."""
    if report.get("ready"):
        return []
    found: List[NpuProblem] = []
    if report.get("amd_device_found") is False:
        found.append(_device_problem())
    if report.get("all_fw_ok") is False:
        versions = [
            ".".join(
                str(d.get(k, 0))
                for k in ("fw_major", "fw_minor", "fw_patch", "fw_build")
            )
            for d in report.get("devices") or []
            if isinstance(d, dict) and d.get("fw_ok") is False
        ]
        found.append(_firmware_problem(versions[0] if versions else "unknown"))
    if report.get("npu_driver_ok") is False:
        found.append(_driver_problem())
    if report.get("memlock_ok") is False:
        # Bytes, as a string ("8388608" from FLM 1.0.7).
        limit = str(report.get("memlock_limit") or "")
        found.append(
            _memlock_problem(_limit_text(int(limit)) if limit.isdigit() else "too low")
        )
    if report.get("enough_cols") is False:
        found.append(
            _runtime_problem(
                "this NPU has fewer than the 8 columns FastFlowLM needs (it "
                "needs a Ryzen AI 300-series or newer NPU)"
            )
        )
    return found or [_runtime_problem("flm validate reported the NPU not ready")]


def _json_report(stdout: str) -> Optional[Dict[str, Any]]:
    """The first JSON object in ``flm validate --json`` output, past any log lines."""
    decoder = json.JSONDecoder()
    start = stdout.find("{")
    while start >= 0:
        try:
            report, _ = decoder.raw_decode(stdout, start)
        except json.JSONDecodeError:
            start = stdout.find("{", start + 1)
            continue
        if isinstance(report, dict):
            return report
        start = stdout.find("{", start + 1)
    return None


def validate(flm: Path) -> List[NpuProblem]:
    """FastFlowLM's own readiness check, ``flm validate --json``, as problems.

    A build that answers without a report but exits 0 is not taken as a
    broken NPU: that is an FLM that predates ``--json``, and is logged.
    """
    # flm inherits our soft limit; judge it at the hard limit Lemonade will get.
    raise_memlock_soft_limit()
    try:
        result = subprocess.run(
            [str(flm), "validate", "--json"],
            env=child_env(),
            capture_output=True,
            text=True,
            errors="replace",
            timeout=VALIDATE_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        log.warning("flm validate at %s failed to run: %s", flm, e)
        return [_runtime_problem(str(e))]
    report = _json_report(result.stdout)
    if report is not None:
        return _validate_problems(report)
    said = (result.stderr or result.stdout).strip()
    if result.returncode == 0:
        log.warning(
            "flm validate at %s gave no JSON report, so its own check is skipped: %s",
            flm,
            said,
        )
        return []
    log.warning("flm validate at %s exited %s: %s", flm, result.returncode, said)
    lines = said.splitlines()
    return [_runtime_problem(lines[-1] if lines else f"exit code {result.returncode}")]


def embedded_flm() -> Optional[Path]:
    """The ``flm`` GAIA's own Lemonade installed, or None before it has one."""
    from gaia.llm.lemonade_embedded import EmbeddedLemonade

    flm_dir = EmbeddedLemonade().cache_dir / "bin" / "flm"
    for path in sorted(flm_dir.rglob("flm")) if flm_dir.is_dir() else []:
        if path.is_file() and os.access(path, os.X_OK):
            return path
    return None


# -- which Lemonade, and the answer -------------------------------------------


def _lemonade_in_use() -> Tuple[bool, bool]:
    """``(GAIA's own, runs on this PC)`` for the Lemonade GAIA uses."""
    from gaia.config import GaiaConfigError
    from gaia.llm import lemonade_choice
    from gaia.llm.lemonade_client import _is_local_url

    try:
        info = lemonade_choice.describe()
    except (lemonade_choice.LemonadeChoiceError, GaiaConfigError) as e:
        log.warning("Can't tell which Lemonade runs FastFlowLM: %s", e)
        return False, False
    if info["mode"] == lemonade_choice.MODE_GAIA:
        return True, True
    return False, bool(info.get("base_url") and _is_local_url(info["base_url"]))


def _probe() -> List[NpuProblem]:
    own, local = _lemonade_in_use()
    if not local:
        # Another PC's NPU: this one's sysfs says nothing about it.
        return []
    if not own:
        # A Lemonade the user runs sets its own limits and user; only the
        # hardware is this PC's to judge.
        return [
            p
            for p in host_problems(memlock_hard=None)
            if p.code in (DRIVER, DEVICE, FIRMWARE)
        ]
    found = host_problems()
    if any(p.code in (DRIVER, DEVICE) for p in found):
        return found
    flm = embedded_flm()
    if flm is None:
        return found
    checked = validate(flm)
    codes = {p.code for p in checked}
    return [p for p in found if p.code not in codes] + checked


_CACHE: Dict[str, Any] = {}
_CACHE_LOCK = threading.Lock()


def problems() -> List[NpuProblem]:
    """What stops FastFlowLM running on this PC's NPU; empty when nothing does.

    One reading stands for :data:`PROBLEMS_TTL_S`; :func:`forget` drops it.
    """
    with _CACHE_LOCK:
        cached = _CACHE.get("problems")
        if cached and time.monotonic() - cached[0] < PROBLEMS_TTL_S:
            return list(cached[1])
    # Outside the lock: flm validate can take seconds, and nothing else waits on it.
    found = _probe()
    with _CACHE_LOCK:
        _CACHE["problems"] = (time.monotonic(), found)
    return list(found)


def forget() -> None:
    """Read the machine afresh next time, after the user fixed something."""
    with _CACHE_LOCK:
        _CACHE.clear()


def check(system_info: Any) -> List[NpuProblem]:
    """:func:`problems` when *system_info* is a Linux Lemonade with an NPU, else none."""
    if not applies_to(system_info):
        return []
    return problems()


def summary(found: List[NpuProblem]) -> Optional[str]:
    """What is missing, as a predicate: "This model {summary}." reads right."""
    if not found:
        return None
    detail = " ".join(f"{p.title} {p.fix}" for p in found).rstrip(".")
    return f"needs this PC's NPU set up first: {detail}"


# -- the one-time locked-memory fix -------------------------------------------


def memlock_fix_installed(root: Path = Path("/")) -> bool:
    """GAIA's locked-memory drop-ins are in place (they apply after a restart)."""
    for path, text in MEMLOCK_DROPINS.items():
        try:
            if (root / path.lstrip("/")).read_text() != text:
                return False
        except OSError:
            return False
    return True


def _boot_time(root: Path = Path("/")) -> Optional[float]:
    """When this PC started, from ``btime`` in ``/proc/stat``."""
    try:
        for line in (root / "proc/stat").read_text().splitlines():
            if line.startswith("btime "):
                return float(line.split()[1])
    except (OSError, ValueError, IndexError):
        return None
    return None


def restarted_since_fix(root: Path = Path("/")) -> bool:
    """The PC has started since GAIA's drop-ins were written, so they should
    already apply."""
    booted = _boot_time(root)
    if booted is None:
        return False
    try:
        written = max(
            (root / path.lstrip("/")).stat().st_mtime for path in MEMLOCK_DROPINS
        )
    except OSError:
        return False
    return booted > written


def memlock_fix_script() -> str:
    """The shell script the administrator step runs: write both drop-ins."""
    lines = ["set -e", "umask 022"]
    for path, text in MEMLOCK_DROPINS.items():
        lines.append(f"mkdir -p '{os.path.dirname(path)}'")
        lines.append(f"cat > '{path}' <<'GAIA_EOF'\n{text}GAIA_EOF")
    lines.append("systemctl daemon-reload 2>/dev/null || true")
    return "\n".join(lines) + "\n"


def apply_memlock_fix() -> None:
    """Lift the locked-memory limit for user sessions, asking for the password.

    Runs one ``pkexec`` step, so the desktop shows its own administrator
    prompt. The limit applies to sessions that start after it, so the PC
    needs a restart before FastFlowLM sees it.

    Raises:
        MemlockFixError: Not Linux, no ``pkexec``, the prompt was dismissed,
            or the step failed. The message says what to do instead.
    """
    if not _on_linux():
        raise MemlockFixError("not_linux", "The locked-memory fix is only for Linux.")
    if memlock_fix_installed():
        forget()
        return
    pkexec = shutil.which("pkexec")
    if pkexec is None:
        raise MemlockFixError(
            "no_pkexec",
            "GAIA can't ask for the administrator password here because polkit's "
            "pkexec isn't installed. Install polkit, or have an administrator "
            "set memlock to unlimited for your account (see "
            "https://amd-gaia.ai/docs/guides/npu#linux), then restart the PC.",
        )
    try:
        result = subprocess.run(
            [pkexec, "/bin/sh", "-c", memlock_fix_script()],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=MEMLOCK_FIX_TIMEOUT_S,
            check=False,
        )
    except subprocess.TimeoutExpired as e:
        raise MemlockFixError(
            "timeout", "The administrator prompt wasn't answered in time. Try again."
        ) from e
    forget()
    if result.returncode == 126:
        raise MemlockFixError(
            "cancelled",
            "The administrator prompt was closed, so nothing changed. Try again "
            "and enter an administrator's password.",
        )
    if result.returncode == 127:
        raise MemlockFixError(
            "not_authorized",
            "No administrator prompt could be shown here (no desktop session), or "
            "this account may not run it. Run GAIA from the desktop, or have an "
            "administrator follow https://amd-gaia.ai/docs/guides/npu#linux.",
        )
    if result.returncode != 0 or not memlock_fix_installed():
        said = (result.stderr or result.stdout).strip()
        log.error("Locked-memory fix failed (exit %s): %s", result.returncode, said)
        raise MemlockFixError(
            "failed",
            "GAIA couldn't raise the locked-memory limit. Details are in the GAIA "
            "server log; https://amd-gaia.ai/docs/guides/npu#linux has the steps "
            "to do it by hand.",
        )
    log.info("Locked-memory fix installed; it applies after a restart")
