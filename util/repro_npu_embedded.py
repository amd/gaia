# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Reproduce an NPU chat through GAIA's own Lemonade, and record why it fails.

Issue #6632: on Linux, a first chat on ``gemma4-it-e4b-FLM`` through GAIA's
embedded Lemonade fails, while an FLM model chats from the Lemonade app on the
same PC. This runs GAIA's path cell by cell, so the failing cell names the cause:

    Lemonade version x model x context window x request

* **Lemonade**: the version GAIA embeds, plus any ``--lemonade VER=TAG``
  (e.g. ``2026.46.0=candidate-v2026.46.0``), each a private install.
* **window**: FastFlowLM's default (32K) and the one GAIA picks for this PC.
* **request**: a plain chat, a 25K-token plain chat (GAIA's first-turn size,
  no tools), and GAIA's real first agent turn (``gaia -p``: system prompt and
  tools, loading the model the way the app does).

Everything runs under a throwaway ``GAIA_HOME`` and its own Lemonade port: the
user's ``~/.gaia`` and their own Lemonade are never changed. FastFlowLM keeps
model weights in ``~/.flm/models`` whatever GAIA's home is, so a model already
pulled there is reused rather than downloaded again.

Output: one ``.tar.gz`` holding ``summary.md`` (one row per cell),
``results.json``, the machine's NPU facts (driver, firmware, ``/dev/accel``,
memlock, groups, ``dmesg``), each Lemonade's log, the FLM binary it runs with
its ``flm version`` and ``flm validate``, the command line, environment and
limits of the running lemond and flm processes, and every HTTP status and error
body. API keys and tokens are redacted.

Usage::

    python util/repro_npu_embedded.py
    python util/repro_npu_embedded.py --lemonade 2026.46.0=candidate-v2026.46.0

Needs GAIA installed (``pip install amd-gaia``) and, for the agent cells, the
flagship agent (``pip install gaia-agent-gaia``); without it those cells are
reported as skipped. Exits 0 when every cell answered, 1 when any failed, 2
when the run could not start.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import subprocess
import sys
import tarfile
import tempfile
import time
import traceback
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

DEFAULT_MODELS = ["gemma4-it-e4b-FLM", "gemma3-4b-FLM"]
#: What the other Lemonade holds on the NPU in the "NPU held" cells: the model
#: the reporter had loaded in the Lemonade app while GAIA's load failed.
HOLDER_MODEL = "gemma3-4b-FLM"
REQUESTS = ["plain", "large", "agent"]
FLM_DEFAULT_CTX = 32768
PLAIN_PROMPT = "Reply with one short sentence: what is 2 + 2?"
AGENT_PROMPT = "Say hello in one short sentence."
#: About the size of GAIA's first agent turn (system prompt and tools), and
#: well inside FastFlowLM's default 32K window.
LARGE_PROMPT_TOKENS = 22000
LOAD_TIMEOUT_S = 900
CHAT_TIMEOUT_S = 900
AGENT_TIMEOUT_S = 1800
PULL_TIMEOUT_S = 7200
BACKEND_TIMEOUT_S = 1800

_SECRET_PATTERNS = [
    re.compile(r"(Bearer\s+)[A-Za-z0-9._~+/=-]{8,}"),
    re.compile(
        r"((?:API_KEY|TOKEN|SECRET|PASSWORD)[A-Za-z_]*\"?\s*[=:]\s*\"?)[^\s\"',}]{6,}",
        re.IGNORECASE,
    ),
    re.compile(r"(hf_)[A-Za-z0-9]{20,}"),
]


def redact(text: str, secrets: List[str]) -> str:
    """*text* with every API key, token and password replaced."""
    for secret in secrets:
        if secret:
            text = text.replace(secret, "<redacted>")
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub(lambda m: m.group(1) + "<redacted>", text)
    return text


def run(argv: List[str], timeout: float = 60, **kwargs: Any) -> Dict[str, Any]:
    """Run a command and keep everything it said, whatever happened."""
    started = time.time()
    try:
        done = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout,
            check=False,
            **kwargs,
        )
    except FileNotFoundError as e:
        return {"argv": argv, "exit": None, "error": f"not found: {e}"}
    except subprocess.TimeoutExpired as e:
        return {
            "argv": argv,
            "exit": None,
            "error": f"timed out after {timeout:.0f}s",
            "stdout": e.stdout if isinstance(e.stdout, str) else "",
            "stderr": e.stderr if isinstance(e.stderr, str) else "",
        }
    return {
        "argv": argv,
        "exit": done.returncode,
        "stdout": done.stdout,
        "stderr": done.stderr,
        "seconds": round(time.time() - started, 1),
    }


def shell(command: str, timeout: float = 60) -> Dict[str, Any]:
    if sys.platform == "win32":
        return {"argv": command, "exit": None, "error": "not run on Windows"}
    return run(["bash", "-c", command], timeout=timeout)


def read(path: str) -> str:
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return f"<unreadable: {e}>"


# -- the machine --------------------------------------------------------------

_LINUX_FACTS = {
    "uname": "uname -a",
    "id": "id",
    "lsmod_amdxdna": "lsmod | grep -i amdxdna",
    "modinfo_amdxdna": "modinfo amdxdna 2>&1 | head -20",
    "fw_version": "cat /sys/bus/pci/drivers/amdxdna/*/fw_version",
    "accel_sysfs": "for a in /sys/class/accel/accel*; do echo $a "
    "$(readlink -f $a/device/driver); done",
    "dev_accel": "ls -l /dev/accel/",
    "firmware_files": "ls -lR /lib/firmware/amdnpu/ 2>&1 | head -40",
    "dmesg_xdna": "dmesg 2>&1 | grep -iE 'xdna|amdnpu|accel' | tail -80",
    "journal_xdna": "journalctl -k --no-pager 2>&1 | grep -iE 'xdna|amdnpu' | tail -80",
    "lspci_npu": "lspci -nn 2>&1 | grep -iE '1022:17f0|1022:1502|signal processing'",
    "xrt_smi": "xrt-smi examine 2>&1 | head -60",
    "system_flm_version": "command -v flm && flm version",
    "system_flm_validate": "command -v flm && flm validate --json",
    "limits_conf_memlock": "grep -ihs memlock /etc/security/limits.conf "
    "/etc/security/limits.d/*",
    "systemd_user_memlock": "systemctl show user@$(id -u).service -p LimitMEMLOCK",
    "ulimit_l": "ulimit -Sl; ulimit -Hl",
    "free": "free -g",
}


def machine_facts() -> Dict[str, Any]:
    """What FastFlowLM needs from this PC, read before anything starts."""
    facts: Dict[str, Any] = {"platform": platform.platform(), "python": sys.version}
    try:
        import gaia.version as gv

        facts["gaia_version"] = gv.__version__
        facts["gaia_lemonade_version"] = gv.LEMONADE_VERSION
    except ImportError as e:
        facts["gaia_version"] = f"<not importable: {e}>"
    if sys.platform.startswith("linux"):
        facts["os_release"] = read("/etc/os-release")
        facts["commands"] = {name: shell(cmd) for name, cmd in _LINUX_FACTS.items()}
    return facts


def users_own_lemonade() -> Dict[str, Any]:
    """Whether a Lemonade already answers on its usual ports. Only read."""
    found: Dict[str, Any] = {}
    for port in (13305, 8000):
        url = f"http://localhost:{port}/api/v1/health"
        try:
            with urllib.request.urlopen(url, timeout=3) as r:
                found[str(port)] = json.loads(r.read().decode(errors="replace"))
        except (urllib.error.URLError, OSError, ValueError) as e:
            found[str(port)] = f"<no answer: {e}>"
    return found


# -- one Lemonade -------------------------------------------------------------


class Lemonade:
    """HTTP to one Lemonade, keeping the status and body of every answer."""

    def __init__(self, base_url: str, api_key: Optional[str]):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key

    def call(
        self, method: str, path: str, body: Any = None, timeout: float = 60
    ) -> Dict[str, Any]:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(
            f"{self.base_url}/{path.lstrip('/')}", data=data, method=method
        )
        req.add_header("Content-Type", "application/json")
        if self.api_key:
            req.add_header("Authorization", f"Bearer {self.api_key}")
        started = time.time()
        out: Dict[str, Any] = {"method": method, "path": path}
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                out["status"] = r.status
                out["body"] = r.read().decode(errors="replace")
        except urllib.error.HTTPError as e:
            out["status"] = e.code
            out["body"] = e.read().decode(errors="replace")
        except (urllib.error.URLError, OSError) as e:
            out["status"] = None
            out["error"] = f"{type(e).__name__}: {e}"
        out["seconds"] = round(time.time() - started, 1)
        return out

    def get_json(self, path: str) -> Any:
        answer = self.call("GET", path, timeout=30)
        try:
            return json.loads(answer.get("body") or "null")
        except ValueError:
            return answer


def processes(root_pid: Optional[int]) -> List[Dict[str, Any]]:
    """lemond and its children (llama-server, flm): command line, limits, env."""
    try:
        import psutil
    except ImportError:
        return [{"error": "psutil is not installed"}]
    if not root_pid:
        return []
    try:
        root = psutil.Process(root_pid)
        tree = [root, *root.children(recursive=True)]
    except psutil.Error as e:
        return [{"pid": root_pid, "error": str(e)}]
    out = []
    for proc in tree:
        entry: Dict[str, Any] = {"pid": proc.pid}
        try:
            entry["cmdline"] = proc.cmdline()
            entry["exe"] = proc.exe()
            entry["cwd"] = proc.cwd()
            entry["status"] = proc.status()
            entry["rss_mb"] = round(proc.memory_info().rss / 1e6)
            entry["environ"] = {
                k: v
                for k, v in proc.environ().items()
                if k.startswith(("PATH", "LD_", "XILINX", "XRT", "HOME", "FLM"))
                or k in ("USER", "LANG", "XDG_RUNTIME_DIR")
            }
        except psutil.Error as e:
            entry["error"] = str(e)
        if sys.platform.startswith("linux"):
            entry["limits"] = read(f"/proc/{proc.pid}/limits")
        out.append(entry)
    return out


def gaia_cli() -> List[str]:
    """The ``gaia`` command of this Python's GAIA install."""
    here = Path(sys.executable).parent
    for candidate in (here / "gaia", here / "gaia.exe", here / "Scripts" / "gaia.exe"):
        if candidate.is_file():
            return [str(candidate)]
    # The same entry point, when the console script isn't beside this Python.
    return [sys.executable, "-m", "gaia.cli"]


def find_flm(cache_dir: Path) -> Optional[Path]:
    """The ``flm`` this Lemonade installed into its private cache."""
    name = "flm.exe" if sys.platform == "win32" else "flm"
    flm_dir = cache_dir / "bin" / "flm"
    for path in sorted(flm_dir.rglob(name)) if flm_dir.is_dir() else []:
        if path.is_file():
            return path
    return None


_FLM_VERSIONS: Dict[str, Dict[str, Any]] = {}


def flm_origin(binary: Path, cache_dir: Path) -> str:
    """Where an flm binary came from: this Lemonade's download, or the system."""
    try:
        binary.resolve().relative_to(cache_dir.resolve())
        return "bundled: this Lemonade's own download"
    except ValueError:
        pass
    if binary.as_posix().startswith(("/usr/", "/opt/")):
        return f"system package: {binary}"
    return f"elsewhere (PATH?): {binary}"


def flm_in_use(procs: List[Dict[str, Any]], cache_dir: Path) -> List[Dict[str, Any]]:
    """Each FastFlowLM process in *procs*: binary, version, origin, command line."""
    found = []
    for proc in procs:
        exe = Path(proc.get("exe") or "")
        if not exe.name.lower().startswith("flm"):
            continue
        # The portable Linux build's `flm` script execs `flm-real` beside it.
        launcher = exe.with_name("flm.exe" if exe.suffix == ".exe" else "flm")
        binary = launcher if launcher.is_file() else exe
        key = str(binary)
        if key not in _FLM_VERSIONS:
            _FLM_VERSIONS[key] = run([key, "version"])
        said = _FLM_VERSIONS[key]
        found.append(
            {
                "pid": proc.get("pid"),
                "exe": str(exe),
                "cmdline": proc.get("cmdline"),
                "origin": flm_origin(binary, cache_dir),
                "version": (said.get("stdout") or said.get("error") or "").strip(),
            }
        )
    return found


def large_prompt() -> str:
    line = "This line only fills the context to the size of a first agent turn. "
    # About 16 tokens a line.
    return line * (LARGE_PROMPT_TOKENS // 16) + "\n\n" + PLAIN_PROMPT


def chat(lemonade: Lemonade, model: str, prompt: str) -> Dict[str, Any]:
    return lemonade.call(
        "POST",
        "chat/completions",
        {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 32,
            "stream": False,
        },
        timeout=CHAT_TIMEOUT_S,
    )


def answer_of(response: Dict[str, Any]) -> Optional[str]:
    if response.get("status") != 200:
        return None
    try:
        body = json.loads(response.get("body") or "{}")
        return body["choices"][0]["message"].get("content") or "<empty reply>"
    except (ValueError, KeyError, IndexError, TypeError):
        return None


def dmesg_tail() -> Dict[str, Any]:
    return shell("dmesg 2>&1 | grep -iE 'xdna|amdnpu' | tail -30")


def pin_release(le: Any, version: str, tag: str) -> Dict[str, str]:
    """Let EmbeddedLemonade install a release GAIA doesn't pin, checked
    against the digest that release publishes."""
    name = le.asset_name(version)
    api = f"https://api.github.com/repos/lemonade-sdk/lemonade/releases/tags/{tag}"
    with urllib.request.urlopen(api, timeout=30) as r:
        release = json.loads(r.read().decode())
    asset = next((a for a in release.get("assets", []) if a["name"] == name), None)
    digest = str((asset or {}).get("digest") or "")
    if not digest.startswith("sha256:"):
        raise RuntimeError(f"{tag} publishes no {name} with a sha256 digest")
    le.EMBEDDABLE_SHA256[name] = digest.split(":", 1)[1]
    url = asset["browser_download_url"]
    le.asset_url = lambda _version=version: url
    return {"tag": tag, "asset": name, "digest": digest}


class VersionRun:
    """Child process: one Lemonade version under its own GAIA_HOME."""

    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.out = Path(args.out_dir)
        self.out.mkdir(parents=True, exist_ok=True)
        self.record: Dict[str, Any] = {"lemonade": args.one_version, "cells": []}
        self.secrets: List[str] = []
        self.lemonade: Optional[Lemonade] = None
        self.pid: Optional[int] = None
        self.base_url = ""
        self.cache_dir = Path()

    def save(self) -> None:
        text = json.dumps(self.record, indent=2, default=str)
        (self.out / "version.json").write_text(
            encoding="utf-8", data=redact(text, self.secrets)
        )

    def run(self) -> int:
        from gaia.config import GaiaConfig
        from gaia.llm import lemonade_embedded as le
        from gaia.llm.lemonade_choice import MODE_GAIA

        record = self.record
        record["gaia_home"] = os.environ.get("GAIA_HOME")
        emb = None
        try:
            if self.args.tag:
                record["release"] = pin_release(
                    le, self.args.one_version, self.args.tag
                )
            GaiaConfig.save_key("lemonade_mode", MODE_GAIA)
            emb = le.EmbeddedLemonade(version=self.args.one_version)
            self.cache_dir = emb.cache_dir
            self._start(emb)
            self._install_flm(emb)
            for model in self.args.model:
                self._model(model)
            if self.args.holder_model:
                self._held_cells(le)
        except Exception:  # pylint: disable=broad-except
            record["fatal"] = traceback.format_exc()
        finally:
            if emb is not None:
                self._collect(emb)
            self.save()
        failed = any(c.get("ok") is False for c in record["cells"])
        return 1 if failed or record.get("fatal") else 0

    def _start(self, emb: Any) -> None:
        started = time.time()
        status = emb.start()
        self.pid = status.pid
        self.record["start"] = {
            "seconds": round(time.time() - started, 1),
            "port": status.port,
            "pid": status.pid,
        }
        api_key = emb.current_api_key()
        self.secrets.append(api_key or "")
        # GAIA's own clients in this process read the key from the environment.
        os.environ["LEMONADE_API_KEY"] = api_key or ""
        self.base_url = emb.base_url_for(status.port)
        self.lemonade = Lemonade(self.base_url, api_key)
        self.record["system_info"] = self.lemonade.get_json("system-info")
        self.save()

    def _install_flm(self, emb: Any) -> None:
        """Install ``flm:npu`` the way ``gaia init`` and the app do: over HTTP."""
        from gaia.llm.lemonade_client import LemonadeClient

        client = LemonadeClient(base_url=self.base_url, verbose=False)
        started = time.time()
        try:
            client.install_backend("flm:npu", timeout=BACKEND_TIMEOUT_S)
            install: Dict[str, Any] = {"ok": True}
        except Exception as e:  # pylint: disable=broad-except
            install = {"ok": False, "error": str(e)}
        install["seconds"] = round(time.time() - started, 1)
        self.record["flm_install"] = install
        self.record["system_info_after_install"] = self.lemonade.get_json("system-info")
        self.record["lemond_config"] = read(str(emb.config_dir / "config.json"))
        flm = find_flm(emb.cache_dir)
        self.record["flm_binary"] = str(flm) if flm else None
        if flm:
            self.record["flm_origin"] = flm_origin(flm, emb.cache_dir)
        if flm:
            self.record["flm_version"] = run([str(flm), "version"])
            self.record["flm_validate"] = run([str(flm), "validate", "--json"])
        self.save()

    def _model(self, model: str) -> None:
        from gaia.llm.lemonade_client import LemonadeClient, resolve_ctx_size

        client = LemonadeClient(base_url=self.base_url, verbose=False)
        started = time.time()
        try:
            # pull_model installs a missing FLM backend first, as the app's download does.
            client.pull_model(model, timeout=PULL_TIMEOUT_S)
        except Exception as e:  # pylint: disable=broad-except
            self.record["cells"].append(
                {
                    "model": model,
                    "request": "pull",
                    "ok": False,
                    "error": str(e),
                    "seconds": round(time.time() - started, 1),
                }
            )
            self.save()
            return
        windows: List[Tuple[str, int]] = [("flm-default", FLM_DEFAULT_CTX)]
        try:
            gaia_ctx = resolve_ctx_size(model, base_url=self.base_url)
        except Exception as e:  # pylint: disable=broad-except
            self.record["cells"].append(
                {"model": model, "request": "gaia-window", "ok": False, "error": str(e)}
            )
            gaia_ctx = None
        if gaia_ctx and gaia_ctx != FLM_DEFAULT_CTX:
            windows.append(("gaia", gaia_ctx))
        for window, ctx in windows:
            for request in self.args.request:
                if request == "agent":
                    cell = self._agent_cell(model, window, ctx)
                elif request == "large" and ctx < LARGE_PROMPT_TOKENS + 4096:
                    continue
                else:
                    cell = self._direct_cell(model, window, ctx, request)
                self.record["cells"].append(cell)
                self.save()

    def _direct_cell(self, model: str, window: str, ctx: int, request: str) -> Dict:
        """Load at *ctx* through Lemonade's ``/load``, then one chat request."""
        lemonade = self.lemonade
        cell: Dict[str, Any] = {
            "model": model,
            "window": window,
            "ctx": ctx,
            "request": request,
        }
        lemonade.call("POST", "unload", {}, timeout=120)
        cell["load"] = lemonade.call(
            "POST",
            "load",
            {"model_name": model, "ctx_size": ctx},
            timeout=LOAD_TIMEOUT_S,
        )
        cell["health_after_load"] = lemonade.get_json("health")
        cell["processes_after_load"] = processes(self.pid)
        cell["flm"] = flm_in_use(cell["processes_after_load"], self.cache_dir)
        if cell["load"].get("status") == 200:
            prompt = large_prompt() if request == "large" else PLAIN_PROMPT
            cell["chat"] = chat(lemonade, model, prompt)
            cell["answer"] = answer_of(cell["chat"])
        cell["ok"] = cell.get("answer") is not None
        cell["health_after"] = lemonade.get_json("health")
        if not cell["ok"]:
            cell["dmesg"] = dmesg_tail()
        return cell

    def _agent_cell(self, model: str, window: str, ctx: int) -> Dict[str, Any]:
        """GAIA's real first turn: ``gaia -p``, which loads the model itself."""
        cell: Dict[str, Any] = {
            "model": model,
            "window": window,
            "ctx": ctx,
            "request": "agent",
        }
        try:
            import gaia_agent  # noqa: F401  pylint: disable=unused-import,import-outside-toplevel
        except ImportError as e:
            cell.update(ok=None, skipped=f"the flagship agent is not installed: {e}")
            return cell
        self.lemonade.call("POST", "unload", {}, timeout=120)
        env = dict(os.environ)
        if window == "flm-default":
            env["GAIA_CTX_SIZE"] = str(ctx)
        with tempfile.TemporaryDirectory(prefix="gaia-repro-cwd-") as cwd:
            cell["gaia_p"] = run(
                [*gaia_cli(), "-p", AGENT_PROMPT, "--model", model]
                + ["--output-format", "json"],
                timeout=AGENT_TIMEOUT_S,
                env=env,
                cwd=cwd,
            )
        said = (cell["gaia_p"].get("stdout") or "") + (
            cell["gaia_p"].get("stderr") or ""
        )
        cell["ok"] = cell["gaia_p"].get("exit") == 0
        cell["errors_5xx"] = re.findall(r"Request failed with status 5\d\d:.*", said)
        cell["health_after"] = self.lemonade.get_json("health")
        cell["processes_after"] = processes(self.pid)
        cell["flm"] = flm_in_use(cell["processes_after"], self.cache_dir)
        if not cell["ok"]:
            cell["dmesg"] = dmesg_tail()
        return cell

    def _held_cells(self, le: Any) -> None:
        """The same loads while a second Lemonade already has an FLM model on the NPU.

        The reporter's Lemonade app had ``gemma3-4b-FLM`` loaded when GAIA's
        load failed: two lemond instances, two flm processes, one NPU.
        """
        model = self.args.holder_model
        home = Path(os.environ["GAIA_HOME"]) / "npu-holder"
        holder = le.EmbeddedLemonade(version=self.args.one_version, home=home)
        held: Dict[str, Any] = {"model": model}
        self.record["npu_holder"] = held
        try:
            status = holder.start()
            key = holder.current_api_key()
            self.secrets.append(key or "")
            other = Lemonade(holder.base_url_for(status.port), key)
            held["install"] = other.call(
                "POST",
                "install",
                {"recipe": "flm", "backend": "npu"},
                timeout=BACKEND_TIMEOUT_S,
            )
            held["pull"] = other.call(
                "POST", "pull", {"model_name": model}, timeout=PULL_TIMEOUT_S
            )
            held["load"] = other.call(
                "POST",
                "load",
                {"model_name": model, "ctx_size": FLM_DEFAULT_CTX},
                timeout=LOAD_TIMEOUT_S,
            )
            held["answer"] = answer_of(chat(other, model, PLAIN_PROMPT))
            held["flm"] = flm_in_use(processes(status.pid), holder.cache_dir)
            self.save()
            if held["answer"] is None:
                self.record["cells"].append(
                    {
                        "model": model,
                        "request": "second Lemonade loads the NPU",
                        "ok": False,
                        "load": held["load"],
                        "error": "the second Lemonade could not load and answer, "
                        "so the NPU-held cells were not run",
                    }
                )
                return
            for target in self.args.model:
                for request in ("plain", "agent"):
                    if request not in self.args.request:
                        continue
                    if request == "agent":
                        cell = self._agent_cell(target, "flm-default", FLM_DEFAULT_CTX)
                    else:
                        cell = self._direct_cell(
                            target, "flm-default", FLM_DEFAULT_CTX, request
                        )
                    cell["request"] = f"{request}, NPU held by {model}"
                    self.record["cells"].append(cell)
                    self.save()
            held["answer_after"] = answer_of(chat(other, model, PLAIN_PROMPT))
        finally:
            try:
                holder.stop()
            except Exception as e:  # pylint: disable=broad-except
                held["stop_error"] = str(e)
            if holder.log_path.exists():
                self._copy(holder.log_path, self.out / "npu-holder-lemond.log")

    def _collect(self, emb: Any) -> None:
        self.record["processes_at_end"] = processes(self.pid)
        # `gaia -p` may have started GAIA's daemon under this throwaway home.
        self.record["daemon_stop"] = run([*gaia_cli(), "daemon", "stop"], timeout=120)
        try:
            emb.stop()
        except Exception as e:  # pylint: disable=broad-except
            self.record["stop_error"] = str(e)
        if emb.log_path.exists():
            self._copy(emb.log_path, self.out / "lemond.log")
        if emb.cache_dir.exists():
            for log in sorted(emb.cache_dir.rglob("*.log")):
                self._copy(log, self.out / f"cache-{log.name}")
        logs = Path(os.environ["GAIA_HOME"]) / "logs"
        if logs.is_dir():
            for log in sorted(logs.rglob("*")):
                if log.is_file():
                    self._copy(log, self.out / "gaia-logs" / log.relative_to(logs))

    def _copy(self, src: Path, dest: Path) -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(encoding="utf-8", data=redact(read(str(src)), self.secrets))


# -- the whole run ------------------------------------------------------------


def _detail(cell: Dict[str, Any]) -> str:
    if cell.get("ok"):
        return str(cell.get("answer") or "")[:60]
    if cell.get("skipped"):
        return cell["skipped"]
    if cell.get("error"):
        return cell["error"]
    if cell.get("request") == "agent":
        said = cell.get("gaia_p") or {}
        return (
            "; ".join(cell.get("errors_5xx") or [])
            or (said.get("stderr") or said.get("error") or "")[-300:]
        )
    for step in ("chat", "load"):
        answer = cell.get(step) or {}
        if answer and answer.get("status") != 200:
            return (
                f"{step} {answer.get('status')}: "
                + (answer.get("body") or answer.get("error") or "")[:300]
            )
    return ""


def _first_line(text: str) -> str:
    lines = (text or "").strip().splitlines()
    return lines[0][:80] if lines else "?"


def _cell_flm(cell: Dict[str, Any], installed: str) -> str:
    """The FLM the cell ran, and where it came from; else the one installed."""
    running = cell.get("flm") or []
    if not running:
        return f"{installed} (not running)"
    origin = running[0]["origin"].split(":")[0]
    return f"{_first_line(running[0]['version'])} ({origin})"


def summarize(versions: List[Dict[str, Any]]) -> str:
    rows = [
        "| Lemonade | FLM | Model | Window | Request | Result | Detail |",
        "|---|---|---|---|---|---|---|",
    ]
    for v in versions:
        installed = _first_line((v.get("flm_version") or {}).get("stdout", ""))
        if v.get("fatal"):
            rows.append(
                f"| {v.get('lemonade')} | - | - | - | - | **run failed** | "
                f"{str(v['fatal']).strip().splitlines()[-1][:300]} |"
            )
        for c in v.get("cells", []):
            result = {None: "skipped", True: "answers"}.get(c.get("ok"), "**fails**")
            detail = _detail(c).replace("|", "/").replace("\n", " ")
            window = f"{c['window']} ({c['ctx']})" if c.get("window") else "-"
            rows.append(
                f"| {v.get('lemonade')} | {_cell_flm(c, installed)} | "
                f"{c.get('model')} | {window} | {c.get('request')} | {result} | {detail} |"
            )
    return "\n".join(rows) + "\n"


def _version_argv(
    args: argparse.Namespace, version: str, tag: Optional[str], out: Path
):
    argv = [sys.executable, os.path.abspath(__file__)]
    argv += ["--one-version", version, "--out-dir", str(out)]
    if tag:
        argv += ["--tag", tag]
    for model in args.model:
        argv += ["--model", model]
    for request in args.request:
        argv += ["--request", request]
    argv += ["--holder-model", args.holder_model or ""]
    return argv


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Reproduce an NPU chat through GAIA's own Lemonade (#6632)."
    )
    parser.add_argument(
        "--lemonade",
        action="append",
        default=[],
        metavar="VERSION=TAG",
        help="Another Lemonade release to try beside GAIA's own, e.g. "
        "2026.46.0=candidate-v2026.46.0",
    )
    parser.add_argument("--model", action="append", help="FLM models to try")
    parser.add_argument("--request", action="append", choices=REQUESTS)
    parser.add_argument(
        "--holder-model",
        default=HOLDER_MODEL,
        help="FLM model a second Lemonade keeps loaded on the NPU for the "
        "'NPU held' cells; empty skips them (default: %(default)s)",
    )
    parser.add_argument("--workdir", help="Where to run (default: a new temp dir)")
    parser.add_argument("--output", help="The .tar.gz to write")
    parser.add_argument("--one-version", help=argparse.SUPPRESS)
    parser.add_argument("--tag", help=argparse.SUPPRESS)
    parser.add_argument("--out-dir", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    args.model = args.model or DEFAULT_MODELS
    args.request = args.request or REQUESTS
    if args.one_version:
        return VersionRun(args).run()
    return run_all(args)


def run_all(args: argparse.Namespace, spawn: Callable[..., Dict] = run) -> int:
    try:
        from gaia.version import LEMONADE_VERSION
    except ImportError as e:
        print(f"GAIA is not installed in this Python ({sys.executable}): {e}")
        return 2
    workdir = Path(args.workdir or tempfile.mkdtemp(prefix="gaia-npu-repro-"))
    report = workdir / "report"
    report.mkdir(parents=True, exist_ok=True)
    print(f"Working in {workdir}", flush=True)
    (report / "machine.json").write_text(
        encoding="utf-8",
        data=redact(json.dumps(machine_facts(), indent=2, default=str), []),
    )
    (report / "users-lemonade.json").write_text(
        encoding="utf-8", data=json.dumps(users_own_lemonade(), indent=2, default=str)
    )
    versions: List[Tuple[str, Optional[str]]] = [(LEMONADE_VERSION, None)]
    for spec in args.lemonade:
        version, _, tag = spec.partition("=")
        versions.append((version, tag or f"v{version}"))

    results = []
    for version, tag in versions:
        out = report / f"lemonade-{version}"
        out.mkdir(parents=True, exist_ok=True)
        env = {**os.environ, "GAIA_HOME": str(workdir / f"home-{version}")}
        for var in ("LEMONADE_BASE_URL", "LEMONADE_API_KEY", "GAIA_CTX_SIZE"):
            env.pop(var, None)
        print(f"Lemonade {version}: {', '.join(args.model)}...", flush=True)
        child = spawn(_version_argv(args, version, tag, out), timeout=8 * 3600, env=env)
        (out / "runner-output.txt").write_text(
            encoding="utf-8",
            data=redact(
                f"exit {child.get('exit')} {child.get('error', '')}\n"
                f"--- stdout\n{child.get('stdout', '')}\n"
                f"--- stderr\n{child.get('stderr', '')}",
                [],
            ),
        )
        try:
            results.append(
                json.loads((out / "version.json").read_text(encoding="utf-8"))
            )
        except (OSError, ValueError):
            results.append(
                {
                    "lemonade": version,
                    "fatal": (child.get("stderr") or "no output")[-2000:],
                }
            )

    summary = "# NPU repro (#6632)\n\n" + summarize(results)
    (report / "summary.md").write_text(encoding="utf-8", data=summary)
    (report / "results.json").write_text(
        encoding="utf-8", data=json.dumps(results, indent=2, default=str)
    )
    output = Path(args.output or workdir / "gaia-npu-repro.tar.gz")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(output, "w:gz") as tar:
        tar.add(report, arcname="gaia-npu-repro")
    print(summary)
    print(f"Report: {output}")
    failed = any(
        v.get("fatal") or any(c.get("ok") is False for c in v.get("cells", []))
        for v in results
    )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
