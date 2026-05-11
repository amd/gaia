from importlib import import_module


def test_instantiate_new_agents_and_register_tools():
def test_instantiate_new_agents():
    # Import without triggering heavy optional deps by relying on skip_lemonade
    chat_mod = import_module("gaia.agents.chat.lite_agent")
    docqa_mod = import_module("gaia.agents.docqa.agent")
    fileio_mod = import_module("gaia.agents.fileio.agent")

    # Use the global tool registry to assert tools were registered
    from gaia.agents.base.tools import _TOOL_REGISTRY

    # Ensure clean registry for the test
    _TOOL_REGISTRY.clear()

    chat = chat_mod.ChatAgentLite()
    assert chat is not None

    # ChatAgentLite should register lightweight tools like screenshots
    assert "take_screenshot" in _TOOL_REGISTRY

    doc = docqa_mod.DocumentQAAgent()
    assert doc is not None

    # DocumentQAAgent should register RAG tools (query_documents)
    assert "query_documents" in _TOOL_REGISTRY

    f = fileio_mod.FileIOAgent()
    assert f is not None

    # FileIOAgent should register file I/O tools such as read_file
    assert "read_file" in _TOOL_REGISTRY
    chat = chat_mod.ChatAgentLite()
    assert chat is not None

    doc = docqa_mod.DocumentQAAgent()
    assert doc is not None

    f = fileio_mod.FileIOAgent()
    assert f is not None
