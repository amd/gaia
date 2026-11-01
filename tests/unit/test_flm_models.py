# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""FastFlowLM (NPU) models from a cold Lemonade: offered, addable, downloadable.

Until its NPU backend is installed Lemonade lists no FLM model and refuses to
pull one by name, so these tests start from that state: a warm machine hides
every failure here (#1655). Hugging Face and GitHub are faked at ``_http_get``;
the model list mirrors the shape of FastFlowLM's ``src/model_list.json``.
"""

import asyncio
import json

import pytest

from gaia import config as config_mod
from gaia.llm import custom_models as cm
from gaia.llm import lemonade_client as lc
from gaia.llm.lemonade_client import LemonadeClient, LemonadeClientError
from gaia.llm.model_fit import (
    MachineCapacity,
    OsMemory,
    capacity_from_system_info,
    npu_capacity,
    npu_free_memory,
)
from gaia.ui import local_models

REPO = "https://huggingface.co/FastFlowLM/Gemma4-E4B-IT-NPU2"


def _flm(name, labels=None, footprint=2.0, **extra):
    entry = {
        "name": name,
        "url": f"https://huggingface.co/FastFlowLM/{name}",
        "footprint": footprint,
        "default_context_length": 32768,
        "details": {"think": False},
        **extra,
    }
    if labels is not None:
        entry["label"] = labels
    return entry


MODEL_LIST = {
    "model_path": "models",
    "models": {
        # One repo backs both: the single-turn tag must never win.
        "gemma4e-flash": {
            "e4b": _flm(
                "Gemma4-E4B-IT-NPU2",
                ["vision", "single-turn", "chat-transcription"],
                9.1,
            )
        },
        "gemma4-it": {
            "e4b": _flm(
                "Gemma4-E4B-IT-NPU2",
                ["audio", "vision", "reasoning", "tool-calling"],
                9.1,
                vlm=True,
            ),
            "e2b": _flm("Gemma4-E2B-IT-NPU2", ["tool-calling"], 6.0),
        },
        "qwen3.5": {"4b": _flm("Qwen3.5-4B-NPU2", ["reasoning", "tool-calling"], 5.0)},
        "lfm2": {"1.2b": _flm("LFM2-1.2B-NPU2", None, 0.96)},
        "embed-gemma": {"300m": _flm("Embedding-Gemma-300M-NPU2", ["embeddings"])},
        "whisper-v3": {"turbo": _flm("Whisper-V3-Turbo-NPU2", ["transcription"])},
        "huge": {"1t": _flm("Huge-NPU2", ["tool-calling"], 900.0)},
        "unsized": {"1b": _flm("Unsized-NPU2", [], "lots")},
        # Entries a hostile or changed list could hold are skipped, not trusted.
        "../../evil": {"x": _flm("Evil-NPU2")},
        "broken": "not a mapping",
        "nourl": {"1b": {"name": "NoUrl-NPU2"}},
    },
}


def _system_info(state="installable", npu=True, version="v1.0.7", **extra):
    info = {
        "Physical Memory": "64.00 GB",
        "model_storage": {"free_bytes": 500e9},
        "devices": {"amd_npu": {"available": npu}},
        "recipes": {
            "flm": {"backends": {"npu": {"state": state, "version": version}}},
        },
    }
    info.update(extra)
    return info


class _Resp:
    def __init__(self, status, body=None):
        self.status_code = status
        self._body = body

    def json(self):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


@pytest.fixture
def web(monkeypatch, tmp_path):
    state = {
        "siblings": [{"rfilename": "model.q4nx"}, {"rfilename": "README.md"}],
        "list": (200, MODEL_LIST),
        "urls": [],
    }

    def get(url, headers=None):
        state["urls"].append(url)
        if url.startswith("https://huggingface.co/api/models/"):
            return _Resp(200, {"siblings": state["siblings"]})
        if url.startswith("https://raw.githubusercontent.com/ROCm/FastFlowLM/"):
            return _Resp(*state["list"])
        raise AssertionError(f"unexpected fetch {url}")

    monkeypatch.setattr(cm, "_http_get", get)
    monkeypatch.setattr(cm, "_flm_cache_path", lambda tag: tmp_path / f"{tag}.json")
    return state


@pytest.fixture
def config_file(monkeypatch, tmp_path):
    path = tmp_path / "config.json"
    monkeypatch.setattr(config_mod, "GAIA_CONFIG_FILE", path)
    return path


def _inspect(url, info=None, **kw):
    return cm.inspect_link(url, system_info=info or _system_info(), **kw)


def _failed(report):
    return [c for c in report["checks"] if not c["ok"]]


# ── Pasting a FastFlowLM link ──────────────────────────────────────────────


def test_the_reported_link_points_at_the_offered_model(web, config_file):
    report = _inspect(REPO)
    assert not report["ok"]
    assert report["model_id"] == "gemma4-it-e4b-FLM"
    assert report["checkpoint"] == "gemma4-it:e4b"
    assert "already in your model list" in report["error"]
    assert "GGUF" not in report["error"]


def test_e2b_is_not_offered_so_its_link_adds_it(web, config_file):
    report = _inspect("FastFlowLM/Gemma4-E2B-IT-NPU2")
    assert report["ok"], report["error"]
    assert report["model_id"] == "gemma4-it-e2b-FLM"


def test_a_repo_two_tags_share_resolves_to_the_chat_build(web, config_file):
    assert _inspect(REPO)["checkpoint"] != "gemma4e-flash:e4b"


@pytest.mark.parametrize(
    "link",
    [
        "fastflowlm/qwen3.5-4b-npu2",
        "https://hf.co/FastFlowLM/Qwen3.5-4B-NPU2/tree/main",
        "https://huggingface.co/FastFlowLM/Qwen3.5-4B-NPU2/blob/main/model.q4nx",
    ],
)
def test_any_form_of_the_link_resolves(web, config_file, link):
    report = _inspect(link)
    assert report["ok"], report["error"]
    assert report["model_id"] == "qwen3.5-4b-FLM"
    assert report["recipe"] == "flm"
    assert report["display_name"] == "Qwen3.5 4B NPU"
    assert report["reasoning"] is True


def test_an_added_model_is_the_built_in_and_pulls_by_name_only(web, config_file):
    cm.add_custom_model(_inspect("FastFlowLM/Qwen3.5-4B-NPU2"))
    req = lc.find_model_requirement("qwen3.5-4b-FLM")
    assert req.recipe == "flm"
    # A recipe on a built-in is Lemonade's 400 (#1655).
    assert req.pull_kwargs() == {}


def test_a_model_without_tool_support_stays_off_the_tools_path(web, config_file):
    report = _inspect("FastFlowLM/LFM2-1.2B-NPU2")
    assert report["ok"] and "no tool support" in report["warnings"][0]
    cm.add_custom_model(report)
    assert lc.is_tool_calling_model("lfm2-1.2b-FLM") is False
    assert lc.is_tool_calling_model("Some-Unknown-GGUF") is True


def test_a_listed_but_undownloaded_built_in_has_nothing_to_delete():
    entry = {"id": "qwen3.5-4b-FLM", "checkpoint": "qwen3.5:4b", "recipe": "flm"}
    listed = {"id": entry["id"], "checkpoint": entry["checkpoint"]}
    assert cm.listed_entry([{**listed, "downloaded": False}], entry) is None
    assert cm.listed_entry([{**listed, "downloaded": True}], entry) == entry["id"]


def test_a_small_window_is_a_warning(web, config_file):
    web["list"] = (
        200,
        {
            "models": {
                "tiny": {
                    "1b": _flm(
                        "Tiny-NPU2",
                        ["tool-calling"],
                        default_context_length=4096,
                    )
                }
            }
        },
    )
    report = _inspect("FastFlowLM/Tiny-NPU2")
    assert report["ok"] and "4,096-token window" in report["warnings"][0]


def test_list_fields_of_the_wrong_type_are_cleaned():
    odd = {**_flm("Odd-NPU2", "tool-calling"), "name": "Odd\x1b[31m-NPU2"}
    build = cm._flm_builds({"models": {"odd": {"1b": odd}}})[0]
    assert build["labels"] == [] and "\x1b" not in build["name"]


def test_remove_forgets_the_built_in_and_its_download(web, config_file):
    cm.add_custom_model(_inspect("FastFlowLM/Qwen3.5-4B-NPU2"))
    deleted = []
    cm.remove_custom_model("qwen3.5-4b-FLM", deleted.append)
    assert [e["id"] for e in deleted] == ["qwen3.5-4b-FLM"]
    assert cm.list_custom_models() == []


@pytest.mark.parametrize(
    "repo, check, says",
    [
        ("FastFlowLM/Embedding-Gemma-300M-NPU2", "chat", "not a chat model"),
        ("FastFlowLM/Whisper-V3-Turbo-NPU2", "chat", "not a chat model"),
        ("FastFlowLM/Newer-Than-This-Release-NPU2", "flm", "has no model from"),
        ("someone/Fork-Of-Gemma4-NPU2", "flm", "has no model from"),
        ("FastFlowLM/Huge-NPU2", "fit", "900.0 GB"),
        ("FastFlowLM/Unsized-NPU2", "fit", "how much memory"),
    ],
)
def test_refusals_name_the_failed_check(web, config_file, repo, check, says):
    report = _inspect(repo)
    assert not report["ok"]
    assert _failed(report)[-1]["id"] == check
    assert says in report["error"]


def test_a_quantization_is_refused_not_ignored(web, config_file):
    assert "no quantizations" in _inspect(f"{REPO}", variant="Q4_K_M")["error"]
    assert "no quantizations" in _inspect(f"{REPO[23:]}:Q4_K_M")["error"]


def test_a_downloaded_model_says_to_pick_it(web, config_file):
    catalog = [{"id": "qwen3.5-4b-FLM", "checkpoint": "qwen3.5:4b", "downloaded": True}]
    report = _inspect("FastFlowLM/Qwen3.5-4B-NPU2", catalog=catalog)
    assert not report["ok"] and "already downloaded" in report["error"]


def test_a_listed_but_not_downloaded_model_can_be_added(web, config_file):
    catalog = [
        {"id": "qwen3.5-4b-FLM", "checkpoint": "qwen3.5:4b", "downloaded": False}
    ]
    assert _inspect("FastFlowLM/Qwen3.5-4B-NPU2", catalog=catalog)["ok"]


def test_adding_twice_is_refused(web, config_file):
    cm.add_custom_model(_inspect("FastFlowLM/Qwen3.5-4B-NPU2"))
    report = _inspect("FastFlowLM/Qwen3.5-4B-NPU2")
    assert not report["ok"] and "already added" in report["error"]


def test_a_repo_with_both_formats_stays_a_gguf_repo(web, config_file):
    web["siblings"] = [{"rfilename": "model.q4nx"}, {"rfilename": "m-Q4_K_M.gguf"}]
    report = _inspect("someone/Both")
    assert _failed(report)[0]["id"] == "chat"  # the GGUF path's next check
    assert not any("raw.githubusercontent" in u for u in web["urls"])


def test_a_repo_with_neither_format_names_both(web, config_file):
    web["siblings"] = [{"rfilename": "model.safetensors"}]
    report = _inspect("someone/Neither")
    assert "-GGUF or -NPU2" in report["error"]


# ── A PC or Lemonade that can't run it ─────────────────────────────────────


@pytest.mark.parametrize(
    "info, says",
    [
        (_system_info(state="unsupported"), "needs an AMD Ryzen AI NPU"),
        (_system_info(npu=False), "reports none on this PC"),
        (dict(_system_info(), unavailable_recipes=["flm"]), "cannot run flm"),
    ],
)
def test_no_npu_is_refused_before_anything_is_fetched(web, config_file, info, says):
    report = _inspect(REPO, info)
    assert not report["ok"] and says in report["error"]
    assert not any("raw.githubusercontent" in u for u in web["urls"])


def test_a_lemonade_that_names_no_flm_release_is_an_error(web, config_file):
    report = _inspect(REPO, _system_info(version=""))
    assert not report["ok"] and "did not say" in report["error"]


@pytest.mark.parametrize(
    "version", ["../../etc/passwd", "v1.0.7/../../x", "v1.0.7\n", "latest", "v1"]
)
def test_a_version_that_is_not_a_release_tag_reaches_no_url(web, config_file, version):
    report = _inspect(REPO, _system_info(version=version))
    assert not report["ok"] and "not a release tag" in report["error"]
    assert not any("raw.githubusercontent" in u for u in web["urls"])


def test_a_bare_version_is_read_as_its_tag(web, config_file):
    _inspect("FastFlowLM/Qwen3.5-4B-NPU2", _system_info(version="1.0.7"))
    assert any("/v1.0.7/src/model_list.json" in u for u in web["urls"])


# ── FastFlowLM's model list ────────────────────────────────────────────────


def test_the_list_is_read_for_the_release_lemonade_runs(web, config_file):
    report = _inspect("FastFlowLM/Qwen3.5-4B-NPU2", _system_info(version="v9.8.7"))
    assert report["flm_version"] == "v9.8.7"
    assert any("/v9.8.7/src/model_list.json" in u for u in web["urls"])


def test_the_list_is_cached_per_release(web, config_file):
    _inspect("FastFlowLM/Qwen3.5-4B-NPU2")
    _inspect("FastFlowLM/Qwen3.5-4B-NPU2")
    assert sum("model_list" in u for u in web["urls"]) == 1


def test_a_corrupt_cache_says_which_file(web, config_file, tmp_path):
    (tmp_path / "v1.0.7.json").write_text("{not json", encoding="utf-8")
    report = _inspect(REPO)
    assert not report["ok"] and "Delete that file" in report["error"]


@pytest.mark.parametrize(
    "answer, says",
    [
        ((404, None), "HTTP 404"),
        ((200, ValueError("bad")), "not JSON"),
        ((200, []), "names no models"),
        ((200, {"models": {}}), "names no models"),
        ((200, {"models": "nope"}), "names no models"),
    ],
)
def test_an_unusable_list_is_an_error_not_a_pass(web, config_file, answer, says):
    web["list"] = answer
    report = _inspect(REPO)
    assert not report["ok"] and says in report["error"]


def test_entries_that_could_not_be_a_model_id_are_dropped():
    ids = {b["model_id"] for b in cm._flm_builds(MODEL_LIST)}
    assert "qwen3.5-4b-FLM" in ids
    assert not any("/" in i or ".." in i for i in ids)
    assert "nourl-1b-FLM" not in ids


# ── The saved list ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("model_id", ["qwen3.5-4b-FLM", "user.x-GGUF"])
def test_saved_entries_are_a_user_model_or_a_built_in_flm_model(model_id, tmp_path):
    entry = dict.fromkeys(cm._ENTRY_KEYS, "x") | {"id": model_id}
    assert cm.validate_entries([entry], tmp_path) == [entry]


@pytest.mark.parametrize(
    "model_id", ["Gemma-4-E4B-it-GGUF", "../x-FLM", "a b-FLM", "-FLM", "x-flm\n"]
)
def test_any_other_saved_id_is_refused(model_id, tmp_path):
    entry = dict.fromkeys(cm._ENTRY_KEYS, "x") | {"id": model_id}
    with pytest.raises(config_mod.GaiaConfigError):
        cm.validate_entries([entry], tmp_path)


# ── Installing the backend ─────────────────────────────────────────────────


class _Lemonade(LemonadeClient):
    def __init__(self, info):  # pylint: disable=super-init-not-called
        self.info, self.sent = info, []
        self.log = lc.get_logger(__name__)
        self.base_url = "http://localhost:1/api/v1"

    def get_system_info(self, *a, **k):
        self.sent.append(("GET", "system-info", None))
        return self.info

    def _send_request(self, method, url, data=None, timeout=None, **k):
        self.sent.append((method, url.rsplit("/", 1)[-1], data))
        return {"status": "success"}

    def list_models(self, *a, **k):
        return {"data": []}

    def cloud_model_provider(self, model):
        return None

    def _wait_for_model_download(self, *a, **k):
        return True


@pytest.mark.parametrize("state", ["installable", "update_required"])
def test_an_flm_model_installs_the_backend_with_a_valid_request(state):
    client = _Lemonade(_system_info(state=state))
    client.ensure_npu_backend("gemma4-it-e4b-FLM")
    # /install 400s on anything but this body (#3175).
    assert ("post", "install", {"recipe": "flm", "backend": "npu"}) in client.sent


def test_an_installed_backend_is_left_alone():
    client = _Lemonade(_system_info(state="installed"))
    client.ensure_npu_backend("gemma4-it-e4b-FLM")
    assert not [c for c in client.sent if c[1] == "install"]


def test_a_gpu_model_never_asks_about_the_npu():
    client = _Lemonade(_system_info())
    client.ensure_npu_backend("Gemma-4-E4B-it-GGUF")
    assert client.sent == []


def test_a_pc_without_an_npu_is_told_so_instead_of_installing():
    client = _Lemonade(_system_info(state="unsupported"))
    with pytest.raises(LemonadeClientError, match="needs an AMD Ryzen AI NPU"):
        client.ensure_npu_backend("gemma4-it-e4b-FLM")
    assert not [c for c in client.sent if c[1] == "install"]


def test_a_cold_stream_pull_reports_the_missing_npu_as_a_progress_error():
    client = _Lemonade(_system_info(state="unsupported"))
    seen = []
    assert not client.ensure_model_downloaded(
        "gemma4-it-e4b-FLM", show_progress=False, on_progress=seen.append
    )
    assert "Ryzen AI NPU" in seen[-1]["error"]


def test_a_direct_pull_installs_the_backend_too():
    client = _Lemonade(_system_info())
    client.pull_model("gemma4-it-e4b-FLM")
    verbs = [c[1] for c in client.sent]
    assert verbs.index("install") < verbs.index("pull")


def test_a_cold_download_installs_first_then_pulls_by_name_only(monkeypatch):
    client = _Lemonade(_system_info())
    assert client.ensure_model_downloaded("gemma4-it-e4b-FLM", show_progress=False)
    verbs = [c[1] for c in client.sent]
    assert verbs.index("install") < verbs.index("pull")
    pull = next(c for c in client.sent if c[1] == "pull")
    assert pull[2] == {"model_name": "gemma4-it-e4b-FLM"}


def test_the_settings_download_reports_a_missing_npu(monkeypatch):
    from gaia.ui.routers import system

    def refuse(self, model):
        raise LemonadeClientError(f"{model} needs an AMD Ryzen AI NPU and its driver.")

    updates = []
    monkeypatch.setattr(LemonadeClient, "ensure_npu_backend", refuse)
    monkeypatch.setattr(
        system, "_set_download_progress", lambda m, p: updates.append(p)
    )

    def no_pull(*a, **k):
        raise AssertionError("must not pull after the backend failed")

    monkeypatch.setattr("httpx.AsyncClient", no_pull)

    async def evicted(model):
        evictions.append(model)

    evictions = []
    monkeypatch.setattr(system, "_evict_progress_after_ttl", evicted)

    async def run():
        await system._stream_lemonade_pull("gemma4-it-e4b-FLM", False)
        await asyncio.sleep(0)

    asyncio.run(run())
    assert updates[-1]["state"] == "error"
    assert "Ryzen AI NPU" in updates[-1]["message"]
    # The install can take minutes, so the row says what it is waiting on.
    assert updates[1]["state"] == "starting"
    assert "NPU backend" in updates[1]["message"]
    # A sticky error would block the retry.
    assert evictions == ["gemma4-it-e4b-FLM"]


# ── The context window ─────────────────────────────────────────────────────


E4B = lc.NPU_DEFAULT_MODEL_NAME
E2B = "gemma4-it-e2b-FLM"


def _e4b_window(npu_gb, gpu_gb=8.0):
    return lc.context_for_capacity(
        lc.find_model_requirement(E4B),
        MachineCapacity(gpu_gb, "AMD iGPU", 500.0, npu_memory_gb=npu_gb),
    )


def test_a_large_pc_gets_the_models_own_maximum():
    assert _e4b_window(96.0) == lc.find_model_requirement(E4B).max_ctx_size


def test_ram_sets_the_window_when_it_runs_out_before_the_model_does():
    assert lc.GPU_CTX_SIZE < _e4b_window(16.0) < 131072


def test_the_window_follows_system_ram_not_the_igpu_share():
    assert _e4b_window(96.0, gpu_gb=1.0) == _e4b_window(96.0, gpu_gb=64.0)


def test_no_npu_means_the_registered_floor():
    assert _e4b_window(None) == lc.GPU_CTX_SIZE


@pytest.mark.parametrize("model", [E4B, E2B, "some-unknown-FLM"])
def test_gaia_ctx_size_overrides_an_flm_window(monkeypatch, model):
    monkeypatch.setenv("GAIA_CTX_SIZE", "131072")
    assert lc.resolve_ctx_size(model) == 131072


def test_settings_offers_e4b_up_to_what_this_pc_holds():
    from gaia.llm.context_choice import context_range

    span = context_range(E4B, _capacity(96.0))
    assert span["max_fit"] == span["automatic"] == 131072
    assert span["fit_checked"]
    assert context_range(E4B, _capacity(16.0))["max_fit"] < 131072


def test_settings_offers_e2b_to_its_maximum_unchecked():
    from gaia.llm.context_choice import context_range

    span = context_range(E2B, _capacity(96.0))
    assert span["max_fit"] == 131072 and not span["fit_checked"]
    assert span["automatic"] == lc.NPU_CTX_SIZE


def test_settings_offers_an_unregistered_flm_model_its_trained_window():
    from gaia.llm.context_choice import context_range

    span = context_range("some-unknown-FLM", _capacity(96.0), trained_max=131072)
    assert span["max_fit"] == 131072 and span["automatic"] == lc.NPU_CTX_SIZE


def test_startup_asks_an_flm_model_for_its_own_window(monkeypatch):
    # The device profile's window here reloaded the model on every start.
    from gaia.llm import lemonade_manager as lm

    class Stop(Exception):
        pass

    seen = {}

    def resolve(**kwargs):
        seen.update(kwargs)
        raise Stop

    monkeypatch.setattr(lm, "resolve_ctx_size", resolve)
    with pytest.raises(Stop):
        lm.LemonadeManager.ensure_ready(model=E2B, quiet=True)
    assert seen["model"] == E2B


def test_the_outgoing_model_is_not_reloaded_at_the_incoming_window():
    from gaia.llm.lemonade_manager import LemonadeManager

    class NoLoad:
        def load_model(self, *a, **k):
            raise AssertionError("a model on its way out must not reload")

    class Status:
        loaded_models = [{"model_name": "Gemma-4-E4B-it-GGUF", "type": "llm"}]

    assert LemonadeManager._try_reload_with_ctx(
        NoLoad(), Status, 98304, True, None, own_window_of=E4B
    )


# ── What is offered ────────────────────────────────────────────────────────


def _capacity(npu_gb, gpu_gb=8.0):
    return MachineCapacity(gpu_gb, "AMD iGPU", 500.0, npu_memory_gb=npu_gb)


def test_gemma_on_the_npu_is_offered_where_it_fits():
    assert lc.npu_chat_models_that_fit(_capacity(64.0)) == list(lc.NPU_MODEL_LADDER)


@pytest.mark.parametrize("npu_gb", [None, 0.0, 1.0, 9.0])
def test_no_npu_or_no_room_offers_none(npu_gb):
    assert lc.npu_chat_models_that_fit(_capacity(npu_gb)) == []


def test_the_npu_is_judged_on_system_ram_not_the_gpu_pool():
    model = {"id": "gemma4-it-e4b-FLM", "recipe": "flm"}
    assert local_models._fit(model, 9.1, _capacity(32.0, gpu_gb=4.0)) == ""


def test_a_pc_without_an_npu_says_why():
    model = {"id": "qwen3.5-4b-FLM", "recipe": "flm"}
    assert "Ryzen AI NPU" in local_models._fit(model, 5.0, _capacity(None))


def test_setup_judges_an_flm_default_on_system_ram():
    from gaia.installer.model_choice import memory_fit

    fit = memory_fit("gemma4-it-e4b-FLM", 9.1, _capacity(32.0, gpu_gb=4.0))
    assert fit["fits"] is True


def test_setup_says_an_flm_model_needs_an_npu():
    from gaia.installer.model_choice import memory_fit

    fit = memory_fit("gemma4-it-e4b-FLM", 9.1, _capacity(None))
    assert fit["fits"] is False and "Ryzen AI NPU" in fit["fit_reason"]


def test_an_apu_carve_out_comes_off_the_npu_memory():
    info = _system_info(
        devices={
            "amd_npu": {"available": True},
            "amd_igpu": {"available": True, "vram_gb": 16.0, "name": "Radeon"},
        }
    )
    assert capacity_from_system_info(info).npu_memory_gb == 48.0


def test_a_load_reads_free_system_ram_not_free_gpu_memory():
    free = npu_free_memory({}, OsMemory(32.0, 20.0, 4.0))
    assert (free.free_gb, free.limit_gb) == (20.0, 24.0)
    assert npu_capacity(_capacity(32.0)).on_gpu is False


# ── The two lists ──────────────────────────────────────────────────────────


def test_every_offered_model_is_a_registered_requirement():
    for model_id in lc.NPU_MODEL_LADDER:
        assert lc.find_model_requirement(model_id).pull_kwargs() == {}


def test_recommended_models_agree_with_the_requirements():
    from pathlib import Path

    doc = json.loads(
        (
            Path(__file__).parents[2]
            / "tui"
            / "internal"
            / "lemonade"
            / "recommended_models.json"
        ).read_text(encoding="utf-8")
    )
    rows = {m["id"]: m for m in doc["models"]}
    for model_id in lc.NPU_MODEL_LADDER:
        assert rows[model_id]["device"] == "npu"
        assert rows[model_id]["size_gb"] == lc.find_model_requirement(model_id).size_gb
        assert (
            rows[model_id]["min_ctx_size"]
            == lc.find_model_requirement(model_id).min_ctx_size
        )
