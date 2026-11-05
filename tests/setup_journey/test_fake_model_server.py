# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""The fake Lemonade's --catalog mode, read by GAIA's real clients.

The setup journey only proves anything if this fake takes the calls Lemonade
takes and refuses the ones it refuses, so these go through the same code that
talks to the real server, not through hand-written requests.
"""

import base64
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from gaia.audio.lemonade_asr import LemonadeASRClient
from gaia.audio.lemonade_tts import LemonadeTTSClient
from gaia.installer import init_command, model_choice
from gaia.installer.init_command import _JsonDownloadProgress, _solid_red_png
from gaia.llm.lemonade_client import LemonadeClient
from gaia.llm.model_fit import (
    capacity_from_system_info,
    free_memory,
    model_store_free_gb,
)
from gaia.sd.mixin import decode_image_response, image_problem

REPO = Path(__file__).resolve().parents[2]
FAKE = REPO / "installer" / "headless" / "fake_model_server.py"
GEMMA = "Gemma-4-E4B-it-GGUF"
EMBEDDER = "user.embeddinggemma-300m-GGUF"


def _start(tmp_path: Path, *args: str, **env: str):
    port_file = tmp_path / "port"
    proc = subprocess.Popen(
        [sys.executable, str(FAKE), "--port-file", str(port_file), *args],
        env={**os.environ, "FAKE_LEMONADE_PULL_SECONDS": "0.2", **env},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    deadline = time.monotonic() + 15
    while not (port_file.exists() and port_file.read_text()):
        assert time.monotonic() < deadline, "the fake server did not start"
        time.sleep(0.05)
    return proc, f"http://127.0.0.1:{port_file.read_text()}/api/v1"


@pytest.fixture
def fake(tmp_path, request):
    """A --catalog server; an indirect parametrize passes its environment."""
    env = getattr(request, "param", {})
    proc, url = _start(tmp_path, "--catalog", **env)
    yield url
    proc.kill()
    proc.wait()


def _client(url: str) -> LemonadeClient:
    return LemonadeClient(base_url=url, verbose=False)


def _status(url: str, body: dict) -> int:
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as reply:
            reply.read()
            return reply.status
    except urllib.error.HTTPError as e:
        return e.code


class _Console:
    def __init__(self):
        self.errors = []

    def print_download_error(self, message, model_id):
        self.errors.append((message, model_id))


class _Events:
    enabled = True

    def __init__(self):
        self.seen = []

    def emit(self, event, **fields):
        self.seen.append({"event": event, **fields})


def _pull(client: LemonadeClient, model: str):
    events = _Events()
    console = _Console()
    progress = _JsonDownloadProgress(console, events, model, 1, 1)
    # Every event reaches the UI, not just the ones a slow bar would keep.
    progress.EMIT_S = 0
    ok = model_choice.pull_model(
        client, model, progress, **model_choice.pull_kwargs_for(model)
    )
    return ok, events.seen, console.errors


class TestNothingDownloaded:
    def test_lists_the_catalog_but_nothing_as_downloaded(self, fake):
        client = _client(fake)
        assert client.list_models().get("data") == []
        assert GEMMA in [m["id"] for m in client.list_models(show_all=True)["data"]]
        assert client.check_model_available(GEMMA) is False

    def test_a_model_it_does_not_have_cannot_run(self, fake):
        assert _status(f"{fake}/load", {"model_name": GEMMA, "ctx_size": 65536}) == 404
        assert _status(f"{fake}/chat/completions", {"model": GEMMA}) == 404


class TestPull:
    def test_the_real_consumer_sees_bytes_climb_to_the_total(self, fake):
        client = _client(fake)
        ok, seen, errors = _pull(client, GEMMA)
        assert ok and not errors
        done = [e["bytes_done"] for e in seen if e["event"] == "progress"]
        assert len(done) >= 5 and done == sorted(done) and done[0] < done[-1]
        assert seen[-1]["bytes_total"] == int(5.56e9) == done[-1]
        assert client.check_model_available(GEMMA) is True
        assert len(client.list_models()["data"]) == 1

    def test_a_user_model_registers_and_lists_without_its_prefix(self, fake):
        client = _client(fake)
        ok, _, errors = _pull(client, EMBEDDER)
        assert ok and not errors
        listed = client.list_models()["data"]
        # What the real server does, which `_model_ids_match` exists for.
        assert [m["id"] for m in listed] == ["embeddinggemma-300m-GGUF"]
        assert "embeddings" in listed[0]["labels"]
        assert client.check_model_available(EMBEDDER) is True

    @pytest.mark.parametrize(
        "body",
        [
            pytest.param(
                {"model_name": GEMMA, "stream": True, "recipe": "llamacpp"},
                id="built-in-with-a-recipe-1655",
            ),
            pytest.param(
                {"model_name": "user.Mine", "stream": True, "recipe": "llamacpp"},
                id="registration-without-a-checkpoint",
            ),
            pytest.param({"model_name": "Nope", "stream": True}, id="unknown-model"),
        ],
    )
    def test_refuses_what_lemonade_refuses(self, fake, body):
        assert _status(f"{fake}/pull", body) == 400

    def test_a_cancelled_pull_downloads_nothing(self, fake):
        request = urllib.request.Request(
            f"{fake}/pull",
            data=json.dumps({"model_name": GEMMA, "stream": True}).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=10) as reply:
            reply.readline()
        time.sleep(0.5)
        assert _client(fake).check_model_available(GEMMA) is False


class TestPullFailure:
    @pytest.mark.parametrize(
        ("fake", "kind"),
        [
            ({"FAKE_LEMONADE_PULL_FAIL": k}, k)
            for k in ("disk_full", "network", "lemonade")
        ],
        indirect=["fake"],
    )
    def test_stops_partway_with_the_words_gaia_classifies_then_works(self, fake, kind):
        client = _client(fake)
        ok, seen, errors = _pull(client, GEMMA)
        assert not ok
        assert model_choice.error_kind(errors[0][0]) == kind
        partway = [e["bytes_done"] for e in seen if e["event"] == "progress"]
        assert partway and 0 < partway[-1] < int(5.56e9)
        assert client.check_model_available(GEMMA) is False

        ok, _, errors = _pull(client, GEMMA)
        assert ok and not errors

    @pytest.mark.parametrize(
        "fake",
        [{"FAKE_LEMONADE_PULL_FAIL": "lemonade", "FAKE_LEMONADE_PULL_FAIL_TIMES": "2"}],
        indirect=True,
    )
    def test_fails_as_many_times_as_it_was_told(self, fake):
        client = _client(fake)
        assert [_pull(client, GEMMA)[0] for _ in range(3)] == [False, False, True]

    def test_an_unknown_failure_mode_refuses_to_start(self):
        done = subprocess.run(
            [sys.executable, str(FAKE), "--catalog"],
            env={**os.environ, "FAKE_LEMONADE_PULL_FAIL": "nope"},
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert done.returncode == 1
        assert "FAKE_LEMONADE_PULL_FAIL" in done.stderr


class TestSystemInfo:
    def test_reads_as_a_gpu_with_a_model_store_and_a_llamacpp_backend(self, fake):
        info = _client(fake).get_system_info()
        capacity = capacity_from_system_info(info)
        assert capacity.memory_gb == 16.0
        free, store = model_store_free_gb(info)
        assert free == 500.0 and store
        assert _client(fake)._default_llamacpp_backend() == "cpu"

    def test_a_discrete_gpu_means_no_free_memory_is_read_from_the_runner(self, fake):
        assert free_memory(_client(fake).get_system_info(), _Memory()) is None

    def test_a_backend_is_installable_until_installed(self, fake):
        client = _client(fake)

        def state():
            return client.get_recipe_status("llamacpp")["backends"]["cpu"]["state"]

        assert state() == "installable"
        client.install_backend("llamacpp:cpu")
        assert state() == "installed"

    @pytest.mark.parametrize("fake", [{"FAKE_LEMONADE_GPU": "igpu"}], indirect=True)
    def test_igpu_mode_reports_a_split_gpu_memory_can_change(self, fake):
        from gaia.llm.model_fit import integrated_gpu_memory

        assert integrated_gpu_memory(_client(fake).get_system_info()) is not None


class _Memory:
    ram_available_gb = 1.0
    swap_free_gb = 0.0
    gpus = ()


class TestTheTestStepsChecks:
    @pytest.fixture
    def ready(self, tmp_path):
        models = [GEMMA, EMBEDDER, "Whisper-Large-v3-Turbo", "kokoro-v1", "SDXL-Turbo"]
        args = [a for m in models for a in ("--model", m)]
        proc, url = _start(tmp_path, "--catalog", *args)
        yield url
        proc.kill()
        proc.wait()

    def _image_message(self):
        image = base64.b64encode(_solid_red_png()).decode("ascii")
        return [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "What colour is this image?"},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/png;base64,{image}"},
                    },
                ],
            }
        ]

    def test_a_vision_model_names_the_colour(self, ready):
        reply = _client(ready).chat_completions(
            model=GEMMA, messages=self._image_message(), auto_download=False
        )
        assert "red" in reply["choices"][0]["message"]["content"].lower()

    def test_a_model_without_vision_refuses_an_image(self, ready):
        assert (
            _status(
                f"{ready}/chat/completions",
                {"model": EMBEDDER, "messages": self._image_message()},
            )
            == 400
        )

    def test_speech_transcribes(self, ready, tmp_path):
        wav = tmp_path / "tone.wav"
        init_command._tone_wav(str(wav))
        result = LemonadeASRClient(
            base_url=ready, model="Whisper-Large-v3-Turbo"
        ).transcribe(str(wav), require_timestamps=False)
        assert result

    def test_voice_speaks_real_samples(self, ready):
        samples, rate = LemonadeTTSClient(base_url=ready, model="kokoro-v1").synthesize(
            "GAIA is ready."
        )
        assert rate == 24000 and len(samples) and abs(samples).max() > 0.1

    def test_an_image_passes_gaias_own_flat_colour_check(self, ready):
        reply = _client(ready).generate_image(
            prompt="test", model="SDXL-Turbo", steps=1, size="512x512"
        )
        image, problem = decode_image_response(reply)
        assert problem is None and image_problem(image) is None

    def test_ctx_size_reads_back_after_a_load(self, ready):
        client = _client(ready)
        _status(f"{ready}/load", {"model_name": EMBEDDER, "ctx_size": 2048})
        loaded = {
            m["model_name"]: m["recipe_options"]["ctx_size"]
            for m in client.health_check()["all_models_loaded"]
        }
        assert loaded == {"embeddinggemma-300m-GGUF": 2048}
        listed = {m["id"]: m for m in client.list_models()["data"]}
        assert listed["embeddinggemma-300m-GGUF"]["recipe_options"] == {
            "ctx_size": 2048
        }


class TestWithoutCatalog:
    def test_every_model_still_counts_as_downloaded(self, tmp_path):
        proc, url = _start(tmp_path, "--model", GEMMA)
        try:
            client = _client(url)
            assert client.list_models()["data"][0]["downloaded"] is True
            assert _status(f"{url}/load", {"model_name": "Anything"}) == 200
            assert (
                _status(f"{url}/pull", {"model_name": "Anything", "recipe": "x"}) == 200
            )
            backends = client.get_recipe_status("llamacpp")["backends"]
            assert backends["cpu"]["state"] == "installed"
        finally:
            proc.kill()
            proc.wait()
