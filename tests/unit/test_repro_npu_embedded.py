# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""util/repro_npu_embedded.py: the #6632 repro reports every cell, and leaks no key."""

import importlib.util
import json
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "repro_npu_embedded", ROOT / "util" / "repro_npu_embedded.py"
)
repro = importlib.util.module_from_spec(spec)
spec.loader.exec_module(repro)


def test_keys_and_tokens_are_redacted():
    text = (
        "Authorization: Bearer abcdefghijklmnop\n"
        "LEMONADE_API_KEY=s3cr3tvalue99\n"
        '{"HF_TOKEN": "hf_abcdefghijklmnopqrstuvwxyz"}\n'
        "key in a log: k-123456789"
    )
    out = repro.redact(text, ["k-123456789"])
    assert "abcdefghijklmnop" not in out
    assert "s3cr3tvalue99" not in out
    assert "hf_abcdefghijklmnopqrstuvwxyz" not in out
    assert "k-123456789" not in out
    assert out.count("<redacted>") == 4


LOAD_500 = {
    "status": 500,
    "body": '{"error":{"type":"model_load_error","message":"FLM NPU validation '
    'failed: Memlock limits are too low."}}',
}


def version_record():
    return {
        "lemonade": "2026.44.1",
        "flm_version": {"stdout": "FLM v1.0.7\n"},
        "cells": [
            {
                "model": "gemma3-4b-FLM",
                "window": "flm-default",
                "ctx": 32768,
                "request": "plain",
                "ok": True,
                "answer": "4.",
            },
            {
                "model": "gemma4-it-e4b-FLM",
                "window": "gaia",
                "ctx": 131072,
                "request": "plain",
                "ok": False,
                "load": LOAD_500,
            },
            {
                "model": "gemma4-it-e4b-FLM",
                "window": "gaia",
                "ctx": 131072,
                "request": "agent",
                "ok": False,
                "gaia_p": {"stderr": "boom"},
                "errors_5xx": ["Request failed with status 500: model_load_error"],
            },
            {
                "model": "gemma4-it-e4b-FLM",
                "window": "flm-default",
                "ctx": 32768,
                "request": "agent",
                "ok": None,
                "skipped": "the flagship agent is not installed",
            },
        ],
    }


def test_summary_has_one_row_per_cell_and_names_the_error():
    table = repro.summarize([version_record()])
    rows = [r for r in table.splitlines() if r.startswith("| 2026.44.1")]
    assert len(rows) == 4
    assert "answers" in rows[0] and "FLM v1.0.7" in rows[0]
    assert "**fails**" in rows[1]
    assert "load 500" in rows[1] and "Memlock limits are too low" in rows[1]
    assert "Request failed with status 500" in rows[2]
    assert "skipped" in rows[3]


def test_a_run_that_could_not_start_is_reported():
    table = repro.summarize(
        [{"lemonade": "2026.46.0", "fatal": "Traceback...\nRuntimeError: no asset"}]
    )
    assert "**run failed**" in table and "RuntimeError: no asset" in table


def test_each_lemonade_runs_in_its_own_home_and_the_report_is_packed(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(repro, "machine_facts", lambda: {"platform": "test"})
    monkeypatch.setattr(repro, "users_own_lemonade", lambda: {})
    monkeypatch.setenv("LEMONADE_API_KEY", "users-own-key")
    seen = []

    def spawn(argv, timeout, env):
        out = Path(argv[argv.index("--out-dir") + 1])
        version = argv[argv.index("--one-version") + 1]
        seen.append((version, env["GAIA_HOME"], argv, env))
        record = version_record() if version == "2026.44.1" else {"lemonade": version}
        (out / "version.json").write_text(json.dumps(record), encoding="utf-8")
        return {"exit": 1, "stdout": "", "stderr": ""}

    monkeypatch.setattr("gaia.version.LEMONADE_VERSION", "2026.44.1")
    args = repro.argparse.Namespace(
        lemonade=["2026.46.0=candidate-v2026.46.0"],
        model=["gemma4-it-e4b-FLM"],
        request=["plain", "agent"],
        holder_model="gemma3-4b-FLM",
        workdir=str(tmp_path),
        output=str(tmp_path / "out.tar.gz"),
    )
    assert repro.run_all(args, spawn=spawn) == 1

    assert [s[0] for s in seen] == ["2026.44.1", "2026.46.0"]
    assert seen[0][1] != seen[1][1]
    assert seen[1][2][seen[1][2].index("--tag") + 1] == "candidate-v2026.46.0"
    assert seen[0][2][seen[0][2].index("--holder-model") + 1] == "gemma3-4b-FLM"
    # The user's own Lemonade settings never reach the private instances.
    assert all("LEMONADE_API_KEY" not in s[3] for s in seen)
    with tarfile.open(tmp_path / "out.tar.gz") as tar:
        names = tar.getnames()
    assert "gaia-npu-repro/summary.md" in names
    assert "gaia-npu-repro/lemonade-2026.44.1/version.json" in names


def test_answer_needs_a_200_with_a_message():
    ok = {"status": 200, "body": '{"choices":[{"message":{"content":"4"}}]}'}
    assert repro.answer_of(ok) == "4"
    assert repro.answer_of({"status": 500, "body": "{}"}) is None
    assert repro.answer_of({"status": 200, "body": "not json"}) is None


def test_large_prompt_is_about_a_first_agent_turn():
    words = len(repro.large_prompt().split())
    assert 15000 < words < 30000


def test_each_cell_names_the_flm_it_ran_and_where_it_came_from(tmp_path, monkeypatch):
    """FLM 1.0.8/1.0.9 broke Lemonade's launch of FLM on Linux; say which one ran."""
    cache = tmp_path / "cache"
    flm_dir = cache / "bin" / "flm" / "npu"
    flm_dir.mkdir(parents=True)
    (flm_dir / "flm").write_text("#!/bin/bash\n")
    (flm_dir / "flm-real").write_text("")
    calls = []

    def fake_run(argv, timeout=60, **kwargs):
        calls.append(argv)
        return {"exit": 0, "stdout": "FLM v1.0.7\n"}

    monkeypatch.setattr(repro, "run", fake_run)
    monkeypatch.setattr(repro, "_FLM_VERSIONS", {})
    procs = [
        {"pid": 1, "exe": "/x/lemond", "cmdline": ["lemond"]},
        {"pid": 2, "exe": str(flm_dir / "flm-real"), "cmdline": ["flm-real", "serve"]},
        {"pid": 3, "exe": str(flm_dir / "flm-real"), "cmdline": ["flm-real", "serve"]},
    ]
    found = repro.flm_in_use(procs, cache)
    assert [f["pid"] for f in found] == [2, 3]
    assert found[0]["origin"].startswith("bundled")
    assert found[0]["version"] == "FLM v1.0.7"
    # The launcher script beside flm-real is asked, once.
    assert calls == [[str(flm_dir / "flm"), "version"]]
    assert repro.flm_origin(Path("/usr/bin/flm"), cache).startswith("system package")

    record = version_record()
    record["cells"][1]["flm"] = found
    rows = repro.summarize([record]).splitlines()
    assert "FLM v1.0.7 (bundled)" in rows[3]
    assert "(not running)" in rows[2]


class _Log:
    """One shared record of every HTTP call, in order, across both Lemonades."""

    def __init__(self, gaia_loaded_after_unload, holder_answers_after=True):
        self.calls = []
        self.gaia_loaded_after_unload = gaia_loaded_after_unload
        self.holder_answers_after = holder_answers_after
        self.holder_chats = 0


class _FakeLemonade:
    def __init__(self, name, log):
        self.name = name
        self.log = log

    def call(self, method, path, body=None, timeout=60):
        self.log.calls.append((self.name, method, path))
        if path == "chat/completions" and self.name == "holder":
            self.log.holder_chats += 1
            if self.log.holder_chats > 1 and not self.log.holder_answers_after:
                return {"status": 500, "body": "npu busy"}
        reply = {"choices": [{"message": {"content": "4"}}]}
        return {"status": 200, "body": json.dumps(reply)}

    def get_json(self, path):
        self.log.calls.append((self.name, "GET", path))
        loaded = self.log.gaia_loaded_after_unload
        return {"all_models_loaded": [{"model_name": m} for m in loaded]}


def _held_run(tmp_path, monkeypatch, log):
    monkeypatch.setenv("GAIA_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(
        repro, "Lemonade", lambda url, key: _FakeLemonade("holder", log)
    )
    monkeypatch.setattr(repro, "processes", lambda pid: [])
    monkeypatch.setattr(repro, "dmesg_tail", lambda: "")

    class Holder:
        cache_dir = tmp_path / "holder-cache"
        log_path = tmp_path / "missing.log"

        def __init__(self, version, home):
            pass

        def start(self):
            return repro.argparse.Namespace(port=1, pid=2)

        def current_api_key(self):
            return "k"

        def base_url_for(self, port):
            return "http://holder"

        def stop(self):
            log.calls.append(("holder", "stop", ""))

    args = repro.argparse.Namespace(
        out_dir=str(tmp_path / "out"),
        one_version="2026.44.1",
        model=["gemma4-it-e4b-FLM"],
        request=["plain"],
        holder_model="gemma3-4b-FLM",
    )
    run = repro.VersionRun(args)
    run.lemonade = _FakeLemonade("gaia", log)
    run._held_cells(repro.argparse.Namespace(EmbeddedLemonade=Holder))
    return run


def test_gaia_frees_the_npu_before_the_holder_loads(tmp_path, monkeypatch):
    """A holder load that fails must be the clash, not GAIA's leftover model."""
    log = _Log(gaia_loaded_after_unload=[])
    run = _held_run(tmp_path, monkeypatch, log)
    unload = log.calls.index(("gaia", "POST", "unload"))
    holder_load = log.calls.index(("holder", "POST", "load"))
    assert unload < holder_load
    # Nothing GAIA-side touches the NPU between its unload and the holder's load.
    assert not [
        c
        for c in log.calls[unload + 1 : holder_load]
        if c[0] == "gaia" and c[2] == "load"
    ]
    rows = run.record["cells"]
    assert [r["request"] for r in rows] == ["plain, NPU held by gemma3-4b-FLM"]
    assert rows[0]["ok"] is True


def test_a_failed_unload_is_reported_and_the_holder_never_loads(tmp_path, monkeypatch):
    log = _Log(gaia_loaded_after_unload=["gemma4-it-e4b-FLM"])
    run = _held_run(tmp_path, monkeypatch, log)
    assert ("holder", "POST", "load") not in log.calls
    assert ("holder", "stop", "") in log.calls
    (row,) = run.record["cells"]
    assert row["ok"] is False and row["request"] == "GAIA's Lemonade frees the NPU"
    assert "gemma4-it-e4b-FLM" in row["error"]
    assert "could not unload" in repro.summarize([run.record])


def test_a_holder_that_stops_answering_is_a_failing_row(tmp_path, monkeypatch):
    log = _Log(gaia_loaded_after_unload=[], holder_answers_after=False)
    run = _held_run(tmp_path, monkeypatch, log)
    last = run.record["cells"][-1]
    assert last["ok"] is False
    assert last["request"] == "second Lemonade still answers after GAIA's loads"
    assert "stopped answering" in repro.summarize([run.record])
