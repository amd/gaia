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
