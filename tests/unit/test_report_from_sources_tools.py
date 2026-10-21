# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The report-from-sources skill's tools.py: manifest contract, registration, path rules.

The tools run through an agent, as they do for a user, so the agent's allowed
folders apply: a path outside them is refused, a folder approved this session
works.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from unittest.mock import patch

import pytest

pytest.importorskip("openpyxl")

from gaia.agents.base.agent import Agent  # noqa: E402
from gaia.agents.base.tools import _TOOL_REGISTRY  # noqa: E402
from gaia.security import PathValidator  # noqa: E402
from gaia.skills.audit import audit_skill  # noqa: E402
from gaia.skills.format import parse_skill_file  # noqa: E402
from gaia.skills.loader import (  # noqa: E402
    register_skill_tools,
    unregister_skill_tools,
)
from gaia.skills.manager import SkillManager  # noqa: E402

SKILL_DIR = (
    Path(__file__).resolve().parents[2] / "hub" / "skills" / "report-from-sources"
)
TOOLS = ("inventory_data_files", "series_from_workbook", "chart_into_workbook")


class _HostAgent(Agent):
    def _get_system_prompt(self) -> str:
        return "test"

    def _register_tools(self) -> None:
        pass

    def _create_console(self):
        from gaia.agents.base.console import AgentConsole

        return AgentConsole()


@pytest.fixture()
def data(tmp_path):
    """The folder the agent may use."""
    folder = tmp_path / "data"
    folder.mkdir()
    return folder


@pytest.fixture()
def agent(tmp_path, data, monkeypatch):
    """An agent allowed into ``data`` only, with the skill loaded as bundled."""
    monkeypatch.setenv("GAIA_HOME", str(tmp_path / "gaia-home"))
    root = tmp_path / "bundled"
    shutil.copytree(SKILL_DIR, root / SKILL_DIR.name)
    with patch("gaia.agents.base.agent.AgentSDK"):
        built = _HostAgent(silent_mode=True, skip_lemonade=True)
    validator = PathValidator(allowed_paths=[str(data)])
    validator.set_access_prompt(lambda path: False)  # the user says no
    built.path_validator = validator
    built._instance_tools = {}
    manager = SkillManager(
        agent_skill_dirs=[root],
        user_skills_root=tmp_path / "no-user-skills",
        include_claude_roots=False,
    )
    built.load_skill(SKILL_DIR.name, manager=manager)
    yield built
    for name in list(built.loaded_skills):
        built.unload_skill(name)


@pytest.fixture()
def tools(agent):
    """Each tool as the agent calls it, by its bare name."""

    def call(name):
        return lambda *args, **kwargs: agent._execute_tool(
            f"{SKILL_DIR.name}/{name}", _named(name, args, kwargs)
        )

    return {name: call(name) for name in TOOLS}


def _named(name, args, kwargs):
    import inspect

    from gaia.agents.base.tools import _TOOL_REGISTRY as registry

    fn = registry[f"{SKILL_DIR.name}/{name}"]["function"]
    return dict(inspect.signature(fn).bind_partial(*args, **kwargs).arguments)


def test_manifest_matches_tools_py_and_registers_namespaced():
    skill = parse_skill_file(SKILL_DIR)
    assert tuple(skill.tool_names) == TOOLS
    before = dict(_TOOL_REGISTRY)
    try:
        registered = register_skill_tools(skill)
        assert sorted(registered) == sorted(f"report-from-sources/{t}" for t in TOOLS)
        for t in TOOLS:
            assert t not in _TOOL_REGISTRY, "skill tools must only appear namespaced"
    finally:
        unregister_skill_tools(skill.name)
        _TOOL_REGISTRY.clear()
        _TOOL_REGISTRY.update(before)


def test_skill_body_leads_with_the_tools():
    skill = parse_skill_file(SKILL_DIR)
    body = skill.body
    first_section = body.split("## 2.")[0]
    assert "Call `inventory_data_files(folder)` first" in first_section
    assert "series_from_workbook" in body and "chart_into_workbook" in body
    assert "Fallback when the tool can't parse" in first_section


def _book(path: Path) -> Path:
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.append(["Item", "2025Q4", "2026Q1", "2026Q2"])
    ws.append(["Revenue", 1.5, 2.5, 3.5])
    wb.save(path)
    return path


def test_relative_paths_are_refused(tools):
    out = tools["series_from_workbook"]("data.xlsx", ["Revenue"])
    assert out["status"] == "error"


def test_paths_outside_the_allowed_folders_are_refused(tools, data, tmp_path):
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    src = _book(outside / "book.xlsx")
    out = tools["series_from_workbook"](str(src), ["Revenue"])
    assert out["status"] == "error" and "not in allowed paths" in out["error"]
    out = tools["inventory_data_files"](str(outside))
    assert out["status"] == "error" and "not in allowed paths" in out["error"]
    ok = _book(data / "book.xlsx")
    assert tools["series_from_workbook"](str(ok), ["Revenue"])["status"] == "success"
    refused = tools["chart_into_workbook"](
        str(outside / "c.xlsx"), str(ok), ["Revenue"]
    )
    assert refused["status"] == "error" and not (outside / "c.xlsx").exists()


def test_a_folder_approved_this_session_works(tools, agent, tmp_path):
    approved = tmp_path / "approved"
    approved.mkdir()
    src = _book(approved / "book.xlsx")
    agent.path_validator.add_allowed_path(str(approved))

    assert tools["inventory_data_files"](str(approved))["status"] == "success"
    assert tools["series_from_workbook"](str(src), ["Revenue"])["status"] == "success"
    out = tools["chart_into_workbook"](str(approved / "c.xlsx"), str(src), ["Revenue"])
    assert out["status"] == "success" and (approved / "c.xlsx").is_file()


def test_the_audit_allows_the_skill():
    assert audit_skill(SKILL_DIR).verdict == "ALLOW"


def test_inventory_skips_secret_files(tools, data):
    _book(data / "book.xlsx")
    (data / "credentials.json").write_text('{"data": [{"k": 1}]}', encoding="utf-8")
    out = tools["inventory_data_files"](str(data))
    assert [f["file"] for f in out["data_files"]] == ["book.xlsx"]
    assert any(n["file"] == "credentials.json" for n in out["not_read"])


def test_chart_tool_never_writes_the_source(tools, data):
    src = _book(data / "book.xlsx")
    before = src.read_bytes()
    out = tools["chart_into_workbook"](str(src), str(src), ["Revenue"])
    assert out["status"] == "error" and src.read_bytes() == before
    out = tools["chart_into_workbook"](str(data / "chart.csv"), str(src), ["Revenue"])
    assert out["status"] == "error"


def test_chart_tool_round_trip_cites_the_source(tools, data):
    src = _book(data / "book.xlsx")
    out = tools["chart_into_workbook"](
        str(data / "chart.xlsx"),
        str(src),
        ["revenue"],
        periods="last 2",
        title="Revenue",
    )
    assert out["status"] == "success" and out["verified"]
    assert out["read_back"] == {"Revenue": [2.5, 3.5]}
    assert out["source"]["series"][0]["cell_range"] == "C2:D2"
    assert out["source"]["provenance"].startswith("Source: book.xlsx")


def test_lookup_errors_come_back_as_structured_results(tools, data):
    src = _book(data / "book.xlsx")
    out = tools["series_from_workbook"](str(src), ["Headcount"])
    assert out["status"] == "error" and out["problems"][0]["status"] == "not_found"
    out = tools["series_from_workbook"](str(src), ["Revenue"], periods="sometime")
    assert out["status"] == "error" and "last 5" in out["error"]
    out = tools["series_from_workbook"](str(data / "missing.xlsx"), ["Revenue"])
    assert "does not exist" in out["error"]
    # A string instead of a list still works: semicolons separate labels.
    out = tools["series_from_workbook"](str(src), "Revenue")
    assert out["status"] == "success"
