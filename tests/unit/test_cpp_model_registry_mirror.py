# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The C++ model table mirrors Python's MODELS.

gaiacpp sends a model native tool calls only when its table says the model
takes them. The table once lagged MODELS by seven entries, GAIA's default large
model among them, so those models silently got the weaker text protocol. This
fails as soon as the two differ.
"""

import re
from pathlib import Path

from gaia.llm.lemonade_client import MODELS

REPO_ROOT = Path(__file__).resolve().parents[2]
REGISTRY = REPO_ROOT / "cpp" / "src" / "model_registry.cpp"
ROW = re.compile(r'\{\s*"(?P<id>[^"]+)"\s*,\s*(?P<flag>true|false)\s*\}')


def _cpp_table() -> list[tuple[str, bool]]:
    text = REGISTRY.read_text(encoding="utf-8")
    body = text[text.index("kModels = {") : text.index("return kModels;")]
    return [(m["id"], m["flag"] == "true") for m in ROW.finditer(body)]


def _python_table() -> list[tuple[str, bool]]:
    seen: dict[str, bool] = {}
    for requirement in MODELS.values():
        known = seen.setdefault(requirement.model_id, requirement.tool_calling)
        assert (
            known == requirement.tool_calling
        ), f"MODELS lists {requirement.model_id} with two tool_calling values"
    return list(seen.items())


def test_the_cpp_table_lists_every_python_model_with_the_same_flag():
    cpp = _cpp_table()
    assert cpp, f"no rows parsed from {REGISTRY}"
    assert dict(cpp) == dict(_python_table()), (
        "cpp/src/model_registry.cpp and MODELS in src/gaia/llm/lemonade_client.py "
        "differ; mirror the change in the same commit"
    )


def _cpp_thinking() -> dict[str, bool]:
    text = REGISTRY.read_text(encoding="utf-8")
    body = text[
        text.index("kThinking = {") : text.index("for (const auto& [id, thinking]")
    ]
    return {m["id"]: m["flag"] == "true" for m in ROW.finditer(body)}


def test_the_cpp_thinking_table_matches_python():
    python = {r.model_id: r.thinking for r in MODELS.values() if r.thinking is not None}
    assert python, "MODELS sets no thinking switch; the C++ table has nothing to mirror"
    assert _cpp_thinking() == python, (
        "kThinking in cpp/src/model_registry.cpp and MODELS' thinking flags "
        "differ; mirror the change in the same commit"
    )


THINKING_PROBES = REPO_ROOT / "cpp" / "tests" / "fixtures" / "thinking_probes.json"


def test_python_sends_the_thinking_choice_the_shared_probes_expect(monkeypatch):
    # The C++ ModelRegistryTest reads the same file, so both sides match ids alike.
    import json

    from gaia.llm import custom_models
    from gaia.llm.lemonade_client import requested_thinking

    monkeypatch.setattr(custom_models, "list_custom_models", lambda: [])
    probes = json.loads(THINKING_PROBES.read_text(encoding="utf-8"))["probes"]
    assert probes
    got = {model_id: requested_thinking(model_id) for model_id in probes}
    assert got == probes


def test_the_shared_probes_cover_every_python_thinking_model():
    import json

    probes = json.loads(THINKING_PROBES.read_text(encoding="utf-8"))["probes"]
    for r in MODELS.values():
        if r.thinking is not None:
            assert probes.get(r.model_id) is r.thinking, r.model_id


def test_the_cpp_table_has_no_duplicate_rows():
    ids = [model_id for model_id, _ in _cpp_table()]
    assert len(ids) == len(set(ids))


def test_gaias_default_models_take_native_tool_calls():
    from gaia.llm.lemonade_client import DEFAULT_MODEL_LADDER

    table = dict(_cpp_table())
    for model_id in DEFAULT_MODEL_LADDER:
        assert table[model_id] is True, model_id


SAMPLING_ROW = re.compile(
    r'\{"(?P<id>[^"]+)",\s*(?P<default>true|false),\s*(?P<thinking>nullptr|R"\((?:.*?)\)"),\s*'
    r'(?P<non>nullptr|R"\((?:.*?)\)")\}'
)


def _cpp_sampling() -> dict:
    import json

    text = REGISTRY.read_text(encoding="utf-8")
    body = text[text.index("kSampling = {") : text.index("for (const auto& row : kSampling)")]

    def profile(literal: str):
        return None if literal == "nullptr" else json.loads(literal[3:-2])

    return {
        m["id"]: (m["default"] == "true", profile(m["thinking"]), profile(m["non"]))
        for m in SAMPLING_ROW.finditer(body)
    }


def test_the_cpp_sampling_table_matches_python():
    # gaiacpp sent temperature 0.2 alone, and Qwen3.6 reasoned until the reply limit.
    from gaia.llm.lemonade_client import MODEL_SAMPLING_PROFILES

    python = {
        model_id: (card.thinks_by_default, card.thinking, card.non_thinking)
        for model_id, card in MODEL_SAMPLING_PROFILES.items()
    }
    assert python, "MODEL_SAMPLING_PROFILES is empty; the C++ table has nothing to mirror"
    assert _cpp_sampling() == python, (
        "kSampling in cpp/src/model_registry.cpp and MODEL_SAMPLING_PROFILES "
        "differ; mirror the change in the same commit"
    )
