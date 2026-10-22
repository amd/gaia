# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Local GGUF models added from a pasted Hugging Face link (gaia.llm.custom_models).

Hugging Face and GitHub are faked at ``_http_get``; the repo payload mirrors
the shape of ``/api/models/<repo>?blobs=true`` and the architecture source the
layout of llama.cpp's ``src/llama-arch.cpp``.
"""

import json

import pytest

from gaia import config as config_mod
from gaia.config import GaiaConfigError
from gaia.llm import custom_models as cm
from gaia.llm.lemonade_client import find_model_requirement

ARCH_SOURCE = """
static const std::map<llm_arch, const char *> LLM_ARCH_NAMES = {
    { LLM_ARCH_LLAMA,            "llama"            },
    { LLM_ARCH_QWEN3,            "qwen3"            },
    { LLM_ARCH_GEMMA4,           "gemma4"           },
    { LLM_ARCH_UNKNOWN,          "(unknown)"        },
};
"""

TEMPLATE = "{% if tools %}<tools>{% endif %}{{ m.reasoning_content }}<|assistant|>"

SYSTEM_INFO = {
    "Physical Memory": "32.00 GB",
    "model_storage": {"free_bytes": 500e9},
    "recipes": {
        "llamacpp": {
            "default_backend": "vulkan",
            "backends": {
                "vulkan": {"state": "installed", "version": "b10825"},
                "rocm": {"state": "installable", "version": "b10820"},
            },
        }
    },
}


def _repo(arch="qwen3", template=TEMPLATE, siblings=None, **extra):
    gb = 10**9
    return {
        "id": "unsloth/Tiny-GGUF",
        "gated": False,
        "disabled": False,
        "gguf": {
            "architecture": arch,
            "context_length": 40960,
            "chat_template": template,
        },
        "siblings": (
            siblings
            if siblings is not None
            else [
                {"rfilename": "README.md", "size": 100},
                {"rfilename": "Tiny-Q4_K_M.gguf", "size": 3 * gb},
                {"rfilename": "Tiny-Q8_0.gguf", "size": 6 * gb},
                {"rfilename": "Tiny-UD-Q2_K_XL.gguf", "size": 2 * gb},
                {"rfilename": "BF16/Tiny-BF16-00001-of-00002.gguf", "size": 20 * gb},
                {"rfilename": "BF16/Tiny-BF16-00002-of-00002.gguf", "size": 20 * gb},
                {"rfilename": "imatrix_unsloth.gguf_file", "size": gb},
                {"rfilename": "mmproj-F32.gguf", "size": gb // 2},
                {"rfilename": "mmproj-F16.gguf", "size": gb // 4},
            ]
        ),
        **extra,
    }


class _Resp:
    def __init__(self, status, body=None, text=""):
        self.status_code = status
        self._body = body
        self.text = text

    def json(self):
        return self._body


@pytest.fixture
def web(monkeypatch, tmp_path):
    """Hugging Face and GitHub, faked; records every URL fetched."""
    state = {"repo": _repo(), "repo_status": 200, "arch": ARCH_SOURCE, "urls": []}

    def get(url, headers=None):
        state["urls"].append(url)
        if url.startswith("https://huggingface.co/api/models/"):
            return _Resp(state["repo_status"], state["repo"])
        if url.startswith("https://raw.githubusercontent.com/ggml-org/llama.cpp/"):
            return _Resp(200, text=state["arch"])
        raise AssertionError(f"unexpected fetch {url}")

    monkeypatch.setattr(cm, "_http_get", get)
    monkeypatch.setattr(cm, "_arch_cache_path", lambda tag: tmp_path / f"{tag}.json")
    return state


@pytest.fixture
def config_file(monkeypatch, tmp_path):
    path = tmp_path / "config.json"
    monkeypatch.setattr(config_mod, "GAIA_CONFIG_FILE", path)
    return path


def _inspect(url="https://huggingface.co/unsloth/Tiny-GGUF", **kw):
    return cm.inspect_link(url, system_info=kw.pop("system_info", SYSTEM_INFO), **kw)


# ── Links ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "text, repo, variant",
    [
        (
            "https://huggingface.co/unsloth/flint-GGUF?_sm_nck=1&controls_id=7c0a",
            "unsloth/flint-GGUF",
            None,
        ),
        ("https://www.huggingface.co/unsloth/flint-GGUF/", "unsloth/flint-GGUF", None),
        ("huggingface.co/unsloth/flint-GGUF", "unsloth/flint-GGUF", None),
        ("https://hf.co/unsloth/flint-GGUF", "unsloth/flint-GGUF", None),
        (
            "https://huggingface.co/unsloth/flint-GGUF/tree/main",
            "unsloth/flint-GGUF",
            None,
        ),
        (
            "https://huggingface.co/unsloth/flint-GGUF/blob/main/flint-Q8_0.gguf",
            "unsloth/flint-GGUF",
            "flint-Q8_0.gguf",
        ),
        (
            "https://huggingface.co/unsloth/flint-GGUF/resolve/main/BF16/a-00001-of-00002.gguf?download=true",
            "unsloth/flint-GGUF",
            "BF16/a-00001-of-00002.gguf",
        ),
        ("unsloth/flint-GGUF", "unsloth/flint-GGUF", None),
        ("unsloth/flint-GGUF:UD-Q4_K_XL", "unsloth/flint-GGUF", "UD-Q4_K_XL"),
    ],
)
def test_parse_link_accepts_the_forms_people_paste(text, repo, variant):
    assert cm.parse_link(text) == cm.HFLink(repo, variant)


@pytest.mark.parametrize(
    "suffix",
    [
        "",
        "/",
        "?_sm_nck=1&controls_id=7c0ac82e-9d82-4c1b-b635-a9a736428b70",
        "/tree/main",
        "/tree/main/BF16",
        "/discussions",
        "/discussions/3",
        "/commits/main",
        "/blob/main/README.md",
        "/blob/main/README.md?code=true",
        "/settings",
        "#model-card",
        "/tree/main?_sm_nck=1#files",
    ],
)
def test_every_page_of_a_repo_names_that_repo(suffix):
    url = f"https://huggingface.co/unsloth/flint-GGUF{suffix}"
    assert cm.parse_link(url) == cm.HFLink("unsloth/flint-GGUF")


@pytest.mark.parametrize(
    "text, says",
    [
        ("", "Paste a Hugging Face link"),
        ("https://example.com/unsloth/flint-GGUF", "example.com"),
        ("https://huggingface.co/datasets/a/b", "not a model page"),
        ("https://huggingface.co/spaces/a/b", "not a model page"),
        ("https://huggingface.co/collections/unsloth/flint-123", "not a model page"),
        ("https://huggingface.co/models?search=flint", "not a model page"),
        ("https://huggingface.co/docs/hub/gguf", "not a model page"),
        ("https://huggingface.co/unsloth", "not a model page"),
        ("not a link at all", "not a Hugging Face link"),
    ],
)
def test_parse_link_refuses_with_the_reason(text, says):
    with pytest.raises(cm.CustomModelError, match=says):
        cm.parse_link(text)


@pytest.mark.parametrize(
    "path, quant",
    [
        ("flint-UD-Q4_K_XL.gguf", "UD-Q4_K_XL"),
        ("gemma-4-E4B-it-Q4_K_M.gguf", "Q4_K_M"),
        ("gpt-oss-20b-MXFP4.gguf", "MXFP4"),
        ("BF16/flint-BF16-00001-of-00003.gguf", "BF16"),
        ("Qwen3-0.6B-IQ4_XS.gguf", "IQ4_XS"),
        ("model.q8_0.gguf", "Q8_0"),
        ("weird-name.gguf", "weird-name"),
    ],
)
def test_quant_of(path, quant):
    assert cm.quant_of(path) == quant


def test_variants_group_shards_and_set_aside_projectors_and_imatrix():
    variants, projectors = cm.gguf_variants(_repo()["siblings"])
    assert [v.quant for v in variants] == ["UD-Q2_K_XL", "Q4_K_M", "Q8_0", "BF16"]
    bf16 = variants[-1]
    assert len(bf16.files) == 2 and bf16.size_gb == 40
    assert bf16.checkpoint_variant == "BF16"
    assert variants[1].checkpoint_variant == "Tiny-Q4_K_M.gguf"
    assert [p["file"] for p in projectors] == ["mmproj-F32.gguf", "mmproj-F16.gguf"]


# ── Checking a link ────────────────────────────────────────────────────────


def test_supported_model_passes_and_describes_its_registration(web, config_file):
    report = _inspect()
    assert report["ok"], report["error"]
    assert [c["id"] for c in report["checks"]] == [
        "link",
        "repo",
        "gguf",
        "chat",
        "architecture",
        "fit",
    ]
    assert report["model_id"] == "user.Tiny-Q4_K_M-GGUF"
    assert report["checkpoint"] == "unsloth/Tiny-GGUF:Tiny-Q4_K_M.gguf"
    assert report["recipe"] == "llamacpp"
    # F16 projector preferred, and counted in the size.
    assert report["mmproj"] == "mmproj-F16.gguf" and report["vision"] is True
    assert report["size_gb"] == 3.2
    assert report["reasoning"] is True and report["tool_calling"] is True
    assert report["llamacpp_version"] == "b10825"
    assert report["warnings"] == []


def test_architecture_missing_from_the_installed_llamacpp_is_refused(web, config_file):
    """The pasted flint repo: a real GGUF whose architecture b10825 predates."""
    web["repo"] = _repo(arch="flint")
    report = _inspect("https://huggingface.co/unsloth/flint-GGUF?_sm_nck=1")
    assert report["ok"] is False
    assert report["checks"][-1]["id"] == "architecture"
    assert "'flint'" in report["error"] and "llama.cpp b10825" in report["error"]
    assert "gaia init" in report["error"]
    # Checked against the release Lemonade runs, not llama.cpp master.
    assert any("/llama.cpp/b10825/" in u for u in web["urls"])


def test_architecture_is_checked_for_the_backend_gaia_prefers(web, config_file):
    _inspect(preferred_backend="rocm")
    assert any("/llama.cpp/b10820/" in u for u in web["urls"])


def test_architecture_list_is_cached_per_release(web, config_file):
    _inspect()
    _inspect()
    assert sum("githubusercontent" in u for u in web["urls"]) == 1


def test_no_llamacpp_version_is_an_error_not_a_pass(web, config_file):
    info = {**SYSTEM_INFO, "recipes": {}}
    report = _inspect(system_info=info)
    assert report["ok"] is False and "llama.cpp release" in report["error"]


@pytest.mark.parametrize(
    "version", ["../../etc", "b1/../x", "master", "b10825 ", "b10825\n"]
)
def test_a_version_that_is_not_a_release_tag_is_refused(web, config_file, version):
    info = json.loads(json.dumps(SYSTEM_INFO))
    info["recipes"]["llamacpp"]["backends"]["vulkan"]["version"] = version
    report = _inspect(system_info=info)
    assert report["ok"] is False and "release tag" in report["error"]
    assert not any("githubusercontent" in u for u in web["urls"])


def test_default_quant_falls_back_to_the_largest_that_fits(web, config_file):
    info = {**SYSTEM_INFO, "Physical Memory": "4.00 GB"}
    report = _inspect(system_info=info)
    assert report["ok"], report["error"]
    assert report["quant"] == "UD-Q2_K_XL"
    fits = {v["quant"]: v["fits"] for v in report["variants"]}
    assert fits == {"UD-Q2_K_XL": True, "Q4_K_M": False, "Q8_0": False, "BF16": False}


def test_nothing_fits_names_the_memory_needed(web, config_file):
    info = {**SYSTEM_INFO, "Physical Memory": "2.00 GB"}
    report = _inspect(system_info=info)
    assert report["ok"] is False and report["checks"][-1]["id"] == "fit"
    assert "smaller quantization" in report["error"]


def test_explicit_quant_and_unknown_quant(web, config_file):
    assert _inspect(variant="q8_0")["checkpoint"].endswith(":Tiny-Q8_0.gguf")
    assert _inspect(variant="BF16")["checkpoint"] == "unsloth/Tiny-GGUF:BF16"
    bad = _inspect(variant="Q3_K_S")
    assert bad["ok"] is False and "Q4_K_M" in bad["error"]


def test_file_link_picks_that_file(web, config_file):
    url = "https://huggingface.co/unsloth/Tiny-GGUF/blob/main/Tiny-Q8_0.gguf"
    assert _inspect(url)["quant"] == "Q8_0"


@pytest.mark.parametrize(
    "repo, status, check, says",
    [
        (_repo(gated="auto"), 200, "repo", "gated"),
        (_repo(), 404, "repo", "no public model"),
        (_repo(siblings=[{"rfilename": "model.safetensors"}]), 200, "gguf", "no GGUF"),
        (_repo(template=""), 200, "chat", "base model"),
        (_repo(arch=""), 200, "architecture", "doesn't report"),
    ],
)
def test_refusals_name_the_failed_check(web, config_file, repo, status, check, says):
    web["repo"], web["repo_status"] = repo, status
    report = _inspect()
    assert report["ok"] is False
    assert report["checks"][-1] == {
        "id": check,
        "label": report["checks"][-1]["label"],
        "ok": False,
        "detail": report["error"],
    }
    assert says in report["error"]


def test_no_tool_support_is_a_warning(web, config_file):
    web["repo"] = _repo(template="<|user|>{{ m.content }}")
    report = _inspect()
    assert report["ok"] and report["tool_calling"] is False
    assert "can't use tools" in report["warnings"][0]


def test_a_model_gaia_already_ships_is_refused(web, config_file):
    web["repo"] = _repo(
        siblings=[{"rfilename": "gpt-oss-20b-MXFP4.gguf", "size": 12 * 10**9}]
    )
    report = _inspect("ggml-org/gpt-oss-20b-GGUF")
    assert report["ok"] is False and "already offers" in report["error"]


# ── The saved list ─────────────────────────────────────────────────────────


def test_added_model_resolves_with_its_registration(web, config_file):
    entry = cm.add_custom_model(_inspect())
    assert entry["source_url"] == "https://huggingface.co/unsloth/Tiny-GGUF"
    # Lemonade lists a registered user model without its namespace.
    req = find_model_requirement("Tiny-Q4_K_M-GGUF")
    assert req is not None and req.model_id == "user.Tiny-Q4_K_M-GGUF"
    assert req.pull_kwargs() == {
        "checkpoint": "unsloth/Tiny-GGUF:Tiny-Q4_K_M.gguf",
        "recipe": "llamacpp",
        "embedding": None,
        "mmproj": "mmproj-F16.gguf",
        "vision": True,
        "reasoning": True,
    }
    # Its KV cache per token is unknown, so it loads at the 32K floor.
    assert req.min_ctx_size == 32768 and not req.scales_with_memory
    # Re-checking the same link now says it is already added.
    again = _inspect()
    assert again["ok"] is False and "already added" in again["error"]


def test_a_name_lemonade_uses_for_another_model_is_refused(web, config_file):
    catalog = [{"id": "Tiny-Q4_K_M-GGUF", "checkpoint": "someone/Tiny-GGUF:Q4_K_M"}]
    report = _inspect(catalog=catalog)
    assert report["ok"] is False and report["checks"][-1]["id"] == "new"
    assert (
        "Lemonade already has another model named Tiny-Q4_K_M-GGUF" in report["error"]
    )
    # The same registration (re-adding after forgetting) is not a clash.
    same = [
        {"id": "Tiny-Q4_K_M-GGUF", "checkpoint": "unsloth/Tiny-GGUF:Tiny-Q4_K_M.gguf"}
    ]
    assert _inspect(catalog=same)["ok"]


def test_another_owners_copy_under_the_same_name_is_refused(web, config_file):
    cm.add_custom_model(_inspect())
    report = _inspect("https://huggingface.co/lmstudio-community/Tiny-GGUF")
    assert report["ok"] is False
    assert (
        "Remove it first" in report["error"] and "unsloth/Tiny-GGUF" in report["error"]
    )


def test_listed_entry_needs_the_name_and_the_checkpoint():
    entry = {"id": "user.Tiny-Q4_K_M-GGUF", "checkpoint": "unsloth/Tiny-GGUF:a.gguf"}
    other = {"id": "Tiny-Q4_K_M-GGUF", "checkpoint": "someone/Tiny-GGUF:a.gguf"}
    mine = {"id": "Tiny-Q4_K_M-GGUF", "checkpoint": "unsloth/Tiny-GGUF:a.gguf"}
    assert cm.listed_entry([other], entry) is None
    assert cm.listed_entry([other, mine], entry) == "Tiny-Q4_K_M-GGUF"


def test_a_repo_answer_that_is_not_an_object_is_a_refusal(web, config_file):
    web["repo"] = ["not", "a", "model"]
    report = _inspect()
    assert report["ok"] is False and "not a model description" in report["error"]


def test_dot_segments_are_not_a_repo():
    for text in ("unsloth/..", "https://huggingface.co/unsloth/../x"):
        with pytest.raises(cm.CustomModelError):
            cm.parse_link(text)


def test_variants_carry_their_first_file_for_picking(web, config_file):
    report = _inspect(variant="BF16/Tiny-BF16-00001-of-00002.gguf")
    assert report["quant"] == "BF16" and report["file"].startswith("BF16/")
    assert {v["file"] for v in report["variants"]} >= {"Tiny-Q4_K_M.gguf"}


def test_add_keeps_other_config_keys(web, config_file):
    config_file.write_text(json.dumps({"default_device": "gpu", "x": 1}))
    cm.add_custom_model(_inspect())
    doc = json.loads(config_file.read_text())
    assert doc["x"] == 1 and len(doc[cm.CONFIG_KEY]) == 1


def test_add_refuses_a_report_that_failed(config_file):
    with pytest.raises(cm.CustomModelError, match="nope"):
        cm.add_custom_model({"ok": False, "error": "nope"})


def test_remove_deletes_the_download_and_forgets_the_choice(web, config_file):
    entry = cm.add_custom_model(_inspect())
    doc = json.loads(config_file.read_text())
    doc.update(default_model=entry["id"], last_model="Tiny-Q4_K_M-GGUF")
    doc["last_provider"] = "local"
    config_file.write_text(json.dumps(doc))
    deleted = []
    result = cm.remove_custom_model("Tiny-Q4_K_M-GGUF", deleted.append)
    assert [e["id"] for e in deleted] == ["user.Tiny-Q4_K_M-GGUF"]
    assert result["cleared"] == ["default_model", "last_model"]
    doc = json.loads(config_file.read_text())
    assert cm.CONFIG_KEY not in doc and "last_provider" not in doc
    assert find_model_requirement("user.Tiny-Q4_K_M-GGUF") is None


def test_remove_keeps_the_entry_when_the_server_delete_fails(web, config_file):
    cm.add_custom_model(_inspect())

    def refuse(_):
        raise RuntimeError("busy")

    with pytest.raises(RuntimeError):
        cm.remove_custom_model("user.Tiny-Q4_K_M-GGUF", refuse)
    assert cm.find_custom_model("user.Tiny-Q4_K_M-GGUF") is not None


def test_remove_refuses_a_model_the_user_did_not_add(config_file):
    with pytest.raises(cm.CustomModelError, match="not a model you added"):
        cm.remove_custom_model("Gemma-4-E4B-it-GGUF", lambda _: None)


def test_a_malformed_saved_list_fails_config_load(config_file):
    from gaia.config import GaiaConfig

    config_file.write_text(json.dumps({cm.CONFIG_KEY: "nope"}))
    with pytest.raises(GaiaConfigError, match="custom_models"):
        GaiaConfig.load()


def test_malformed_saved_list_fails_loudly(config_file):
    config_file.write_text(json.dumps({cm.CONFIG_KEY: [{"id": "no-namespace"}]}))
    with pytest.raises(GaiaConfigError, match="custom_models"):
        cm.list_custom_models()
