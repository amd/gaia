#!/usr/bin/env python
#
# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""A client that hangs up mid-call must not keep the ``gaia mcp`` bridge busy (#5392).

Calls take turns on the bridge, so an abandoned call used to hold the next
caller back until an answer nobody would read was finished, and the failed
reply printed a raw traceback instead of a log line.
"""

import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

from gaia.llm.lemonade_client import cloud_model_provider
from gaia.mcp.mcp_bridge import GAIAMCPBridge, MCPHTTPHandler

pytestmark = pytest.mark.allow_network

REPO_SRC = Path(__file__).resolve().parents[2] / "src"

# The real ``start_server`` with an LLM whose first reply takes 30 s, streamed
# or not, like a long answer on a busy Lemonade.
# ``started-N`` marks the Nth call reaching the LLM; ``closed`` marks the first
# reply being closed early, which is what hangs up on Lemonade.
_STREAMING_BRIDGE = """
import itertools, sys, time
from pathlib import Path
from gaia.mcp import mcp_bridge

port, marks = int(sys.argv[1]), Path(sys.argv[2])
calls = itertools.count()

class StreamingLLM:
    def generate(self, stream=False, **_kwargs):
        n = next(calls)
        (marks / f"started-{n}").write_text("")
        if n:
            return iter([f"call {n}"]) if stream else f"call {n}"
        if not stream:
            time.sleep(30)
            return "done"

        def slow():
            try:
                for _ in range(300):
                    yield "."
                    time.sleep(0.1)
            except GeneratorExit:
                (marks / "closed").write_text("")
                raise

        return slow()

    def get_last_finish_reason(self):
        return "stop"

    def get_last_reasoning(self):
        return None

    def cloud_model_provider(self, model=None):
        return None

mcp_bridge.create_client = lambda *_a, **_k: StreamingLLM()
mcp_bridge.start_server("127.0.0.1", port)
"""


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _post_llm(port, timeout):
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/llm",
        data=json.dumps({"query": "hi"}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return response.status, json.loads(response.read())


def _open_llm_call(port):
    """Send a /llm request on a raw socket, so the test decides when to hang up."""
    body = json.dumps({"query": "hi"}).encode("utf-8")
    conn = socket.create_connection(("127.0.0.1", port), timeout=5)
    conn.sendall(
        b"POST /llm HTTP/1.1\r\nHost: 127.0.0.1\r\n"
        b"Content-Type: application/json\r\n"
        + f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
        + body
    )
    return conn


def _wait_for(path, what):
    deadline = time.monotonic() + 10
    while not path.exists():
        assert time.monotonic() < deadline, what
        time.sleep(0.05)


@pytest.fixture(name="streaming_bridge")
def _streaming_bridge(tmp_path):
    port = _free_port()
    marks = tmp_path / "marks"
    marks.mkdir()
    log = tmp_path / "bridge.log"
    env = dict(os.environ)
    env.update(
        {
            "PYTHONPATH": str(REPO_SRC),
            "GAIA_HOME": str(tmp_path / "gaia_home"),
            # Nothing listens here; the fake client never dials it anyway.
            "LEMONADE_BASE_URL": "http://127.0.0.1:9/api/v1",
            "PYTHONIOENCODING": "utf-8",
        }
    )
    env.pop("GAIA_MCP_AUTH_TOKEN", None)
    with open(log, "w", encoding="utf-8") as log_file:
        proc = subprocess.Popen(
            [sys.executable, "-c", _STREAMING_BRIDGE, str(port), str(marks)],
            env=env,
            stdout=log_file,
            stderr=subprocess.STDOUT,
        )
        try:
            deadline = time.monotonic() + 60
            while True:
                try:
                    with urllib.request.urlopen(
                        f"http://127.0.0.1:{port}/health", timeout=2
                    ):
                        break
                except OSError:
                    if proc.poll() is not None or time.monotonic() > deadline:
                        pytest.fail(f"bridge did not start (exit code {proc.poll()})")
                    time.sleep(0.2)
            yield port, marks, log
        finally:
            proc.kill()
            proc.wait(timeout=10)


def test_hang_up_mid_reply_frees_the_bridge_for_the_next_caller(streaming_bridge):
    port, marks, log = streaming_bridge
    abandoned = _open_llm_call(port)
    _wait_for(marks / "started-0", "the first call never reached the LLM")

    abandoned.close()
    started = time.monotonic()
    status, body = _post_llm(port, timeout=10)

    assert (status, body["result"]) == (200, "call 1")
    assert time.monotonic() - started < 5
    _wait_for(marks / "closed", "the abandoned reply was never closed")
    text = log.read_text(encoding="utf-8")
    assert "MCP client disconnected; stopped its gaia.query call" in text
    assert "Traceback" not in text


def _wait_for_log(log, text, what):
    deadline = time.monotonic() + 10
    while text not in log.read_text(encoding="utf-8"):
        assert time.monotonic() < deadline, what
        time.sleep(0.05)


def test_caller_that_hangs_up_while_queued_never_runs(streaming_bridge):
    port, marks, log = streaming_bridge
    first = _open_llm_call(port)
    _wait_for(marks / "started-0", "the first call never reached the LLM")
    queued = _open_llm_call(port)

    # Hanging up before or after it reaches the queue ends the same way.
    queued.close()
    _wait_for_log(log, "was still queued", "the queued call never left the queue")
    first.close()
    status, body = _post_llm(port, timeout=10)

    # The queued call left without reaching the LLM, so this is the second call.
    assert (status, body["result"]) == (200, "call 1")


class _FakeLLM:
    def __init__(self, finish_reason="stop", reasoning=None):
        self.finish_reason = finish_reason
        self.reasoning = reasoning

    def get_last_finish_reason(self):
        return self.finish_reason

    def get_last_reasoning(self):
        return self.reasoning

    def cloud_model_provider(self, model=None):
        return cloud_model_provider(model)


def _bridge_with(llm):
    bridge = GAIAMCPBridge.__new__(GAIAMCPBridge)
    bridge.llm_client = llm
    return bridge


def _never_gone():
    return False


def test_streamed_answer_leaves_out_reasoning():
    bridge = _bridge_with(_FakeLLM(reasoning="plan"))
    reply = iter(["<think>", "plan", "</think>", "Hello", " world"])

    assert bridge._answer_from(reply, _never_gone) == "Hello world"


def test_a_think_tag_mentioned_in_the_answer_is_kept():
    bridge = _bridge_with(_FakeLLM())
    reply = iter(["Wrap it in a ", "<think>", " tag and close it later."])

    assert bridge._answer_from(reply, _never_gone) == (
        "Wrap it in a <think> tag and close it later."
    )


def test_answer_routed_into_reasoning_is_returned():
    bridge = _bridge_with(_FakeLLM(finish_reason="stop", reasoning="PONG"))

    assert bridge._answer_from(iter(["<think>", "PONG</think>"]), _never_gone) == (
        "PONG"
    )


@pytest.mark.parametrize(
    "model", ["fireworks.deepseek-v4p1-flash", "amd.DeepSeek-V4.1-Flash"]
)
@pytest.mark.parametrize(
    "reasoning",
    ["PONG", '{"tool": "run_shell", "tool_args": {"command": "rm -rf ~"}}'],
)
def test_a_cloud_models_reasoning_is_never_the_answer(model, reasoning):
    bridge = _bridge_with(_FakeLLM(finish_reason="stop", reasoning=reasoning))
    reply = iter(["<think>", reasoning, "</think>"])

    assert bridge._answer_from(reply, _never_gone, model) == ""


@pytest.mark.parametrize("model", ["Gemma-4-E4B-it-GGUF", "user.Qwen3.8-Flash-Next"])
def test_a_local_models_answer_routed_into_reasoning_still_wins(model):
    bridge = _bridge_with(_FakeLLM(finish_reason="stop", reasoning="PONG"))

    assert bridge._answer_from(
        iter(["<think>", "PONG</think>"]), _never_gone, model
    ) == ("PONG")


def test_a_cloud_models_real_answer_is_kept_without_its_reasoning():
    bridge = _bridge_with(_FakeLLM(finish_reason="stop", reasoning="plan"))
    reply = iter(["<think>", "plan", "</think>", "42"])

    assert bridge._answer_from(reply, _never_gone, "fireworks.glm-5p3-flash") == "42"


def test_reasoning_cut_off_by_the_token_limit_is_not_an_answer():
    bridge = _bridge_with(_FakeLLM(finish_reason="length", reasoning="half a plan"))

    assert bridge._answer_from(iter(["<think>", "half a plan"]), _never_gone) == ""


def _handler_on(sock):
    handler = MCPHTTPHandler.__new__(MCPHTTPHandler)
    handler.connection = sock
    return handler


def test_a_connected_client_is_not_gone():
    bridge_end, client_end = socket.socketpair()
    with bridge_end, client_end:
        assert _handler_on(bridge_end)._client_gone() is False


def test_a_client_that_closed_is_gone():
    bridge_end, client_end = socket.socketpair()
    client_end.close()
    with bridge_end:
        assert _handler_on(bridge_end)._client_gone() is True


def test_a_client_that_sent_more_data_is_not_gone():
    bridge_end, client_end = socket.socketpair()
    with bridge_end, client_end:
        client_end.sendall(b"\r\n")
        assert _handler_on(bridge_end)._client_gone() is False


def test_a_reset_connection_is_gone():
    class ResetSocket:
        def fileno(self):
            return bridge_end.fileno()

        def recv(self, *_args):
            raise ConnectionResetError

    bridge_end, client_end = socket.socketpair()
    with bridge_end, client_end:
        client_end.sendall(b"x")
        assert _handler_on(ResetSocket())._client_gone() is True
