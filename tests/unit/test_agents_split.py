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
