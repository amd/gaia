# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""util/check_npu_agent_chat.py: the NPU agent-chat job fails for the right reasons.

The job's hardware path runs only on an NPU runner, so these pin the verdicts
it reaches from what the backend and Lemonade say: the #6632 "Couldn't reach
the local LLM" turn fails, an answer from a GPU-loaded model fails, and a
Linux box without the amdxdna driver fails naming what to provision.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "util"))

import check_npu_agent_chat as npu  # noqa: E402

MODEL = "gemma4-it-e4b-FLM"


def sse(*events):
    return [f"data: {json.dumps(e)}" for e in events] + ["data: [DONE]"]


# ── The turn ────────────────────────────────────────────────────────────


def test_a_streamed_answer_passes():
    result = npu.read_turn(
        sse(
            {"type": "status", "message": "Loading model"},
            {"type": "chunk", "content": "Hel"},
            {"type": "chunk", "content": "lo"},
            {"type": "answer", "content": "Hello there."},
            {"type": "done", "content": ""},
        )
    )
    assert result.answer == "Hello there."
    assert npu.turn_problems(result) == []


def test_chunks_alone_are_the_answer():
    result = npu.read_turn(
        sse({"type": "chunk", "content": " Hi. "}, {"type": "done", "content": ""})
    )
    assert result.answer == "Hi."
    assert npu.turn_problems(result) == []


def test_the_6632_turn_fails():
    # What the user saw: the agent's first model call never reached Lemonade.
    result = npu.read_turn(
        sse(
            {
                "type": "agent_error",
                "content": "Couldn't reach the local LLM. It may be loading or "
                "briefly busy (LemonadeNetworkError).",
            },
            {"type": "done", "content": ""},
        )
    )
    problems = npu.turn_problems(result)
    assert any("#6632" in p for p in problems)
    assert any("empty answer" in p for p in problems)


def test_an_unreachable_message_written_as_the_answer_still_fails():
    result = npu.read_turn(
        sse(
            {"type": "answer", "content": "Couldn't reach the local LLM."},
            {"type": "done", "content": ""},
        )
    )
    assert any("#6632" in p for p in npu.turn_problems(result))


@pytest.mark.parametrize("flag", ["failed", "cancelled"])
def test_a_turn_the_done_event_marks_failed_or_cancelled_fails(flag):
    # The reason reads like an answer and names no unreachable marker.
    result = npu.read_turn(
        sse({"type": "done", "content": "The model stopped responding.", flag: True})
    )
    problems = npu.turn_problems(result)
    assert any(f"ended {flag}" in p for p in problems), problems


def test_a_done_event_with_the_flags_false_still_passes():
    result = npu.read_turn(
        sse({"type": "done", "content": "Hi.", "failed": False, "cancelled": False})
    )
    assert npu.turn_problems(result) == []


def test_a_stream_cut_short_fails():
    result = npu.read_turn([f"data: {json.dumps({'type': 'chunk', 'content': 'Hi'})}"])
    assert any("without a done event" in p for p in npu.turn_problems(result))


def test_noise_lines_are_ignored():
    lines = [
        ": keepalive",
        "",
        "data: not json",
        *sse({"type": "done", "content": "Ok."}),
    ]
    result = npu.read_turn(lines)
    assert result.answer == "Ok." and result.done


# ── Where the model ran ─────────────────────────────────────────────────


def health(*entries):
    return {"status": "ok", "all_models_loaded": list(entries)}


def test_model_loaded_with_flm_passes():
    entry = {"model_name": MODEL, "recipe": "flm", "device": "npu"}
    assert npu.npu_load_problems(health(entry), MODEL) == []


def test_model_loaded_on_the_gpu_fails():
    entry = {"model_name": MODEL, "recipe": "llamacpp", "device": "gpu"}
    assert "not 'flm'" in npu.npu_load_problems(health(entry), MODEL)[0]


def test_model_not_loaded_names_what_is():
    entry = {"model_name": "Gemma-4-E4B-it-GGUF", "recipe": "llamacpp"}
    problem = npu.npu_load_problems(health(entry), MODEL)[0]
    assert "not loaded" in problem and "Gemma-4-E4B-it-GGUF" in problem


# ── Lemonade's NPU backend ──────────────────────────────────────────────


def system_info(state, message=""):
    return {
        "recipes": {
            "flm": {
                "display_name": "FastFlowLM NPU",
                "backends": {"npu": {"state": state, "message": message}},
            }
        }
    }


def test_installed_backend_passes():
    assert npu.flm_backend_problem(system_info("installed")) is None


@pytest.mark.parametrize("state", ["installable", "update_required"])
def test_backend_not_yet_installed_after_init_fails(state):
    assert state in npu.flm_backend_problem(system_info(state))


def test_unsupported_backend_names_lemonades_reason():
    problem = npu.flm_backend_problem(system_info("unsupported", "No NPU detected"))
    assert "Ryzen AI NPU" in problem and "No NPU detected" in problem


# ── The Linux driver ────────────────────────────────────────────────────


def test_linux_without_amdxdna_names_both_gaps(tmp_path):
    problems = npu.linux_npu_problems(tmp_path)
    assert any("amdxdna" in p for p in problems)
    assert any("/dev/accel" in p for p in problems)


def test_linux_with_the_driver_passes(tmp_path):
    (tmp_path / "sys" / "module" / "amdxdna").mkdir(parents=True)
    (tmp_path / "dev" / "accel").mkdir(parents=True)
    (tmp_path / "dev" / "accel" / "accel0").touch()
    (tmp_path / "lib" / "firmware" / "amdnpu" / "17f0_10").mkdir(parents=True)
    (tmp_path / "lib" / "firmware" / "amdnpu" / "17f0_10" / "npu.sbin").touch()
    assert npu.linux_npu_problems(tmp_path) == []
    facts = npu.linux_npu_facts(tmp_path)
    assert facts["amdxdna_loaded"] is True
    assert facts["accel_nodes"] == [str(Path("dev/accel/accel0"))]
    assert facts["firmware"] == [str(Path("17f0_10/npu.sbin"))]


def test_the_driver_help_says_what_to_provision():
    for need in ("amdxdna", "/lib/firmware/amdnpu", "/dev/accel", "flm:npu"):
        assert need in npu.LINUX_DRIVER_HELP


# ── The run's guards ────────────────────────────────────────────────────


def test_refuses_without_gaia_home(monkeypatch):
    monkeypatch.delenv("GAIA_HOME", raising=False)
    with pytest.raises(npu.CheckFailed, match="GAIA_HOME is not set"):
        npu._check_gaia_home()  # pylint: disable=protected-access


def test_refuses_a_used_gaia_home(monkeypatch, tmp_path):
    (tmp_path / "config.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("GAIA_HOME", str(tmp_path))
    with pytest.raises(npu.CheckFailed, match="not empty"):
        npu._check_gaia_home()  # pylint: disable=protected-access


def test_sidecar_logs_are_found_under_the_agents_dir(tmp_path):
    log = tmp_path / "agents" / "gaia" / "logs" / "sidecar-51234.log"
    log.parent.mkdir(parents=True)
    log.touch()
    assert npu.sidecar_logs(tmp_path) == [log]
    assert npu.sidecar_logs(tmp_path / "elsewhere") == []


def test_the_real_cause_behind_couldnt_reach_is_read_from_the_sidecar_log(tmp_path):
    # #6632: a 500 model_load_error surfaced to the user as "Couldn't reach".
    log = tmp_path / "agents" / "gaia" / "logs" / "sidecar-51234.log"
    log.parent.mkdir(parents=True)
    log.write_text(
        "INFO turn started\n"
        'ERROR Request failed with status 500: {"error": {"type": '
        '"model_load_error", "message": "FLM NPU validation failed"}}\n',
        encoding="utf-8",
    )
    (line,) = npu.sidecar_request_failures(tmp_path)
    assert "model_load_error" in line and line.startswith("sidecar-51234.log")
