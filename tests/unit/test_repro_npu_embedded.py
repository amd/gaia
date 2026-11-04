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
