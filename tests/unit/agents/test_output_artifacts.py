# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Exact omitted evidence is available in bounded pages within its session."""

import json
from types import SimpleNamespace

import pytest

from gaia.agents.base.artifacts import (
    ArtifactStore,
    read_text_page,
    retain_excerpt,
    store_for,
)
from tests.unit.agents.test_large_tool_result_truncation import make_agent


def test_exact_middle_recovered_through_registered_continuation():
    agent = make_agent()
    original = "HEAD" + "λ" * 35000 + "MIDDLE-9c2f" + "x" * 35000 + "TAIL"
    result = agent._handle_large_tool_result("read_file", original, [])
    assert "MIDDLE-9c2f" not in result["head"] + result["tail"]
    reader = agent._tools_registry["read_tool_output"]["function"]
    recovered = []
    offset = 0
    while offset is not None:
        page = reader(result["artifact"], offset, 8000)
        assert len(page["content"]) <= 8000
        recovered.append(page["content"])
        offset = page["next_offset"]
    assert "".join(recovered) == original
    with pytest.raises(ValueError, match="Unknown output"):
        store_for(make_agent()).read(result["artifact"])


def test_quota_expiry_and_invalid_paging_fail_loudly(monkeypatch):
    from gaia.agents.base import artifacts

    clock = [1]
    monkeypatch.setattr(artifacts.time, "monotonic", lambda: clock[0])
    store = ArtifactStore(max_bytes=8, ttl=10)
    handle = store.put("λ" * 4)
    with pytest.raises(ValueError, match="full"):
        store.put("x")
    for offset, limit in [(-1, 1), (0, 8001), (0, 0), (True, 1), (5, 1)]:
        with pytest.raises(ValueError):
            store.read(handle, offset, limit)
    clock[0] = 11
    with pytest.raises(ValueError, match="Expired"):
        store.read(handle)
    store.put("ok")


def test_tool_local_excerpt_retains_exact_unicode_and_tail():
    owner = SimpleNamespace()
    text = "a" * 12000 + "MIDλ" + "b" * 12000
    result = json.loads(retain_excerpt(owner, text, 2000))
    assert len(json.dumps(result, ensure_ascii=False)) <= 2000
    assert store_for(owner).read(result["artifact"], 12000, 4)["content"] == "MIDλ"
    assert result["omitted_chars"] == len(text) - len(result["head"]) - len(
        result["tail"]
    )
    assert retain_excerpt(owner, "small", 2000) == "small"


def test_text_pages_recover_a_large_single_line_and_unicode(tmp_path):
    path = tmp_path / "large.txt"
    text = "λ" * 20000 + "exact-middle" + "z" * 20000
    path.write_text(text, encoding="utf-8")
    offset, pages = 0, []
    while offset is not None:
        page = read_text_page(path, offset, 8000)
        pages.append(page["content"])
        offset = page["next_offset"]
    assert "".join(pages) == text


def test_reader_remains_instance_owned_after_another_agent_and_refresh():
    first = make_agent()
    first_handle = store_for(first).put("first private evidence")
    second = make_agent()
    second_handle = store_for(second).put("second private evidence")
    first._snapshot_tools()
    reader = first._tools_registry["read_tool_output"]["function"]
    assert reader(first_handle)["content"] == "first private evidence"
    with pytest.raises(ValueError, match="Unknown output"):
        reader(second_handle)


def test_registering_reader_preserves_system_prompt_and_tool_instructions():
    agent = make_agent()
    assert isinstance(agent.system_prompt, str) and agent.system_prompt
    assert "read_tool_output" in agent.system_prompt
    agent._register_output_reader()
    assert isinstance(agent.system_prompt, str) and agent.system_prompt
    assert "read_tool_output" in agent.system_prompt


def test_real_shell_output_is_archived_before_its_local_cap(tmp_path):
    from gaia.agents.base.tools import get_tool_metadata
    from gaia.agents.tools.shell_tools import ShellToolsMixin

    class Host(ShellToolsMixin):
        pass

    host = Host()
    host.register_shell_tools()
    original = "prefix\n" + "x" * 15000 + "EXACT-MIDDLE" + "y" * 15000 + "\ntail"
    path = tmp_path / "long.txt"
    path.write_text(original, encoding="utf-8")
    result = get_tool_metadata("run_shell_command")["function"](
        command='cat "long.txt"', working_directory=str(tmp_path)
    )
    assert result["return_code"] == 0
    assert result["output_truncated"] is True
    excerpt = json.loads(result["stdout"])
    page = store_for(host).read(excerpt["artifact"], original.index("EXACT-MIDDLE"), 12)
    assert page["content"] == "EXACT-MIDDLE"


@pytest.mark.parametrize(
    "payload,expected",
    [
        ("[]", []),
        ('["a"]', ["a"]),
        ('[{"artifact":"original"}]', [{"artifact": "original"}]),
    ],
)
def test_whitespace_json_list_keeps_original_records_and_recovers_bytes(
    payload, expected
):
    agent = make_agent()
    raw = "[" + " " * 100000 + payload[1:]
    excerpt = json.loads(agent._handle_large_tool_result("read_file", raw, []))
    assert excerpt[: len(expected)] == expected
    assert store_for(agent).read(excerpt[-1]["artifact"], 0, 2)["content"] == "[ "
