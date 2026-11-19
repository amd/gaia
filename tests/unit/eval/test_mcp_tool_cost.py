# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""MCP tool cost and recall harness (#1971).

Mock MCP servers go through GAIA's real MCP conversion and are measured on the
flagship skeleton with the loader off and on. The recall floor and jump-rate
ceiling below are the CI guard for routing MCP tools through the loader: that
work may raise the floor or lower the ceiling, never the reverse.
"""

import pytest

pytest.importorskip("gaia_agent")

from gaia.eval import tool_cost  # noqa: E402
from gaia.eval import tool_recall  # noqa: E402
from gaia.eval.mcp_fixture import (  # noqa: E402
    SERVER_KINDS,
    build_mock_servers,
    convert_mock_servers,
    lexical_embedder,
    recall_probes,
)
from gaia.eval.tool_recall import (  # noqa: E402
    MCPRecallReport,
    check_mcp_recall,
    measure_mcp_recall,
)

# Loader-on recall on 5 servers x 5 tools with the word-overlap embedder. Exact
# on purpose: the stand-in embedder is deterministic, and every hit today comes
# from moving the session to every tool, so floor and ceiling are equal. Any core
# tool's description edit can move a probe either way and fail this test; re-run
# `python -m gaia.eval.tool_cost --mcp` and re-pin both to the new measurement.
# MCP-routing work (WS1) may only raise the floor or lower the ceiling.
MCP_RECALL_FLOOR = 18 / 25
MCP_JUMP_RATE_CEILING = 18 / 25

# The same measurement on the two paths #1972 started gating: the `web` profile
# with its MCP-only loader, and the flagship with memory off. Same caveats and
# same way to re-pin. Each one sent every MCP tool before, so 100% then.
MCP_ONLY_RECALL = 19 / 25
MEMORY_OFF_RECALL = 18 / 25

# The issue's bar: on 3+ servers the loader-on first turn, menu included, costs
# at most 40% of what eager registration sent on the native path.
MIN_NATIVE_CUT = 0.60

# What joins the menu to the rest of the system prompt; it counts as menu cost.
MENU_SEPARATOR = "\n\n"


@pytest.fixture(scope="module")
def agents():
    return tool_cost.build_mcp_cost_agents()


@pytest.fixture(scope="module")
def largest():
    servers = build_mock_servers(5, 5)
    return servers, recall_probes(servers)


def test_mock_servers_cycle_kinds_and_number_repeats():
    servers = build_mock_servers(len(SERVER_KINDS) + 1, 2)
    names = [s.name for s in servers]
    assert names[: len(SERVER_KINDS)] == list(SERVER_KINDS)
    assert names[-1] == f"{names[0]}_2"
    assert servers[-1].kind == servers[0].kind
    assert all(len(s.list_tools()) == 2 for s in servers)


@pytest.mark.parametrize("n, k", [(0, 1), (1, 0), (1, 99)])
def test_mock_servers_refuse_bad_counts(n, k):
    with pytest.raises(ValueError):
        build_mock_servers(n, k)


def test_mock_tools_go_through_gaias_own_conversion(agents):
    off, _ = agents
    entries = convert_mock_servers(off, build_mock_servers(2, 3))
    assert len(entries) == 6
    entry = entries["mcp_repos_create_issue"]
    assert entry["_mcp_server"] == "repos"
    assert entry["description"].startswith("[MCP:repos] ")
    assert entry["parameters"]["repo"]["required"] is True
    assert entry["parameters"]["labels"]["items"] == {"type": "string"}


def test_probes_cover_each_kind_once_and_never_spell_out_the_tool():
    servers = build_mock_servers(len(SERVER_KINDS) + 2, 3)
    probes = recall_probes(servers)
    first_of_kind = servers[: len(SERVER_KINDS)]
    expected = {
        f"mcp_{s.prefix}_{spec.name}": spec for s in first_of_kind for spec in s.specs
    }
    assert [tool for _, tool in probes] == list(expected)
    for request, tool in probes:
        assert expected[tool].name not in request


def test_lexical_embedder_is_deterministic_and_normalized():
    import numpy as np

    embed = lexical_embedder()
    a, b = embed("Create an issue"), embed("create   AN issue")
    assert np.allclose(a, b)
    assert np.isclose(np.linalg.norm(a), 1.0)
    assert not embed("the of and").any()


def test_mcp_cost_grows_per_server_and_native_costs_more(agents):
    cost = tool_cost.measure_mcp_cost((1, 3, 5), (5,), agents=agents)
    rows = cost["rows"]
    natives = [r["off"]["native_chars"] for r in rows]
    assert natives == sorted(natives) and natives[0] > 0
    for row in rows:
        assert row["off"]["native_chars"] > row["off"]["text_chars"] > 0
        assert row["mcp_tools"] == row["servers"] * 5
        # The loader opens a session on its core list; MCP tools are not on it,
        # so their whole first-turn cost is the menu that names them.
        assert row["mcp_offered"] == 0
        assert row["on"]["native_chars"] == row["menu"]["chars"] + len(MENU_SEPARATOR)
        if row["servers"] >= 3:
            assert tool_cost.mcp_reduction(row, "native_chars") >= MIN_NATIVE_CUT
    menus = [r["menu"]["chars"] for r in rows]
    assert menus == sorted(menus)


def test_measuring_leaves_the_agents_tools_untouched(agents, largest):
    off, on = agents
    before = (off._instance_tools, on._instance_tools)
    tool_cost.measure_mcp_cost((2,), (3,), agents=agents)
    entries = convert_mock_servers(off, largest[0])
    measure_mcp_recall(on, entries, largest[1])
    assert (off._instance_tools, on._instance_tools) == before
    assert not any(name.startswith("mcp_") for name in on._instance_tools)


def test_mcp_recall_holds_the_floor_and_the_jump_ceiling(agents, largest):
    off, on = agents
    servers, probes = largest
    entries = convert_mock_servers(off, servers)
    recall_off = measure_mcp_recall(off, entries, probes)
    recall_on = measure_mcp_recall(on, entries, probes)
    assert (recall_off.listed, recall_off.jumps) == (len(probes), 0)
    assert recall_on.recall >= MCP_RECALL_FLOOR, recall_on.misses
    assert recall_on.jump_rate <= MCP_JUMP_RATE_CEILING
    assert (
        check_mcp_recall(recall_on, recall_off, MCP_RECALL_FLOOR, MCP_JUMP_RATE_CEILING)
        is None
    )


def test_a_loader_that_cannot_embed_fails_loudly(largest):
    def broken(text):
        raise ConnectionError("embedder down")

    off, on = tool_cost.build_mcp_cost_agents(embed_fn=broken)
    entries = convert_mock_servers(off, largest[0])
    with pytest.raises(RuntimeError, match="did not select"):
        measure_mcp_recall(on, entries, largest[1])
    with pytest.raises(RuntimeError, match="did not select"):
        tool_cost.measure_mcp_cost((1,), (1,), agents=(off, on))


def test_a_loader_outside_dynamic_tools_mode_fails_loudly(agents, largest, monkeypatch):
    off, on = agents
    # Memory off no longer idles the loader once MCP tools are present (#1972);
    # a hosted model still does.
    monkeypatch.setattr(on, "_hosted_model", lambda: True)
    entries = convert_mock_servers(off, largest[0])
    with pytest.raises(RuntimeError, match="did not select"):
        measure_mcp_recall(on, entries, largest[1])


def test_recall_rejects_empty_or_unlisted_probes(agents):
    off, _ = agents
    entries = convert_mock_servers(off, build_mock_servers(1, 1))
    with pytest.raises(ValueError, match="at least one probe"):
        measure_mcp_recall(off, entries, [])
    with pytest.raises(ValueError, match="no mock server lists"):
        measure_mcp_recall(off, entries, [("anything", "mcp_nowhere_tool")])


def test_recall_check_names_each_failure():
    full = MCPRecallReport(probes=4, listed=4, jumps=0)
    low = MCPRecallReport(4, 0, 2, misses=[("mcp_a_x", "r1"), ("mcp_a_y", "r2")])
    jumpy = MCPRecallReport(4, 1, 3)
    assert "mcp_a_x, mcp_a_y" in check_mcp_recall(low, full, 0.75)
    assert "probes or the mock servers" in check_mcp_recall(full, low, 0.5)
    assert "above the 50.0% ceiling" in check_mcp_recall(jumpy, full, 1.0, 0.5)
    assert check_mcp_recall(jumpy, full, 1.0) is None
    assert check_mcp_recall(full, full, 1.0, 0.0) is None


def test_cli_prints_the_report_and_passes(capsys):
    assert tool_cost.main(["--mcp", "--servers", "1", "--min-mcp-recall", "0"]) == 0
    out = capsys.readouterr().out
    assert "First-turn tokens the MCP tools add" in out
    assert "Paths that sent every MCP tool before #1972" in out
    assert "| 1 x 5 | 5 |" in out
    assert "the loader still thins native schemas" in out
    assert "**FAIL:**" not in out


def test_cli_exits_1_when_the_guard_fails(capsys, monkeypatch):
    monkeypatch.setattr(tool_recall, "check_mcp_recall", lambda *a: "forced failure")
    assert tool_cost.main(["--mcp", "--servers", "1", "--min-mcp-recall", "1"]) == 1
    assert "**FAIL:** forced failure" in capsys.readouterr().out


@pytest.mark.parametrize(
    "argv",
    [
        ["--servers", "2"],
        ["--max-mcp-jump-rate", "0.5"],
        ["--mcp", "--agent", "chat"],
        ["--mcp", "--servers", "0"],
        ["--mcp", "--servers", "x"],
        ["--mcp", "--tools-per-server", "99"],
        ["--mcp", "--min-mcp-recall", "5"],
        ["--mcp", "--max-mcp-jump-rate", "-1"],
        ["--mcp", "--scorecard", "scorecard.json"],
        ["--mcp", "--trials", "9"],
    ],
)
def test_cli_rejects_bad_mcp_flags(argv):
    with pytest.raises(SystemExit):
        tool_cost.main(argv)


@pytest.fixture(scope="module")
def gating_paths():
    return tool_cost.measure_gating_paths((1, 3, 5), (5,))


def test_newly_gated_paths_cut_the_first_turn_on_three_or_more_servers(gating_paths):
    rows = gating_paths["rows"]
    assert {r["path"] for r in rows} == {"mcp_only", "memory_off"}
    for row in rows:
        menu = row["menu"]["chars"] + len(MENU_SEPARATOR)
        if row["path"] == "memory_off":
            assert row["on"]["native_chars"] == menu
        else:
            # search_tools registers only once MCP tools arrive, so it is MCP cost.
            assert row["on"]["native_chars"] > menu
        if row["servers"] >= 3:
            assert tool_cost.mcp_reduction(row, "native_chars") >= MIN_NATIVE_CUT


def test_newly_gated_paths_hold_their_recall_and_keep_every_own_tool(gating_paths):
    recall = gating_paths["recall"]
    assert recall["mcp_only"].recall >= MCP_ONLY_RECALL, recall["mcp_only"].misses
    assert recall["memory_off"].recall >= MEMORY_OFF_RECALL, recall["memory_off"].misses
    assert gating_paths["own_tools_kept"] == {"mcp_only": True, "memory_off": True}


def test_search_tools_reaches_every_tool_the_first_turn_missed(agents, largest):
    from gaia_agent.chat_agent import TOOL_SEARCH_HITS

    off, on = agents
    servers, probes = largest
    entries = convert_mock_servers(off, servers)
    misses = measure_mcp_recall(on, entries, probes).misses
    assert misses
    # Exact with the stand-in embedder; re-run the harness and re-pin if a core
    # tool's description edit moves it.
    assert tool_recall.search_reach(on, entries, misses, TOOL_SEARCH_HITS) == len(
        misses
    )


def test_search_reach_needs_a_loader(agents):
    off, _ = agents
    with pytest.raises(ValueError, match="tool loader"):
        tool_recall.search_reach(off, {}, [], 5)


def test_an_mcp_only_agent_lists_every_own_tool_for_each_probe(largest):
    off, on = tool_cost.build_mcp_only_agents()
    servers, probes = largest
    entries = convert_mock_servers(off, servers)
    with tool_cost.with_mcp_tools(on, entries):
        own = [n for n in on._tools_registry if n not in entries]
        for request, _tool in probes:
            names = tool_cost.first_turn_tools(on, request)
            assert names is not None
            assert names[: len(own)] == own
