import sys
from importlib import import_module


def test_instantiate_new_agents():
    # Import without triggering heavy optional deps by relying on skip_lemonade
    chat_mod = import_module("gaia.agents.chat.lite_agent")
    docqa_mod = import_module("gaia.agents.docqa.agent")
    fileio_mod = import_module("gaia.agents.fileio.agent")

    chat = chat_mod.ChatAgentLite()
    assert chat is not None

    doc = docqa_mod.DocumentQAAgent()
    assert doc is not None

    f = fileio_mod.FileIOAgent()
    assert f is not None


def test_instantiate_browser_and_analyst_agents(tmp_path):
    browser_mod = import_module("gaia.agents.browser.agent")
    analyst_mod = import_module("gaia.agents.analyst.agent")

    browser = browser_mod.BrowserAgent()
    assert {"fetch_page", "search_web", "download_file"} <= set(
        browser.get_tools_info()
    )
    assert "query_data" not in browser.get_tools_info()
    browser.close()

    analyst = analyst_mod.AnalystAgent(
        analyst_mod.AnalystAgentConfig(
            scratchpad_db_path=str(tmp_path / "scratchpad.db")
        )
    )
    assert {
        "create_table",
        "insert_data",
        "query_data",
        "list_tables",
        "drop_table",
    } == set(analyst.get_tools_info())
    analyst.close()


def test_registry_uses_specialized_browser_and_analyst_agents(tmp_path):
    from gaia.agents.analyst.agent import AnalystAgent
    from gaia.agents.browser.agent import BrowserAgent
    from gaia.agents.registry import AgentRegistry

    registry = AgentRegistry()
    registry._register_builtin_agents()

    web = registry.create_agent("web")
    assert isinstance(web, BrowserAgent)
    assert {"fetch_page", "search_web", "download_file"} <= set(web.get_tools_info())
    web.close()

    data = registry.create_agent(
        "data", scratchpad_db_path=str(tmp_path / "scratchpad.db")
    )
    assert isinstance(data, AnalystAgent)
    assert "query_data" in data.get_tools_info()
    assert "fetch_page" not in data.get_tools_info()
    data.close()


def test_registry_uses_specialized_lite_browser_and_analyst_agents(tmp_path):
    # #1162: the "-lite" IDs are now legacy aliases for the base web/data
    # agents on the "lite" model tier, not separate registrations. The lite
    # model preset is read from the base agent's ``model_tiers``.
    from gaia.agents.analyst.agent import AnalystAgent
    from gaia.agents.browser.agent import BrowserAgent
    from gaia.agents.registry import AgentRegistry

    registry = AgentRegistry()
    registry._register_builtin_agents()

    def _lite_model(agent_id):
        tiers = registry.get(agent_id).model_tiers
        lite = next(t for t in tiers if t.name == "lite")
        return lite.models[0]

    lite_model = _lite_model("web")
    web = registry.create_agent("web-lite")
    assert isinstance(web, BrowserAgent)
    assert web.config.model_id == lite_model
    assert {"fetch_page", "search_web", "download_file"} <= set(web.get_tools_info())
    web.close()

    lite_model = _lite_model("data")
    data = registry.create_agent(
        "data-lite", scratchpad_db_path=str(tmp_path / "scratchpad.db")
    )
    assert isinstance(data, AnalystAgent)
    assert data.config.model_id == lite_model
    assert "query_data" in data.get_tools_info()
    assert "fetch_page" not in data.get_tools_info()
    data.close()


def test_browse_and_analyze_cli_list_tools(monkeypatch, tmp_path, capsys):
    from gaia import cli

    monkeypatch.setenv("HOME", str(tmp_path))
    original_argv = sys.argv
    try:
        sys.argv = ["gaia", "browse", "--no-lemonade-check", "--list-tools"]
        cli.main()
        browse_output = capsys.readouterr().out
        assert "Registered Tools for BrowserAgent" in browse_output
        assert "fetch_page" in browse_output
        assert "search_web" in browse_output
        assert "query_data" not in browse_output

        sys.argv = ["gaia", "analyze", "--no-lemonade-check", "--list-tools"]
        cli.main()
        analyze_output = capsys.readouterr().out
        assert "Registered Tools for AnalystAgent" in analyze_output
        assert "query_data" in analyze_output
        assert "create_table" in analyze_output
        assert "fetch_page" not in analyze_output
    finally:
        sys.argv = original_argv


def test_get_mcp_status_report_does_not_raise(tmp_path):
    """Regression: agents that inherit MCPClientMixin must survive
    ``get_mcp_status_report()`` even when MCP is not initialised.

    The Agent UI auto-calls this on every chat send
    (src/gaia/ui/_chat_helpers.py:1644). Before the fix, any agent whose MRO
    had ``MCPClientMixin`` after ``Agent`` raised
    ``AttributeError: '<Agent>' object has no attribute '_mcp_manager'``
    because ``Agent.__init__`` doesn't chain ``super().__init__()``.
    """
    from gaia.agents.analyst.agent import AnalystAgent, AnalystAgentConfig
    from gaia.agents.browser.agent import BrowserAgent
    from gaia.agents.chat.lite_agent import ChatAgentLite
    from gaia.agents.docqa.agent import DocumentQAAgent
    from gaia.agents.fileio.agent import FileIOAgent

    agents = [
        BrowserAgent(),
        AnalystAgent(AnalystAgentConfig(scratchpad_db_path=str(tmp_path / "s.db"))),
        DocumentQAAgent(),
        FileIOAgent(),
        ChatAgentLite(),
    ]
    try:
        for agent in agents:
            assert agent.get_mcp_status_report() == [], (
                f"{type(agent).__name__}.get_mcp_status_report() must return [] "
                f"when MCP is not initialised"
            )
            assert (
                agent._mcp_manager is None
            ), f"{type(agent).__name__}._mcp_manager must resolve to None"
    finally:
        for agent in agents:
            if hasattr(agent, "close"):
                agent.close()
