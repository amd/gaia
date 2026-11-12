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
        assert known == requirement.tool_calling, (
            f"MODELS lists {requirement.model_id} with two tool_calling values"
        )
    return list(seen.items())


def test_the_cpp_table_lists_every_python_model_with_the_same_flag():
    cpp = _cpp_table()
    assert cpp, f"no rows parsed from {REGISTRY}"
    assert dict(cpp) == dict(_python_table()), (
        "cpp/src/model_registry.cpp and MODELS in src/gaia/llm/lemonade_client.py "
        "differ; mirror the change in the same commit"
    )


def test_the_cpp_table_has_no_duplicate_rows():
    ids = [model_id for model_id, _ in _cpp_table()]
    assert len(ids) == len(set(ids))


def test_gaias_default_models_take_native_tool_calls():
    from gaia.llm.lemonade_client import DEFAULT_MODEL_NAME, LARGE_DEFAULT_MODEL_NAME

    table = dict(_cpp_table())
    assert table[DEFAULT_MODEL_NAME] is True
    assert table[LARGE_DEFAULT_MODEL_NAME] is True
