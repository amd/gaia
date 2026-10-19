# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Several GAIA processes write ``~/.gaia`` at once; none may corrupt another.

The daemon, the Agent UI, the CLI and the TUI share these files. Each test
races real processes against one file and checks the reader never sees a torn
file and no writer's change is lost.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

REPO_SRC = Path(__file__).resolve().parents[2] / "src"


#: Prepended to a racing script. ``together()`` blocks until every racer is
#: ready, so the processes overlap instead of running one after another.
_TOGETHER = """
import os as _os, time as _time

def together():
    open(_os.environ["GAIA_TEST_READY"], "w").close()
    while not _os.path.exists(_os.environ["GAIA_TEST_GO"]):
        _time.sleep(0.005)

def finished():
    open(_os.environ["GAIA_TEST_DONE"], "w").close()

def others_finished():
    return _os.path.exists(_os.environ["GAIA_TEST_DONE"])
"""


def _spawn(code: str, tmp_path: Path, *args: str, env_extra=None) -> subprocess.Popen:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_SRC) + os.pathsep + env.get("PYTHONPATH", "")
    env["GAIA_HOME"] = str(tmp_path / "gaia-home")
    env["HOME"] = env["USERPROFILE"] = str(tmp_path / "home")
    env.update(env_extra or {})
    return subprocess.Popen(
        [sys.executable, "-c", textwrap.dedent(code), *args],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def _race(tmp_path: Path, specs, tag: str = "race") -> list:
    """Start ``(script, *args)`` racers and release them at the same instant."""
    go, done = tmp_path / f"{tag}-go", tmp_path / f"{tag}-done"
    procs, readies = [], []
    for i, (code, *args) in enumerate(specs):
        ready = tmp_path / f"{tag}-ready-{i}"
        readies.append(ready)
        env = {
            "GAIA_TEST_READY": str(ready),
            "GAIA_TEST_GO": str(go),
            "GAIA_TEST_DONE": str(done),
        }
        procs.append(_spawn(_TOGETHER + code, tmp_path, *args, env_extra=env))
    deadline = time.monotonic() + 60
    while not all(r.exists() for r in readies):
        for proc in procs:
            assert proc.poll() is None, proc.communicate()
        assert time.monotonic() < deadline, "racers never became ready"
        time.sleep(0.01)
    go.touch()
    return procs


def _finish(procs, timeout: float = 120):
    results = []
    for proc in procs:
        out, err = proc.communicate(timeout=timeout)
        results.append((proc.returncode, out, err))
    return results


# --------------------------------------------------------------------------
# ~/.gaia/config.json
# --------------------------------------------------------------------------

_CONFIG_WRITER = """
import os, sys, time
from pathlib import Path
from gaia.config import GaiaConfig
path, key, n = Path(sys.argv[1]), sys.argv[2], int(sys.argv[3])
# Optional: keep writing until a reader has overlapped enough reads.
reader_ready = sys.argv[4] if len(sys.argv) > 4 else None
together()
i, deadline = 0, time.monotonic() + 60
while i < n or (
    reader_ready and not os.path.exists(reader_ready) and time.monotonic() < deadline
):
    GaiaConfig.save_key(key, f"{key}-{i}", path)
    i += 1
finished()
"""

_CONFIG_READER = """
import sys, time
from pathlib import Path
from gaia.config import GaiaConfig, GaiaConfigError
path, enough = Path(sys.argv[1]), sys.argv[2]
errors = reads = 0
together()
while not others_finished():
    reads += 1
    try:
        GaiaConfig.load(path)
    except (GaiaConfigError, OSError) as e:
        errors += 1
        last = repr(e)
    if reads == 200:
        open(enough, "w").close()
print(errors, reads)
if errors:
    print(last, file=sys.stderr)
"""


def test_config_reader_never_sees_a_half_written_file(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"tui_only_key": "x" * 4000}), encoding="utf-8")
    enough = tmp_path / "reader-overlapped"
    procs = _race(
        tmp_path,
        [
            (_CONFIG_WRITER, str(path), "last_model", "300", str(enough)),
            (_CONFIG_READER, str(path), str(enough)),
        ],
    )
    (w_rc, _, w_err), (r_rc, r_out, r_err) = _finish(procs)
    assert w_rc == 0, w_err
    assert r_rc == 0, r_err
    errors, reads = map(int, r_out.split())
    assert reads >= 20, f"only {reads} reads overlapped the writer"
    assert errors == 0, f"reader hit {errors} errors in {reads} reads: {r_err}"


def test_config_concurrent_save_key_keeps_both_updates(tmp_path):
    path = tmp_path / "config.json"
    path.write_text("{}", encoding="utf-8")
    procs = _race(
        tmp_path,
        [
            (_CONFIG_WRITER, str(path), key, "150")
            for key in ("last_model", "default_model", "last_provider")
        ],
    )
    for rc, _, err in _finish(procs):
        assert rc == 0, err
    doc = json.loads(path.read_text(encoding="utf-8"))
    assert doc.get("last_model") == "last_model-149"
    assert doc.get("default_model") == "default_model-149"
    assert doc.get("last_provider") == "last_provider-149"


def test_config_save_keeps_keys_it_does_not_declare(tmp_path):
    from gaia.config import GaiaConfig

    path = tmp_path / "config.json"
    path.write_text(json.dumps({"tui_theme": "dark"}), encoding="utf-8")
    config = GaiaConfig.load(path)
    config.profile = "npu"
    config.save(path)

    doc = json.loads(path.read_text(encoding="utf-8"))
    assert doc["tui_theme"] == "dark"
    assert doc["profile"] == "npu"


def test_config_update_applies_against_the_latest_file(tmp_path):
    """``gaia init`` runs for minutes; a key set meanwhile must survive it."""
    from gaia.config import GaiaConfig

    path = tmp_path / "config.json"
    path.write_text("{}", encoding="utf-8")

    def _set_profile(config):
        # Another process sets default_model while init is mid-update.
        assert config.default_model == "user-pick"
        config.profile = "npu"

    GaiaConfig.save_key("default_model", "user-pick", path)
    GaiaConfig.update(_set_profile, path)

    doc = json.loads(path.read_text(encoding="utf-8"))
    assert doc["profile"] == "npu"
    assert doc["default_model"] == "user-pick"


def test_config_update_refuses_an_unreadable_file_unless_told_to_reset(tmp_path):
    from gaia.config import GaiaConfig, GaiaConfigError

    path = tmp_path / "config.json"
    path.write_text("{not json", encoding="utf-8")

    with pytest.raises(GaiaConfigError):
        GaiaConfig.update(lambda c: setattr(c, "profile", "npu"), path)
    assert path.read_text(encoding="utf-8") == "{not json"

    reported = []
    GaiaConfig.update(
        lambda c: setattr(c, "profile", "npu"), path, on_unreadable=reported.append
    )
    assert len(reported) == 1 and "not valid JSON" in str(reported[0])
    assert json.loads(path.read_text(encoding="utf-8"))["profile"] == "npu"


def test_config_save_writes_only_what_this_instance_changed(tmp_path):
    """A stale instance must not revert a key another process saved meanwhile."""
    from gaia.config import GaiaConfig

    path = tmp_path / "config.json"
    path.write_text("{}", encoding="utf-8")
    stale = GaiaConfig.load(path)
    GaiaConfig.save_key("default_model", "picked-elsewhere", path)

    stale.lemonade_mode = "external"
    stale.save(path)

    doc = json.loads(path.read_text(encoding="utf-8"))
    assert doc["lemonade_mode"] == "external"
    assert doc["default_model"] == "picked-elsewhere"


@pytest.mark.parametrize("parent_exists", [True, False])
def test_config_loaded_before_the_file_exists_keeps_a_later_save(
    tmp_path, parent_exists
):
    """First run: a config loaded from no file must not write its defaults over
    a key another process saved after the load."""
    from gaia.config import GaiaConfig

    path = tmp_path / "gaia" / "config.json"
    if parent_exists:
        path.parent.mkdir()
    early = GaiaConfig.load(path)
    GaiaConfig.save_key("default_model", "picked-elsewhere", path)

    early.lemonade_mode = "external"
    early.save(path)

    doc = json.loads(path.read_text(encoding="utf-8"))
    assert doc["lemonade_mode"] == "external"
    assert doc["default_model"] == "picked-elsewhere"


def test_config_loads_from_a_directory_it_cannot_write(tmp_path, monkeypatch):
    """No lock file can be made there, and no writer can replace the file either."""
    import errno

    from gaia.config import GaiaConfig
    from gaia.utils import file_lock as file_lock_mod

    path = tmp_path / "config.json"
    path.write_text(json.dumps({"profile": "npu"}), encoding="utf-8")
    real_open = os.open

    def read_only_dir(name, flags, *args):
        if str(name).endswith(".lock"):
            raise PermissionError(errno.EACCES, "Permission denied", str(name))
        return real_open(name, flags, *args)

    monkeypatch.setattr(file_lock_mod.os, "open", read_only_dir)
    monkeypatch.setattr(
        file_lock_mod.os,
        "access",
        lambda p, mode: False if Path(p) == tmp_path else os.path.exists(p),
    )
    assert GaiaConfig.load(path).profile == "npu"


@pytest.mark.skipif(os.name == "nt", reason="POSIX directory permissions")
def test_config_loads_from_a_real_read_only_directory(tmp_path):
    from gaia.config import GaiaConfig

    config_dir = tmp_path / "ro"
    config_dir.mkdir()
    path = config_dir / "config.json"
    path.write_text(json.dumps({"profile": "npu"}), encoding="utf-8")
    config_dir.chmod(0o500)
    try:
        assert GaiaConfig.load(path).profile == "npu"
    finally:
        config_dir.chmod(0o700)


_PROVIDER_PICKER = """
import sys
from pathlib import Path
from gaia import config as config_mod
config_mod.GAIA_CONFIG_FILE = Path(sys.argv[1])
from gaia.ui.routers import providers
together()
for i in range(int(sys.argv[2])):
    providers._write_config_keys({"last_provider": "local", "last_model": f"m-{i}"})
"""


def test_agent_ui_model_pick_and_config_set_keep_both(tmp_path):
    """The Agent UI's last-model save races a `gaia config set` in another process."""
    path = tmp_path / "config.json"
    path.write_text("{}", encoding="utf-8")
    procs = _race(
        tmp_path,
        [
            (_PROVIDER_PICKER, str(path), "100"),
            (_CONFIG_WRITER, str(path), "default_model", "100"),
        ],
    )
    for rc, _, err in _finish(procs):
        assert rc == 0, err
    doc = json.loads(path.read_text(encoding="utf-8"))
    assert doc.get("last_model") == "m-99"
    assert doc.get("default_model") == "default_model-99"


# --------------------------------------------------------------------------
# ~/.gaia/cache/context7
# --------------------------------------------------------------------------

_CONTEXT7_WRITER = """
import sys
from pathlib import Path
from gaia.mcp.context7_cache import Context7Cache
cache = Context7Cache(Path(sys.argv[1]))
prefix, n = sys.argv[2], int(sys.argv[3])
together()
for i in range(n):
    cache.set_library_id(f"{prefix}-{i}", f"/org/{prefix}-{i}")
"""

_CONTEXT7_TOKENS = """
import sys, time
from pathlib import Path
from gaia.mcp.context7_cache import Context7RateLimiter
# Long-lived, like the limiter a running Agent UI or daemon holds.
limiter = Context7RateLimiter(Path(sys.argv[1]))
together()
for _ in range(int(sys.argv[2])):
    limiter.consume_token()
"""


def test_context7_concurrent_writers_keep_every_library_id(tmp_path):
    cache_dir = tmp_path / "context7"
    procs = _race(
        tmp_path,
        [(_CONTEXT7_WRITER, str(cache_dir), name, "60") for name in ("a", "b", "c")],
    )
    for rc, _, err in _finish(procs):
        assert rc == 0, err

    from gaia.mcp.context7_cache import Context7Cache

    cache = Context7Cache(cache_dir)
    missing = [
        f"{name}-{i}"
        for name in ("a", "b", "c")
        for i in range(60)
        if cache.get_library_id(f"{name}-{i}") is None
    ]
    assert not missing, f"{len(missing)} library ids were lost: {missing[:5]}"


def test_context7_rate_limit_counts_every_process(tmp_path):
    state = tmp_path / "context7" / "rate_state.json"
    procs = _race(tmp_path, [(_CONTEXT7_TOKENS, str(state), "10")] * 2)
    for rc, _, err in _finish(procs):
        assert rc == 0, err

    from gaia.mcp.context7_cache import Context7RateLimiter

    # 20 of 30 spent; refill over the test's few seconds is a fraction of one.
    assert Context7RateLimiter(state).get_status()["tokens_available"] < 11


def test_context7_corrupt_cache_fails_loudly_instead_of_wiping(tmp_path):
    from gaia.mcp.context7_cache import Context7Cache, Context7CacheError

    cache = Context7Cache(tmp_path / "context7")
    cache.library_ids_file.write_text('{"react": {"value": "/fb/re', encoding="utf-8")

    with pytest.raises(Context7CacheError, match="gaia cache clear --context7"):
        cache.set_library_id("vue", "/vuejs/core")
    assert cache.library_ids_file.read_text(encoding="utf-8").startswith('{"react"')


def test_context7_corrupt_rate_state_fails_loudly(tmp_path):
    from gaia.mcp.context7_cache import Context7CacheError, Context7RateLimiter

    state = tmp_path / "rate_state.json"
    state.write_text('{"tokens": 0.0, "last_upd', encoding="utf-8")

    with pytest.raises(Context7CacheError, match="rate_state.json"):
        Context7RateLimiter(state).consume_token()


# --------------------------------------------------------------------------
# RAG cache: HMAC key, signed chunk caches
# --------------------------------------------------------------------------

_RAG_SIGNER = """
import logging, types
from gaia.rag.sdk import RAGSDK

def make_signer():
    # A stand-in RAGSDK carrying only what the signing methods use.
    signer = types.SimpleNamespace(_hmac_key=None, log=logging.getLogger("rag"))
    for name in ("_get_hmac_key", "_save_cache", "_verify_and_load_cache"):
        setattr(signer, name, types.MethodType(getattr(RAGSDK, name), signer))
    return signer
"""


def _rag_signer():
    namespace: dict = {}
    exec(_RAG_SIGNER, namespace)  # pylint: disable=exec-used
    return namespace["make_signer"]()


def test_rag_first_run_callers_agree_on_one_hmac_key(tmp_path, monkeypatch):
    import secrets
    import threading
    from concurrent.futures import ThreadPoolExecutor

    from gaia.rag import sdk

    home = tmp_path / "home"
    monkeypatch.setattr(sdk.Path, "home", lambda: home)
    real_token_bytes = secrets.token_bytes
    barrier = threading.Barrier(4)

    def slow_token_bytes(n):
        # Every first-run caller is past the existence check before any writes.
        time.sleep(0.2)
        return real_token_bytes(n)

    monkeypatch.setattr(sdk.secrets, "token_bytes", slow_token_bytes)

    def first_run(_):
        signer = _rag_signer()
        barrier.wait()
        return signer._get_hmac_key()

    with ThreadPoolExecutor(4) as pool:
        keys = list(pool.map(first_run, range(4)))

    on_disk = (home / ".gaia" / "cache" / "hmac.key").read_bytes()
    assert {len(k) for k in keys} == {32}
    assert set(keys) == {on_disk}, "callers signed caches with different keys"


_RAG_CACHE_WORKER = _RAG_SIGNER + """
import os, sys, time
signer = make_signer()
role, cache_path, writes = sys.argv[1], sys.argv[2], int(sys.argv[3])
# Written by the reader once enough reads overlapped; on a fast runner a fixed
# number of writes can finish before the reader's first read returns.
enough = sys.argv[4]
failures = reads = 0
together()
if role == "write":
    i = 0
    deadline = time.monotonic() + 60
    while i < writes or (not os.path.exists(enough) and time.monotonic() < deadline):
        data = {"chunks": [str(i) * 2000], "full_text": "", "metadata": {}}
        signer._save_cache(cache_path, data)
        i += 1
    finished()
else:
    while not others_finished():
        reads += 1
        try:
            signer._verify_and_load_cache(cache_path)
        except Exception as e:
            failures += 1
            last = repr(e)
        if reads == 50:
            open(enough, "w").close()
print(failures, reads)
if failures:
    print(last, file=sys.stderr)
"""


def test_rag_cache_reader_never_sees_a_half_written_cache(tmp_path):
    cache_path = tmp_path / "rag" / "doc.json"
    cache_path.parent.mkdir()
    enough = str(tmp_path / "enough-reads")
    # One write first, so both racers share the HMAC key and an initial cache.
    seed_enough = str(tmp_path / "seed-enough")
    Path(seed_enough).touch()
    (seed,) = _race(
        tmp_path,
        [(_RAG_CACHE_WORKER, "write", str(cache_path), "1", seed_enough)],
        tag="seed",
    )
    assert _finish([seed])[0][0] == 0

    procs = _race(
        tmp_path,
        [
            (_RAG_CACHE_WORKER, "write", str(cache_path), "400", enough),
            (_RAG_CACHE_WORKER, "read", str(cache_path), "0", enough),
        ],
    )
    (w_rc, _, w_err), (r_rc, r_out, r_err) = _finish(procs)
    assert w_rc == 0, w_err
    assert r_rc == 0, r_err
    failures, reads = map(int, r_out.split())
    assert reads >= 20, f"only {reads} reads overlapped the writer"
    assert failures == 0, f"{failures} of {reads} verifications failed: {r_err}"


# --------------------------------------------------------------------------
# Hub agent installs: the per-agent guard, the shared .pth
# --------------------------------------------------------------------------

_PTH_WRITER = """
import sys
from pathlib import Path
from gaia.hub import installer
site_packages, prefix, n = Path(sys.argv[1]), sys.argv[2], int(sys.argv[3])
together()
for i in range(n):
    installer._add_wheel_agent_to_active_env_path(
        Path(f"/agents/{prefix}-{i}/site-packages"), site_packages
    )
"""


def test_hub_concurrent_installs_keep_every_pth_line(tmp_path):
    site_packages = tmp_path / "site-packages"
    site_packages.mkdir()
    procs = _race(
        tmp_path,
        [(_PTH_WRITER, str(site_packages), name, "40") for name in ("a", "b", "c")],
    )
    for rc, _, err in _finish(procs):
        assert rc == 0, err

    from gaia.hub.installer import ACTIVE_ENV_PTH_FILENAME

    lines = set(
        (site_packages / ACTIVE_ENV_PTH_FILENAME).read_text("utf-8").splitlines()
    )
    expected = {
        str(Path(f"/agents/{name}-{i}/site-packages"))
        for name in ("a", "b", "c")
        for i in range(40)
    }
    assert not expected - lines, f"{len(expected - lines)} .pth lines were lost"


_HUB_SLOW_INSTALL = """
import json, sys, time
from pathlib import Path
from gaia.hub import installer
manifest = json.loads(Path(sys.argv[1]).read_text())
root, site_packages = Path(sys.argv[2]), Path(sys.argv[3])
ready, go = Path(sys.argv[4]), Path(sys.argv[5])
artifact = manifest["versions"]["1.0.0"]["artifact"]["path"]

def fetcher(url):
    if url.endswith("/gaia-agent.yaml"):
        return b"id: demo\\nname: Demo\\n"
    if url.endswith(artifact):
        ready.touch()
        while not go.exists():
            time.sleep(0.01)
        return b"wheel-bytes"
    raise AssertionError(url)

installer.install("demo", manifest=manifest, base_url="https://hub.test",
                  fetcher=fetcher, run_pip=lambda args: None, install_root=root,
                  active_env_site_packages=site_packages)
"""


def test_hub_install_guard_holds_across_processes(tmp_path):
    import hashlib

    from gaia.hub import installer

    artifact = b"wheel-bytes"
    manifest = {
        "id": "demo",
        "language": "python",
        "security_tier": "verified",
        "latest_version": "1.0.0",
        "requirements": {"platforms": []},
        "versions": {
            "1.0.0": {
                "version": "1.0.0",
                "artifact": {
                    "filename": "demo-1.0.0-py3-none-any.whl",
                    "path": "agents/demo/1.0.0/demo-1.0.0.whl",
                    "size_bytes": len(artifact),
                    "sha256": hashlib.sha256(artifact).hexdigest(),
                    "content_type": "application/octet-stream",
                },
            }
        },
    }
    manifest_file = tmp_path / "manifest.json"
    manifest_file.write_text(json.dumps(manifest), encoding="utf-8")
    root, site_packages = tmp_path / "agents", tmp_path / "site-packages"
    ready, go = tmp_path / "ready", tmp_path / "go"
    other = _spawn(
        _HUB_SLOW_INSTALL,
        tmp_path,
        str(manifest_file),
        str(root),
        str(site_packages),
        str(ready),
        str(go),
    )
    try:
        deadline = time.monotonic() + 60
        while not ready.exists():
            assert other.poll() is None, other.communicate()
            assert time.monotonic() < deadline, "the other install never started"
            time.sleep(0.01)

        def fetcher(url):
            if url.endswith("/gaia-agent.yaml"):
                return b"id: demo\nname: Demo\n"
            return artifact

        with pytest.raises(installer.InstallInProgressError):
            installer.install(
                "demo",
                manifest=manifest,
                base_url="https://hub.test",
                fetcher=fetcher,
                run_pip=lambda args: None,
                install_root=root,
                active_env_site_packages=site_packages,
            )
    finally:
        go.touch()
        rc, _, err = _finish([other])[0]
    assert rc == 0, err
    assert installer.read_sentinel("demo", root).version == "1.0.0"
