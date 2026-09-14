# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Run the maintained quickstart example with a deterministic model response."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from gaia.agents.base import tools as tools_module

REPO_ROOT = Path(__file__).resolve().parents[2]
DOC_PATH = REPO_ROOT / "docs" / "quickstart.mdx"


def test_quickstart_constructs_registers_and_runs():
    spec = importlib.util.spec_from_file_location(
        "check_doc_code", REPO_ROOT / "util" / "check_doc_code.py"
    )
    checker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(checker)
    examples = [
        block
        for block in checker.extract_code_blocks(DOC_PATH, REPO_ROOT)
        if "class CountAgent(Agent)" in block.source
    ]
    assert len(examples) == 1, "The quickstart must contain its runnable agent example"

    with (
        patch.dict(tools_module._TOOL_REGISTRY, {}, clear=True),
        patch("gaia.llm.lemonade_manager.LemonadeManager.ensure_ready"),
        patch("gaia.agents.base.agent.AgentSDK") as sdk,
    ):
        sdk.return_value.send_messages.side_effect = [
            SimpleNamespace(
                text='{"tool": "count_words", "tool_args": {"text": "GAIA runs on my computer"}}',
                stats=None,
            ),
            SimpleNamespace(text='{"answer": "5"}', stats=None),
        ]
        namespace = {}
        exec(  # pylint: disable=exec-used
            compile(examples[0].source, str(DOC_PATH), "exec"), namespace
        )

        count_words = tools_module._TOOL_REGISTRY["count_words"]["function"]
        assert count_words("one  two\nthree") == {"count": 3}
        assert count_words("") == {"count": 0}

    assert namespace["result"]["status"] == "success"
    assert namespace["result"]["result"].startswith("5")
    assert sdk.return_value.send_messages.call_count == 2
