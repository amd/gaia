# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""`gaia init` downloads what the user chose, from every starting state (#6226).

Two users found `gaia init` pulling models they did not want next to the ones
their Lemonade already had; one runs only FLM (NPU) models. These tests start
from the states users are really in: an empty Lemonade, one that already has
everything, an FLM-only one, and a saved skip. Lemonade is faked at the client
boundary, and what each test asserts is the pull request that would reach it,
not just that a pull happened.
"""

import json
from unittest.mock import patch

import pytest

import gaia.config
from gaia.installer import model_choice
from gaia.installer.init_command import InitCommand
from gaia.llm import lemonade_client
from gaia.llm.gpu_memory import GB_PER_GIB
from gaia.llm.lemonade_client import (
    DEFAULT_EMBEDDING_CHECKPOINT,
    DEFAULT_EMBEDDING_MODEL,
    DEFAULT_MODEL_NAME,
    LARGE_DEFAULT_MODEL_NAME,
    NPU_DEFAULT_MODEL_NAME,
    LemonadeClient,
)
from gaia.ui import setup_plan as sp

GB = 1_000_000_000
FLM = "gemma4-it-e2b-FLM"
EMBEDDER = DEFAULT_EMBEDDING_MODEL.removeprefix("user.")
# Not this PC, so no probe reads this machine's real free memory.
URL = "http://lemonade.test:13305/api/v1"


class FakeLemonade(LemonadeClient):
    """A Lemonade Server whose catalog and disk the test chooses.

    ``pulls`` records each ``/pull`` the real ``ensure_model_downloaded``
    sends: the model name and the registration fields beside it.
    """

    def __init__(
        self, store, downloaded=(), vram_gb=12, free_gb=500.0, extra=(), npu=False
    ):
        super().__init__(base_url=URL, verbose=False)
        self.store, self.vram_gb, self.free_gb = str(store), vram_gb, free_gb
        self.npu = npu
        self.entries = {
            m: {
                "id": m,
                "size": size,
                "downloaded": False,
                "labels": labels,
                "recipe": recipe,
                # Lets init look for what an earlier, interrupted pull left.
                "checkpoint": f"org/{m}:Q4",
            }
            for m, size, labels, recipe in (
                (LARGE_DEFAULT_MODEL_NAME, 23.3, [], "llamacpp"),
                (DEFAULT_MODEL_NAME, 5.97, ["vision"], "llamacpp"),
                (EMBEDDER, 0.33, ["embeddings"], "llamacpp"),
                (FLM, 3.4, ["chat"], "flm"),
                *extra,
            )
        }
        for m in downloaded:
            self.entries[m]["downloaded"] = True
        self.pulls = []
        self.deleted = []

    def health_check(self):
        return {"status": "ok", "version": "11.8.0"}

    def get_system_info(self, timeout=None):
        devices = {"amd_igpu": {"available": True, "vram_gb": self.vram_gb}}
        info = {
            "devices": devices,
            "model_storage": {"free_bytes": self.free_gb * GB, "path": self.store},
        }
        if self.npu:
            devices["amd_npu"] = {"available": True, "family": "XDNA2"}
            info["Physical Memory"] = "64 GB"
        return info

    def list_models(self, show_all=False):
        return {"data": [dict(e) for e in self.entries.values()]}

    def check_model_available(self, model_id):
        entry = self._entry(model_id)
        return bool(entry and entry["downloaded"])

    def pull_model_stream(self, model_name, **fields):
        self.pulls.append(
            (model_name, {k: v for k, v in fields.items() if v is not None})
        )
        entry = self._entry(model_name)
        size = int(entry["size"] * GB) if entry else GB
        yield {
            "event": "progress",
            "file": "model.gguf",
            "bytes_downloaded": size,
            "bytes_total": size,
            "percent": 100,
        }
        if entry:
            entry["downloaded"] = True
        yield {"event": "complete", "percent": 100}

    def delete_model(self, model_name, timeout=None):
        self.deleted.append(model_name)
        self._entry(model_name)["downloaded"] = False
        return {"status": "success"}

    def _entry(self, model_id):
        return next(
            (
                e
                for e in self.entries.values()
                if model_choice.ids_match(e["id"], model_id)
            ),
            None,
        )


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    """A fresh user: temp HOME and ~/.gaia, an empty config, no saved server."""
    for var in ("HOME", "USERPROFILE"):
        monkeypatch.setenv(var, str(tmp_path / "home"))
    monkeypatch.setenv("GAIA_HOME", str(tmp_path / "gaia"))
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path / "hf"))
    monkeypatch.delenv("LEMONADE_BASE_URL", raising=False)
    config = tmp_path / "gaia" / "config.json"
    config.parent.mkdir()
    monkeypatch.setattr(gaia.config, "GAIA_CONFIG_FILE", config)
    return config


@pytest.fixture
def lemonade(monkeypatch, tmp_path):
    """Point init and the setup plan at a :class:`FakeLemonade`."""
    store = tmp_path / "hub"
    store.mkdir()

    def use(**kwargs):
        fake = FakeLemonade(store, **kwargs)
        monkeypatch.setattr(lemonade_client, "LemonadeClient", lambda *a, **k: fake)
        monkeypatch.setattr(sp, "_client", lambda url: fake)
        monkeypatch.setattr(sp, "_lemonade", lambda: {"mode": "gaia", "url": URL})
        return fake

    return use


def _download(cmd):
    return cmd._download_models()


def _pulled(fake):
    return [name for name, _ in fake.pulls]


def _config(home):
    return json.loads(home.read_text()) if home.exists() else {}


# ── starting states ────────────────────────────────────────────────────────


def test_cold_lemonade_pulls_the_plan_with_requests_lemonade_accepts(lemonade):
    fake = lemonade()
    assert _download(InitCommand(profile="gaia", yes=True))
    assert fake.pulls == [
        # A built-in: by name only. A recipe here 400s on a fresh pull (#1655).
        (DEFAULT_MODEL_NAME, {}),
        (
            DEFAULT_EMBEDDING_MODEL,
            {
                "checkpoint": DEFAULT_EMBEDDING_CHECKPOINT,
                "recipe": "llamacpp",
                "embedding": True,
            },
        ),
    ]


def test_warm_lemonade_pulls_nothing_and_asks_nothing(lemonade, monkeypatch):
    fake = lemonade(downloaded=[DEFAULT_MODEL_NAME, EMBEDDER])
    monkeypatch.setattr("builtins.input", lambda *a: pytest.fail("prompted"))
    assert _download(InitCommand(profile="gaia"))
    assert fake.pulls == []


def test_flm_only_user_picks_their_npu_model_and_gets_no_gguf_chat_model(
    lemonade, home, capsys
):
    fake = lemonade(downloaded=[FLM])
    sp.choose_chat_model(FLM)
    assert _config(home)["default_model"] == FLM

    assert _download(InitCommand(profile="gaia", yes=True))
    assert _pulled(fake) == [DEFAULT_EMBEDDING_MODEL]
    out = capsys.readouterr().out
    assert f"Not downloading {DEFAULT_MODEL_NAME}" in out


def test_flm_only_user_who_also_skips_the_embedder_downloads_nothing(lemonade, home):
    fake = lemonade(downloaded=[FLM])
    sp.choose_chat_model(FLM)
    sp.set_model_skipped(DEFAULT_EMBEDDING_MODEL, True)
    assert _download(InitCommand(profile="gaia", yes=True))
    assert fake.pulls == []


def test_skipping_gemma_beside_a_larger_chat_model_says_vision_stops(
    lemonade, home, capsys, monkeypatch
):
    from gaia.llm.model_fit import FreeMemory

    monkeypatch.setattr(
        lemonade_client, "_free_memory_snapshot", lambda url: (FreeMemory(90, 90), {})
    )
    fake = lemonade(vram_gb=96)
    home.write_text(json.dumps({"skipped_models": [DEFAULT_MODEL_NAME]}))

    cmd = InitCommand(profile="gaia", yes=True, progress_json=True)
    assert _download(cmd)
    assert _pulled(fake) == [DEFAULT_EMBEDDING_MODEL, LARGE_DEFAULT_MODEL_NAME]
    out = capsys.readouterr().out
    assert "can't read images or screenshots" in out
    plan = next(
        json.loads(line)
        for line in out.splitlines()
        if line.startswith('{"event": "plan"')
    )
    assert plan["skipped"] == [DEFAULT_MODEL_NAME]
    assert [m["id"] for m in plan["models"]] == [
        DEFAULT_EMBEDDING_MODEL,
        LARGE_DEFAULT_MODEL_NAME,
    ]


def test_declining_gemma_on_a_small_pc_leaves_no_chat_model_and_says_so(
    lemonade, home, capsys
):
    fake = lemonade()
    sp.set_model_skipped(DEFAULT_MODEL_NAME, True)
    cmd = InitCommand(profile="gaia", yes=True)
    # Same verdict as `gaia init --check`: no chat model is a failed setup.
    assert not _download(cmd)
    assert _pulled(fake) == [DEFAULT_EMBEDDING_MODEL]
    out = capsys.readouterr().out
    assert "no local chat model" in out and "images" in out
    assert f"No local chat model: {DEFAULT_MODEL_NAME} is skipped" in out
    # Nothing downloaded for chat, so nothing becomes everyone's default.
    config = gaia.config.GaiaConfig.load()
    cmd._record_chat_choice(config)
    assert config.default_model is None


def test_download_model_takes_a_skip_back(lemonade, home):
    fake = lemonade()
    sp.set_model_skipped(DEFAULT_MODEL_NAME, True)
    sp.set_model_skipped(DEFAULT_MODEL_NAME, False)
    assert _config(home)["skipped_models"] == []
    assert _download(InitCommand(profile="gaia", yes=True))
    assert DEFAULT_MODEL_NAME in _pulled(fake)


# ── non-interactive runs are unchanged ─────────────────────────────────────


def test_no_tty_without_yes_still_refuses(monkeypatch, capsys):
    monkeypatch.setattr("gaia.installer.init_command.stdin_is_tty", lambda: False)
    assert InitCommand(profile="gaia").run() == 1
    assert "--yes" in capsys.readouterr().err


def test_yes_never_reads_stdin(lemonade, monkeypatch):
    fake = lemonade()
    monkeypatch.setattr("builtins.input", lambda *a: pytest.fail("prompted"))
    assert _download(InitCommand(profile="gaia", yes=True))
    assert _pulled(fake) == [DEFAULT_MODEL_NAME, DEFAULT_EMBEDDING_MODEL]


def test_too_little_disk_under_yes_still_stops(lemonade):
    fake = lemonade(free_gb=1.0)
    assert not _download(InitCommand(profile="gaia", yes=True))
    assert fake.pulls == []


def test_allow_low_disk_downloads_anyway(lemonade, capsys):
    fake = lemonade(free_gb=1.0)
    assert _download(InitCommand(profile="gaia", yes=True, allow_low_disk=True))
    assert _pulled(fake) == [DEFAULT_MODEL_NAME, DEFAULT_EMBEDDING_MODEL]
    assert "--allow-low-disk" in capsys.readouterr().out


# ── adversarial ────────────────────────────────────────────────────────────


def test_eof_at_the_prompt_downloads_nothing(lemonade, monkeypatch):
    fake = lemonade()

    def eof(*_a):
        raise EOFError

    monkeypatch.setattr("builtins.input", eof)
    assert _download(InitCommand(profile="gaia"))
    assert fake.pulls == []


@pytest.mark.parametrize("answer", ["maybe", "nope", "2", "\x1b[A"])
def test_an_unrecognised_answer_downloads_nothing(lemonade, monkeypatch, answer):
    fake = lemonade()
    monkeypatch.setattr("builtins.input", lambda *a: answer)
    assert _download(InitCommand(profile="gaia"))
    assert fake.pulls == []


@pytest.mark.parametrize(
    "model_id",
    [
        "made-up-model-GGUF",  # nowhere
        EMBEDDER,  # downloaded, but not a chat model
        "Qwen3-0.6B-GGUF",  # in Lemonade's catalog, never downloaded, unknown to GAIA
        "",
    ],
)
def test_a_chat_model_that_is_not_there_is_refused_and_nothing_is_saved(
    lemonade, home, model_id
):
    lemonade(
        downloaded=[EMBEDDER],
        extra=[("Qwen3-0.6B-GGUF", 0.4, [], "llamacpp")],
    )
    with pytest.raises(sp.SetupPlanError):
        sp.choose_chat_model(model_id)
    assert "default_model" not in _config(home)


def test_a_model_too_big_for_this_pc_needs_confirming(lemonade, home):
    lemonade(vram_gb=12)
    with pytest.raises(sp.NeedsAcknowledgement) as ask:
        sp.choose_chat_model(LARGE_DEFAULT_MODEL_NAME)
    assert ask.value.detail["code"] == "no_fit"
    assert ask.value.detail["capacity_gb"] == round(12 * GB_PER_GIB, 1)
    assert ask.value.detail["need_gb"] > 23
    assert "default_model" not in _config(home)
    sp.choose_chat_model(LARGE_DEFAULT_MODEL_NAME, acknowledge_no_fit=True)
    assert _config(home)["default_model"] == LARGE_DEFAULT_MODEL_NAME


def test_skipping_a_model_setup_does_not_download_is_refused(lemonade, home):
    lemonade()
    with pytest.raises(sp.SetupPlanError, match="nothing to skip"):
        sp.set_model_skipped("SDXL-Turbo", True)
    assert "skipped_models" not in _config(home)


def test_a_hand_broken_skip_list_stops_init_before_any_download(lemonade, home):
    fake = lemonade()
    home.write_text(json.dumps({"skipped_models": DEFAULT_MODEL_NAME}))
    with pytest.raises(gaia.config.GaiaConfigError, match="skipped_models"):
        InitCommand(profile="gaia", yes=True)
    assert fake.pulls == []


def test_check_does_not_count_a_skipped_model_as_missing(lemonade, home, monkeypatch):
    """Gemma only does vision beside a user-chosen chat model; skipping it is fine."""
    from gaia.installer.init_command import check_setup_status

    lemonade(downloaded=[EMBEDDER, FLM])
    monkeypatch.setenv("LEMONADE_BASE_URL", URL)
    home.write_text(json.dumps({"default_model": FLM}))
    assert check_setup_status("gaia").ready
    lemonade(downloaded=[EMBEDDER])
    home.write_text(json.dumps({"skipped_models": [DEFAULT_EMBEDDING_MODEL]}))
    assert not check_setup_status("gaia").ready


def test_check_says_there_is_no_chat_model_when_the_chat_model_is_skipped(
    lemonade, home, monkeypatch
):
    """Ready here would let the first chat make Lemonade pull the skipped model."""
    from gaia.installer.init_command import check_setup_status

    lemonade(downloaded=[EMBEDDER])
    monkeypatch.setenv("LEMONADE_BASE_URL", URL)
    home.write_text(json.dumps({"skipped_models": [DEFAULT_MODEL_NAME]}))
    status = check_setup_status("gaia")
    assert not status.ready
    assert status.reasons == [
        f"No local chat model: {DEFAULT_MODEL_NAME} is skipped. Pick another chat "
        "model (`gaia init --use-model`) or a cloud provider"
    ]
    # A cloud-backed session needs no local chat model.
    assert check_setup_status("gaia", skip_chat_model=True).ready


def test_verify_never_loads_a_skipped_chat_model(lemonade, home, monkeypatch):
    """Loading a model Lemonade lacks makes it pull the model, unasked."""
    from gaia.llm import lemonade_manager

    lemonade(downloaded=[EMBEDDER])
    home.write_text(json.dumps({"skipped_models": [DEFAULT_MODEL_NAME]}))
    monkeypatch.setattr(
        lemonade_manager.LemonadeManager,
        "ensure_ready",
        lambda *a, **k: pytest.fail("loaded the skipped chat model"),
    )
    cmd = InitCommand(profile="gaia", yes=True)
    with patch.object(cmd, "_test_model_inference", return_value=(True, None)) as tried:
        cmd._verify_setup()
    assert all(c.args[1] != DEFAULT_MODEL_NAME for c in tried.call_args_list)


def test_download_model_fetches_vision_for_a_user_whose_chat_model_replaced_it(
    lemonade, home
):
    """The caveat says `gaia init --download-model Gemma`; it must work."""
    fake = lemonade(downloaded=[FLM, EMBEDDER])
    sp.choose_chat_model(FLM)
    cmd = InitCommand(profile="gaia", yes=True, extra_models=[DEFAULT_MODEL_NAME])
    assert _download(cmd)
    assert fake.pulls == [(DEFAULT_MODEL_NAME, {})]


def test_every_model_skipped_fails_loudly_and_still_sends_the_plan(
    lemonade, home, capsys
):
    fake = lemonade()
    sp.set_model_skipped(DEFAULT_MODEL_NAME, True)
    sp.set_model_skipped(DEFAULT_EMBEDDING_MODEL, True)
    cmd = InitCommand(profile="gaia", yes=True, progress_json=True)
    assert not _download(cmd)
    assert fake.pulls == []
    out = capsys.readouterr().out
    events = [json.loads(line) for line in out.splitlines() if line.startswith("{")]
    plan = next(e for e in events if e["event"] == "plan")
    assert plan["models"] == [] and set(plan["skipped"]) == {
        DEFAULT_MODEL_NAME,
        DEFAULT_EMBEDDING_MODEL,
    }
    error = next(e for e in events if e["event"] == "error")
    assert "No local chat model" in error["message"]
    assert "initialization complete" not in out.lower()


def test_npu_profile_can_skip_its_own_flm_model(lemonade, home):
    lemonade(npu=True, extra=[(NPU_DEFAULT_MODEL_NAME, 9.1, ["chat"], "flm")])
    with pytest.raises(sp.UnknownModel):
        # Not one of the gaia profile's models.
        sp.set_model_skipped(NPU_DEFAULT_MODEL_NAME, True)
    sp.set_model_skipped(NPU_DEFAULT_MODEL_NAME, True, profile="npu")
    assert _config(home)["skipped_models"] == [NPU_DEFAULT_MODEL_NAME]


def test_npu_plan_on_a_pc_without_an_npu_says_so(lemonade, home):
    lemonade()
    with pytest.raises(sp.SetupPlanError, match="no available AMD NPU"):
        sp.build_plan("npu")


def test_download_later_with_the_chat_model_skipped_succeeds_on_its_own_pull(
    lemonade, home, capsys
):
    """`--download-model X` is judged on X; readiness stays `--check`'s job."""
    fake = lemonade(downloaded=[EMBEDDER])
    sp.set_model_skipped(DEFAULT_MODEL_NAME, True)
    cmd = InitCommand(profile="gaia", yes=True, extra_models=[LARGE_DEFAULT_MODEL_NAME])
    assert _download(cmd)
    assert _pulled(fake) == [LARGE_DEFAULT_MODEL_NAME]
    assert "No local chat model" in capsys.readouterr().out


def test_download_later_still_fails_when_its_own_pull_fails(lemonade, home):
    fake = lemonade(downloaded=[EMBEDDER])
    sp.set_model_skipped(DEFAULT_MODEL_NAME, True)

    def broken(model_name, **_fields):
        yield {"event": "error", "error": "checkpoint not found"}

    fake.pull_model_stream = broken
    cmd = InitCommand(profile="gaia", yes=True, extra_models=[LARGE_DEFAULT_MODEL_NAME])
    assert not _download(cmd)
