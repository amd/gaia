# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""What a model is for, what stops working without it, the saved skip list,
the low-disk warning, and the opt-in retry of a dropped download (#6226)."""

import json

import pytest

import gaia.config
from gaia.config import GaiaConfig, GaiaConfigError
from gaia.installer import model_choice as mc
from gaia.llm.lemonade_client import DEFAULT_EMBEDDING_MODEL, DEFAULT_MODEL_NAME
from gaia.llm.model_fit import MachineCapacity

QWEN = "Qwen3.6-35B-A3B-GGUF"
FLM = "gemma4-it-e2b-FLM"


@pytest.fixture(autouse=True)
def config_file(tmp_path, monkeypatch):
    for var in ("HOME", "USERPROFILE"):
        monkeypatch.setenv(var, str(tmp_path / "home"))
    path = tmp_path / "gaia" / "config.json"
    path.parent.mkdir()
    monkeypatch.setattr(gaia.config, "GAIA_CONFIG_FILE", path)
    return path


# ── roles and caveats ──────────────────────────────────────────────────────


def test_gemma_is_vision_even_when_another_model_chats():
    assert mc.roles_for(DEFAULT_MODEL_NAME, QWEN) == ["vision"]
    assert mc.roles_for(DEFAULT_MODEL_NAME, DEFAULT_MODEL_NAME) == ["chat", "vision"]
    assert mc.roles_for(QWEN, QWEN) == ["chat"]
    assert mc.roles_for(DEFAULT_EMBEDDING_MODEL, QWEN) == ["embedding"]


def test_skipping_gemma_says_images_stop_working_and_how_to_get_it_back():
    caveat = mc.if_skipped(DEFAULT_MODEL_NAME, ["vision"])
    assert "can't read images" in caveat
    assert f"gaia init --download-model {DEFAULT_MODEL_NAME}" in caveat


def test_skipping_the_only_chat_model_says_there_is_none():
    caveat = mc.if_skipped(DEFAULT_MODEL_NAME, ["chat", "vision"])
    assert "no local chat model" in caveat and "images" in caveat


def test_skipping_the_embedder_names_documents_and_memory():
    assert "documents" in mc.if_skipped(DEFAULT_EMBEDDING_MODEL, ["embedding"])


@pytest.mark.parametrize(
    "entry, chat",
    [
        ({"id": FLM, "recipe": "flm", "labels": ["chat", "audio"]}, True),
        ({"id": "Qwen3-0.6B-GGUF", "recipe": "llamacpp", "labels": []}, True),
        ({"id": "embed-gemma-300m-FLM", "recipe": "flm", "labels": []}, False),
        ({"id": "nomic", "labels": ["embeddings"]}, False),
        ({"id": "Whisper-Base", "recipe": "whispercpp", "labels": []}, False),
        ({"id": "SDXL-Turbo", "recipe": "sd-cpp", "labels": ["image"]}, False),
    ],
)
def test_only_chat_capable_lemonade_models_can_be_the_chat_model(entry, chat):
    assert mc.is_chat_catalog_entry(entry) is chat


# ── the skip list ──────────────────────────────────────────────────────────


def test_skip_list_round_trips_and_keeps_other_keys(config_file):
    config_file.write_text(json.dumps({"default_model": FLM, "future_key": 1}))
    assert mc.set_skipped(DEFAULT_MODEL_NAME, True) == [DEFAULT_MODEL_NAME]
    # Skipping twice, or under the other namespace spelling, never duplicates.
    assert mc.set_skipped(DEFAULT_MODEL_NAME.lower(), True) == [
        DEFAULT_MODEL_NAME.lower()
    ]
    doc = json.loads(config_file.read_text())
    assert doc["default_model"] == FLM and doc["future_key"] == 1
    assert mc.set_skipped(DEFAULT_MODEL_NAME, False) == []


def test_user_namespace_matches_when_splitting():
    kept, dropped = mc.split_skipped(
        [DEFAULT_MODEL_NAME, DEFAULT_EMBEDDING_MODEL],
        [DEFAULT_EMBEDDING_MODEL.removeprefix("user.")],
    )
    assert kept == [DEFAULT_MODEL_NAME]
    assert dropped == [DEFAULT_EMBEDDING_MODEL]


@pytest.mark.parametrize("bad", ["Gemma", [1, 2], {"a": 1}, [None]])
def test_a_hand_edited_skip_list_of_the_wrong_type_fails_loudly(config_file, bad):
    config_file.write_text(json.dumps({"skipped_models": bad}))
    with pytest.raises(GaiaConfigError, match="skipped_models"):
        GaiaConfig.load()


def test_config_set_takes_a_comma_list(config_file):
    GaiaConfig.save_key("skipped_models", f" {DEFAULT_MODEL_NAME}, ,{QWEN},{QWEN}")
    assert GaiaConfig.load().skipped_models == [DEFAULT_MODEL_NAME, QWEN]
    GaiaConfig.save_key("skipped_models", "")
    assert GaiaConfig.load().skipped_models == []


# ── fit and disk ───────────────────────────────────────────────────────────


def test_fit_gives_the_numbers_behind_the_verdict():
    small = MachineCapacity(16, "AMD iGPU", disk_free_gb=1)
    fit = mc.memory_fit(QWEN, 23.3, small)
    assert fit["fits"] is False
    assert fit["need_gb"] > 23.3 and fit["capacity_gb"] == 16
    assert "16 GB" in fit["fit_reason"]


def test_fit_ignores_disk_a_downloaded_model_no_longer_needs():
    tiny_disk = MachineCapacity(96, "AMD iGPU", disk_free_gb=1)
    assert mc.memory_fit(QWEN, 23.3, tiny_disk)["fits"] is True


@pytest.mark.parametrize(
    "capacity, size", [(None, 23.3), (MachineCapacity(16, "x", None), None)]
)
def test_unknown_fit_is_none_never_a_guess(capacity, size):
    fit = mc.memory_fit(FLM, size, capacity)
    assert fit["fits"] is None and fit["need_gb"] is None


def test_enough_disk_is_no_warning():
    assert mc.disk_shortfall([(QWEN, 23.3)], {"free_gb": 100, "path": "D:\\"}) is None


def test_unknown_free_space_is_no_warning():
    assert mc.disk_shortfall([(QWEN, 23.3)], {"free_gb": None, "path": None}) is None


def test_short_disk_says_how_much_to_free_and_what_to_skip():
    warning = mc.disk_shortfall(
        [(QWEN, 23.3), (DEFAULT_MODEL_NAME, 6.0), (DEFAULT_EMBEDDING_MODEL, 0.0)],
        {"free_gb": 11.2, "path": "D:\\hub"},
    )
    assert warning["code"] == "low_disk"
    assert warning["free_up_gb"] == pytest.approx(18.1)
    assert warning["drop"] == [QWEN]
    assert warning["acknowledge"] == "acknowledge_low_disk"
    assert "D:\\hub" in warning["message"] and "skip " + QWEN in warning["message"]


def test_when_no_single_model_is_enough_it_says_skip_more():
    warning = mc.disk_shortfall(
        [(QWEN, 23.3), (DEFAULT_MODEL_NAME, 6.0)], {"free_gb": 1.0, "path": None}
    )
    assert warning["drop"] == []
    assert "skip more models" in warning["message"]


def test_store_reported_by_lemonade_wins(tmp_path):
    info = {"model_storage": {"free_bytes": 42e9, "path": "E:\\models"}}
    assert mc.model_store(info, local=True) == {
        "path": "E:\\models",
        "free_gb": 42.0,
        "source": "lemonade",
    }


def test_gaias_own_server_before_it_runs_reads_this_pcs_hf_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path / "not" / "yet" / "hub"))
    store = mc.model_store(None, local=True)
    assert store["source"] == "local"
    assert store["path"] == str(tmp_path / "not" / "yet" / "hub")
    assert store["free_gb"] is not None


def test_a_remote_server_that_does_not_say_is_unknown():
    assert mc.model_store({}, local=False) == {
        "path": None,
        "free_gb": None,
        "source": None,
    }


# ── pulls ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "message, kind",
    [
        ("No space left on device", "disk_full"),
        ("Insufficient disk space in Lemonade's model cache", "disk_full"),
        ("Connection reset by peer", "network"),
        ("Read timed out", "network"),
        ("checkpoint not found on Hugging Face", "lemonade"),
        (None, None),
    ],
)
def test_failures_are_told_apart(message, kind):
    assert mc.error_kind(message) == kind


class _FlakyClient:
    """Fails each pull with *errors* in turn, then succeeds."""

    def __init__(self, errors):
        self.errors = list(errors)
        self.calls = []

    def ensure_model_downloaded(self, model_id, on_progress=None, **kwargs):
        self.calls.append((model_id, kwargs))
        if self.errors:
            on_progress({"event": "error", "error": self.errors.pop(0)})
            return False
        return True


class _Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def test_without_keep_trying_a_dropped_connection_fails_at_once():
    client = _FlakyClient(["Connection reset by peer"])
    assert not mc.pull_model(client, QWEN, lambda e: None, sleep=pytest.fail)
    assert len(client.calls) == 1


def test_keep_trying_resumes_after_a_dropped_connection():
    client, clock, retries = _FlakyClient(["Connection reset"] * 3), _Clock(), []
    assert mc.pull_model(
        client,
        QWEN,
        lambda e: None,
        keep_trying_s=120,
        on_retry=retries.append,
        sleep=clock.sleep,
        clock=clock,
    )
    assert len(client.calls) == 4 and len(retries) == 3


def test_keep_trying_gives_up_after_two_minutes():
    client, clock = _FlakyClient(["Connection reset"] * 1000), _Clock()
    assert not mc.pull_model(
        client, QWEN, lambda e: None, keep_trying_s=120, sleep=clock.sleep, clock=clock
    )
    assert clock.now == pytest.approx(120)
    assert len(client.calls) == 1 + 120 // 5


def test_keep_trying_never_retries_a_full_disk():
    client = _FlakyClient(["No space left on device"])
    assert not mc.pull_model(
        client, QWEN, lambda e: None, keep_trying_s=120, sleep=pytest.fail
    )


def test_builtin_models_pull_by_name_only():
    """Lemonade 400s a recipe on a built-in (#1655); only user. models register."""
    assert set(mc.pull_kwargs_for(QWEN)) == {"timeout"}
    assert mc.pull_kwargs_for(FLM) == {}
    custom = mc.pull_kwargs_for(DEFAULT_EMBEDDING_MODEL)
    assert custom["recipe"] == "llamacpp" and custom["embedding"] is True
    assert custom["checkpoint"]
