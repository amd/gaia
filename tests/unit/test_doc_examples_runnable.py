# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Run the maintained quickstart example with a deterministic model response."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from gaia.agents.base import tools as tools_module
from gaia.agents.registry import _accepted_init_params

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

        agent_class = namespace["CountAgent"]
        kwargs = {"model_id": "test-model", "silent_mode": True, "session_id": "test"}
        accepted = _accepted_init_params(agent_class)
        filtered = (
            kwargs
            if accepted is None
            else {key: value for key, value in kwargs.items() if key in accepted}
        )
        registered_agent = agent_class(**filtered)
        assert registered_agent.model_id == "test-model"

    assert namespace["result"]["status"] == "success"
    assert namespace["result"]["result"].startswith("5")
    assert sdk.return_value.send_messages.call_count == 2


def test_custom_agent_tutorial_runs_and_checks_tools():
    """Execute both published scripts, including their entry points."""
    import sys
    from types import ModuleType

    doc_path = REPO_ROOT / "docs" / "sdk" / "core" / "custom-agents.mdx"
    spec = importlib.util.spec_from_file_location(
        "check_doc_code", REPO_ROOT / "util" / "check_doc_code.py"
    )
    checker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(checker)
    blocks = [
        block.source
        for block in checker.extract_code_blocks(doc_path, REPO_ROOT)
        if block.lang == "python"
    ]
    assert len(blocks) == 2, "Execute every Python example in the tutorial"

    with (
        patch.dict(tools_module._TOOL_REGISTRY, {}, clear=True),
        patch("gaia.llm.lemonade_manager.LemonadeManager.ensure_ready"),
        patch("gaia.agents.base.agent.AgentSDK") as sdk,
    ):
        sdk.return_value.send_messages.side_effect = [
            SimpleNamespace(
                text='{"tool": "get_stock", "tool_args": {"item": "notebooks"}}',
                stats=None,
            ),
            SimpleNamespace(text='{"answer": "12 notebooks"}', stats=None),
        ]
        namespace = {"__name__": "__main__"}
        exec(compile(blocks[0], str(doc_path), "exec"), namespace)
        assert namespace["result"]["status"] == "success"
        assert namespace["result"]["result"].startswith("12 notebooks")
        assert sdk.return_value.send_messages.call_count == 2

        module = ModuleType("catalog_agent")
        module.CatalogAgent = namespace["CatalogAgent"]
        with patch.dict(sys.modules, {"catalog_agent": module}):
            exec(compile(blocks[1], str(doc_path), "exec"), {})
        assert sdk.return_value.send_messages.call_count == 2

        configured = module.CatalogAgent(
            catalog={"pencils": 4},
            model_id="chosen-model",
            max_steps=7,
            silent_mode=True,
            skip_lemonade=True,
        )
        assert configured.model_id == "chosen-model"
        assert configured.max_steps == 7
        assert configured.get_tools_info()["list_items"]["function"]() == {
            "items": ["pencils"]
        }


def _python_examples(relative_path):
    path = REPO_ROOT / relative_path
    spec = importlib.util.spec_from_file_location(
        "check_doc_code", REPO_ROOT / "util" / "check_doc_code.py"
    )
    checker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(checker)
    return [
        b.source
        for b in checker.extract_code_blocks(path, REPO_ROOT)
        if b.lang == "python"
    ]


def test_published_pytest_examples_execute():
    import sys
    from types import ModuleType

    catalog = ModuleType("catalog_agent")
    exec(_python_examples("docs/sdk/core/custom-agents.mdx")[0], catalog.__dict__)
    with patch.dict(sys.modules, {"catalog_agent": catalog}):
        namespace = {}
        for source in _python_examples("docs/sdk/testing.mdx"):
            exec(source, namespace)
        namespace["test_stock_validation"]()
        namespace["test_catalog_query_uses_the_tool"]()


def test_composition_example_registers_code_index_tools():
    with patch.dict(tools_module._TOOL_REGISTRY, {}, clear=True):
        namespace = {"__name__": "__main__"}
        examples = _python_examples("docs/sdk/mixins/tool-mixins.mdx")
        assert len(examples) == 1
        exec(examples[0], namespace)
        agent = namespace["agent"]
        assert set(agent.get_tools_info()) == {
            "index_codebase",
            "search_code_index",
            "get_index_status",
            "clear_code_index",
        }


def test_mixin_table_matches_registry():
    from gaia.agents.registry import KNOWN_TOOLS

    document = (REPO_ROOT / "docs/sdk/mixins/tool-mixins.mdx").read_text()
    for name, (module, class_name) in KNOWN_TOOLS.items():
        source_path = module.replace(".", "/")
        assert (
            f"[`{name}`](https://github.com/amd/gaia/blob/main/src/{source_path}.py)"
            in document
        )
        assert f"(`{class_name}`)" in document


def test_memory_subclasses_accept_base_options_without_live_memory(monkeypatch):
    """Check constructor/MRO contracts, not live embedding or persistence."""
    import ast

    from gaia.agents.base.agent import Agent
    from gaia.agents.base.memory import MemoryMixin

    monkeypatch.setenv("GAIA_MEMORY_DISABLED", "1")
    namespace = {"Agent": Agent, "MemoryMixin": MemoryMixin, "Path": Path}
    checked = []
    for source in _python_examples("docs/sdk/sdks/memory.mdx"):
        if "class " not in source:
            continue  # API signature fences are references, not subclasses.
        for node in ast.parse(source).body:
            if not isinstance(node, ast.ClassDef):
                continue
            if not any(isinstance(b, ast.Name) and b.id == "Agent" for b in node.bases):
                continue
            exec(
                compile(ast.Module(body=[node], type_ignores=[]), "memory.mdx", "exec"),
                namespace,
            )
            with patch.dict(tools_module._TOOL_REGISTRY, {}, clear=True):
                agent = namespace[node.name](skip_lemonade=True, silent_mode=True)
                assert agent.memory_store is None
            checked.append(node.name)
    assert checked == ["MyAgent", "RememberBot", "WorkPersonalAgent", "TestAgent"]
